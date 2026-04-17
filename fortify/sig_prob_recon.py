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
        #return a + b - a * b
        return a + b - 2 * a * b
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


def _lut_const_bit_prob(bus, default_bit, exception_keys, bit_prob_fn):
    abits = _bits_from_bus(bus)
    if abits is None:
        return 0.5

    def _exact_key_prob(key):
        p = 1.0
        for idx, abit in enumerate(abits):
            bit = (key >> idx) & 1
            pa = bit_prob_fn(abit)
            p *= pa if bit else (1.0 - pa)
        return p

    delta = sum(_exact_key_prob(int(key)) for key in exception_keys)
    return delta if int(default_bit) == 0 else (1.0 - delta)


def prob_with_clamps_atomic(sig, truthTableMap, clamps, cache,
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
        p = prob_with_clamps_atomic(exp, truthTableMap, clamps, cache,
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
                     "EqVec", "EqBus", "LutConstBit"}
        if op not in known_ops:
            p = prob_with_clamps_atomic(op, truthTableMap, clamps, cache,
                                        s_hat, s_hat_0, s_hat_1, ref_name)
            cache[key] = p
            return p

        if op == "Cond":
            cond = exp[1] if len(exp) > 1 else 0
            tval = exp[2] if len(exp) > 2 else 0
            fval = exp[3] if len(exp) > 3 else 0
            p_cond = prob_with_clamps_atomic(cond, truthTableMap, clamps, cache,
                                             s_hat, s_hat_0, s_hat_1, ref_name,
                                             visiting)
            p_t = prob_with_clamps_atomic(tval, truthTableMap, clamps, cache,
                                          s_hat, s_hat_0, s_hat_1, ref_name,
                                          visiting)
            p_f = prob_with_clamps_atomic(fval, truthTableMap, clamps, cache,
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
                pa = prob_with_clamps_atomic(a_elem, truthTableMap, clamps, cache,
                                             s_hat, s_hat_0, s_hat_1, ref_name,
                                             visiting)
                pb = b_elem if isinstance(b_elem, int) else prob_with_clamps_atomic(
                    b_elem, truthTableMap, clamps, cache,
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
                pa = prob_with_clamps_atomic(abit, truthTableMap, clamps, cache,
                                             s_hat, s_hat_0, s_hat_1, ref_name,
                                             visiting)
                pb = bbit if isinstance(bbit, int) else prob_with_clamps_atomic(
                    bbit, truthTableMap, clamps, cache,
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

        if op == "LutConstBit":
            bus = exp[1] if len(exp) > 1 else ""
            default_bit = exp[2] if len(exp) > 2 else 0
            exception_keys = exp[3] if len(exp) > 3 else []
            p = _lut_const_bit_prob(
                bus,
                default_bit,
                exception_keys,
                lambda bit_name: prob_with_clamps_atomic(
                    bit_name, truthTableMap, clamps, cache,
                    s_hat, s_hat_0, s_hat_1, ref_name, visiting
                ),
            )
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        if op == "Not":
            c = exp[1] if len(exp) > 1 else 0
            p = 1.0 - prob_with_clamps_atomic(c, truthTableMap, clamps, cache,
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
            ps = [prob_with_clamps_atomic(part, truthTableMap, clamps, cache,
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
            p = prob_with_clamps_atomic(left, truthTableMap, clamps, cache,
                                        s_hat, s_hat_0, s_hat_1, ref_name,
                                        visiting)
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        a = exp[1] if len(exp) > 1 else 0
        b = exp[2] if len(exp) > 2 else 0
        pA = prob_with_clamps_atomic(a, truthTableMap, clamps, cache,
                                     s_hat, s_hat_0, s_hat_1, ref_name,
                                     visiting)
        pB = prob_with_clamps_atomic(b, truthTableMap, clamps, cache,
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


def gate_prob_recon_dp(op, a, b, Z, truthTableMap, clamps,
                       s_hat, s_hat_0, s_hat_1, ref_name=None):
    if not Z:
        cache = {}
        pA = prob_with_clamps_atomic(a, truthTableMap, clamps, cache,
                                     s_hat, s_hat_0, s_hat_1, ref_name)
        pB = prob_with_clamps_atomic(b, truthTableMap, clamps, cache,
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
        pA_z = prob_with_clamps_atomic(a, truthTableMap, clamps_z, cache,
                                       s_hat, s_hat_0, s_hat_1, ref_name)
        pB_z = prob_with_clamps_atomic(b, truthTableMap, clamps_z, cache,
                                       s_hat, s_hat_0, s_hat_1, ref_name)
        pY += gate_formula(op, pA_z, pB_z) * pz
    return pY


def populateSigProbs_recon_dp(signalNames, s_hat, s_hat_0, s_hat_1,
                              truthTableMap, refSigBitNames, inputSigBitNames, sigWidths,
                              recon_only_set=None, graph_artifacts=None):
    if graph_artifacts is None:
        graph_artifacts = build_recon_graph_artifacts(signalNames, truthTableMap)
    parents = graph_artifacts["parents"]
    order = graph_artifacts["order"]
    print("finished topo order")

    known_ops = {"Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq",
                 "Nand", "Nor", "Srl", "Sll", "Plus", "Times", "Minus",
                 "EqVec", "EqBus", "LutConstBit"}
    binary_ops = {"And", "Or", "Xor", "Eq", "NotEq", "Nand", "Nor"}
    print("start calculation")

    primary_input_ancestor_cache = {}

    def _is_primary_input_signal(sig):
        return isinstance(sig, str) and (
            sig in inputSigBitNames or sig.split("@", 1)[0] in inputSigBitNames
        )

    def _collect_primary_input_parents(sig, visiting=None):
        if not isinstance(sig, str):
            return set()
        if sig in primary_input_ancestor_cache:
            return set(primary_input_ancestor_cache[sig])
        if _is_primary_input_signal(sig):
            primary_input_ancestor_cache[sig] = {sig}
            return {sig}
        if visiting is None:
            visiting = set()
        if sig in visiting:
            return set()
        visiting.add(sig)
        result = set()
        for parent in parents.get(sig, set()):
            result |= _collect_primary_input_parents(parent, visiting)
        visiting.remove(sig)
        primary_input_ancestor_cache[sig] = set(result)
        return result

    def _operand_signal_refs(expr):
        if isinstance(expr, int):
            return []
        if isinstance(expr, str):
            return [expr]
        if isinstance(expr, list):
            if not expr:
                return []
            op = expr[0]
            if op == "Cond":
                refs = []
                if len(expr) > 1:
                    refs.extend(_operand_signal_refs(expr[1]))
                if len(expr) > 2:
                    refs.extend(_operand_signal_refs(expr[2]))
                if len(expr) > 3:
                    refs.extend(_operand_signal_refs(expr[3]))
                return refs
            refs = []
            for part in expr[1:]:
                refs.extend(_operand_signal_refs(part))
            return refs
        return []

    def _shared_primary_input_parents(a, b):
        a_refs = _operand_signal_refs(a)
        b_refs = _operand_signal_refs(b)
        shared = set()

        for a_ref in a_refs:
            a_pi = _collect_primary_input_parents(a_ref)
            for b_ref in b_refs:
                shared |= (a_pi & _collect_primary_input_parents(b_ref))

        for idx, a_ref in enumerate(a_refs):
            a_pi = _collect_primary_input_parents(a_ref)
            for other_ref in a_refs[idx + 1:]:
                shared |= (a_pi & _collect_primary_input_parents(other_ref))

        for idx, b_ref in enumerate(b_refs):
            b_pi = _collect_primary_input_parents(b_ref)
            for other_ref in b_refs[idx + 1:]:
                shared |= (b_pi & _collect_primary_input_parents(other_ref))

        return shared

    def _is_input_reachable(bit_name):
        return bool(_collect_primary_input_parents(bit_name))

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
            if op == "LutConstBit":
                bus = expr[1] if len(expr) > 1 else ""
                default_bit = expr[2] if len(expr) > 2 else 0
                exception_keys = expr[3] if len(expr) > 3 else []
                return _lut_const_bit_prob(
                    bus,
                    default_bit,
                    exception_keys,
                    lambda bit_name: _expr_prob(bit_name, ref_name, ref_val),
                )
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
            s_hat, s_hat_0, s_hat_1, ref_name=ref_name
        )
        _recon_dp_cache[key] = val
        return val

    def _compute_expr_tables(expr):
        p = _expr_prob(expr)
        p0 = {ref: _expr_prob(expr, ref, 0) for ref in refSigBitNames}
        p1 = {ref: _expr_prob(expr, ref, 1) for ref in refSigBitNames}
        return p, p0, p1

    def _compute_recon_tables(op, a, b, z_vars):
        p = _recon_gate_prob_cached(op, a, b, z_vars, {}, None)
        p0 = {}
        p1 = {}
        for ref in refSigBitNames:
            z_eff = [z for z in z_vars if z != ref]
            p0[ref] = _recon_gate_prob_cached(op, a, b, z_eff, {ref: 0}, ref)
            p1[ref] = _recon_gate_prob_cached(op, a, b, z_eff, {ref: 1}, ref)
        return p, p0, p1

    def _compute_signal_result(sig):
        if sig == 'top.AES.a1.X_v1.o[0:0]':
            print("hi")
        exp = truthTableMap.get(sig, None)
        if exp is None:
            return 0.5, {ref: 0.5 for ref in refSigBitNames}, {ref: 0.5 for ref in refSigBitNames}, True

        if isinstance(exp, int):
            val = float(exp)
            return val, {ref: val for ref in refSigBitNames}, {ref: val for ref in refSigBitNames}, False

        if isinstance(exp, str):
            return (
                s_hat.get(exp, 0.5),
                {ref: s_hat_0.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames},
                {ref: s_hat_1.get(exp, {}).get(ref, 0.5) for ref in refSigBitNames},
                False,
            )

        if isinstance(exp, list):
            op = exp[0] if exp else None
            if op not in known_ops:
                p, p0, p1 = _compute_expr_tables(op)
                return p, p0, p1, False

            if op in binary_ops:
                a = exp[1] if len(exp) > 1 else 0
                b = exp[2] if len(exp) > 2 else 0
                if op == "Eq":
                    a_width = extract_signal_width_from_range(a)
                    b_width = extract_signal_width_from_range(b)
                    if (not isinstance(a, int) and a_width > 10 and _expr_input_reachable(a)) or (
                            not isinstance(b, int) and b_width > 10 and _expr_input_reachable(b)):
                        return 1.0, {ref: 1.0 for ref in refSigBitNames}, {ref: 1.0 for ref in refSigBitNames}, True

                shared_primary_inputs = _shared_primary_input_parents(a, b)
                if (recon_only_set is not None) and (sig in recon_only_set) and shared_primary_inputs and 0:
                    print("sig ",sig," shared_primary_inputs ",shared_primary_inputs)
                    p, p0, p1 = _compute_recon_tables(op, a, b, sorted(shared_primary_inputs))
                    return p, p0, p1, True

            p, p0, p1 = _compute_expr_tables(exp)
            return p, p0, p1, False

        return 0.5, {ref: 0.5 for ref in refSigBitNames}, {ref: 0.5 for ref in refSigBitNames}, True
    #print("inputSigBitNames ",inputSigBitNames)
    #_collect_operand_primary_input_parents('top.Tj_Trigger.state[127:0]')

    for sig in order:
        if sig in s_hat:
            continue
        s_hat[sig], s_hat_0[sig], s_hat_1[sig], _ = _compute_signal_result(sig)
