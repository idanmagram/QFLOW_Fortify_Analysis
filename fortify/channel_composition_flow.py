# channel_composition_flow.py

import os, math, time, argparse
from datetime import datetime
from collections import defaultdict, deque
import itertools

# If tqdm is available you'll get progress bars; otherwise it falls back gracefully.
try:
    from tqdm import tqdm
except Exception:
    def tqdm(iterable, *args, **kwargs):
        return iterable


# =========================
# Gate / probability helpers
# =========================

def gate_inputs(expr):
    """Return input signal names for an expression node (arity-agnostic)."""
    if isinstance(expr, list):   # [op, in1, in2, ...]
        return expr[1:]
    if isinstance(expr, str):    # wire/alias
        return [expr]
    return []                    # int/None

def combine_gate_probs(op, probs):
    """
    Probability of gate output given input probabilities.
    Supports unary (Not/Buf) and n-ary (And/Or/Xor).
    """
    op = op.capitalize()
    if op == "Not":
        if len(probs) != 1: raise ValueError("Not expects 1 input")
        return 1.0 - probs[0]
    if op in ("Buf","Buffer"):
        if len(probs) != 1: raise ValueError("Buf expects 1 input")
        return probs[0]
    if op == "And":
        p = 1.0
        for q in probs: p *= q
        return p
    if op == "Or":
        p0 = 1.0
        for q in probs: p0 *= (1.0 - q)
        return 1.0 - p0
    if op == "Xor":
        if not probs: return 0.0
        p = probs[0]
        for q in probs[1:]:
            p = p + q - 2.0 * p * q
        return p
    # Add more ops here if your netlists contain them (Nand/Nor/Xnor, etc.)
    raise ValueError(f"Unsupported op: {op}")


# =========================
# Graph utilities (arity-safe)
# =========================

def topo_order(signalNames, truthTableMap):
    indeg = defaultdict(int)
    succ  = defaultdict(list)
    for y, e in truthTableMap.items():
        for v in gate_inputs(e):
            succ[v].append(y)
            indeg[y] += 1
    q = deque([s for s in signalNames if indeg[s] == 0 or indeg[s] == 1])
    order, seen = [], set()
    while q:
        u = q.popleft()
        if u in seen: continue
        seen.add(u)
        order.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    for s in signalNames:
        if s not in seen:
            order.append(s)
    return order

def compute_ref_deps(signalNames, truthTableMap, refSigBitNames):
    """For each node, which reference bits can reach it? (arity-safe)"""
    memo = {}
    refset = set(refSigBitNames)

    def dfs(u):
        if u in memo: return memo[u]
        e = truthTableMap.get(u, None)
        if e is None:
            memo[u] = ({u} if u in refset else set())
            return memo[u]
        if isinstance(e, int):
            memo[u] = set(); return memo[u]
        if isinstance(e, str):
            memo[u] = dfs(e); return memo[u]
        if isinstance(e, list):
            deps = set()
            for v in gate_inputs(e):
                deps |= dfs(v)
            memo[u] = deps; return memo[u]
        memo[u] = set(); return memo[u]

    return {s: dfs(s) for s in signalNames}


# =========================
# Channelization (disjoint, bigger, alias-aware)
# =========================

def _is_leaf(u, truthTableMap):
    e = truthTableMap.get(u, None)
    return (e is None) or isinstance(e, int)

def _is_alias(u, truthTableMap):
    return isinstance(truthTableMap.get(u, None), str)

def _collapse_alias(u, truthTableMap):
    """Follow pure alias chains to the ultimate base."""
    cur = u
    seen = set()
    while isinstance(truthTableMap.get(cur, None), str) and cur not in seen:
        seen.add(cur)
        cur = truthTableMap[cur]
    return cur

def _peel_alias(v, truthTableMap):
    """
    Return (base, chain) by following alias/buf chains *without* regard to 'assigned'.
    We include chain nodes into the region only if they are not assigned later.
    """
    chain = []
    cur = v
    while isinstance(truthTableMap.get(cur, None), str):
        chain.append(cur)
        cur = truthTableMap[cur]
    return cur, chain  # base (leaf or real gate), and alias nodes on the path


from collections import defaultdict, deque

def topo_order_channels(channels, truthTableMap):
    # Map canonical output -> producing channel index
    def canon(x): return _collapse_alias(x, truthTableMap)

    out_to_ch = {}
    for i, ch in enumerate(channels):
        for o in ch["outputs"]:
            out_to_ch[canon(o)] = i

    succ = defaultdict(set)
    indeg = [0]*len(channels)

    for j, ch in enumerate(channels):
        for t in ch["inputs"]:
            prod = out_to_ch.get(canon(t))
            if prod is not None and prod != j and j not in succ[prod]:
                succ[prod].add(j)
                indeg[j] += 1

    # Kahn topo sort
    q = deque([i for i,d in enumerate(indeg) if d==0])
    order_idx, seen = [], set()
    while q:
        i = q.popleft()
        if i in seen: continue
        seen.add(i); order_idx.append(i)
        for nb in succ[i]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                q.append(nb)

    # Append any leftovers (shouldn't happen in pure combinational)
    for i in range(len(channels)):
        if i not in seen:
            order_idx.append(i)

    return [channels[i] for i in order_idx]


def channelize(signalNames, truthTableMap, inputSigBitNames, max_frontier=8, min_region_nodes=4):
    """
    Disjoint channels; inputs are canonicalized to true sources (e.g., X), not pin nets.
    Each channel: {"inputs": S, "nodes": V, "outputs": boundary(V)}.
    """
    # Topo order
    indeg = defaultdict(int); succ = defaultdict(list)
    for y, e in truthTableMap.items():
        for v in gate_inputs(e):
            succ[v].append(y); indeg[y] += 1
    q = deque([s for s in inputSigBitNames])
    order, seen = [], set()
    while q:
        u = q.popleft()
        if u in seen: continue
        seen.add(u); order.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0: q.append(v)
    for s in signalNames:
        if s not in seen: order.append(s)

    # Consumers (for boundary + forward-closure)
    consumers = defaultdict(set)
    for y, e in truthTableMap.items():
        for v in gate_inputs(e):
            consumers[v].add(y)

    assigned = set()
    channels = []

    for root in order:
        # seed only at real gates not yet covered
        if root in assigned:
            continue
        er = truthTableMap.get(root, None)
        if _is_leaf(root, truthTableMap) or _is_alias(root, truthTableMap):
            # skip as seed, but don't mark assigned; we may peel through later
            continue
        if not isinstance(er, list):
            continue  # unknown node type as seed

        region   = set([root])
        frontier = set()
        qback = deque([root])

        # ---- backward growth with alias peeling ----
        while qback:
            u = qback.popleft()
            eu = truthTableMap.get(u, None)
            if isinstance(eu, list):
                for pred0 in gate_inputs(eu):
                    base, chain = _peel_alias(pred0, truthTableMap)

                    if base in assigned:
                        frontier.add(base); continue

                    if _is_leaf(base, truthTableMap):
                        frontier.add(base)
                        for n in chain:
                            if n not in assigned: region.add(n)
                        continue

                    # base is real internal gate; try to include it (and unassigned alias chain)
                    for n in chain:
                        if n not in assigned: region.add(n)
                    if base not in region:
                        region.add(base); qback.append(base)

            elif isinstance(eu, str):
                base, chain = _peel_alias(eu, truthTableMap)
                if base in assigned:
                    frontier.add(base); continue
                if _is_leaf(base, truthTableMap):
                    frontier.add(base)
                    for n in chain:
                        if n not in assigned: region.add(n)
                else:
                    for n in chain:
                        if n not in assigned: region.add(n)
                    if base not in region:
                        region.add(base); qback.append(base)

            # guard: if canonical frontier grows too much, stop expansion
            canon_frontier = { _collapse_alias(s, truthTableMap) for s in frontier }
            if len(canon_frontier) > max_frontier:
                break

        # ---- forward-closure to reach min size (doesn't change frontier) ----
        if len(region) < min_region_nodes:
            grew = True
            while grew and len(region) < min_region_nodes:
                grew = False
                for u in list(region):
                    for c in consumers[u]:
                        if c in assigned or c in region:
                            continue
                        ec = truthTableMap.get(c, None)
                        if isinstance(ec, str):
                            region.add(c); grew = True
                        elif isinstance(ec, list):
                            if set(gate_inputs(ec)).issubset(region):
                                region.add(c); grew = True

        # ---- boundary outputs ----
        outputs = set()
        for u in region:
            for c in consumers[u]:
                if c not in region:
                    outputs.add(u)
        if not outputs:
            outputs.add(root)

        # ---- canonicalize the frontier to true sources (e.g., X) ----
        inputs_sorted = sorted({ _collapse_alias(s, truthTableMap) for s in frontier })

        channels.append({
            "inputs":  inputs_sorted,
            "nodes":   sorted(region),
            "outputs": sorted(outputs),
        })

        assigned |= region  # disjointness
    print("channels: ", channels)
    return channels


# =========================
# Cascade + leakage
# =========================
def cascade_channels(signalNames, truthTableMap, refSigBitNames, inputSigBitNames,
                     outputs_of_interest=None, max_frontier=8, priors_override=None):
    """
    Partition → per-channel kernels (over EXTERNAL independent sources T) → cascade.
    Static/analytical: uses combine_gate_probs; no boolean simulation.
    Prints p(o | T) per channel and final p(y | r) at sinks.

    Returns: s_hat, s_hat_0, s_hat_1, leakage_dict, channels
    """
    import itertools
    from collections import defaultdict

    # ---------- probability pushing (static) ----------
    def combine_gate_probs(op, probs):
        """Closed-form probability identities for arbitrary arity."""
        if op == "Buf":  # rarely used (aliases handled by 'str'), but keep for completeness
            return probs[0]
        if op == "Not":
            return 1.0 - probs[0]
        if op == "And":
            out = 1.0
            for p in probs: out *= p
            return out
        if op == "Or":
            prod = 1.0
            for p in probs: prod *= (1.0 - p)
            return 1.0 - prod
        if op == "Xor":
            # fold XOR via p⊕q = p + q − 2pq; extend arity by reduction
            from functools import reduce
            return reduce(lambda a, b: a + b - 2*a*b, probs, 0.0)
        raise ValueError(f"Unknown op: {op}")

    def P_given_assumptions(u, assumptions, local_priors, cache):
        """Static P(u=1 | assignments on T): push probabilities through the DAG."""
        key = (u, tuple(sorted(assumptions.items())))
        if key in cache: return cache[key]

        # If T specifies this node, it's fixed (0/1)
        if u in assumptions:
            cache[key] = float(assumptions[u]); return cache[key]

        e = truthTableMap.get(u, None)
        if e is None:
            # Leaf outside channel / dangling: use prior (0.5 default)
            cache[key] = float(local_priors.get(u, 0.5)); return cache[key]
        if isinstance(e, int):
            cache[key] = float(e); return cache[key]
        if isinstance(e, str):
            cache[key] = P_given_assumptions(e, assumptions, local_priors, cache); return cache[key]
        if isinstance(e, list):
            op, ins = e[0], e[1:]
            probs = [P_given_assumptions(v, assumptions, local_priors, cache) for v in ins]
            cache[key] = combine_gate_probs(op, probs); return cache[key]
        raise ValueError(f"Unsupported expr for {u}: {e}")

    # ---------- helpers ----------
    def gate_inputs(e):
        if isinstance(e, list): return e[1:]
        if isinstance(e, str):  return [e]
        return []

    def channel_external_sources(ch):
        """
        EXTERNAL independent sources T for this channel:
        only (public inputs ∪ ref bits) that can reach the channel nodes.
        """
        source_pool = set(inputSigBitNames) | set(refSigBitNames)
        seen, sources = set(), set()

        def dfs(u):
            if u in seen: return
            seen.add(u)
            if u in source_pool:
                sources.add(u); return
            e = truthTableMap.get(u, None)
            if e is None or isinstance(e, int): return
            if isinstance(e, str):
                dfs(e); return
            if isinstance(e, list):
                for v in e[1:]: dfs(v); return

        for n in ch["nodes"]:
            dfs(n)
        return sorted(sources)

    def build_kernel_over_T(ch, local_priors):
        """
        For each channel output o, enumerate EXTERNAL sources T and build p(o | T) table.
        (This is the channel's probability matrix 'like in the paper', but defined on T.)
        """
        T = channel_external_sources(ch)
        outs = list(ch["outputs"])
        tables = {}
        cache = {}

        for o in outs:
            rows = []
            if not T:
                # No external sources → o has a fixed probability (constants/aliases/priors)
                p1 = P_given_assumptions(o, {}, local_priors, cache)
                rows.append(([], [1.0 - p1, p1]))
            else:
                for bits in itertools.product([0, 1], repeat=len(T)):
                    assm = {name: val for name, val in zip(T, bits)}
                    p1 = P_given_assumptions(o, assm, local_priors, cache)
                    rows.append((list(bits), [1.0 - p1, p1]))
            tables[o] = {"T": T, "rows": rows}
        return tables

    def mix_kernel_with_q_T(table, q_map):
        """
        LOTP over independent T:
        P(o=1) = sum_t  P(o=1 | t) * Π_i P(T_i = t_i)
        (for conditionals, q_map gives P(T_i=1 | r=v))
        """
        T = table["T"]
        p1 = 0.0
        for bits, probs in table["rows"]:
            w = 1.0
            for name, val in zip(T, bits):
                q = q_map.get(name, 0.5)
                w *= (q if val == 1 else (1.0 - q))
            p1 += w * probs[1]
        return p1

    def print_kernel_tables(ch_idx, tables):
        """Pretty-print p(o | T) per channel."""
        if not tables: return
        any_tbl = next(iter(tables.values()))
        T = any_tbl["T"]
        print(f"\n=== Channel {ch_idx} ===")
        print(f"Inputs T: {T}")
        print(f"Outputs: {list(tables.keys())}")
        for o, tbl in tables.items():
            print(f"  - Output: {o}")
            hdr = " ".join(tbl["T"]) if tbl["T"] else "(no T)"
            print(f"    T_bits ({hdr}) ->  P({o}=0|T)   P({o}=1|T)")
            for bits, probs in tbl["rows"]:
                bits_str = "".join(str(b) for b in bits) if bits else "-"
                print(f"    {bits_str:<16}    {probs[0]:.6f}     {probs[1]:.6f}")

    # ---------- priors ----------
    priors = {s: 0.5 for s in inputSigBitNames}
    priors.update({r: 0.5 for r in refSigBitNames})
    if priors_override: priors.update(priors_override)

    # ---------- channelize ----------
    channels = channelize(signalNames, truthTableMap, inputSigBitNames,
                          max_frontier=max_frontier, min_region_nodes=4)
    channels = topo_order_channels(channels, truthTableMap)

    # ---------- global storages ----------
    s_hat   = {}                # P(u=1)
    s_hat_0 = defaultdict(dict) # P(u=1 | r=0)
    s_hat_1 = defaultdict(dict) # P(u=1 | r=1)

    # ---------- cascade ----------
    for ch_idx, ch in enumerate(channels):
        # best (unconditional) priors available for this round
        local = dict(priors)
        for s in ch["inputs"]:
            if s in s_hat: local[s] = s_hat[s]

        # (A) Build & print per-channel matrices over EXTERNAL sources T
        kernel_T = build_kernel_over_T(ch, local)
        print_kernel_tables(ch_idx, kernel_T)

        # (B) Unconditional marginals for all nodes in the channel via LOTP over T
        #     (static prob pushing; no boolean eval)
        # (B) Unconditional marginals for all nodes in the channel via LOTP over the independent cut T
        T = list(
            next(iter(kernel_T.values()))["T"]) if kernel_T else []  # independent external sources for this channel
        V = ch["nodes"]
        cache = {}
        P1 = defaultdict(float)
        mass = 0.0

        print("\n[TRACE] === Unconditional marginals over T ===")
        print(f"[TRACE] T = {T if T else '∅'}")
        if T:
            print("[TRACE] Priors on T:")
            for t in T:
                print(f"[TRACE]   P({t}=1) = {local.get(t, 0.5):.8f}")

        if not T:
            # No external sources to enumerate: every node's probability is determined by constants/aliases/priors
            for u in V:
                pu = P_given_assumptions(u, {}, local, cache)
                P1[u] = pu
                print(f"[TRACE]   T=∅ → P({u}=1) = {pu:.8f} (from constants/aliases/priors)")
        else:
            total_rows = 2 ** len(T)
            print(f"[TRACE] Enumerating {total_rows} assignment(s) over T")
            for row_idx, bits in enumerate(itertools.product([0, 1], repeat=len(T))):
                assm = {name: val for name, val in zip(T, bits)}  # this row's T assignment
                bits_s = " ".join(f"{n}={v}" for n, v in assm.items())

                # Row weight w = Π_i P(T_i = t_i), using current best priors (assumed independent on T)
                w = 1.0
                for t, v in assm.items():
                    q = local.get(t, 0.5)  # prior P(T_i=1)
                    w *= (q if v == 1 else (1.0 - q))  # multiply in P(T_i=t_i)

                print(f"[TRACE]   Row {row_idx:>3}: {bits_s}  weight={w:.12f}")

                # Accumulate weighted conditional probabilities: P1[u] += w * P(u=1 | T=assm)
                for u in V:
                    pu_t = P_given_assumptions(u, assm, local, cache)  # P(u=1 | T=bits)
                    P1[u] += w * pu_t
                    print(f"[TRACE]           u={u}: P(u|row)={pu_t:.12f}  contrib=+{(w * pu_t):.12f}")

                mass += w

            print(f"[TRACE] Sum of row weights (mass) = {mass:.12f}")
            # Numerical hygiene: if floating error made mass ≠ 1, renormalize the mixture
            if mass > 0 and abs(mass - 1.0) > 1e-8:
                print(f"[TRACE] Normalizing by mass (was {mass:.12f})")
                for u in V:
                    P1[u] /= mass

        print("[TRACE] Final P(u=1) for channel nodes (cascaded downstream):")
        for u, p in P1.items():
            s_hat[u] = p  # stores P(u=1)
            print(f"[TRACE]   {u}: {p:.12f}")

        # (C) Conditionals w.r.t. each secret bit r: mix the precomputed channel kernel with P(T | r)
        print("[TRACE] === Conditionals per secret bit (mix kernel with P(T|r)) ===")
        if T:
            Tset = set(T)
            for r in refSigBitNames:
                if r not in Tset:
                    print(f"[TRACE]   skip r={r} (not in T)")
                    continue  # this channel does not depend on r at its boundary

                for v in (0, 1):
                    q = {}
                    for t in T:
                        if t == r:
                            q[t] = float(v)  # clamp the secret bit itself (P(r=v|r=v)=1)
                        else:
                            # If upstream provided P(t|r=v), use it; otherwise fall back to local prior for t
                            if v == 0:
                                q[t] = s_hat_0.get(t, {}).get(r, local.get(t, 0.5))
                            else:
                                q[t] = s_hat_1.get(t, {}).get(r, local.get(t, 0.5))
                    print(f"[TRACE]   Using P(T|{r}={v}): " + ", ".join(f"{t}={q[t]:.8f}" for t in T))

                    # For each channel output o, compute P(o=1 | r=v) by mixing p(o|T) with q(T|r=v)
                    for o, tbl in kernel_T.items():
                        py1 = mix_kernel_with_q_T(tbl, q)
                        if v == 0:
                            s_hat_0[o][r] = py1  # store P(o=1 | r=0)
                        else:
                            s_hat_1[o][r] = py1  # store P(o=1 | r=1)
                        print(f"[TRACE]     → P({o}=1 | {r}={v}) = {py1:.12f}")
        else:
            # No T at all: outputs depend only on constants/aliases; the conditional is same for r=0 and r=1
            print("[TRACE]   T=∅ → conditionals identical for r=0 and r=1")
            for r in refSigBitNames:
                for o, tbl in kernel_T.items():
                    p1 = tbl["rows"][0][1][1]  # the single row's P(o=1|T)
                    s_hat_0[o][r] = p1
                    s_hat_1[o][r] = p1
                    print(f"[TRACE]     P({o}=1 | {r}=0/1) = {p1:.12f} (single-row kernel)")

    # ---------- choose real outputs (sinks) ----------
    if outputs_of_interest is None:
        consumers = defaultdict(int)
        for y, e in truthTableMap.items():
            for v in gate_inputs(e): consumers[v] += 1
        outputs_of_interest = sorted({s for s in signalNames if consumers[s] == 0})

    # Print final end-to-end 2×2 p(y|r) matrices (from s_hat_0/_1)
    print_final_output_kernels(outputs_of_interest, refSigBitNames, s_hat_0, s_hat_1)

    # ---------- PBV leakage from final matrices ----------
    leakage = defaultdict(dict)
    for y in outputs_of_interest:
        for r in refSigBitNames:
            p10 = s_hat_0.get(y, {}).get(r, None)  # P(y=1|r=0)
            p11 = s_hat_1.get(y, {}).get(r, None)  # P(y=1|r=1)
            if p10 is None or p11 is None: continue
            t0 = max(1.0 - p10, 1.0 - p11)
            t1 = max(p10,       p11)
            pbv = 0.5 * (t0 + t1)
            leakage[y][r] = 2.0 * pbv  # multiplicative Bayes leakage (vs prior 0.5)

    print("s_hat ",s_hat)
    return s_hat, s_hat_0, s_hat_1, leakage, channels


def print_final_output_kernels(outputs_of_interest, refSigBitNames, s_hat_0, s_hat_1):
    print("\n=== Final end-to-end kernels p(y | r) (after cascading) ===")
    for y in outputs_of_interest:
        for r in refSigBitNames:
            if r not in s_hat_0.get(y, {}) or r not in s_hat_1.get(y, {}):
                continue  # y doesn’t depend on r (or not yet computed)
            p10 = s_hat_0[y][r]  # P(y=1 | r=0)
            p11 = s_hat_1[y][r]  # P(y=1 | r=1)
            p00 = 1.0 - p10
            p01 = 1.0 - p11
            print(f"\nOutput: {y}  |  Secret: {r}")
            print("           r=0         r=1")
            print(f"y=0   {p00:10.6f}  {p01:10.6f}")
            print(f"y=1   {p10:10.6f}  {p11:10.6f}")


def compute_signal_leakage_scores(sigWidths, s_hat, s_hat_0, s_hat_1, refSigBitNames):
    """
    Keeps your original scoring per bus: normalize (p0-p1)^2 by y_bar and sum over bits.
    """
    baseLeak = 1.0 / math.sqrt(max(1, len(refSigBitNames)))
    sigLeaks = {}

    for sig in sigWidths:
        width = sigWidths[sig]
        leak_acc = []
        ok = True
        for j in range(width):
            sigName = f"{sig}[{j}:{j}]"
            y = s_hat.get(sigName, 0.5)
            y_bar = 2.0 * y * (1.0 - y)
            denom = 4.0 * y_bar * (1.0 - y_bar)

            bit_leak = 0.0
            for ref in refSigBitNames:
                p0 = s_hat_0.get(sigName, {}).get(ref, y)
                p1 = s_hat_1.get(sigName, {}).get(ref, y)
                lv = (p0 - p1) ** 2
                if lv > 0 and denom != 0:
                    lv = lv / math.sqrt(denom)
                bit_leak += lv
            leak_acc.append(bit_leak ** 2)
        if ok:
            val = math.sqrt(sum(leak_acc)) * baseLeak
            sigLeaks[sig] = min(1.0, val)
    return sigLeaks


# =========================
# Main (wired to your pipeline)
# =========================

def main(input_file_path, top_module_name, ref_module_name, ref_instance_name,
         ref_sig_name, ref_sig_width, design, leaks_file_path, time_file_path):
    import module_maps  # your existing parser that returns the bit-blasted graph

    startTime = time.time()

    print("\n******************************************************************")
    print("Design:", design)
    print()
    os.environ["PATH"] = r"C:\iverilog\bin;" + os.environ.get("PATH", "")

    # reference signal bit names
    refSigBitNames = [f"{ref_sig_name}[{j}:{j}]" for j in range(ref_sig_width)]

    # Parse RTL → graph (bit-level)
    inputNames, inputWidths, signalNames, sigWidths, truthTableMap = \
        module_maps.subCircuitExtract(input_file_path,
                                      top_module_name,
                                      ref_module_name,
                                      ref_instance_name,
                                      refSigBitNames)

    # input signal bit names (public)
    inputSigBitNames = []
    for inp, wid in zip(inputNames, inputWidths):
        inputSigBitNames.extend([f"{inp}[{i}:{i}]" for i in range(wid)])

    # Channel cascade (static, LOTP)
    MAX_FRONTIER = 8
    s_hat, s_hat_0, s_hat_1, leakage_matrix, channels = cascade_channels(
        signalNames=signalNames,
        truthTableMap=truthTableMap,
        refSigBitNames=refSigBitNames,
        inputSigBitNames=inputSigBitNames,
        outputs_of_interest=None,
        max_frontier=MAX_FRONTIER,
        priors_override=None
    )

    # Your original per-signal leakage scoring
    print("\nScoring leakage per signal...")
    sigLeaks = compute_signal_leakage_scores(sigWidths, s_hat, s_hat_0, s_hat_1, refSigBitNames)

    endTime = time.time()

    print("\nNumber of signals: {}".format(len(sigLeaks)))
    print("Total time taken: {:.4f}s".format(endTime - startTime))

    with open(time_file_path, "w") as tf:
        tf.write("Number of signals: {}\n".format(len(sigLeaks)))
        tf.write("Total time taken: {:.4f}s\n".format(endTime - startTime))

    with open(leaks_file_path, "w") as lf:
        lf.write("%s,%s\n" % ("Signal", "Leakage"))
        for sig in sorted(sigLeaks, key=sigLeaks.get, reverse=True):
            lf.write("%s,%.4f\n" % (sig, sigLeaks[sig]))

    print("\nCompleted!")
    print("******************************************************************\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Channel-cascade QIF analysis (static LOTP)')
    parser.add_argument('InputFilePath', type=str)
    parser.add_argument('TopModuleName', type=str)
    parser.add_argument('RefModuleName', type=str)
    parser.add_argument('RefInstanceName', type=str)
    parser.add_argument('RefSigName', type=str)
    parser.add_argument('RefSigWidth', type=int)
    parser.add_argument('Design', type=str)
    parser.add_argument('-r', '--results-path', type=str,
                        help='directory within results/ (default: timestamp)')
    args = parser.parse_args()

    input_file_path = args.InputFilePath
    top_module_name = args.TopModuleName
    ref_module_name = args.RefModuleName
    ref_instance_name = args.RefInstanceName
    ref_sig_name = args.RefSigName
    ref_sig_width = args.RefSigWidth
    design = args.Design

    results_path = args.results_path
    if results_path:
        results_path = os.path.join('results', results_path, design)
    else:
        results_path = os.path.join('results', datetime.today().strftime('%Y-%m-%d-%H:%M:%S'), design)
    os.makedirs(results_path, exist_ok=True)

    leaks_file_path = os.path.join(results_path, 'leaks.txt')
    time_file_path  = os.path.join(results_path, 'time.txt')

    main(input_file_path, top_module_name, ref_module_name, ref_instance_name,
         ref_sig_name, ref_sig_width, design, leaks_file_path, time_file_path)
