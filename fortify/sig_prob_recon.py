# sig_prob_recon.py
import sys
import os
import multiprocessing as mp
from itertools import product
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.setrecursionlimit(100000)

# -----------------------
# Tunable parallel knobs
# -----------------------
PAR_REF_THRESHOLD = 8      # parallelize conditional loop if >= this many refs
CUT_ASSIGN_PAR_MIN = 16    # parallelize cutset enumeration if > this many assignments

def _in_subprocess() -> bool:
    return mp.current_process().name != "MainProcess"

# ------------------------------------------------------------
# Incremental signal probability (valid when inputs are
# independent *under the current conditioning*).
# ------------------------------------------------------------
def incSigProb(a, b, op):
    op = op.capitalize()
    if op == "And":
        return a * b
    elif op == "Or":
        return a + b - a * b
    elif op == "Xor":
        return a + b - 2.0 * a * b
    elif op == "Nand":
        return 1.0 - a * b
    elif op == "Nor":
        return (1.0 - a) * (1.0 - b)
    else:
        raise ValueError(f"Unsupported op {op}")

def gate_formula(op, pA, pB):
    # same semantics, just without 'independence' wording
    return incSigProb(pA, pB, op)

# ------------------------------------------------------------------
# Helper: extract signal names referenced by an expression node
# Forms supported:
#   - int constant
#   - str alias (e.g., 'mod.sig[0:0]')
#   - list gate: ['Not', x] or ['And'/'Or'/'Xor'/'Nand'/'Nor', a, b]
# ------------------------------------------------------------------
def _extract_signal_names(exp):
    if isinstance(exp, int):
        return set()
    if isinstance(exp, str):
        return {exp}
    if isinstance(exp, list):
        op = exp[0]
        if op == "Not":
            return _extract_signal_names(exp[1])
        if op == "Mix":
            out = set()
            for part in exp[1:]:
                out |= _extract_signal_names(part)
            return out
        # Fallback: union all children beyond op position
        out = set()
        for part in exp[1:]:
            out |= _extract_signal_names(part)
        return out
    return set()

# ------------------------------------------------------------------
# Factorized prior over variables. If you have a joint prior,
# pass a callable prior(assign_dict) -> probability.
# ------------------------------------------------------------------
def _pz(var_names, bits, prior_map_or_callable, fallback_prob=0.5):
    if callable(prior_map_or_callable):
        return float(prior_map_or_callable(dict(zip(var_names, bits))))
    p = 1.0
    for name, bit in zip(var_names, bits):
        p1 = float(prior_map_or_callable.get(name, fallback_prob))
        p *= p1 if bit else (1.0 - p1)
    return p

# ------------------------------------------------------------------
# Clamp-aware probability evaluator:
#   returns P(sig=1 | clamps), recursively using truthTableMap.
#   Caches per (sig, frozenset(clamps.items())).
#   NOTE: This combines sub-probs with gate_formula; for exact handling
#         of reconvergence between the *two* inputs of a single gate,
#         use the cutset helpers below at the caller level.
# ------------------------------------------------------------------
def prob_with_clamps(sig, truthTableMap, clamps, cache):
    key = (sig, frozenset(clamps.items()))
    if key in cache:
        return cache[key]

    if sig in clamps:
        cache[key] = float(clamps[sig])
        return cache[key]

    if sig not in truthTableMap:
        cache[key] = 0.5  # unknown leaf default
        return cache[key]

    exp = truthTableMap[sig]

    if isinstance(exp, int):
        cache[key] = float(exp)
        return cache[key]

    if isinstance(exp, str):
        p = prob_with_clamps(exp, truthTableMap, clamps, cache)
        cache[key] = p
        return p

    if isinstance(exp, list):
        op = exp[0]
        if op == "Not":
            c = exp[1]
            p = 1.0 - prob_with_clamps(c, truthTableMap, clamps, cache)
            cache[key] = p
            return p
        if op == "Mix":
            parts = exp[1:]
            if not parts:
                cache[key] = 0.0
                return cache[key]
            ps = [prob_with_clamps(part, truthTableMap, clamps, cache) for part in parts]
            p = sum(ps) / len(ps)
            cache[key] = p
            return p
        else:
            a, b = exp[1], exp[2]
            pA = prob_with_clamps(a, truthTableMap, clamps, cache)
            pB = prob_with_clamps(b, truthTableMap, clamps, cache)
            p = gate_formula(op, pA, pB)
            cache[key] = p
            return p

    cache[key] = 0.5
    return cache[key]

# ========== Parallel helpers (top-level & picklable) ==========

def _chunk_bits(all_bits, n):
    L = list(all_bits)
    if n <= 1 or len(L) <= n:
        return [L]
    size = (len(L) + n - 1) // n
    return [L[i:i + size] for i in range(0, len(L), size)]

def _gate_prob_chunk(op, a, b, truthTableMap, clamps_base, prior_map_or_callable, bits_chunk, Z):
    cache = {}
    total = 0.0
    for bits in bits_chunk:
        z_assign = dict(zip(Z, bits))
        pz = _pz(Z, bits, prior_map_or_callable)
        if pz <= 0.0:
            continue
        if clamps_base:
            clamps = {**clamps_base, **z_assign}
        else:
            clamps = z_assign
        pA_z = prob_with_clamps(a, truthTableMap, clamps, cache)
        pB_z = prob_with_clamps(b, truthTableMap, clamps, cache)
        total += gate_formula(op, pA_z, pB_z) * pz
    return total

def _cond_for_one_ref(ref, op, a, b,
                      s0_a, s1_a, s0_b, s1_b,
                      truthTableMap, depinfo, inputSigBitNames,
                      prior_map_or_callable, max_cut):
    # Independence given {ref}?
    if depinfo is None or _indep_given(depinfo, a, b, {ref}, pi_set=inputSigBitNames):
        p0 = incSigProb(s0_a[ref], s0_b[ref], op)
        p1 = incSigProb(s1_a[ref], s1_b[ref], op)
        return ref, p0, p1
    else:
        # In a worker, we'll fall back to serial cutset enumeration inside these calls
        p0 = gate_prob_depaware_with_clamps(
            op, a, b, truthTableMap, depinfo, inputSigBitNames,
            prior_map_or_callable or {}, {ref: 0}, max_cut
        )
        p1 = gate_prob_depaware_with_clamps(
            op, a, b, truthTableMap, depinfo, inputSigBitNames,
            prior_map_or_callable or {}, {ref: 1}, max_cut
        )
        return ref, p0, p1

# ------------------------------------------------------------------
# Reconvergence-aware gate probability (unconditional):
#   choose a small cutset Z from shared ancestors of a and b.
#   Sum over z in {0,1}^|Z|: P(Y|z) P(z).
#   NOTE: we *restrict shared ancestors to primary inputs* via inputSigBitNames.
# ------------------------------------------------------------------
def gate_prob_depaware(op, a, b, truthTableMap, depinfo,
                       prior_map_or_callable, inputSigBitNames,
                       max_cut=3, rare_thresh=0.1):
    # shared ancestors among primary inputs (exclude the nodes themselves for cutset)
    Sa = depinfo.ancestors.get(a, set())
    Sb = depinfo.ancestors.get(b, set())
    shared = ((Sa | {a}) & (Sb | {b}) & set(inputSigBitNames)) - {a, b}

    if not shared:
        cache = {}
        pA = prob_with_clamps(a, truthTableMap, {}, cache)
        pB = prob_with_clamps(b, truthTableMap, {}, cache)
        return gate_formula(op, pA, pB)

    # pick a small cutset Z ⊆ shared (heuristics: fanout/depth if available)
    Z = list(shared)
    if hasattr(depinfo, "fanout"):
        Z.sort(key=lambda z: -depinfo.fanout.get(z, 0))
    if hasattr(depinfo, "depth"):
        Z.sort(key=lambda z: -depinfo.depth.get(z, 0))
    Z = Z[:max_cut]

    num_assign = 1 << len(Z)

    # Small job or inside a worker → do it serially
    if num_assign <= CUT_ASSIGN_PAR_MIN or _in_subprocess():
        pY = 0.0
        cache = {}
        for bits in product((0, 1), repeat=len(Z)):
            pz = _pz(Z, bits, prior_map_or_callable)
            if pz < rare_thresh:
                continue
            z_assign = dict(zip(Z, bits))
            pA_z = prob_with_clamps(a, truthTableMap, z_assign, cache)
            pB_z = prob_with_clamps(b, truthTableMap, z_assign, cache)
            pY += gate_formula(op, pA_z, pB_z) * pz
        return pY

    # Parallel over assignments (each worker keeps its own cache)
    all_bits = [bits for bits in product((0, 1), repeat=len(Z))
                if _pz(Z, bits, prior_map_or_callable) >= rare_thresh]
    if not all_bits:
        return 0.0

    workers = min(os.cpu_count() or 4, max(1, len(all_bits) // 4))
    chunks = _chunk_bits(all_bits, workers)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(
            _gate_prob_chunk,
            [op]*len(chunks), [a]*len(chunks), [b]*len(chunks),
            [truthTableMap]*len(chunks),
            [None]*len(chunks),  # clamps_base None here
            [prior_map_or_callable]*len(chunks),
            chunks, [Z]*len(chunks)
        ))
    return sum(parts)

# ------------------------------------------------------------------
# Same as above, but WITH external clamps (e.g., ref=0/1).
# We remove clamped names from the cutset, then condition on Z as well.
#   NOTE: cutset is restricted to primary inputs via inputSigBitNames.
# ------------------------------------------------------------------
def gate_prob_depaware_with_clamps(op, a, b, truthTableMap, depinfo,
                                   inputSigBitNames, prior_map_or_callable, clamps,
                                   max_cut=3, rare_thresh=0.1):
    clamp_names = set(clamps.keys())
    Sa = depinfo.ancestors.get(a, set())
    Sb = depinfo.ancestors.get(b, set())
    shared = ((Sa | {a}) & (Sb | {b}) & set(inputSigBitNames)) - (clamp_names | {a, b})

    if not shared:
        cache = {}
        pA = prob_with_clamps(a, truthTableMap, clamps, cache)
        pB = prob_with_clamps(b, truthTableMap, clamps, cache)
        return gate_formula(op, pA, pB)

    Z = list(shared)
    if hasattr(depinfo, "fanout"):
        Z.sort(key=lambda z: -depinfo.fanout.get(z, 0))
    if hasattr(depinfo, "depth"):
        Z.sort(key=lambda z: -depinfo.depth.get(z, 0))
    Z = Z[:max_cut]

    num_assign = 1 << len(Z)

    # Small job or inside a worker → do it serially
    if num_assign <= CUT_ASSIGN_PAR_MIN or _in_subprocess():
        pY = 0.0
        cache = {}
        for bits in product((0, 1), repeat=len(Z)):
            pz = _pz(Z, bits, prior_map_or_callable)
            if pz < rare_thresh:
                continue
            clamps_z = {**clamps, **dict(zip(Z, bits))}
            pA_z = prob_with_clamps(a, truthTableMap, clamps_z, cache)
            pB_z = prob_with_clamps(b, truthTableMap, clamps_z, cache)
            pY += gate_formula(op, pA_z, pB_z) * pz
        return pY

    # Parallel over assignments (each worker keeps its own cache)
    all_bits = [bits for bits in product((0, 1), repeat=len(Z))
                if _pz(Z, bits, prior_map_or_callable) >= rare_thresh]
    if not all_bits:
        return 0.0

    workers = min(os.cpu_count() or 4, max(1, len(all_bits) // 4))
    chunks = _chunk_bits(all_bits, workers)
    clamps_base = dict(clamps)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(
            _gate_prob_chunk,
            [op]*len(chunks), [a]*len(chunks), [b]*len(chunks),
            [truthTableMap]*len(chunks),
            [clamps_base]*len(chunks),
            [prior_map_or_callable]*len(chunks),
            chunks, [Z]*len(chunks)
        ))
    return sum(parts)

# ------------------------------------------------------------------
# Small helpers for independence checks using depinfo
#   NOTE: Independence is checked *only through primary inputs* if pi_set provided.
# ------------------------------------------------------------------
def _indep(depinfo, a, b, pi_set=None):
    Sa = depinfo.ancestors.get(a, set())
    Sb = depinfo.ancestors.get(b, set())
    shared = (Sa | {a}) & (Sb | {b})
    if pi_set is not None:
        shared &= set(pi_set)
    return len(shared) == 0

def _indep_given(depinfo, a, b, clamp_names, pi_set=None):
    clamp_names = set(clamp_names)
    Sa = depinfo.ancestors.get(a, set()) - clamp_names
    Sb = depinfo.ancestors.get(b, set()) - clamp_names
    shared = ( (Sa | ({a} - clamp_names)) & (Sb | ({b} - clamp_names)) )
    if pi_set is not None:
        shared &= set(pi_set)
    return len(shared) == 0

# ------------------------------------------------------------------
# Public API: populateSigProbs (dependency-aware, conditional-aware)
#    - s_hat[s]           = P(s=1)
#    - s_hat_0[s][ref]    = P(s=1 | ref=0)
#    - s_hat_1[s][ref]    = P(s=1 | ref=1)
# ------------------------------------------------------------------
def populateSigProbs(sig, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                     truthTableMap, refSigBitNames, inputSigBitNames, inputNames,
                     depinfo=None, prior_map_or_callable=None, max_cut=3):
    if sig in s_hat:
        return

    if sig in encounteredSigs:
        # cycle guard
        s_hat[sig] = 0.0
        s_hat_0[sig] = {ref: 0.0 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0.0 for ref in refSigBitNames}
        return

    encounteredSigs.add(sig)

    if sig in truthTableMap:
        exp = truthTableMap[sig]

        # constant
        if isinstance(exp, int):
            val = float(exp)
            s_hat[sig] = val
            s_hat_0[sig] = {ref: val for ref in refSigBitNames}
            s_hat_1[sig] = {ref: val for ref in refSigBitNames}

        # alias
        elif isinstance(exp, str):
            populateSigProbs(exp, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                             truthTableMap, refSigBitNames, inputSigBitNames, inputNames,
                             depinfo, prior_map_or_callable, max_cut)
            s_hat[sig] = s_hat[exp]
            s_hat_0[sig] = {ref: s_hat_0[exp][ref] for ref in refSigBitNames}
            s_hat_1[sig] = {ref: s_hat_1[exp][ref] for ref in refSigBitNames}

        # gate
        elif isinstance(exp, list):
            op = exp[0]
            if op == "Not":
                c = exp[1]
                populateSigProbs(c, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                                 truthTableMap, refSigBitNames, inputSigBitNames, inputNames,
                                 depinfo, prior_map_or_callable, max_cut)
                s_hat[sig]   = 1.0 - s_hat[c]
                s_hat_0[sig] = {ref: 1.0 - s_hat_0[c][ref] for ref in refSigBitNames}
                s_hat_1[sig] = {ref: 1.0 - s_hat_1[c][ref] for ref in refSigBitNames}

            else:
                a, b = exp[1], exp[2]
                # recurse first so children's s_hat / s_hat_0/1 exist
                populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                                 truthTableMap, refSigBitNames, inputSigBitNames, inputNames,
                                 depinfo, prior_map_or_callable, max_cut)
                populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                                 truthTableMap, refSigBitNames, inputSigBitNames, inputNames,
                                 depinfo, prior_map_or_callable, max_cut)

                # UNCONDITIONAL
                if depinfo is None or _indep(depinfo, a, b, pi_set=inputSigBitNames):
                    s_hat[sig] = incSigProb(s_hat[a], s_hat[b], op)
                else:
                    s_hat[sig] = gate_prob_depaware(
                        op, a, b, truthTableMap, depinfo,
                        prior_map_or_callable or {}, inputSigBitNames, max_cut
                    )

                # CONDITIONAL per ref (parallelized when many refs)
                s_hat_0[sig] = {}
                s_hat_1[sig] = {}

                s0_a, s1_a = s_hat_0[a], s_hat_1[a]
                s0_b, s1_b = s_hat_0[b], s_hat_1[b]

                do_parallel = (len(refSigBitNames) >= PAR_REF_THRESHOLD) and not _in_subprocess()

                if do_parallel:
                    max_workers = min(os.cpu_count() or 4, len(refSigBitNames))
                    with ProcessPoolExecutor(max_workers=max_workers) as ex:
                        futs = [
                            ex.submit(_cond_for_one_ref, ref, op, a, b,
                                      s0_a, s1_a, s0_b, s1_b,
                                      truthTableMap, depinfo, inputSigBitNames,
                                      prior_map_or_callable, max_cut)
                            for ref in refSigBitNames
                        ]
                        for f in as_completed(futs):
                            r, p0, p1 = f.result()
                            s_hat_0[sig][r] = p0
                            s_hat_1[sig][r] = p1
                else:
                    for ref in refSigBitNames:
                        r, p0, p1 = _cond_for_one_ref(ref, op, a, b,
                                                      s0_a, s1_a, s0_b, s1_b,
                                                      truthTableMap, depinfo, inputSigBitNames,
                                                      prior_map_or_callable, max_cut)
                        s_hat_0[sig][r] = p0
                        s_hat_1[sig][r] = p1

    else:
        # Unknown net (shouldn't happen for well-formed maps)
        s_hat[sig] = 0.5
        s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}

    encounteredSigs.remove(sig)

# ------------------------------------------------------------------
# Picklable DepInfo (moved to top-level so it can be sent to workers)
# ------------------------------------------------------------------
class DepInfo:
    __slots__ = ("ancestors", "parents", "fanout", "depth", "universe")
    def __init__(self, ancestors, parents, fanout, depth, universe):
        self.ancestors = ancestors
        self.parents   = parents
        self.fanout    = fanout
        self.depth     = depth
        self.universe  = universe

# ------------------------------------------------------------------
# Dependency extraction (parents / fanout / depth / ancestors)
# over the FULL universe: design signals + internal pins in exps.
# ------------------------------------------------------------------
def build_dependency_info(truthTableMap, signalNames):
    universe = set(signalNames) | set(truthTableMap.keys())
    for exp in truthTableMap.values():
        universe |= _extract_signal_names(exp)

    # immediate parents
    parents = {s: set() for s in universe}
    for s in universe:
        exp = truthTableMap.get(s, None)
        if exp is not None:
            parents[s] = _extract_signal_names(exp)

    # fanout
    fanout = {s: 0 for s in universe}
    for s, ps in parents.items():
        for p in ps:
            fanout[p] = fanout.get(p, 0) + 1

    # depth (cycle-safe)
    depth_memo, visiting = {}, set()
    def depth_of(s):
        if s in depth_memo:
            return depth_memo[s]
        if s in visiting:
            depth_memo[s] = 0
            return 0
        visiting.add(s)
        ps = parents.get(s, set())
        d = 0 if not ps else 1 + max(depth_of(p) for p in ps)
        visiting.remove(s)
        depth_memo[s] = d
        return d
    depth = {s: depth_of(s) for s in universe}

    # ancestors (transitive closure; cycle-safe)
    anc_memo, visiting = {}, set()
    def ancestors_of(s):
        if s in anc_memo:
            return anc_memo[s]
        if s in visiting:
            return set()
        visiting.add(s)
        ps = parents.get(s, set())
        ancs = set(ps)
        for p in ps:
            ancs |= ancestors_of(p)
        visiting.remove(s)
        anc_memo[s] = ancs
        return ancs
    ancestors = {s: ancestors_of(s) for s in universe}

    return DepInfo(ancestors=ancestors, parents=parents, fanout=fanout, depth=depth, universe=universe)
