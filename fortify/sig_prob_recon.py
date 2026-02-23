# sig_prob_recon.py
import sys
from itertools import product
from recon_graph_artifacts import build_recon_graph_artifacts

sys.setrecursionlimit(100000)

# ------------------------------------------------------------
# Incremental signal probability for standard logic gates.
# ------------------------------------------------------------
def incSigProb(a, b, op):
    op = op.capitalize()
    if op == "And":
        return a * b
    elif op == "Or":
        return a + b - a * b
    elif op == "Xor":
        return a + b - 2.0 * a * b
    elif op == "Eq":
        return a * b + (1.0 - a) * (1.0 - b)
    elif op == "Noteq":
        return a + b - 2.0 * a * b
    elif op == "Nand":
        return 1.0 - a * b
    elif op == "Nor":
        return (1.0 - a) * (1.0 - b)
    else:
        raise ValueError(f"Unsupported op {op}")


def gate_formula(op, pA, pB):
    return incSigProb(pA, pB, op)


# ------------------------------------------------------------------
# Helper: extract signal names referenced by an expression node.
# ------------------------------------------------------------------
def _extract_signal_names(exp, self_name=None):
    if isinstance(exp, int):
        return set()
    if isinstance(exp, str):
        if self_name is not None and exp == self_name:
            return set()
        return {exp}
    if isinstance(exp, list):
        if not exp:
            return set()
        op = exp[0]
        if op == "Cond":
            cond = exp[1]
            tval = exp[2] if len(exp) > 2 else 0
            fval = exp[3] if len(exp) > 3 else 0
            return (_extract_signal_names(cond, self_name=self_name) |
                    _extract_signal_names(tval, self_name=self_name) |
                    _extract_signal_names(fval, self_name=self_name))
        out = set()
        for part in exp[1:]:
            out |= _extract_signal_names(part, self_name=self_name)
        return out
    return set()


# -----------------------
# Reconvergence DP helpers
# -----------------------
def _atomic_prob(sig, clamps, s_hat, s_hat_0, s_hat_1, ref_name=None):
    if sig in clamps:
        return float(clamps[sig])
    if ref_name is not None and ref_name in clamps:
        ref_val = clamps[ref_name]
        if sig in s_hat_0 and sig in s_hat_1 and ref_name in s_hat_0[sig]:
            return float(s_hat_0[sig][ref_name] if ref_val == 0 else s_hat_1[sig][ref_name])
    return float(s_hat.get(sig, 0.5))


def _bits_from_bus(bus):
    if not isinstance(bus, str):
        return None
    if "[" in bus and ":" in bus and bus.endswith("]"):
        try:
            base = bus.split("[", 1)[0]
            rng = bus.split("[", 1)[1].split("]")[0]
            msb, lsb = map(int, rng.split(":"))
            return [f"{base}[{i}:{i}]" for i in range(lsb, msb + 1)]
        except Exception:
            return None
    return None


def prob_with_clamps_atomic(sig, truthTableMap, clamps, cache, atomic_set,
                            s_hat, s_hat_0, s_hat_1, ref_name=None,
                            visiting=None):
    key = (str(sig), frozenset(clamps.items()))
    if key in cache:
        return cache[key]

    if visiting is None:
        visiting = set()

    if isinstance(sig, int):
        cache[key] = float(sig)
        return cache[key]

    if isinstance(sig, str):
        if sig in visiting:
            #print(f"[cycle] {sig} -> atomic fallback")
            cache[key] = _atomic_prob(sig, clamps, s_hat, s_hat_0, s_hat_1, ref_name)
            return cache[key]
        visiting.add(sig)
        if sig in clamps:
            cache[key] = float(clamps[sig])
            visiting.remove(sig)
            return cache[key]
        if sig in atomic_set or sig not in truthTableMap:
            cache[key] = _atomic_prob(sig, clamps, s_hat, s_hat_0, s_hat_1, ref_name)
            visiting.remove(sig)
            return cache[key]
        exp = truthTableMap[sig]
    else:
        exp = sig

    if isinstance(exp, int):
        cache[key] = float(exp)
        if isinstance(sig, str):
            visiting.remove(sig)
        return cache[key]
    if isinstance(exp, str):
        p = prob_with_clamps_atomic(exp, truthTableMap, clamps, cache, atomic_set,
                                    s_hat, s_hat_0, s_hat_1, ref_name,
                                    visiting)
        cache[key] = p
        if isinstance(sig, str):
            visiting.remove(sig)
        return p

    if isinstance(exp, list):
        op = exp[0] if exp else None
        known_ops = {"Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq",
                     "Nand", "Nor", "Srl", "Sll", "Plus", "Times", "Minus",
                     "EqVec", "EqBus"}
        if op not in known_ops:
            p = prob_with_clamps_atomic(op, truthTableMap, clamps, cache, atomic_set,
                                        s_hat, s_hat_0, s_hat_1, ref_name)
            cache[key] = p
            return p

        if op == "Cond":
            cond = exp[1] if len(exp) > 1 else 0
            tval = exp[2] if len(exp) > 2 else 0
            fval = exp[3] if len(exp) > 3 else 0
            p_cond = prob_with_clamps_atomic(cond, truthTableMap, clamps, cache, atomic_set,
                                             s_hat, s_hat_0, s_hat_1, ref_name,
                                             visiting)
            p_t = prob_with_clamps_atomic(tval, truthTableMap, clamps, cache, atomic_set,
                                          s_hat, s_hat_0, s_hat_1, ref_name,
                                          visiting)
            p_f = prob_with_clamps_atomic(fval, truthTableMap, clamps, cache, atomic_set,
                                          s_hat, s_hat_0, s_hat_1, ref_name,
                                          visiting)
            p = p_cond * p_t + (1.0 - p_cond) * p_f
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        if op == "EqVec":
            bits_a = exp[1] if len(exp) > 1 else []
            bits_b = exp[2] if len(exp) > 2 else []
            floor = exp[3] if len(exp) > 3 else 0.0
            eqps = []
            for a_elem, b_elem in zip(bits_a, bits_b):
                pa = prob_with_clamps_atomic(a_elem, truthTableMap, clamps, cache, atomic_set,
                                             s_hat, s_hat_0, s_hat_1, ref_name,
                                             visiting)
                pb = b_elem if isinstance(b_elem, int) else prob_with_clamps_atomic(
                    b_elem, truthTableMap, clamps, cache, atomic_set,
                    s_hat, s_hat_0, s_hat_1, ref_name,
                    visiting
                )
                eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
            val = min(eqps) if eqps else 0.5
            val = max(val, floor)
            cache[key] = val
            if isinstance(sig, str):
                visiting.remove(sig)
            return val

        if op == "EqBus":
            a = exp[1] if len(exp) > 1 else ""
            b = exp[2] if len(exp) > 2 else 0
            floor = exp[3] if len(exp) > 3 else 0.0
            abits = _bits_from_bus(a)
            bbits = _bits_from_bus(b) if isinstance(b, str) else None
            if abits is None:
                cache[key] = floor
                if isinstance(sig, str):
                    visiting.remove(sig)
                return floor
            eqps = []
            for idx, abit in enumerate(abits):
                bbit = bbits[idx] if bbits and idx < len(bbits) else (
                    (b >> idx) & 1 if isinstance(b, int) else b
                )
                pa = prob_with_clamps_atomic(abit, truthTableMap, clamps, cache, atomic_set,
                                             s_hat, s_hat_0, s_hat_1, ref_name,
                                             visiting)
                pb = bbit if isinstance(bbit, int) else prob_with_clamps_atomic(
                    bbit, truthTableMap, clamps, cache, atomic_set,
                    s_hat, s_hat_0, s_hat_1, ref_name,
                    visiting
                )
                eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
            val = min(eqps) if eqps else 0.5
            val = max(val, floor)
            cache[key] = val
            if isinstance(sig, str):
                visiting.remove(sig)
            return val

        if op == "Not":
            c = exp[1] if len(exp) > 1 else 0
            p = 1.0 - prob_with_clamps_atomic(c, truthTableMap, clamps, cache, atomic_set,
                                              s_hat, s_hat_0, s_hat_1, ref_name,
                                              visiting)
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        if op == "Mix":
            parts = exp[1:]
            if not parts:
                cache[key] = 0.0
                if isinstance(sig, str):
                    visiting.remove(sig)
                return cache[key]
            ps = [prob_with_clamps_atomic(part, truthTableMap, clamps, cache, atomic_set,
                                          s_hat, s_hat_0, s_hat_1, ref_name,
                                          visiting)
                  for part in parts]
            p = sum(ps) / len(ps)
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
            left = exp[1] if len(exp) > 1 else 0
            p = prob_with_clamps_atomic(left, truthTableMap, clamps, cache, atomic_set,
                                        s_hat, s_hat_0, s_hat_1, ref_name,
                                        visiting)
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        a = exp[1] if len(exp) > 1 else 0
        b = exp[2] if len(exp) > 2 else 0
        pA = prob_with_clamps_atomic(a, truthTableMap, clamps, cache, atomic_set,
                                     s_hat, s_hat_0, s_hat_1, ref_name,
                                     visiting)
        pB = prob_with_clamps_atomic(b, truthTableMap, clamps, cache, atomic_set,
                                     s_hat, s_hat_0, s_hat_1, ref_name,
                                     visiting)
        p = gate_formula(op, pA, pB)
        cache[key] = p
        if isinstance(sig, str):
            visiting.remove(sig)
        return p

    cache[key] = 0.5
    if isinstance(sig, str) and sig in visiting:
        visiting.remove(sig)
    return cache[key]


def _pz_atomic(Z, bits, clamps, s_hat, s_hat_0, s_hat_1, ref_name=None):
    p = 1.0
    for name, bit in zip(Z, bits):
        p1 = _atomic_prob(name, clamps, s_hat, s_hat_0, s_hat_1, ref_name)
        p *= p1 if bit else (1.0 - p1)
    return p


def gate_prob_recon_dp(op, a, b, Z, truthTableMap, clamps, atomic_set,
                       s_hat, s_hat_0, s_hat_1, ref_name=None):
    if not Z:
        cache = {}
        pA = prob_with_clamps_atomic(a, truthTableMap, clamps, cache, atomic_set,
                                     s_hat, s_hat_0, s_hat_1, ref_name)
        print("pA = {}".format(pA))
        pB = prob_with_clamps_atomic(b, truthTableMap, clamps, cache, atomic_set,
                                     s_hat, s_hat_0, s_hat_1, ref_name)
        return gate_formula(op, pA, pB)

    pY = 0.0
    for bits in product((0, 1), repeat=len(Z)):
        pz = _pz_atomic(Z, bits, clamps, s_hat, s_hat_0, s_hat_1, ref_name)
        if pz <= 0.0:
            continue
        z_assign = dict(zip(Z, bits))
        clamps_z = {**clamps, **z_assign}
        cache = {}
        pA_z = prob_with_clamps_atomic(a, truthTableMap, clamps_z, cache, atomic_set,
                                       s_hat, s_hat_0, s_hat_1, ref_name)
        pB_z = prob_with_clamps_atomic(b, truthTableMap, clamps_z, cache, atomic_set,
                                       s_hat, s_hat_0, s_hat_1, ref_name)
        pY += gate_formula(op, pA_z, pB_z) * pz
    return pY


def populateSigProbs_recon_dp(signalNames, s_hat, s_hat_0, s_hat_1,
                              truthTableMap, refSigBitNames, inputSigBitNames, sigWidths,
                              recon_only_set=None, graph_artifacts=None):
    if graph_artifacts is None:
        graph_artifacts = build_recon_graph_artifacts(signalNames, truthTableMap)
    universe = graph_artifacts["universe"]
    parents = graph_artifacts["parents"]
    order = graph_artifacts["order"]
    print("finished topo order")
    #print("order: ", order)
    #return
    '''
    if len(order) < len(universe):
        remaining = [s for s in universe if s not in order]
        #print("remaining signals: ",remaining)
        order.extend(remaining)
    '''
    eff_ancestors = {}
    atomic_set = set(inputSigBitNames) | set(refSigBitNames)

    known_ops = {"Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq",
                 "Nand", "Nor", "Srl", "Sll", "Plus", "Times", "Minus",
                 "EqVec", "EqBus"}
    binary_ops = {"And", "Or", "Xor", "Eq", "NotEq", "Nand", "Nor"}
    print("start calculation")

    def _is_input_reachable(bit_name):
        if bit_name in inputSigBitNames:
            return True
        seen = set()
        stack = [bit_name]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            for p in parents.get(n, set()):
                print("p ",p, "for bit_name ",bit_name)
                if p in inputSigBitNames or p+'@0' in inputSigBitNames:
                    return True
                stack.append(p)
        return False

    def _expr_input_reachable(expr):
        if isinstance(expr, int):
            return False
        if isinstance(expr, str):
            bits = _bits_from_bus(expr)
            if bits is None:
                return _is_input_reachable(expr)
            #return all(_is_input_reachable(b) for b in bits)
            return (_is_input_reachable(bits[0]))
        if isinstance(expr, list):
            refs = _extract_signal_names(expr)
            for r in refs:
                bits = _bits_from_bus(r)
                if bits is None:
                    if _is_input_reachable(r):
                        return True
                else:
                    #if all(_is_input_reachable(b) for b in bits):
                    #    return True
                    return (_is_input_reachable(bits[0]))
            return False
        return False

    def _expr_prob_uncached(expr, ref_name=None, ref_val=None):
        if isinstance(expr, int):
            return float(expr)
        if isinstance(expr, str):
            if ref_name is None:
                return float(s_hat.get(expr, 0.5))
            table = s_hat_0 if ref_val == 0 else s_hat_1
            return float(table.get(expr, {}).get(ref_name, s_hat.get(expr, 0.5)))
        if isinstance(expr, list):
            op = expr[0] if expr else None
            if op not in known_ops:
                return _expr_prob(op, ref_name, ref_val)
            if op == "Cond":
                cond = expr[1] if len(expr) > 1 else 0
                tval = expr[2] if len(expr) > 2 else 0
                fval = expr[3] if len(expr) > 3 else 0
                p_cond = _expr_prob(cond, ref_name, ref_val)
                p_t = _expr_prob(tval, ref_name, ref_val)
                p_f = _expr_prob(fval, ref_name, ref_val)
                return p_cond * p_t + (1.0 - p_cond) * p_f
            if op == "EqVec":
                bits_a = expr[1] if len(expr) > 1 else []
                bits_b = expr[2] if len(expr) > 2 else []
                floor = expr[3] if len(expr) > 3 else 0.0
                eqps = []
                for a_elem, b_elem in zip(bits_a, bits_b):
                    pa = _expr_prob(a_elem, ref_name, ref_val)
                    pb = b_elem if isinstance(b_elem, int) else _expr_prob(b_elem, ref_name, ref_val)
                    eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
                val = min(eqps) if eqps else 0.5
                return max(val, floor)
            if op == "EqBus":

                a = expr[1] if len(expr) > 1 else ""
                b = expr[2] if len(expr) > 2 else 0
                #if a == "top.U_RSA.indata[31:0]":
                    #print("lior")
                floor = expr[3] if len(expr) > 3 else 0.0
                if (not isinstance(a, int) and '[0:0]' not in a and _expr_input_reachable(a) ) or (
                      not  isinstance(b, int) and '[0:0]' not in b and _expr_input_reachable(b)):
                    return 1
                abits = _bits_from_bus(a)
                bbits = _bits_from_bus(b) if isinstance(b, str) else None
                if abits is None:
                    return floor
                eqps = []
                for idx, abit in enumerate(abits):
                    bbit = bbits[idx] if bbits and idx < len(bbits) else (
                        (b >> idx) & 1 if isinstance(b, int) else b)
                    pa = _expr_prob(abit, ref_name, ref_val)
                    pb = bbit if isinstance(bbit, int) else _expr_prob(bbit, ref_name, ref_val)
                    eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
                val = min(eqps) if eqps else 0.5
                return max(val, floor)
            if op == "Not":
                c = expr[1] if len(expr) > 1 else 0
                return 1.0 - _expr_prob(c, ref_name, ref_val)
            if op == "Mix":
                parts = expr[1:]
                if not parts:
                    return 0.0
                return sum(_expr_prob(p, ref_name, ref_val) for p in parts) / len(parts)
            if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
                left = expr[1] if len(expr) > 1 else 0
                return _expr_prob(left, ref_name, ref_val)
            a = expr[1] if len(expr) > 1 else 0
            b = expr[2] if len(expr) > 2 else 0
            if op == "Eq" and 'count' not in a:
                print("a 2 is ", a)
            if op == "Eq" and (a in sigWidths and sigWidths[a] > 10):
                if (isinstance(a, int) and _expr_input_reachable(b)) or (
                        isinstance(b, int) and _expr_input_reachable(a)):
                    return 1
            return gate_formula(op, _expr_prob(a, ref_name, ref_val), _expr_prob(b, ref_name, ref_val))
        return 0.5

    def _freeze_expr(expr):
        if isinstance(expr, list):
            return tuple(_freeze_expr(e) for e in expr)
        return expr

    _expr_cache = {}

    def _expr_prob(expr, ref_name=None, ref_val=None):
        key = (_freeze_expr(expr), ref_name, ref_val)
        if key in _expr_cache:
            #print("key ",key)
            return _expr_cache[key]
        val = _expr_prob_uncached(expr, ref_name, ref_val)
        _expr_cache[key] = val
        return val

    def _direct_inputs(expr):
        if isinstance(expr, int):
            return set()
        if isinstance(expr, str):
            return {expr}
        if isinstance(expr, list):
            op = expr[0] if expr else None
            if op not in known_ops:
                return _direct_inputs(op)
            return _extract_signal_names(expr)
        return set()

    #print("all signals ", order)
    #return
    for sig in order:
        #print("-----sig-----: ",sig)
        if sig == 'top.tro.load[0:0]@2' and recon_only_set != None:
            print("idan!!!!!! ",truthTableMap[sig])
        if sig in s_hat:
            if sig not in eff_ancestors:
                eff_ancestors[sig] = {sig}
            continue

        exp = truthTableMap.get(sig, None)
        if exp is None:
            #print("sig not in truthTableMap")
            s_hat[sig] = 0.5
            s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
            s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
            eff_ancestors[sig] = {sig}
            atomic_set.add(sig)
            continue

        if isinstance(exp, int):
            val = float(exp)
            s_hat[sig] = val
            s_hat_0[sig] = {ref: val for ref in refSigBitNames}
            s_hat_1[sig] = {ref: val for ref in refSigBitNames}
            eff_ancestors[sig] = set()
            continue

        if isinstance(exp, str):
            s_hat[sig] = s_hat.get(exp, 0.5)
            #print("s_hat[sig] ",s_hat[sig], " for exp ",exp, "s_hat.get(exp) ",s_hat.get(exp))
            s_hat_0[sig] = {ref: s_hat_0.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames}
            s_hat_1[sig] = {ref: s_hat_1.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames}
            eff_ancestors[sig] = eff_ancestors.get(exp, {exp})
            continue

        if isinstance(exp, list):
            op = exp[0] if exp else None
            if op not in known_ops:
                target = op
                s_hat[sig] = _expr_prob(target)
                s_hat_0[sig] = {ref: _expr_prob(target, ref, 0) for ref in refSigBitNames}
                s_hat_1[sig] = {ref: _expr_prob(target, ref, 1) for ref in refSigBitNames}
                #print("1eff_ancestors[sig] = ", eff_ancestors[sig]," of sig ",sig)
                eff_ancestors[sig] = _direct_inputs(target)
                continue

            if op in binary_ops:
                a = exp[1] if len(exp) > 1 else 0
                b = exp[2] if len(exp) > 2 else 0
                if op == "Eq":
                    if (isinstance(a, int) and _expr_input_reachable(b)) or (
                            isinstance(b, int) and _expr_input_reachable(a)):
                        s_hat[sig] = 1.0
                        s_hat_0[sig] = {ref: 1.0 for ref in refSigBitNames}
                        s_hat_1[sig] = {ref: 1.0 for ref in refSigBitNames}
                        eff_ancestors[sig] = {sig}
                        eff_ancestors[sig] = {sig}
                        atomic_set.add(sig)
                        continue

                #if recon_only_set is not None:
                #if recon_only_set is not None and shared and (sig in recon_only_set):
                anc_a = eff_ancestors.get(a, _direct_inputs(a)) if isinstance(a, str) else _direct_inputs(a)
                anc_b = eff_ancestors.get(b, _direct_inputs(b)) if isinstance(b, str) else _direct_inputs(b)
                shared = anc_a & anc_b
                if (recon_only_set is not None) and (sig in recon_only_set) and shared:
                #if shared:
                    print("exp: ", exp, " anc_a ", anc_a, " anc_b ", anc_b, " shared ", shared)
                    Z = sorted(shared)
                    s_hat[sig] = gate_prob_recon_dp(
                        op, a, b, Z, truthTableMap, {},
                        atomic_set, s_hat, s_hat_0, s_hat_1, ref_name=None
                    )

                    s_hat_0[sig] = {}
                    s_hat_1[sig] = {}
                    for ref in refSigBitNames:
                        z_eff = [z for z in Z if z != ref]
                        s_hat_0[sig][ref] = gate_prob_recon_dp(
                            op, a, b, z_eff, truthTableMap, {ref: 0},
                            atomic_set, s_hat, s_hat_0, s_hat_1, ref_name=ref
                        )
                        s_hat_1[sig][ref] = gate_prob_recon_dp(
                            op, a, b, z_eff, truthTableMap, {ref: 1},
                            atomic_set, s_hat, s_hat_0, s_hat_1, ref_name=ref
                        )

                    eff_ancestors[sig] = {sig}
                    #print("2eff_ancestors[sig] = ", eff_ancestors[sig], " of sig ", sig)

                    atomic_set.add(sig)
                else:
                    s_hat[sig] = incSigProb(_expr_prob(a), _expr_prob(b), op)
                    s_hat_0[sig] = {ref: incSigProb(_expr_prob(a, ref, 0),
                                                    _expr_prob(b, ref, 0), op)
                                    for ref in refSigBitNames}
                    s_hat_1[sig] = {ref: incSigProb(_expr_prob(a, ref, 1),
                                                    _expr_prob(b, ref, 1), op)
                                    for ref in refSigBitNames}
                    # propagate upstream ancestors so multi-level reconvergence is detectable
                    merged_anc = list(anc_a | anc_b)
                    #if len(merged_anc) > 3:
                    #    merged_anc = merged_anc[:3]

                    eff_ancestors[sig] = set(merged_anc)
                    #print("3eff_ancestors[sig] = ", eff_ancestors[sig], " of sig ", sig)

                continue

            if op == "Not":
                c = exp[1] if len(exp) > 1 else 0
                c_anc = eff_ancestors.get(c, _direct_inputs(c)) if isinstance(c, str) else _direct_inputs(c)
                s_hat[sig] = _expr_prob(exp)
                s_hat_0[sig] = {ref: _expr_prob(exp, ref, 0) for ref in refSigBitNames}
                s_hat_1[sig] = {ref: _expr_prob(exp, ref, 1) for ref in refSigBitNames}
                eff_ancestors[sig] = c_anc
                #print("4eff_ancestors[sig] = ", eff_ancestors[sig]," of sig ",sig)

                continue

            s_hat[sig] = _expr_prob(exp)
            s_hat_0[sig] = {ref: _expr_prob(exp, ref, 0) for ref in refSigBitNames}
            s_hat_1[sig] = {ref: _expr_prob(exp, ref, 1) for ref in refSigBitNames}
            eff_ancestors[sig] = _direct_inputs(exp)
            #print("5eff_ancestors[sig] = ", eff_ancestors[sig], " of sig ", sig)

            continue

        s_hat[sig] = 0.5
        s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
        eff_ancestors[sig] = {sig}
        atomic_set.add(sig)
