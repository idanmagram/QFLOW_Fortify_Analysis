"""
Article-based reconvergence-aware signal probability estimation.

This module keeps the conditioned-probability reconvergence flow separate from
`sig_prob_recon.py`, as requested. The implementation follows the same
high-level approach described in `Reconvergance_article.txt`:

1. Identify reconvergence sources for each sink.
2. Evaluate local conditional probabilities along the reconvergent branches.
3. Recombine them at the sink by enumerating assignments of the reconvergence
   sources for that sink.

The rest of the probability model and interfaces match the existing project
conventions so it can be dropped into the same runner flow as `sig_prob_recon`.
"""

import sys
from itertools import product

from recon_graph_artifacts import build_recon_graph_artifacts
from sig_prob_recon import _bits_from_bus, _lut_const_bit_prob, gate_formula


sys.setrecursionlimit(100000)
MAX_SHARED_ANCESTORS = 2


def _lut_const_bit_prob_with_clk(
    bus, default_bit, exception_keys, bit_prob_fn, clk_name=None
):
    p = _lut_const_bit_prob(bus, default_bit, exception_keys, bit_prob_fn)
    if isinstance(clk_name, str):
        return p * bit_prob_fn(clk_name)
    return p


def populateSigProbs_recon_article(
    signalNames,
    s_hat,
    s_hat_0,
    s_hat_1,
    truthTableMap,
    refSigBitNames,
    inputSigBitNames,
    sigWidths,
    recon_only_set=None,
    graph_artifacts=None,
    max_shared_ancestors=MAX_SHARED_ANCESTORS,
):
    """
    Populate:
      - s_hat[sig]   = Pr(sig=1)
      - s_hat_0[sig][ref] = Pr(sig=1 | ref=0)
      - s_hat_1[sig][ref] = Pr(sig=1 | ref=1)

    Reconvergence sinks are handled by conditioning on sink-specific
    reconvergence sources: nodes whose fanout branches separately reach the
    left and right sink inputs. This matches the article's conditioned
    probabilities flow more closely than generic common-ancestor selection.
    """
    if graph_artifacts is None:
        graph_artifacts = build_recon_graph_artifacts(signalNames, truthTableMap)

    parents = graph_artifacts["parents"]
    order = graph_artifacts["order"]

    known_ops = {
        "Cond",
        "Not",
        "Mix",
        "And",
        "Or",
        "Xor",
        "Eq",
        "NotEq",
        "Nand",
        "Nor",
        "Srl",
        "Sll",
        "Plus",
        "Times",
        "Minus",
        "EqVec",
        "EqBus",
        "LutConstBit",
    }
    binary_ops = {"And", "Or", "Xor", "Eq", "NotEq", "Nand", "Nor"}

    primary_input_ancestor_cache = {}
    ancestor_cache = {}
    descendants_cache = {}
    _expr_cache = {}
    _recon_dp_cache = {}

    def _expr_to_str(expr):
        if isinstance(expr, list):
            if not expr:
                return "[]"
            op = expr[0]
            args = ", ".join(_expr_to_str(arg) for arg in expr[1:])
            return f"{op}({args})"
        return str(expr)

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

    def _collect_ancestors_any(sig):
        if not isinstance(sig, str):
            return set()
        if sig in ancestor_cache:
            return set(ancestor_cache[sig])

        seen = {sig}
        stack = [sig]
        while stack:
            node = stack.pop()
            for parent in parents.get(node, set()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)

        ancestor_cache[sig] = seen
        return set(seen)

    def _collect_descendants_any(sig):
        if not isinstance(sig, str):
            return set()
        if sig in descendants_cache:
            return set(descendants_cache[sig])

        seen = {sig}
        stack = [sig]
        while stack:
            node = stack.pop()
            for child in graph_artifacts["children"].get(node, set()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)

        descendants_cache[sig] = seen
        return set(seen)

    def _shared_recon_sources(a, b):
        a_refs = _operand_signal_refs(a)
        b_refs = _operand_signal_refs(b)
        if not a_refs or not b_refs:
            return set()

        candidate = set()
        local_ancestor_cache = {}

        def _anc(sig):
            if sig not in local_ancestor_cache:
                local_ancestor_cache[sig] = _collect_ancestors_any(sig)
            return local_ancestor_cache[sig]

        for a_ref in a_refs:
            anc_a = _anc(a_ref)
            for b_ref in b_refs:
                candidate |= anc_a & _anc(b_ref)

        if not candidate:
            return set()

        a_ref_set = set(a_refs)
        b_ref_set = set(b_refs)
        valid_sources = set()

        def _child_hits(child, refs):
            child_desc = _collect_descendants_any(child)
            return bool(child_desc & refs)

        for source in candidate:
            source_children = graph_artifacts["children"].get(source, set())
            if len(source_children) < 2:
                continue

            left_children = {child for child in source_children if _child_hits(child, a_ref_set)}
            right_children = {child for child in source_children if _child_hits(child, b_ref_set)}

            if any(left_child != right_child for left_child in left_children for right_child in right_children):
                valid_sources.add(source)

        if not valid_sources:
            return set()

        minimal = set(valid_sources)
        for source in list(valid_sources):
            source_desc = _collect_descendants_any(source)
            for other in valid_sources:
                if other == source:
                    continue
                if other in source_desc:
                    minimal.discard(source)
                    break

        return minimal

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
            return _is_input_reachable(bits[0])
        if isinstance(expr, list):
            refs = _operand_signal_refs(expr)
            for ref in refs:
                bits = _bits_from_bus(ref)
                if bits is None:
                    if _is_input_reachable(ref):
                        return True
                else:
                    return _is_input_reachable(bits[0])
        return False

    def _freeze_expr(expr):
        if isinstance(expr, list):
            return tuple(_freeze_expr(e) for e in expr)
        return expr

    def _atomic_prob(sig, clamps, ref_name=None):
        if sig in clamps:
            return float(clamps[sig])
        if ref_name is not None and ref_name in clamps:
            ref_val = clamps[ref_name]
            if sig in s_hat_0 and sig in s_hat_1 and ref_name in s_hat_0[sig]:
                return float(
                    s_hat_0[sig][ref_name] if ref_val == 0 else s_hat_1[sig][ref_name]
                )
        return float(s_hat.get(sig, 0.5))

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
                    pb = b_elem if isinstance(b_elem, int) else _expr_prob(
                        b_elem, ref_name, ref_val
                    )
                    eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
                val = min(eqps) if eqps else 0.5
                return max(val, floor)
            if op == "EqBus":
                a = expr[1] if len(expr) > 1 else ""
                b = expr[2] if len(expr) > 2 else 0
                floor = expr[3] if len(expr) > 3 else 0.0
                a_width = extract_signal_width_from_range(a)
                b_width = extract_signal_width_from_range(b)
                if (not isinstance(a, int) and a_width > 10 and _expr_input_reachable(a)) or (
                    not isinstance(b, int) and b_width > 10 and _expr_input_reachable(b)
                ):
                    return 0.5
                abits = _bits_from_bus(a)
                bbits = _bits_from_bus(b) if isinstance(b, str) else None
                if abits is None:
                    return floor
                eqps = []
                for idx, abit in enumerate(abits):
                    bbit = (
                        bbits[idx]
                        if bbits and idx < len(bbits)
                        else ((b >> idx) & 1 if isinstance(b, int) else b)
                    )
                    pa = _expr_prob(abit, ref_name, ref_val)
                    pb = bbit if isinstance(bbit, int) else _expr_prob(
                        bbit, ref_name, ref_val
                    )
                    eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
                val = min(eqps) if eqps else 0.5
                return max(val, floor)
            if op == "LutConstBit":
                bus = expr[1] if len(expr) > 1 else ""
                default_bit = expr[2] if len(expr) > 2 else 0
                exception_keys = expr[3] if len(expr) > 3 else []
                clk_name = expr[4] if len(expr) > 4 else None
                return _lut_const_bit_prob_with_clk(
                    bus,
                    default_bit,
                    exception_keys,
                    lambda bit_name: _expr_prob(bit_name, ref_name, ref_val),
                    clk_name=clk_name,
                )
            if op == "Not":
                c = expr[1] if len(expr) > 1 else 0
                return 1.0 - _expr_prob(c, ref_name, ref_val)
            if op == "Mix":
                parts = expr[1:]
                if not parts:
                    return 0.0
                return (sum(_expr_prob(p, ref_name, ref_val) for p in parts) + 1) / len(
                    parts
                )
            if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
                left = expr[1] if len(expr) > 1 else 0
                return _expr_prob(left, ref_name, ref_val)

            a = expr[1] if len(expr) > 1 else 0
            b = expr[2] if len(expr) > 2 else 0
            if op == "Eq" and (a in sigWidths and sigWidths[a] > 10):
                a_width = extract_signal_width_from_range(a)
                b_width = extract_signal_width_from_range(b)
                if (not isinstance(a, int) and a_width > 10 and _expr_input_reachable(a)) or (
                    not isinstance(b, int) and b_width > 10 and _expr_input_reachable(b)
                ):
                    return 0.5
            return gate_formula(
                op,
                _expr_prob(a, ref_name, ref_val),
                _expr_prob(b, ref_name, ref_val),
            )
        return 0.5

    def _expr_prob(expr, ref_name=None, ref_val=None):
        key = (_freeze_expr(expr), ref_name, ref_val)
        if key in _expr_cache:
            return _expr_cache[key]
        val = _expr_prob_uncached(expr, ref_name, ref_val)
        _expr_cache[key] = val
        return val

    def prob_with_clamps_atomic(sig, clamps, cache, ref_name=None, visiting=None):
        key = (repr(sig), frozenset(clamps.items()), ref_name)
        if key in cache:
            return cache[key]

        if visiting is None:
            visiting = set()

        if isinstance(sig, int):
            cache[key] = float(sig)
            return cache[key]

        if isinstance(sig, str):
            if sig in visiting:
                cache[key] = _atomic_prob(sig, clamps, ref_name=ref_name)
                return cache[key]
            visiting.add(sig)

            if sig in clamps:
                cache[key] = float(clamps[sig])
                visiting.remove(sig)
                return cache[key]
            if sig not in truthTableMap:
                cache[key] = _atomic_prob(sig, clamps, ref_name=ref_name)
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
            p = prob_with_clamps_atomic(
                exp, clamps, cache, ref_name=ref_name, visiting=visiting
            )
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        if isinstance(exp, list):
            op = exp[0] if exp else None
            if op not in known_ops:
                p = _atomic_prob(op, clamps, ref_name=ref_name)
                cache[key] = p
                if isinstance(sig, str):
                    visiting.remove(sig)
                return p

            if op == "Cond":
                cond = exp[1] if len(exp) > 1 else 0
                tval = exp[2] if len(exp) > 2 else 0
                fval = exp[3] if len(exp) > 3 else 0
                p_cond = prob_with_clamps_atomic(
                    cond, clamps, cache, ref_name=ref_name, visiting=visiting
                )
                p_t = prob_with_clamps_atomic(
                    tval, clamps, cache, ref_name=ref_name, visiting=visiting
                )
                p_f = prob_with_clamps_atomic(
                    fval, clamps, cache, ref_name=ref_name, visiting=visiting
                )
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
                    pa = prob_with_clamps_atomic(
                        a_elem, clamps, cache, ref_name=ref_name, visiting=visiting
                    )
                    pb = (
                        float(b_elem)
                        if isinstance(b_elem, int)
                        else prob_with_clamps_atomic(
                            b_elem,
                            clamps,
                            cache,
                            ref_name=ref_name,
                            visiting=visiting,
                        )
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
                    if bbits and idx < len(bbits):
                        bbit = bbits[idx]
                    else:
                        bbit = ((b >> idx) & 1) if isinstance(b, int) else b
                    pa = prob_with_clamps_atomic(
                        abit, clamps, cache, ref_name=ref_name, visiting=visiting
                    )
                    pb = (
                        float(bbit)
                        if isinstance(bbit, int)
                        else prob_with_clamps_atomic(
                            bbit,
                            clamps,
                            cache,
                            ref_name=ref_name,
                            visiting=visiting,
                        )
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
                clk_name = exp[4] if len(exp) > 4 else None

                def _first_pass_bit_prob(bit_name):
                    if ref_name is None:
                        return float(s_hat.get(bit_name, 0.5))
                    ref_val = clamps.get(ref_name, 0)
                    table = s_hat_0 if ref_val == 0 else s_hat_1
                    return float(table.get(bit_name, {}).get(ref_name, s_hat.get(bit_name, 0.5)))

                p = _lut_const_bit_prob_with_clk(
                    bus,
                    default_bit,
                    exception_keys,
                    _first_pass_bit_prob,
                    clk_name=clk_name,
                )
                cache[key] = p
                if isinstance(sig, str):
                    visiting.remove(sig)
                return p

            if op == "Not":
                c = exp[1] if len(exp) > 1 else 0
                p = 1.0 - prob_with_clamps_atomic(
                    c, clamps, cache, ref_name=ref_name, visiting=visiting
                )
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
                ps = [
                    prob_with_clamps_atomic(
                        part, clamps, cache, ref_name=ref_name, visiting=visiting
                    )
                    for part in parts
                ]
                p = (sum(ps) + 1) / len(ps)
                cache[key] = p
                if isinstance(sig, str):
                    visiting.remove(sig)
                return p

            if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
                left = exp[1] if len(exp) > 1 else 0
                p = prob_with_clamps_atomic(
                    left, clamps, cache, ref_name=ref_name, visiting=visiting
                )
                cache[key] = p
                if isinstance(sig, str):
                    visiting.remove(sig)
                return p

            a = exp[1] if len(exp) > 1 else 0
            b = exp[2] if len(exp) > 2 else 0
            pA = prob_with_clamps_atomic(
                a, clamps, cache, ref_name=ref_name, visiting=visiting
            )
            pB = prob_with_clamps_atomic(
                b, clamps, cache, ref_name=ref_name, visiting=visiting
            )
            p = gate_formula(op, pA, pB)
            cache[key] = p
            if isinstance(sig, str):
                visiting.remove(sig)
            return p

        cache[key] = 0.5
        if isinstance(sig, str) and sig in visiting:
            visiting.remove(sig)
        return cache[key]

    def _pz_atomic(Z, bits, clamps_base, ref_name=None):
        p = 1.0
        for name, bit in zip(Z, bits):
            p1 = _atomic_prob(name, clamps_base, ref_name=ref_name)
            p *= p1 if bit else (1.0 - p1)
        return p

    def gate_prob_recon_article(op, a, b, Z, clamps, ref_name=None):
        if not Z:
            cache = {}
            pA = prob_with_clamps_atomic(a, clamps, cache, ref_name=ref_name)
            if op == "Not":
                return 1.0 - pA
            pB = prob_with_clamps_atomic(b, clamps, cache, ref_name=ref_name)
            return gate_formula(op, pA, pB)

        pY = 0.0
        clamps_base = dict(clamps) if clamps else {}
        for bits in product((0, 1), repeat=len(Z)):
            pz = _pz_atomic(Z, bits, clamps_base, ref_name=ref_name)
            if pz <= 0.0:
                continue

            z_assign = dict(zip(Z, bits))
            clamps_z = {**clamps_base, **z_assign}

            cache = {}
            pA_z = prob_with_clamps_atomic(a, clamps_z, cache, ref_name=ref_name)
            if op == "Not":
                pY += (1.0 - pA_z) * pz
            else:
                pB_z = prob_with_clamps_atomic(b, clamps_z, cache, ref_name=ref_name)
                pY += gate_formula(op, pA_z, pB_z) * pz
        return pY

    def _recon_gate_prob_cached(op, a, b, z_vars, clamps, ref_name):
        def _expand_temp_expr(expr, seen=None):
            if seen is None:
                seen = set()
            if isinstance(expr, str) and expr.startswith("__recon_tmp__"):
                if expr in seen:
                    return expr
                seen.add(expr)
                expanded = truthTableMap.get(expr, expr)
                return _expand_temp_expr(expanded, seen)
            if isinstance(expr, list):
                return [expr[0]] + [_expand_temp_expr(e, seen.copy()) for e in expr[1:]]
            return expr

        key = (
            op,
            _freeze_expr(_expand_temp_expr(a)),
            _freeze_expr(_expand_temp_expr(b)),
            tuple(z_vars),
            tuple(sorted(clamps.items())) if clamps else (),
            ref_name,
        )
        if key in _recon_dp_cache:
            return _recon_dp_cache[key]

        val = gate_prob_recon_article(op, a, b, z_vars, clamps, ref_name=ref_name)
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
        exp = truthTableMap.get(sig, None)
        if exp is None:
            return (
                0.5,
                {ref: 0.5 for ref in refSigBitNames},
                {ref: 0.5 for ref in refSigBitNames},
                True,
            )

        if isinstance(exp, int):
            val = float(exp)
            return (
                val,
                {ref: val for ref in refSigBitNames},
                {ref: val for ref in refSigBitNames},
                False,
            )

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

            if op in binary_ops or op == "Not":
                a = exp[1] if len(exp) > 1 else 0
                b = exp[2] if len(exp) > 2 else 0

                if op == "Eq":
                    a_width = extract_signal_width_from_range(a)
                    b_width = extract_signal_width_from_range(b)
                    if (not isinstance(a, int) and a_width > 10 and _expr_input_reachable(a)) or (
                        not isinstance(b, int) and b_width > 10 and _expr_input_reachable(b)
                    ):
                        return (
                            1.0,
                            {ref: 1.0 for ref in refSigBitNames},
                            {ref: 1.0 for ref in refSigBitNames},
                            True,
                        )

                if recon_only_set is not None and sig in recon_only_set:
                    shared_sources = _shared_recon_sources(a, b)
                    #shared_sources = shared_sources - {'top.clk[0:0]'}
                    if shared_sources:
                        print(f"[ARTICLE-RECON] sink={sig}")
                        print(f"[ARTICLE-RECON] left={_expr_to_str(a)}")
                        print(f"[ARTICLE-RECON] right={_expr_to_str(b)}")
                        print("max_shared_ancestors ",max_shared_ancestors)

                        print(
                            "[ARTICLE-RECON] recon_sources="
                            + ", ".join(sorted(shared_sources))
                        )
                        shared_sources = shared_sources - {'top.clk[0:0]'}
                        shared_sources = sorted(shared_sources)
                        print("max_shared_ancestors ",max_shared_ancestors)
                        shared_sources = shared_sources[:max_shared_ancestors]
                        print(
                            "[ARTICLE-RECON] recon_sources="
                            + ", ".join(sorted(shared_sources))
                        )

                        p, p0, p1 = _compute_recon_tables(
                            op, a, b, shared_sources
                        )
                        return p, p0, p1, True

            p, p0, p1 = _compute_expr_tables(exp)
            return p, p0, p1, False

        return (
            0.5,
            {ref: 0.5 for ref in refSigBitNames},
            {ref: 0.5 for ref in refSigBitNames},
            True,
        )

    for sig in order:
        if sig in s_hat:
            continue
        s_hat[sig], s_hat_0[sig], s_hat_1[sig], _ = _compute_signal_result(sig)
