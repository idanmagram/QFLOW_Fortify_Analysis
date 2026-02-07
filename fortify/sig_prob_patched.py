
import sys
sys.setrecursionlimit(100000)
_SUPPORT_CACHE = {}
_SUPPORT_INPROG = set()
_SUPPORT_EXPR_INPROG = set()

# incremental signal probability formulae for the standard logic gates
def incSigProb(a, b, op):
    if op == "And":
        return a * b
    elif op == "Or":
        return a + b - a * b
    elif op == "Xor":
        return a + b - 2 * a * b
    elif op == "Eq":
        return a * b + (1 - a) * (1 - b)
    elif op == "NotEq":
        return a + b - 2 * a * b
    else:
        raise ValueError("Unknown op: " + op)

def _key(sig):
    """
    Convert potentially nested list-based expressions into a hashable tuple
    so they can be used as dict/set keys.
    """
    if isinstance(sig, list):
        return tuple(_key(x) for x in sig)
    return sig



# ---------------- Reconvergence-aware dynamic programming ----------------
# Operationally:
# 1) We evaluate in a dependency-respecting order (recursive DFS behaves like topo for each cone).
# 2) When we hit a reconvergent gate (fanin supports overlap), we resolve it by conditioning on a
#    small cut of the shared sources, cache the result, and "seal" it.
# 3) Later uses treat sealed nodes as atomic (do not look back to ancestors again).
#
# This is dynamic programming on reconvergent cones.

# Cache of which nodes have been reconvergence-resolved ("sealed cones")
_SEALED_CONES = set()

# Memoized support sets: key -> set(atomic sources)
_SUPPORT_CACHE = {}

# Clamp-eval memo: (ctx_tag, key, clamps_key) -> probability
_CLAMP_MEMO = {}

def _is_atomic_source(k: str, truthTableMap, inputSigBitNames, refSigBitNames) -> bool:
    # A conservative definition: anything without a known defining expression is atomic,
    # plus explicit inputs/refs if provided.
    return (k in inputSigBitNames) or (k in refSigBitNames) or (k not in truthTableMap)

def _support(sig, truthTableMap, stop_at_sealed=True):
    """
    Leaf-only support. Returns a set of string signal-bit names.
    Cycle-safe: if we detect recursion/cycle, we stop expanding and treat as leaf.
    Uses global cache for speed.
    """

    # constants
    if sig == 0 or sig == '0' or sig is None:
        return set()
    if sig == 1 or sig == '1':
        return set()

    # expression node
    if isinstance(sig, (list, tuple)):
        expr_key = _key(sig)
        if expr_key in _SUPPORT_CACHE:
            return _SUPPORT_CACHE[expr_key]
        if expr_key in _SUPPORT_EXPR_INPROG:
            return set()

        _SUPPORT_EXPR_INPROG.add(expr_key)
        try:
            op = sig[0] if len(sig) > 0 else None
            out = set()

            if op == 'Not' and len(sig) > 1:
                out |= _support(sig[1], truthTableMap, stop_at_sealed)
            elif op == 'Cond':
                if len(sig) > 1:
                    out |= _support(sig[1], truthTableMap, stop_at_sealed)
                if len(sig) > 2:
                    out |= _support(sig[2], truthTableMap, stop_at_sealed)
                if len(sig) > 3:
                    out |= _support(sig[3], truthTableMap, stop_at_sealed)
            elif op == 'Mix':
                for part in sig[1:]:
                    out |= _support(part, truthTableMap, stop_at_sealed)
            else:
                known_ops = {"And", "Or", "Xor", "Eq", "NotEq", "Srl", "Sll", "Plus", "Times", "Minus", "EqBus"}
                if isinstance(op, str) and op in known_ops:
                    if len(sig) > 1:
                        out |= _support(sig[1], truthTableMap, stop_at_sealed)
                    if len(sig) > 2:
                        out |= _support(sig[2], truthTableMap, stop_at_sealed)
                    if len(sig) > 3:
                        out |= _support(sig[3], truthTableMap, stop_at_sealed)
                else:
                    # Treat as concatenation/unknown composite: traverse all elements
                    for part in sig:
                        out |= _support(part, truthTableMap, stop_at_sealed)

            out = {x for x in out if isinstance(x, str)}
            _SUPPORT_CACHE[expr_key] = out
            return out
        finally:
            _SUPPORT_EXPR_INPROG.remove(expr_key)

    # from here: must be a string key
    if not isinstance(sig, str):
        return set()

    k = sig

    # stop at sealed cones
    if stop_at_sealed and k in _SEALED_CONES:
        return {k}

    # memoized
    if k in _SUPPORT_CACHE:
        return _SUPPORT_CACHE[k]

    # cycle detection
    if k in _SUPPORT_INPROG:
        # break cycle / deep recursion: treat as leaf
        return {k}

    _SUPPORT_INPROG.add(k)
    try:
        if k in truthTableMap:
            s = _support(truthTableMap[k], truthTableMap, stop_at_sealed)
        else:
            s = {k}  # leaf/input
        _SUPPORT_CACHE[k] = s
        return s
    finally:
        _SUPPORT_INPROG.remove(k)



def _ctx_prob_of_key(k, s_hat, s_hat_0, s_hat_1, refSigBitNames, ctx):
    """Fetch cached probability for key k under context ctx."""
    if ctx is None:
        return s_hat.get(k, 0.5)
    ref, ref_val = ctx
    if ref not in refSigBitNames:
        return s_hat.get(k, 0.5)
    if ref_val == 0:
        return s_hat_0.get(k, {}).get(ref, s_hat.get(k, 0.5))
    else:
        return s_hat_1.get(k, {}).get(ref, s_hat.get(k, 0.5))

def _prob_with_clamps(sig, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                      s_hat, s_hat_0, s_hat_1, ctx=None, _stack=None):
    """Evaluate P(sig=1 | clamps, ctx) while respecting sealed-cone boundaries."""
    k = _key(sig)
    if isinstance(k, int):
        return float(k)
    if k in clamps:
        return float(clamps[k])

    # sealed nodes are treated as atomic: do not look back
    if k in _SEALED_CONES and k in s_hat:
        return _ctx_prob_of_key(k, s_hat, s_hat_0, s_hat_1, refSigBitNames, ctx)

    # memo key
    clamps_key = tuple(sorted(clamps.items()))
    memo_key = (ctx, k, clamps_key)
    if memo_key in _CLAMP_MEMO:
        return _CLAMP_MEMO[memo_key]

    if _stack is None:
        _stack = set()
    if k in _stack:
        # cycle fallback
        val = 0.5
        _CLAMP_MEMO[memo_key] = val
        return val
    _stack.add(k)

    # If we don't know how it's defined, treat it atomic using cached prob in ctx
    if k not in truthTableMap:
        if k not in s_hat:
            populateSigProbs(k, set(), s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
        val = _ctx_prob_of_key(k, s_hat, s_hat_0, s_hat_1, refSigBitNames, ctx)
    else:
        exp = truthTableMap.get(sig, truthTableMap.get(k))
        if isinstance(exp, int):
            val = float(exp)
        elif isinstance(exp, str):
            val = _prob_with_clamps(exp, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                    s_hat, s_hat_0, s_hat_1, ctx, _stack)
        elif isinstance(exp, list):
            op = exp[0] if len(exp) > 0 else None
            if op in ("Not",):
                a = exp[1] if len(exp) > 1 else 0
                pa = _prob_with_clamps(a, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                       s_hat, s_hat_0, s_hat_1, ctx, _stack)
                val = 1 - pa
            elif op in ("And", "Or", "Xor", "Eq", "NotEq"):
                a = exp[1] if len(exp) > 1 else 0
                b = exp[2] if len(exp) > 2 else 0
                pa = _prob_with_clamps(a, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                       s_hat, s_hat_0, s_hat_1, ctx, _stack)
                pb = _prob_with_clamps(b, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                       s_hat, s_hat_0, s_hat_1, ctx, _stack)
                val = incSigProb(pa, pb, op)
            elif op == "Cond":
                cond = exp[1] if len(exp) > 1 else 0
                tval = exp[2] if len(exp) > 2 else 0
                fval = exp[3] if len(exp) > 3 else 0
                pc = _prob_with_clamps(cond, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                       s_hat, s_hat_0, s_hat_1, ctx, _stack)
                pt = _prob_with_clamps(tval, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                       s_hat, s_hat_0, s_hat_1, ctx, _stack)
                pf = _prob_with_clamps(fval, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                       s_hat, s_hat_0, s_hat_1, ctx, _stack)
                val = pc * pt + (1 - pc) * pf
            elif op == "Mix":
                parts = exp[1:]
                if not parts:
                    val = 0.0
                else:
                    probs = [
                        _prob_with_clamps(p, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                          s_hat, s_hat_0, s_hat_1, ctx, _stack)
                        for p in parts
                    ]
                    val = sum(probs) / len(probs)
            else:
                # unknown op fallback
                val = _ctx_prob_of_key(k, s_hat, s_hat_0, s_hat_1, refSigBitNames, ctx)
        else:
            val = _ctx_prob_of_key(k, s_hat, s_hat_0, s_hat_1, refSigBitNames, ctx)

    _stack.remove(k)
    _CLAMP_MEMO[memo_key] = val
    return val

def _enumerate_bits(n):
    for mask in range(1 << n):
        yield [(mask >> i) & 1 for i in range(n)]

def _resolve_reconvergent_gate(op, a, b, truthTableMap, inputSigBitNames, refSigBitNames,
                              s_hat, s_hat_0, s_hat_1, max_cut=3):
    """Reconvergence-aware computation for a single binary gate."""
    sa = _support(a, truthTableMap)
    sb = _support(b, truthTableMap)
    shared = sorted(list(sa & sb))
    if not shared:
        # independence is OK when supports do not overlap
        p = incSigProb(s_hat[_key(a)], s_hat[_key(b)], op)
        p0 = {ref: incSigProb(s_hat_0[_key(a)][ref], s_hat_0[_key(b)][ref], op) for ref in refSigBitNames}
        p1 = {ref: incSigProb(s_hat_1[_key(a)][ref], s_hat_1[_key(b)][ref], op) for ref in refSigBitNames}
        return p, p0, p1, False

    #print("op ",op ," shared ", shared)
    cut = shared[:max_cut]
    # ensure cut sources have cached probs
    for zsig in cut:
        # zsig is a key; populate may have stored under exact string
        if zsig not in s_hat:
            populateSigProbs(zsig, set(), s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

    # Unconditional
    p_total = 0.0
    for bits in _enumerate_bits(len(cut)):
        clamps = dict(zip(cut, bits))
        pz = 1.0
        for zs, bv in clamps.items():
            pz *= (s_hat.get(zs, 0.5) if bv == 1 else (1 - s_hat.get(zs, 0.5)))
        pa = _prob_with_clamps(a, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                               s_hat, s_hat_0, s_hat_1, ctx=None)
        pb = _prob_with_clamps(b, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                               s_hat, s_hat_0, s_hat_1, ctx=None)
        p_total += pz * incSigProb(pa, pb, op)

    # Per-ref contexts
    p0_map = {}
    p1_map = {}
    for ref in refSigBitNames:
        p0 = 0.0
        p1 = 0.0
        for bits in _enumerate_bits(len(cut)):
            clamps = dict(zip(cut, bits))

            # P(z | ref=0/1) approximated from cached conditional probs of cut sources
            pz0 = 1.0
            pz1 = 1.0
            for zs, bv in clamps.items():
                q0 = _ctx_prob_of_key(zs, s_hat, s_hat_0, s_hat_1, refSigBitNames, (ref, 0))
                q1 = _ctx_prob_of_key(zs, s_hat, s_hat_0, s_hat_1, refSigBitNames, (ref, 1))
                pz0 *= (q0 if bv == 1 else (1 - q0))
                pz1 *= (q1 if bv == 1 else (1 - q1))

            pa0 = _prob_with_clamps(a, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                    s_hat, s_hat_0, s_hat_1, ctx=(ref, 0))
            pb0 = _prob_with_clamps(b, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                    s_hat, s_hat_0, s_hat_1, ctx=(ref, 0))
            p0 += pz0 * incSigProb(pa0, pb0, op)

            pa1 = _prob_with_clamps(a, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                    s_hat, s_hat_0, s_hat_1, ctx=(ref, 1))
            pb1 = _prob_with_clamps(b, clamps, truthTableMap, inputSigBitNames, refSigBitNames,
                                    s_hat, s_hat_0, s_hat_1, ctx=(ref, 1))
            p1 += pz1 * incSigProb(pa1, pb1, op)

        p0_map[ref] = p0
        p1_map[ref] = p1

    return p_total, p0_map, p1_map, True

# recursive signal probability and conditional signal probability calculation
def populateSigProbs(sig, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames):
    key = _key(sig)
    fallback = 0.5
    if key == "top.TSC.beeps[0:0]":
        print("idna")

    # to avoid recomputation of already calculated signal probability values
    if key in s_hat:
        return

    # to avoid infinite recursion caused by circular dependencies, assigning zero signal probabilities
    if key in encounteredSigs:
        s_hat[key] = fallback
        s_hat_0[key] = {ref: fallback for ref in refSigBitNames}
        s_hat_1[key] = {ref: fallback for ref in refSigBitNames}
        return

    encounteredSigs.add(key)

    # Expression nodes (e.g., Eq, Cond) that are not standalone signals
    if isinstance(sig, list):
        op = sig[0]
        known_ops = {"Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq", "Srl", "Sll", "Plus", "Times", "Minus", "EqVec", "EqBus"}
        if op not in known_ops:
            # Treat as pass-through of first element if unrecognized op (e.g., concatenation artifacts)
            target = op
            populateSigProbs(target, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = s_hat[_key(target)]
            s_hat_0[key] = {ref: s_hat_0[_key(target)][ref] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat_1[_key(target)][ref] for ref in refSigBitNames}
            return

        if op == "Cond":
            cond = sig[1]
            tval = sig[2] if len(sig) > 2 else 0
            fval = sig[3] if len(sig) > 3 else 0

            populateSigProbs(cond, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            populateSigProbs(tval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            # if fval is None (e.g., truncated unroll), treat as 0
            if fval is not None:
                populateSigProbs(fval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            else:
                s_hat[_key(fval)] = 0
                s_hat_0[_key(fval)] = {ref: 0 for ref in refSigBitNames}
                s_hat_1[_key(fval)] = {ref: 0 for ref in refSigBitNames}

            p_cond = s_hat[_key(cond)]
            s_hat[key] = p_cond * s_hat[_key(tval)] + (1 - p_cond) * s_hat[_key(fval)]
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                p0 = s_hat_0[_key(cond)][ref]
                p1 = s_hat_1[_key(cond)][ref]
                #p0 = 0.5
                #p1 = 0.5
                s_hat_0[key][ref] = p0 * s_hat_0[_key(tval)][ref] + (1 - p0) * s_hat_0[_key(fval)][ref]
                s_hat_1[key][ref] = p1 * s_hat_1[_key(tval)][ref] + (1 - p1) * s_hat_1[_key(fval)][ref]
            return
        if op == "EqVec":
            bits_a = sig[1]
            bits_b = sig[2]
            floor = sig[3] if len(sig) > 3 else 0.0
            eqps = []
            eqps0 = {ref: [] for ref in refSigBitNames}
            eqps1 = {ref: [] for ref in refSigBitNames}
            for a_elem, b_elem in zip(bits_a, bits_b):
                populateSigProbs(a_elem, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                if not isinstance(b_elem, int):
                    populateSigProbs(b_elem, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                pa = s_hat.get(_key(a_elem), 0.5)
                pb = b_elem if isinstance(b_elem, int) else s_hat.get(_key(b_elem), 0.5)
                eqp = pa * pb + (1 - pa) * (1 - pb)
                eqps.append(eqp)
                for ref in refSigBitNames:
                    pa0 = s_hat_0.get(_key(a_elem), {}).get(ref, 0.5)
                    pa1 = s_hat_1.get(_key(a_elem), {}).get(ref, 0.5)
                    pb0 = b_elem if isinstance(b_elem, int) else s_hat_0.get(_key(b_elem), {}).get(ref, 0.5)
                    pb1 = b_elem if isinstance(b_elem, int) else s_hat_1.get(_key(b_elem), {}).get(ref, 0.5)
                    eqp0 = pa0 * pb0 + (1 - pa0) * (1 - pb0)
                    eqp1 = pa1 * pb1 + (1 - pa1) * (1 - pb1)
                    eqps0[ref].append(eqp0)
                    eqps1[ref].append(eqp1)
            val = min(eqps) if eqps else 0.5
            val = max(val, floor)
            s_hat[key] = val
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                v0 = min(eqps0[ref]) if eqps0[ref] else 0.5
                v1 = min(eqps1[ref]) if eqps1[ref] else 0.5
                s_hat_0[key][ref] = max(v0, floor)
                s_hat_1[key][ref] = max(v1, floor)
            return
        if op == "EqBus":
            a = sig[1]
            b = sig[2]
            floor = sig[3] if len(sig) > 3 else 0.0
            def _bits(x):
                if isinstance(x, str) and "[" in x and ":" in x and x.endswith("]"):
                    try:
                        base = x.split("[",1)[0]
                        rng = x.split("[",1)[1].split("]")[0]
                        msb, lsb = map(int, rng.split(":"))
                        return [f"{base}[{i}:{i}]" for i in range(lsb, msb+1)]
                    except Exception:
                        return None
                return None
            abits = _bits(a)
            bbits = _bits(b) if isinstance(b, str) else None
            if abits is None:
                s_hat[key] = floor
                s_hat_0[key] = {ref: floor for ref in refSigBitNames}
                s_hat_1[key] = {ref: floor for ref in refSigBitNames}
                return
            eqps = []
            eqps0 = {ref: [] for ref in refSigBitNames}
            eqps1 = {ref: [] for ref in refSigBitNames}
            for idx, abit in enumerate(abits):
                bbit = bbits[idx] if bbits and idx < len(bbits) else ((b >> idx) & 1 if isinstance(b, int) else b)
                populateSigProbs(abit, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                if isinstance(bbit, str):
                    populateSigProbs(bbit, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                pa = s_hat.get(_key(abit), 0.5)
                pb = bbit if isinstance(bbit, int) else s_hat.get(_key(bbit), 0.5)
                eqp = pa * pb + (1 - pa) * (1 - pb)
                eqps.append(eqp)
                for ref in refSigBitNames:
                    pa0 = s_hat_0.get(_key(abit), {}).get(ref, 0.5)
                    pa1 = s_hat_1.get(_key(abit), {}).get(ref, 0.5)
                    pb0 = bbit if isinstance(bbit, int) else s_hat_0.get(_key(bbit), {}).get(ref, 0.5)
                    pb1 = bbit if isinstance(bbit, int) else s_hat_1.get(_key(bbit), {}).get(ref, 0.5)
                    eqp0 = pa0 * pb0 + (1 - pa0) * (1 - pb0)
                    eqp1 = pa1 * pb1 + (1 - pa1) * (1 - pb1)
                    eqps0[ref].append(eqp0)
                    eqps1[ref].append(eqp1)
            val = min(eqps) if eqps else 0.5
            val = max(val, floor)
            s_hat[key] = val
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                v0 = min(eqps0[ref]) if eqps0[ref] else 0.5
                v1 = min(eqps1[ref]) if eqps1[ref] else 0.5
                s_hat_0[key][ref] = max(v0, floor)
                s_hat_1[key][ref] = max(v1, floor)
            return

        if op == "Not":
            a = sig[1] if len(sig) > 1 else 0
            populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = 1 - s_hat[_key(a)]
            s_hat_0[key] = {ref: 1 - s_hat_0[_key(a)][ref] for ref in refSigBitNames}
            s_hat_1[key] = {ref: 1 - s_hat_1[_key(a)][ref] for ref in refSigBitNames}
            return

        if op == "Mix":
            parts = sig[1:]
            if not parts:
                s_hat[key] = 0
                s_hat_0[key] = {ref: 0 for ref in refSigBitNames}
                s_hat_1[key] = {ref: 0 for ref in refSigBitNames}
                return
            for p in parts:
                populateSigProbs(p, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = sum(s_hat[_key(p)] for p in parts) / len(parts)
            s_hat_0[key] = {ref: sum(s_hat_0[_key(p)][ref] for p in parts) / len(parts) for ref in refSigBitNames}
            s_hat_1[key] = {ref: sum(s_hat_1[_key(p)][ref] for p in parts) / len(parts) for ref in refSigBitNames}
            return

        # Binary ops: And/Or/Xor/Eq/NotEq

        a = sig[1] if len(sig) > 1 else 0
        b = sig[2] if len(sig) > 2 else 0
        populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
        populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

        if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
            # Approximate: treat as pass-through of left operand for probabilities
            s_hat[key] = s_hat[_key(a)]
            s_hat_0[key] = {ref: s_hat_0[_key(a)][ref] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat_1[_key(a)][ref] for ref in refSigBitNames}
            return

        # Reconvergence-aware DP at the current gate level
        #print("key is ",key)
        p, p0_map, p1_map, sealed = _resolve_reconvergent_gate(
            op, a, b,
            truthTableMap, inputSigBitNames, refSigBitNames,
            s_hat, s_hat_0, s_hat_1,
            max_cut=2
        )
        s_hat[key] = p
        s_hat_0[key] = p0_map
        s_hat_1[key] = p1_map
        if sealed:
            _SEALED_CONES.add(key)
        return

    if sig in truthTableMap:
        # logical expression corresponding to this signal
        if not (isinstance(key, int)) and "Baud8GeneratorACC[25:25]" in key:
            print("Lior")
        exp = truthTableMap[sig]

        if isinstance(exp, int):
            s_hat[key] = exp
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                s_hat_0[key][ref] = exp
                s_hat_1[key][ref] = exp

        elif isinstance(exp, str):
            populateSigProbs(exp, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = s_hat.get(exp, 0.5)
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                s_hat_0[key][ref] = s_hat_0.get(exp, {}).get(ref, 0.5)
                s_hat_1[key][ref] = s_hat_1.get(exp, {}).get(ref, 0.5)

        # for expressions corresponding to a logical operation (gate) like Not, And, Or, Xor,
        # we calculate the signal probability recursively from the signal probabilities
        # of the inputs of the operation (gate)
        elif isinstance(exp, list):
            op = exp[0]
            if op == "Cond":
                cond = exp[1]
                tval = exp[2]
                fval = exp[3]
                if cond == 'top.TSC.MUX_Sel[0:0]':
                    print("idan")

                populateSigProbs(cond, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                populateSigProbs(tval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                populateSigProbs(fval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

                p_cond = s_hat[_key(cond)]
                s_hat[key] = p_cond * s_hat[_key(tval)] + (1 - p_cond) * s_hat[_key(fval)]
                s_hat_0[key] = {}
                s_hat_1[key] = {}
                for ref in refSigBitNames:
                    p0 = s_hat_0[_key(cond)][ref]
                    p1 = s_hat_1[_key(cond)][ref]
                    s_hat_0[key][ref] = p0 * s_hat_0[_key(tval)][ref] + (1 - p0) * s_hat_0[_key(fval)][ref]
                    s_hat_1[key][ref] = p1 * s_hat_1[_key(tval)][ref] + (1 - p1) * s_hat_1[_key(fval)][ref]

            elif op == "Not":
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = 1 - s_hat[_key(exp[1])]
                s_hat_0[key] = {}
                s_hat_1[key] = {}
                for ref in refSigBitNames:
                    s_hat_0[key][ref] = 1 - s_hat_0[_key(exp[1])][ref]
                    s_hat_1[key][ref] = 1 - s_hat_1[_key(exp[1])][ref]
            elif op == "Mix":
                parts = exp[1:]
                for p in parts:
                    populateSigProbs(p, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = sum(s_hat[_key(p)] for p in parts) / len(parts) if parts else 0
                s_hat_0[key] = {ref: sum(s_hat_0[_key(p)][ref] for p in parts) / len(parts) if parts else 0 for ref in refSigBitNames}
                s_hat_1[key] = {ref: sum(s_hat_1[_key(p)][ref] for p in parts) / len(parts) if parts else 0 for ref in refSigBitNames}
            elif op in ("Srl", "Sll", "Plus", "Times", "Minus"):
                # Approximate: treat as pass-through of left operand
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = s_hat[_key(exp[1])]
                s_hat_0[key] = {ref: s_hat_0[_key(exp[1])][ref] for ref in refSigBitNames}
                s_hat_1[key] = {ref: s_hat_1[_key(exp[1])][ref] for ref in refSigBitNames}
            else:  # And, Or, Xor, Eq, NotEq
                a = exp[1]
                b = exp[2]


                # 1) Decide reconvergence BEFORE computing full fanins (optional but better)
                Sa = _support(a, truthTableMap)
                Sb = _support(b, truthTableMap)
                shared = Sa & Sb

                if not shared:
                    populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames,
                                     inputSigBitNames)
                    populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames,
                                     inputSigBitNames)
                    ka = _key(a)
                    kb = _key(b)
                    s_hat[key] = incSigProb(s_hat[ka], s_hat[kb], op)
                    s_hat_0[key] = {}
                    s_hat_1[key] = {}
                    for ref in refSigBitNames:
                        s_hat_0[key][ref] = incSigProb(s_hat_0[ka][ref], s_hat_0[kb][ref], op)
                        s_hat_1[key][ref] = incSigProb(s_hat_1[ka][ref], s_hat_1[kb][ref], op)
                    return

                # ---- reconvergence-aware path ----
                #print("shared ", shared, " for ", key)
                # Ensure fanins are populated so clamps/ctx use real cached values
                populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames,
                                 inputSigBitNames)
                populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames,
                                 inputSigBitNames)
                if (a == "top.TSC.beep1[0:0]" or a == "top.TSC.beep2[0:0]"):
                    print("Tali")
                p, p0_map, p1_map, sealed = _resolve_reconvergent_gate(op, a, b,truthTableMap, inputSigBitNames, refSigBitNames,
                    s_hat, s_hat_0, s_hat_1,max_cut=3)
                s_hat[key] = p
                s_hat_0[key] = p0_map
                s_hat_1[key] = p1_map
                if sealed:
                    _SEALED_CONES.add(key)
                return

    else:
        # missing definition; try per-bit product if this looks like a bus slice, else default to 0.5
        prod = None
        prod0 = {ref: None for ref in refSigBitNames}
        prod1 = {ref: None for ref in refSigBitNames}

        if isinstance(sig, str) and '[' in sig and ':' in sig:
            try:
                # Separate the base name and the suffix (if any)
                # Example: 'top.U_RSA.exp[31:0]@22' -> base_part='top.U_RSA.exp', rest='31:0]@22'
                base_part, rest = sig.split('[', 1)

                # Split the range from the suffix
                # Example: '31:0]@22' -> rng_str='31:0', suffix='@22'
                rng_str, suffix = rest.split(']', 1)

                msb, lsb = map(int, rng_str.split(':'))
                bits = range(lsb, msb + 1)
                num_bits = len(bits)  # Store the count for averaging
                p = 1.0
                p0 = {ref: 1 for ref in refSigBitNames}
                p1 = {ref: 1 for ref in refSigBitNames}

                for i in bits:
                    # Reconstruct the bitname including the suffix
                    # Example: 'top.U_RSA.exp[0:0]@22'
                    bitname = f"{base_part}[{i}:{i}]{suffix}"

                    populateSigProbs(bitname, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                                     truthTableMap, refSigBitNames, inputSigBitNames)
                    #print("bitname 1:", bitname, " p = ",s_hat.get(_key(bitname), 0.5))

                    p *= s_hat.get(_key(bitname), 0.5)
                    for ref in refSigBitNames:
                        p0[ref] = min(s_hat_0.get(_key(bitname), {}).get(ref, 0.5),p0[ref])
                        p1[ref] = min(s_hat_1.get(_key(bitname), {}).get(ref, 0.5),p1[ref])
                    #for ref in refSigBitNames:
                        # Summing the probabilities
                    #    p0[ref] += s_hat_0.get(_key(bitname), {}).get(ref, 0.5)
                    #    p1[ref] += s_hat_1.get(_key(bitname), {}).get(ref, 0.5)

                #prod = p
                if "top.U_RSA.exp[31:0]@5" in sig:
                    print("hi")

                #if num_bits > 0:
                #    for ref in refSigBitNames:
                #        p0[ref] /= num_bits
                #        p1[ref] /= num_bits

                prod0 = p0
                prod1 = p1
                bitname = f"{base_part}[{0}:{0}]{suffix}"
                prod = s_hat.get(_key(bitname), 0.5)

                #print("final prod:", prod, "s_hat_0 ",prod0, "s_hat_1 ",s_hat_1)

            except Exception:
                prod = None

        #print("final prod:", prod, " for ", sig)
        if prod is None:
            s_hat[key] = 0.5 if sig not in (0, 1) else sig
            s_hat_0[key] = {ref: s_hat[key] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat[key] for ref in refSigBitNames}
        else:
            pmin = 0.0000000001
            s_hat[key] = max(prod, pmin)
            #s_hat_0[key] = {ref: max(prod0[ref], pmin) for ref in refSigBitNames}
            #s_hat_1[key] = {ref: max(prod1[ref], pmin) for ref in refSigBitNames}
            s_hat_0[key] = {ref: s_hat[key] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat[key] for ref in refSigBitNames}
