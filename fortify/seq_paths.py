# seq_paths.py
# Stage 2: Loop detection + invalid path pruning (depinfo-agnostic: dict or object).

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Tuple, Set, Optional, Iterable

# -------------------------------------------------------------------
# DepInfo adapter: support both dict-style and attribute-style depinfo
# -------------------------------------------------------------------

def _first_present(obj, names: Iterable[str]):
    """Return the first present attribute/key from names; else None."""
    # try attributes first
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    # then try dict-style
    if isinstance(obj, dict):
        for n in names:
            if n in obj:
                return obj[n]
    return None

def _get_ancestors_map(depinfo):
    # common variants we’ve seen across Fortify/QFlow code
    return _first_present(depinfo, [
        'ancestors_map', 'ancestors', 'ancestor_map', 'ancestorsMap'
    ]) or {}

def _get_fanout_map(depinfo):
    return _first_present(depinfo, [
        'fanout_map', 'fanout', 'fanoutMap', 'descendants_map', 'children_map'
    ]) or {}

def _get_parents_map(depinfo):
    return _first_present(depinfo, [
        'parents_map', 'parents', 'parent_map', 'parentsMap'
    ]) or {}

def _get_depth_map(depinfo):
    return _first_present(depinfo, [
        'depth_map', 'depth', 'signal_depth', 'depthMap'
    ]) or {}

def _get_universe(depinfo):
    return _first_present(depinfo, [
        'universe', 'signals', 'nodes'
    ]) or set()

# -------------------------------------------------------------------
# Public API types
# -------------------------------------------------------------------

@dataclass
class PathCheckResult:
    sink: str
    signal_chain: List[str]         # secret ... -> ... -> sink (bit-level)
    state_seq: List[str]            # per-signal inferred state labels / "<comb>"
    primary_leak_state: Optional[str]
    min_loop_iters: int             # >=1 if loop, else 0
    note: str = ""

@dataclass
class PathReject:
    sink: str
    reason: str

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _ancestors_of(sig: str, depinfo) -> List[str]:
    amap = _get_ancestors_map(depinfo)
    return list(amap.get(sig, []))

def _descendants_of(sig: str, depinfo) -> List[str]:
    fmap = _get_fanout_map(depinfo)
    return list(fmap.get(sig, []))

def _is_ref(sig: str, refSigBits: Set[str]) -> bool:
    return sig in refSigBits

def _signal_base(sig: str) -> str:
    return sig.split('[')[0] if '[' in sig else sig

# ----------------------
# FSM-aware annotations
# ----------------------

def _index_assign_states(cond_assigns, state_var: Optional[str]) -> Dict[str, Set[str]]:
    """
    Map each LHS base name -> set of states in which it is (re)assigned.
    Uses textual guards; extracts tokens like '(state == S)'.
    """
    lhs_states = defaultdict(set)
    if not state_var:
        return lhs_states
    token = f"{state_var} =="
    for ca in cond_assigns:
        lhsb = _signal_base(ca.lhs)
        g = ca.guard or ""
        parts = g.split('(')
        for p in parts:
            q = p.strip()
            if q.startswith(token):
                toks = q.replace(')', ' ').replace('&&',' ').replace('||',' ').split()
                if len(toks) >= 3:
                    S = toks[2]
                    lhs_states[lhsb].add(S)
    return lhs_states


def _map_signal_to_states(sig_chain: List[str],
                          lhs_assign_states: Dict[str, Set[str]]) -> List[str]:
    """
    For each signal in the chain, pick *some* state in which it's written; else <comb>.
    """
    seq = []
    for s in sig_chain:
        base = _signal_base(s)
        if base in lhs_assign_states and lhs_assign_states[base]:
            seq.append(sorted(lhs_assign_states[base])[0])  # deterministic choice
        else:
            seq.append("<comb>")
    return seq

def _first_index(lst: List[str], pred) -> int:
    for i, x in enumerate(lst):
        if pred(x): return i
    return -1

def _prune_overwrite(sig_chain: List[str],
                     state_seq: List[str],
                     cond_assigns,
                     refSigBits: Set[str]) -> Tuple[bool, str]:
    """
    Rule (v): invalidate if an intermediate is overwritten by non-secret before reaching sink.
    Heuristic: If a signal gets an assignment in a later state whose RHS has no ref bit mention, drop the path.
    """
    ref_names = list(refSigBits)
    def rhs_tainted(rhs: str) -> bool:
        return any(rn in rhs for rn in ref_names)

    by_lhs = defaultdict(list)
    for ca in cond_assigns:
        by_lhs[_signal_base(ca.lhs)].append(ca)

    # walk chain (skip sink)
    for idx, s in enumerate(sig_chain[:-1]):
        base = _signal_base(s)
        # look for later-state writes to the same base
        later_states = set(st for st in state_seq[idx+1:] if st != "<comb>")
        if not later_states:
            continue
        for ca in by_lhs.get(base, []):
            if rhs_tainted(ca.rhs):
                continue
            g = ca.guard or ""
            # coarse: if any later state appears in guard, treat as overwrite
            if any(st in g for st in later_states):
                return False, f"overwrite of {base} by non-secret before sink"
    return True, ""

def _validate_fsm_order(state_seq: List[str], fsm) -> Tuple[bool, str]:
    """
    Rule (iv): sequence must respect FSM transitions (ignore <comb>, collapse duplicates).
    """
    adj = defaultdict(set)
    for src, edges in fsm.transitions.items():
        if src == "_ANY_":  # ignore generic
            continue
        for _, dst in edges:
            adj[src].add(dst)

    states = [s for s in state_seq if s != "<comb>"]
    if not states:
        return True, ""  # purely combinational

    collapsed = [states[0]]
    for s in states[1:]:
        if s != collapsed[-1]:
            collapsed.append(s)

    for a, b in zip(collapsed, collapsed[1:]):
        if b not in adj.get(a, set()):
            return False, f"illegal transition {a} -> {b}"
    return True, ""

def _find_primary_leak_state(state_seq: List[str]) -> Optional[str]:
    for s in reversed(state_seq):
        if s != "<comb>":
            return s
    return None

def _detect_loop_and_min_iters(state_seq: List[str], fsm) -> int:
    """
    Rule (vi): detect loops in the state sequence and estimate minimal steps
    from first to last concrete state via BFS on FSM adjacency.
    """
    concrete = [s for s in state_seq if s != "<comb>"]
    if len(concrete) < 2:
        return 0
    loop_present = len(set(concrete)) < len(concrete)
    if not loop_present:
        return 0

    # BFS minimal steps
    adj = defaultdict(set)
    for src, edges in fsm.transitions.items():
        if src == "_ANY_": continue
        for _, dst in edges:
            adj[src].add(dst)

    start, target = concrete[0], concrete[-1]
    if start == target:
        return 1

    q = deque([(start, 0)])
    seen = {start}
    while q:
        u, d = q.popleft()
        if u == target:
            return max(1, d)
        for v in adj.get(u, set()):
            if v not in seen:
                seen.add(v)
                q.append((v, d+1))
    return 1  # conservative

# -------------------------------------------------------------------
# Path harvesting and Stage-2 orchestration
# -------------------------------------------------------------------
from typing import List, Tuple, Dict, Iterable
from collections import defaultdict

# --- helpers ---------------------------------------------------------

def _is_ref(sig: str, refset: set) -> bool:
    return sig in refset

def _parents_of(sig: str,
                depinfo,
                truthTableMap: Dict[str, object],
                extract_names_fn=None) -> Iterable[str]:
    """
    Prefer depinfo.parents if present; otherwise extract from truthTableMap[sig].
    """
    # depinfo.parents may exist (recommended)
    if hasattr(depinfo, "parents"):
        return depinfo.parents.get(sig, set())

    # fallback: parse from truthTableMap entry
    if extract_names_fn is None:
        # minimal inline extractor (int, str, ['Not', x], ['And'/'Or'/..., a, b])
        def extract_names(exp):
            if isinstance(exp, int):
                return set()
            if isinstance(exp, str):
                return {exp}
            if isinstance(exp, list):
                op = exp[0]
                if op == "Not":
                    return extract_names(exp[1])
                return extract_names(exp[1]) | extract_names(exp[2])
            return set()
        extract_names_fn = extract_names

    exp = truthTableMap.get(sig, None)
    if exp is None:
        return set()
    return extract_names_fn(exp)

def _is_output(sig: str, depinfo) -> bool:
    """A true sink has zero fanout."""
    if hasattr(depinfo, "fanout"):
        return depinfo.fanout.get(sig, 0) == 0
    # if no fanout info, conservatively treat nothing as a sink
    return False

def _expand_bits_from_truthmap(base: str, truthTableMap: Dict[str, object]) -> List[str]:
    """
    If caller passed a bus base (e.g., 'foo.bar'), expand to bit names present in truthTableMap.
    If already a bit (e.g., 'foo.bar[3:3]'), just return it.
    """
    if "[" in base and ":" in base and base.endswith("]"):
        return [base]
    pref = base + "["
    return [k for k in truthTableMap.keys() if isinstance(k, str) and k.startswith(pref)]

# --- DFS path harvester ----------------------------------------------

def harvest_candidate_paths(sigLeaks_sorted: List[Tuple[str, float]],
                            truthTableMap: Dict[str, object],
                            depinfo,
                            refSigBitNames: List[str],
                            top_k_sinks: int = 500000) -> List[Tuple[str, List[str]]]:
    """
    Build candidate paths by DFS **from outputs only** (fanout==0) backward to the first reference bit.
    Returns list of (sink_bit, chain [ref ... sink_bit]).
    - Uses depinfo.parents (if available) to walk backward.
    - Stops at the first ref encountered per sink bit.
    """
    refset = set(refSigBitNames)
    out: List[Tuple[str, List[str]]] = []

    # iterate by descending leakage (already sorted by caller)
    count = 0
    for sink_name, _ in sigLeaks_sorted:
        if count >= top_k_sinks:
            break

        # Expand to bits as needed
        sink_bits = _expand_bits_from_truthmap(sink_name, truthTableMap)
        if not sink_bits:
            # if nothing to expand and we can’t confirm bit presence, skip
            continue

        # keep only those bits that are *true outputs* (fanout == 0)
        sink_bits = [sb for sb in sink_bits if _is_output(sb, depinfo)]
        if not sink_bits:
            continue

        count += 1

        for sb in sink_bits:
            # classic DFS until reference; record first path found
            found_path = None
            visited = set()

            def dfs(cur: str, path_rev: List[str]) -> bool:
                # path_rev builds from sink upward; we’ll reverse when done
                if cur in visited:
                    return False
                visited.add(cur)

                if _is_ref(cur, refset):
                    # found a ref; finalize path as [ref ... sink]
                    nonlocal found_path
                    found_path = list(reversed(path_rev + [cur]))
                    return True

                # walk to parents (ancestors)
                parents = _parents_of(cur, depinfo, truthTableMap)
                # If no parents, dead-end
                if not parents:
                    return False

                for p in parents:
                    if dfs(p, path_rev + [cur]):
                        return True
                return False

            if dfs(sb, []):
                # found_path is [ref ... sb]; append (sink_bit, path)
                out.append((sb, found_path))

    return out


def stage2_validate_and_annotate(
    candidate_paths: List[Tuple[str, List[str]]],
    cond_assigns,
    fsm,
    depinfo,
    refSigBitNames: List[str]
) -> Tuple[List[PathCheckResult], List[PathReject]]:
    """
    Apply Step 2 checks (ii–vi): FSM-order validation, overwrite pruning,
    primary leaking cycle, and loop-iteration estimate.
    """
    refset = set(refSigBitNames)
    state_var = next(iter(fsm.state_regs)) if fsm.state_regs else None
    lhs_assign_states = _index_assign_states(cond_assigns, state_var)

    valids: List[PathCheckResult] = []
    rejects: List[PathReject] = []

    for sink_bit, chain in candidate_paths:
        state_seq = _map_signal_to_states(chain, lhs_assign_states)

        ok, why = _validate_fsm_order(state_seq, fsm)
        if not ok:
            rejects.append(PathReject(sink_bit, f"fsm-order: {why}"))
            continue

        ok, why = _prune_overwrite(chain, state_seq, cond_assigns, refset)
        if not ok:
            rejects.append(PathReject(sink_bit, f"overwrite: {why}"))
            continue

        primary = _find_primary_leak_state(state_seq)
        min_iters = _detect_loop_and_min_iters(state_seq, fsm)

        valids.append(PathCheckResult(
            sink=sink_bit,
            signal_chain=chain,
            state_seq=state_seq,
            primary_leak_state=primary,
            min_loop_iters=min_iters,
            note=""
        ))

    return valids, rejects
