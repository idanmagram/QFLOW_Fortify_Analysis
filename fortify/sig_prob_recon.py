# sig_prob_recon.py
import sys
from itertools import product
sys.setrecursionlimit(100000)

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
        return _extract_signal_names(exp[1]) | _extract_signal_names(exp[2])
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
        else:
            a, b = exp[1], exp[2]
            pA = prob_with_clamps(a, truthTableMap, clamps, cache)
            pB = prob_with_clamps(b, truthTableMap, clamps, cache)
            p = gate_formula(op, pA, pB)
            cache[key] = p
            return p

    cache[key] = 0.5
    return cache[key]

# ------------------------------------------------------------------
# Reconvergence-aware gate probability (unconditional):
#   choose a small cutset Z from shared ancestors of a and b.
#   Sum over z in {0,1}^|Z|: P(Y|z) P(z).
# ------------------------------------------------------------------
def gate_prob_depaware(op, a, b, truthTableMap, depinfo,
                       prior_map_or_callable, max_cut=3, rare_thresh=1e-12):
    # shared ancestors (exclude the nodes themselves for cutset)
    Sa = depinfo.ancestors.get(a, set())
    Sb = depinfo.ancestors.get(b, set())
    shared = (Sa | {a}) & (Sb | {b})
    shared -= {a, b}

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

    pY = 0.0
    cache = {}
    for bits in product((0, 1), repeat=len(Z)):
        z_assign = dict(zip(Z, bits))
        pz = _pz(Z, bits, prior_map_or_callable)
        if pz < rare_thresh:
            continue
        pA_z = prob_with_clamps(a, truthTableMap, z_assign, cache)
        pB_z = prob_with_clamps(b, truthTableMap, z_assign, cache)
        pY  += gate_formula(op, pA_z, pB_z) * pz
    return pY

# ------------------------------------------------------------------
# Same as above, but WITH external clamps (e.g., ref=0/1).
# We remove clamped names from the cutset, then condition on Z as well.
# ------------------------------------------------------------------
def gate_prob_depaware_with_clamps(op, a, b, truthTableMap, depinfo,
                                   prior_map_or_callable, clamps,
                                   max_cut=3, rare_thresh=1e-12):
    clamp_names = set(clamps.keys())
    Sa = depinfo.ancestors.get(a, set())
    Sb = depinfo.ancestors.get(b, set())
    shared = (Sa | {a}) & (Sb | {b})
    shared -= (clamp_names | {a, b})

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

    pY = 0.0
    cache = {}
    for bits in product((0, 1), repeat=len(Z)):
        z_assign = dict(zip(Z, bits))
        pz = _pz(Z, bits, prior_map_or_callable)
        if pz < rare_thresh:
            continue
        clamps_z = {**clamps, **z_assign}
        pA_z = prob_with_clamps(a, truthTableMap, clamps_z, cache)
        pB_z = prob_with_clamps(b, truthTableMap, clamps_z, cache)
        pY  += gate_formula(op, pA_z, pB_z) * pz
    return pY

# ------------------------------------------------------------------
# Small helpers for independence checks using depinfo
# ------------------------------------------------------------------
def _indep(depinfo, a, b):
    Sa = depinfo.ancestors.get(a, set())
    Sb = depinfo.ancestors.get(b, set())
    return len((Sa | {a}) & (Sb | {b})) == 0

def _indep_given(depinfo, a, b, clamp_names):
    Sa = depinfo.ancestors.get(a, set()) - set(clamp_names)
    Sb = depinfo.ancestors.get(b, set()) - set(clamp_names)
    return len((Sa | ({a} - set(clamp_names))) &
               (Sb | ({b} - set(clamp_names)))) == 0

# ------------------------------------------------------------------
# Public API: populateSigProbs (dependency-aware, conditional-aware)
#    - s_hat[s]           = P(s=1)
#    - s_hat_0[s][ref]    = P(s=1 | ref=0)
#    - s_hat_1[s][ref]    = P(s=1 | ref=1)
# ------------------------------------------------------------------
def populateSigProbs(sig, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                     truthTableMap, refSigBitNames, inputSigBitNames,
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
                             truthTableMap, refSigBitNames, inputSigBitNames,
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
                                 truthTableMap, refSigBitNames, inputSigBitNames,
                                 depinfo, prior_map_or_callable, max_cut)
                s_hat[sig]   = 1.0 - s_hat[c]
                s_hat_0[sig] = {ref: 1.0 - s_hat_0[c][ref] for ref in refSigBitNames}
                s_hat_1[sig] = {ref: 1.0 - s_hat_1[c][ref] for ref in refSigBitNames}

            else:
                a, b = exp[1], exp[2]
                # recurse first so children's s_hat / s_hat_0/1 exist
                populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                                 truthTableMap, refSigBitNames, inputSigBitNames,
                                 depinfo, prior_map_or_callable, max_cut)
                populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                                 truthTableMap, refSigBitNames, inputSigBitNames,
                                 depinfo, prior_map_or_callable, max_cut)

                # UNCONDITIONAL
                if depinfo is None or _indep(depinfo, a, b):
                    s_hat[sig] = incSigProb(s_hat[a], s_hat[b], op)
                else:
                    s_hat[sig] = gate_prob_depaware(op, a, b, truthTableMap, depinfo,
                                                    prior_map_or_callable or {}, max_cut)

                # CONDITIONAL per ref
                s_hat_0[sig] = {}
                s_hat_1[sig] = {}
                for ref in refSigBitNames:
                    # If independent GIVEN {ref}, combine children's conditionals
                    if depinfo is None or _indep_given(depinfo, a, b, {ref}):
                        s_hat_0[sig][ref] = incSigProb(s_hat_0[a][ref], s_hat_0[b][ref], op)
                        s_hat_1[sig][ref] = incSigProb(s_hat_1[a][ref], s_hat_1[b][ref], op)
                    else:
                        # Still dependent given ref → small cutset + clamps
                        s_hat_0[sig][ref] = gate_prob_depaware_with_clamps(
                            op, a, b, truthTableMap, depinfo,
                            prior_map_or_callable or {}, {ref: 0}, max_cut
                        )
                        s_hat_1[sig][ref] = gate_prob_depaware_with_clamps(
                            op, a, b, truthTableMap, depinfo,
                            prior_map_or_callable or {}, {ref: 1}, max_cut
                        )

    else:
        # Unknown net (shouldn't happen for well-formed maps)
        s_hat[sig] = 0.5
        s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}

    encounteredSigs.remove(sig)

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

    # pack
    class DepInfo:
        __slots__ = ("ancestors", "parents", "fanout", "depth", "universe")
        def __init__(self, ancestors, parents, fanout, depth, universe):
            self.ancestors = ancestors
            self.parents   = parents
            self.fanout    = fanout
            self.depth     = depth
            self.universe  = universe

    return DepInfo(ancestors=ancestors, parents=parents, fanout=fanout, depth=depth, universe=universe)
