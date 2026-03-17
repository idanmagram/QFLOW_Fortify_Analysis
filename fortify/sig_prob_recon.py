# sig_prob_recon.py
import sys
from itertools import product
from concurrent.futures import ThreadPoolExecutor, as_completed
from recon_graph_artifacts import build_recon_graph_artifacts

sys.setrecursionlimit(100000)

LOWERABLE_OPS = {
    "Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq",
    "Nand", "Nor", "Srl", "Sll", "Plus", "Times", "Minus",
    "EqVec", "EqBus"
}


def lower_truth_table_map(truth_table_map):
    """Lower nested expression trees into named intermediate nodes.

    This makes reconvergence visible at each binary merge point, because
    every internal sub-expression gets a signal name in the map.
    """
    lowered = {}
    tmp_counter = 0

    def _new_tmp():
        nonlocal tmp_counter
        name = f"__recon_tmp__{tmp_counter}"
        tmp_counter += 1
        return name

    def _lower_expr(expr):
        if isinstance(expr, (int, str)):
            return expr
        if not isinstance(expr, list) or not expr:
            return expr

        op = expr[0]
        if not isinstance(op, str):
            return expr

        # Keep unsupported list forms unchanged to preserve behavior.
        if op not in LOWERABLE_OPS:
            return expr

        lowered_args = []
        for arg in expr[1:]:
            la = _lower_expr(arg)
            if isinstance(la, list):
                tmp = _new_tmp()
                lowered[tmp] = la
                lowered_args.append(tmp)
            else:
                lowered_args.append(la)
        return [op] + lowered_args

    for sig, expr in truth_table_map.items():
        lowered[sig] = _lower_expr(expr)

    return lowered


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
        #return a + b - 2.0 * a * b
        return a + b - a * b
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
            cache[key] = _atomic_prob(sig, clamps, s_hat, s_hat_0, s_hat_1, ref_name)
            return cache[key]
        visiting.add(sig)
        if sig in clamps:
            cache[key] = float(clamps[sig])
            visiting.remove(sig)
            return cache[key]
        #if sig in atomic_set or sig not in truthTableMap:
        if sig not in truthTableMap:
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
        if a == '__recon_tmp__1':
            print("idan")
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
    primary_input_ancestors = {}
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
                #print("p ",p, "for bit_name ",bit_name)
                if p in inputSigBitNames or p+'@0' in inputSigBitNames:
                    return True
                stack.append(p)
        return False

    def extract_signal_width_from_range(signal_name):
        """Return width from trailing [msb:lsb] in a signal string.

        Examples:
          top.AES.r6.t3.t0.u_s.in[31:0] -> 32
          top.sig[7:7] -> 1
        Returns None when no valid range is found.
        """
        if not isinstance(signal_name, str):
            return None
        if "[" not in signal_name or "]" not in signal_name or ":" not in signal_name:
            return None
        try:
            rng = signal_name.rsplit("[", 1)[1].split("]", 1)[0]
            msb_s, lsb_s = rng.split(":", 1)
            msb = int(msb_s.strip())
            lsb = int(lsb_s.strip())
            return abs(msb - lsb) + 1
        except Exception:
            return None

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
                floor = expr[3] if len(expr) > 3 else 0.0
                a_width = extract_signal_width_from_range(a)
                b_width = extract_signal_width_from_range(b)
                if (not isinstance(a, int) and a_width > 10 and _expr_input_reachable(a) ) or (
                      not  isinstance(b, int) and b_width > 10 and _expr_input_reachable(b)):
                    return 0.5
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
                return (sum(_expr_prob(p, ref_name, ref_val) for p in parts)+1) / len(parts)
            if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
                left = expr[1] if len(expr) > 1 else 0
                return _expr_prob(left, ref_name, ref_val)
            a = expr[1] if len(expr) > 1 else 0
            b = expr[2] if len(expr) > 2 else 0

            if op == "Eq" and (a in sigWidths and sigWidths[a] > 10):
                a_width = extract_signal_width_from_range(a)
                b_width = extract_signal_width_from_range(b)
                if (not isinstance(a, int) and a_width > 10 and _expr_input_reachable(a)) or (
                        not isinstance(b, int) and b_width > 10 and _expr_input_reachable(b)):
                    return 0.5
            return gate_formula(op, _expr_prob(a, ref_name, ref_val), _expr_prob(b, ref_name, ref_val))
        return 0.5

    def _freeze_expr(expr):
        if isinstance(expr, list):
            return tuple(_freeze_expr(e) for e in expr)
        return expr

    _expr_cache = {}
    _recon_dp_cache = {}

    def _expr_prob(expr, ref_name=None, ref_val=None):
        key = (_freeze_expr(expr), ref_name, ref_val)
        if key in _expr_cache:
            #print("key ",key)
            return _expr_cache[key]
        val = _expr_prob_uncached(expr, ref_name, ref_val)
        _expr_cache[key] = val
        return val

    def _recon_gate_prob_cached(op, a, b, z_vars, clamps, ref_name):
        def _expand_temp_expr(x, seen=None):
            if seen is None:
                seen = set()
            if isinstance(x, str) and x.startswith("__recon_tmp__"):
                if x in seen:
                    return x
                seen.add(x)
                ex = truthTableMap.get(x, x)
                return _expand_temp_expr(ex, seen)
            if isinstance(x, list):
                return [x[0]] + [_expand_temp_expr(e, seen.copy()) for e in x[1:]]
            return x

        a_key_expr = _expand_temp_expr(a)
        b_key_expr = _expand_temp_expr(b)
        key = (
            op,
            _freeze_expr(a_key_expr),
            _freeze_expr(b_key_expr),
            tuple(z_vars),
            tuple(sorted(clamps.items())) if clamps else (),
            ref_name
        )
        if key in _recon_dp_cache:
            return _recon_dp_cache[key]
        val = gate_prob_recon_dp(
            op, a, b, z_vars, truthTableMap, clamps,
            atomic_set, s_hat, s_hat_0, s_hat_1, ref_name=ref_name
        )
        _recon_dp_cache[key] = val
        return val

    def _direct_inputs(expr):
        if isinstance(expr, int):
            return set()

        if isinstance(expr, list) or 'tmp' in expr:
            op = expr[0] if expr else None

            if op not in known_ops:
                return _direct_inputs(op)
            if 'tmp' in expr:
                sigs = _extract_signal_names(truthTableMap[expr])
            else:
                sigs = _extract_signal_names(expr)
            result = set()
            for sig in sigs:
                result |= eff_ancestors.get(sig, _direct_inputs(sig))
            return result
        if isinstance(expr, str):
            return {expr}
        return set()

    def _primary_inputs(expr):

        if isinstance(expr, int):
            return set()
        if '__recon_tmp__1823' in expr:
            print("hi")
        if isinstance(expr, str):
            if expr in inputSigBitNames or expr.split("@", 1)[0] in inputSigBitNames:
                return {expr}
            if expr in primary_input_ancestors:
                return set(primary_input_ancestors[expr])
            if expr in truthTableMap:
                return _primary_inputs(truthTableMap[expr])
            return set()
        if isinstance(expr, list):
            result = set()
            for ref in _extract_signal_names(expr):
                result |= _primary_inputs(ref)
            return result
        return set()

    #print("all signals ", order)
    #return
    for sig in order:
        if sig in s_hat:
            if sig not in eff_ancestors:
                eff_ancestors[sig] = {sig}
            if sig not in primary_input_ancestors:
                primary_input_ancestors[sig] = _primary_inputs(sig)
            continue
        if sig == '__recon_tmp__1823':
            print("lior")
        exp = truthTableMap.get(sig, None)
        if exp is None:
            #print("sig not in truthTableMap")
            s_hat[sig] = 0.5
            s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
            s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
            eff_ancestors[sig] = {sig}
            primary_input_ancestors[sig] = _primary_inputs(sig)
            atomic_set.add(sig)
            continue

        if isinstance(exp, int):
            val = float(exp)
            s_hat[sig] = val
            s_hat_0[sig] = {ref: val for ref in refSigBitNames}
            s_hat_1[sig] = {ref: val for ref in refSigBitNames}
            eff_ancestors[sig] = set()
            #primary_input_ancestors[sig] = set()
            continue

        if isinstance(exp, str):
            s_hat[sig] = s_hat.get(exp, 0.5)
            #print("s_hat[sig] ",s_hat[sig], " for exp ",exp, "s_hat.get(exp) ",s_hat.get(exp))
            s_hat_0[sig] = {ref: s_hat_0.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames}
            s_hat_1[sig] = {ref: s_hat_1.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames}
            eff_ancestors[sig] = eff_ancestors.get(exp, {exp})
            primary_input_ancestors[sig] = _primary_inputs(exp)
            continue

        if isinstance(exp, list):
            op = exp[0] if exp else None
            if op not in known_ops:
                target = op
                s_hat[sig] = _expr_prob(target)
                s_hat_0[sig] = {ref: _expr_prob(target, ref, 0) for ref in refSigBitNames}
                s_hat_1[sig] = {ref: _expr_prob(target, ref, 1) for ref in refSigBitNames}
                eff_ancestors[sig] = _direct_inputs(target)
                primary_input_ancestors[sig] = _primary_inputs(target)
                continue

            if op in binary_ops:
                a = exp[1] if len(exp) > 1 else 0
                b = exp[2] if len(exp) > 2 else 0
                if op == "Eq":
                    a_width = extract_signal_width_from_range(a)
                    b_width = extract_signal_width_from_range(b)
                    if (not isinstance(a, int) and a_width > 10 and _expr_input_reachable(a)) or (
                            not isinstance(b, int) and b_width > 10 and _expr_input_reachable(b)):
                        s_hat[sig] = 1.0
                        s_hat_0[sig] = {ref: 1.0 for ref in refSigBitNames}
                        s_hat_1[sig] = {ref: 1.0 for ref in refSigBitNames}
                        eff_ancestors[sig] = {sig}
                        eff_ancestors[sig] = {sig}
                      #  primary_input_ancestors[sig] = set()
                        atomic_set.add(sig)
                        continue

                #if recon_only_set is not None:
                #if recon_only_set is not None and shared and (sig in recon_only_set):
                if sig == "top.TSC.beeps[0:0]@3":
                    print("top.TSC.beeps[0:0]@3")
                anc_a = eff_ancestors.get(a, _direct_inputs(a)) if isinstance(a, str) else _direct_inputs(a)
                anc_b = eff_ancestors.get(b, _direct_inputs(b)) if isinstance(b, str) else _direct_inputs(b)
                pi_a = _primary_inputs(a)
                pi_b = _primary_inputs(b)
                shared = pi_a & pi_b
                if (recon_only_set is not None) and (sig in recon_only_set) and shared:
                    #pi_a = _primary_inputs(a)
                    #pi_b = _primary_inputs(b)
                    print("sssig ", sig, " exp: ", exp, " anc_a ", anc_a, " anc_b ", anc_b, " shared ", shared)
                    Z = sorted(shared)
                    s_hat[sig] = _recon_gate_prob_cached(op, a, b, Z, {}, None)

                    s_hat_0[sig] = {}
                    s_hat_1[sig] = {}
                    for ref in refSigBitNames:
                        z_eff = [z for z in Z if z != ref]
                        s_hat_0[sig][ref] = _recon_gate_prob_cached(op, a, b, z_eff, {ref: 0}, ref)
                        s_hat_1[sig][ref] = _recon_gate_prob_cached(op, a, b, z_eff, {ref: 1}, ref)

                    # Keep structural ancestors for downstream reconvergence detection.
                    # Atomic handling is controlled separately by atomic_set.
                    eff_ancestors[sig] = set(anc_a | anc_b)
                    if sig == 'top.TSC.Baud8GeneratorACC[0:0]@1':
                        print("lior")
                    primary_input_ancestors[sig] = set(pi_a | pi_b)
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
                    if sig == 'top.TSC.Baud8GeneratorACC[0:0]@1':
                        print("lior")
                    primary_input_ancestors[sig] = set(pi_a | pi_b)
                    #print("3eff_ancestors[sig] = ", eff_ancestors[sig], " of sig ", sig)

                continue

            if op == "Not":
                c = exp[1] if len(exp) > 1 else 0
                c_anc = eff_ancestors.get(c, _direct_inputs(c)) if isinstance(c, str) else _direct_inputs(c)
                s_hat[sig] = _expr_prob(exp)
                s_hat_0[sig] = {ref: _expr_prob(exp, ref, 0) for ref in refSigBitNames}
                s_hat_1[sig] = {ref: _expr_prob(exp, ref, 1) for ref in refSigBitNames}
                eff_ancestors[sig] = c_anc
                primary_input_ancestors[sig] = _primary_inputs(c)
                #print("4eff_ancestors[sig] = ", eff_ancestors[sig]," of sig ",sig)

                continue

            s_hat[sig] = _expr_prob(exp)
            s_hat_0[sig] = {ref: _expr_prob(exp, ref, 0) for ref in refSigBitNames}
            s_hat_1[sig] = {ref: _expr_prob(exp, ref, 1) for ref in refSigBitNames}
            eff_ancestors[sig] = _direct_inputs(exp)
            primary_input_ancestors[sig] = _primary_inputs(exp)
            #print("5eff_ancestors[sig] = ", eff_ancestors[sig], " of sig ", sig)

            continue

        s_hat[sig] = 0.5
        s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
        eff_ancestors[sig] = {sig}
        primary_input_ancestors[sig] = _primary_inputs(sig)
        atomic_set.add(sig)


def populateSigProbs_recon_dp_parallel(signalNames, s_hat, s_hat_0, s_hat_1,
                                       truthTableMap, refSigBitNames, inputSigBitNames, sigWidths,
                                       recon_only_set=None, graph_artifacts=None, max_workers=None,
                                       min_parallel_level_size=128, chunk_size=32, debug=True):
    if max_workers is not None and max_workers <= 1:
        # Fast path: avoid executor/future overhead when effectively serial.
        return populateSigProbs_recon_dp(
            signalNames, s_hat, s_hat_0, s_hat_1,
            truthTableMap, refSigBitNames, inputSigBitNames, sigWidths,
            recon_only_set=recon_only_set, graph_artifacts=graph_artifacts
        )

    if graph_artifacts is None:
        graph_artifacts = build_recon_graph_artifacts(signalNames, truthTableMap)
    parents = graph_artifacts["parents"]
    levels = graph_artifacts.get("levels", [[s] for s in graph_artifacts["order"]])

    eff_ancestors = {}
    primary_input_ancestors = {}
    atomic_set = set(inputSigBitNames) | set(refSigBitNames)
    known_ops = {"Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq",
                 "Nand", "Nor", "Srl", "Sll", "Plus", "Times", "Minus",
                 "EqVec", "EqBus"}
    binary_ops = {"And", "Or", "Xor", "Eq", "NotEq", "Nand", "Nor"}

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

    def _primary_inputs(expr, pi_snapshot=None):
        snapshot = primary_input_ancestors if pi_snapshot is None else pi_snapshot
        if isinstance(expr, int):
            return set()
        if isinstance(expr, str):
            if expr in inputSigBitNames or expr.split("@", 1)[0] in inputSigBitNames:
                return {expr}
            if expr in snapshot:
                return set(snapshot[expr])
            if expr in truthTableMap:
                return _primary_inputs(truthTableMap[expr], pi_snapshot=snapshot)
            return set()
        if isinstance(expr, list):
            result = set()
            for ref in _extract_signal_names(expr):
                result |= _primary_inputs(ref, pi_snapshot=snapshot)
            return result
        return set()

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
                if p in inputSigBitNames or p + '@0' in inputSigBitNames:
                    return True
                stack.append(p)
        return False

    def extract_signal_width_from_range(signal_name):
        if not isinstance(signal_name, str):
            return None
        if "[" not in signal_name or "]" not in signal_name or ":" not in signal_name:
            return None
        try:
            rng = signal_name.rsplit("[", 1)[1].split("]", 1)[0]
            msb_s, lsb_s = rng.split(":", 1)
            msb = int(msb_s.strip())
            lsb = int(lsb_s.strip())
            return abs(msb - lsb) + 1
        except Exception:
            return None

    def _expr_input_reachable(expr):
        if isinstance(expr, int):
            return False
        if isinstance(expr, str):
            bits = _bits_from_bus(expr)
            if bits is None:
                return _is_input_reachable(expr)
            return _is_input_reachable(bits[0])
        if isinstance(expr, list):
            refs = _extract_signal_names(expr)
            for r in refs:
                bits = _bits_from_bus(r)
                if bits is None:
                    if _is_input_reachable(r):
                        return True
                else:
                    return _is_input_reachable(bits[0])
            return False
        return False

    def _compute_one(sig, atomic_snapshot, eff_snapshot, pi_snapshot):
        if sig in s_hat:
            return None

        exp = truthTableMap.get(sig, None)
        if exp is None:
            return {
                "sig": sig,
                "p": 0.5,
                "p0": {ref: 0.5 for ref in refSigBitNames},
                "p1": {ref: 0.5 for ref in refSigBitNames},
                "anc": {sig},
                "pi_anc": _primary_inputs(sig, pi_snapshot=pi_snapshot),
                "add_atomic": True,
            }

        if isinstance(exp, int):
            val = float(exp)
            return {
                "sig": sig,
                "p": val,
                "p0": {ref: val for ref in refSigBitNames},
                "p1": {ref: val for ref in refSigBitNames},
                "anc": set(),
                "pi_anc": set(),
                "add_atomic": False,
            }

        if isinstance(exp, str):
            return {
                "sig": sig,
                "p": s_hat.get(exp, 0.5),
                "p0": {ref: s_hat_0.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames},
                "p1": {ref: s_hat_1.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames},
                "anc": eff_snapshot.get(exp, {exp}),
                "pi_anc": _primary_inputs(exp, pi_snapshot=pi_snapshot),
                "add_atomic": False,
            }

        if isinstance(exp, list):
            op = exp[0] if exp else None

            if op in binary_ops:
                a = exp[1] if len(exp) > 1 else 0
                b = exp[2] if len(exp) > 2 else 0

                if op == "Eq":
                    a_width = extract_signal_width_from_range(a)
                    b_width = extract_signal_width_from_range(b)
                    if (not isinstance(a, int) and a_width and a_width > 10 and _expr_input_reachable(a)) or \
                       (not isinstance(b, int) and b_width and b_width > 10 and _expr_input_reachable(b)):
                        return {
                            "sig": sig,
                            "p": 1.0,
                            "p0": {ref: 1.0 for ref in refSigBitNames},
                            "p1": {ref: 1.0 for ref in refSigBitNames},
                            "anc": {sig},
                            "pi_anc": set(),
                            "add_atomic": True,
                        }

                anc_a = eff_snapshot.get(a, _direct_inputs(a)) if isinstance(a, str) else _direct_inputs(a)
                anc_b = eff_snapshot.get(b, _direct_inputs(b)) if isinstance(b, str) else _direct_inputs(b)
                pi_a = _primary_inputs(a, pi_snapshot=pi_snapshot)
                pi_b = _primary_inputs(b, pi_snapshot=pi_snapshot)
                shared = pi_a & pi_b

                if (recon_only_set is not None) and (sig in recon_only_set) and shared:
                    Z = sorted(shared)
                    p = gate_prob_recon_dp(
                        op, a, b, Z, truthTableMap, {},
                        atomic_snapshot, s_hat, s_hat_0, s_hat_1, ref_name=None
                    )
                    p0 = {}
                    p1 = {}
                    for ref in refSigBitNames:
                        z_eff = [z for z in Z if z != ref]
                        p0[ref] = gate_prob_recon_dp(
                            op, a, b, z_eff, truthTableMap, {ref: 0},
                            atomic_snapshot, s_hat, s_hat_0, s_hat_1, ref_name=ref
                        )
                        p1[ref] = gate_prob_recon_dp(
                            op, a, b, z_eff, truthTableMap, {ref: 1},
                            atomic_snapshot, s_hat, s_hat_0, s_hat_1, ref_name=ref
                        )
                    return {
                        "sig": sig,
                        "p": p,
                        "p0": p0,
                        "p1": p1,
                        "anc": set(anc_a | anc_b),
                        "pi_anc": set(pi_a | pi_b),
                        "add_atomic": True,
                    }

                cache = {}
                p = prob_with_clamps_atomic(exp, truthTableMap, {}, cache, atomic_snapshot,
                                            s_hat, s_hat_0, s_hat_1, ref_name=None)
                p0 = {}
                p1 = {}
                for ref in refSigBitNames:
                    cache0 = {}
                    cache1 = {}
                    p0[ref] = prob_with_clamps_atomic(exp, truthTableMap, {ref: 0}, cache0, atomic_snapshot,
                                                      s_hat, s_hat_0, s_hat_1, ref_name=ref)
                    p1[ref] = prob_with_clamps_atomic(exp, truthTableMap, {ref: 1}, cache1, atomic_snapshot,
                                                      s_hat, s_hat_0, s_hat_1, ref_name=ref)
                return {
                    "sig": sig,
                    "p": p,
                    "p0": p0,
                    "p1": p1,
                    "anc": set(anc_a | anc_b),
                    "pi_anc": set(pi_a | pi_b),
                    "add_atomic": False,
                }

            cache = {}
            p = prob_with_clamps_atomic(exp, truthTableMap, {}, cache, atomic_snapshot,
                                        s_hat, s_hat_0, s_hat_1, ref_name=None)
            p0 = {}
            p1 = {}
            for ref in refSigBitNames:
                cache0 = {}
                cache1 = {}
                p0[ref] = prob_with_clamps_atomic(exp, truthTableMap, {ref: 0}, cache0, atomic_snapshot,
                                                  s_hat, s_hat_0, s_hat_1, ref_name=ref)
                p1[ref] = prob_with_clamps_atomic(exp, truthTableMap, {ref: 1}, cache1, atomic_snapshot,
                                                  s_hat, s_hat_0, s_hat_1, ref_name=ref)
            return {
                "sig": sig,
                "p": p,
                "p0": p0,
                "p1": p1,
                "anc": _direct_inputs(exp),
                "pi_anc": _primary_inputs(exp, pi_snapshot=pi_snapshot),
                "add_atomic": False,
            }

        return {
            "sig": sig,
            "p": 0.5,
            "p0": {ref: 0.5 for ref in refSigBitNames},
            "p1": {ref: 0.5 for ref in refSigBitNames},
            "anc": {sig},
            "pi_anc": _primary_inputs(sig, pi_snapshot=pi_snapshot),
            "add_atomic": True,
        }

    def _compute_chunk(sig_chunk, atomic_snapshot, eff_snapshot, pi_snapshot):
        out = {}
        for sig in sig_chunk:
            res = _compute_one(sig, atomic_snapshot, eff_snapshot, pi_snapshot)
            if res is not None:
                out[sig] = res
        return out

    total_levels = len(levels)
    if debug:
        print(f"[parallel] start populateSigProbs_recon_dp_parallel: levels={total_levels}, max_workers={max_workers}", flush=True)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for level_idx, level in enumerate(levels, start=1):
            pending = [sig for sig in level if sig not in s_hat]
            if not pending:
                continue

            atomic_snapshot = set(atomic_set)
            eff_snapshot = dict(eff_ancestors)
            pi_snapshot = dict(primary_input_ancestors)
            computed = {}

            # Small levels are cheaper to run serially.
            if len(pending) < max(min_parallel_level_size, chunk_size):
                for sig in pending:
                    res = _compute_one(sig, atomic_snapshot, eff_snapshot, pi_snapshot)
                    if res is not None:
                        computed[sig] = res
            else:
                chunks = [pending[i:i + chunk_size] for i in range(0, len(pending), chunk_size)]
                future_map = {
                    executor.submit(_compute_chunk, c, atomic_snapshot, eff_snapshot, pi_snapshot): idx
                    for idx, c in enumerate(chunks)
                }
                if debug:
                    print(f"[parallel] level {level_idx}/{total_levels}: pending={len(pending)}, chunks={len(chunks)}", flush=True)
                for fut in as_completed(future_map):
                    out = fut.result()
                    if out:
                        computed.update(out)

            for sig in pending:
                res = computed.get(sig)
                if not res:
                    continue
                s_hat[sig] = res["p"]
                s_hat_0[sig] = res["p0"]
                s_hat_1[sig] = res["p1"]
                eff_ancestors[sig] = res["anc"]
                primary_input_ancestors[sig] = res["pi_anc"]
                if res["add_atomic"]:
                    atomic_set.add(sig)

    if debug:
        print("[parallel] done populateSigProbs_recon_dp_parallel", flush=True)
