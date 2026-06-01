import argparse
import os
import time
from collections import defaultdict
from datetime import datetime
from itertools import product

import module_maps
from recon_graph_artifacts import build_recon_graph_artifacts
from sig_prob_recon import lower_truth_table_map
from tqdm import tqdm


SUPPORTED_BOOL_OPS = {
    "And", "Or", "Xor", "Eq", "NotEq", "Nand", "Nor", "Not", "Cond",
    "EqVec", "EqBus", "LutConstBit", "Srl", "Sll", "Plus", "Times", "Minus",
}
UNROLL_DEPTH = 2


def _freeze_expr(expr):
    if isinstance(expr, list):
        return tuple(_freeze_expr(part) for part in expr)
    return expr


def _expr_signal_refs(expr):
    if isinstance(expr, int):
        return set()
    if isinstance(expr, str):
        return {expr}
    if isinstance(expr, list):
        refs = set()
        for part in expr[1:]:
            refs |= _expr_signal_refs(part)
        return refs
    return set()


def _direct_signal_inputs(expr):
    if isinstance(expr, str):
        return {expr}
    if not isinstance(expr, list) or not expr:
        return set()
    refs = set()
    for part in expr[1:]:
        if isinstance(part, str):
            refs.add(part)
        elif isinstance(part, list):
            refs |= _expr_signal_refs(part)
    return refs


def _string_only_expr(expr):
    if isinstance(expr, int):
        return expr
    if isinstance(expr, str):
        return expr
    if isinstance(expr, list):
        return [expr[0]] + [_string_only_expr(part) for part in expr[1:]]
    return expr


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


def _bool_prob(prob_one, bit):
    return prob_one if bit else (1.0 - prob_one)


def _pbv_from_conditionals(p1_if_0, p1_if_1, prior_0=0.5):
    prior_1 = 1.0 - prior_0
    return (
        max(prior_0 * (1.0 - p1_if_0), prior_1 * (1.0 - p1_if_1)) +
        max(prior_0 * p1_if_0, prior_1 * p1_if_1)
    )


def _collect_ops(expr):
    ops = set()
    if isinstance(expr, list) and expr:
        ops.add(expr[0])
        for part in expr[1:]:
            ops |= _collect_ops(part)
    return ops


def _eval_expr(expr, assignment):
    if isinstance(expr, int):
        return 1 if expr else 0
    if isinstance(expr, str):
        return 1 if assignment.get(expr, 0) else 0
    if not isinstance(expr, list) or not expr:
        raise ValueError(f"Unsupported expression node: {expr!r}")

    op = expr[0]
    if op == "Not":
        return 1 - _eval_expr(expr[1], assignment)
    if op == "And":
        return _eval_expr(expr[1], assignment) & _eval_expr(expr[2], assignment)
    if op == "Or":
        return _eval_expr(expr[1], assignment) | _eval_expr(expr[2], assignment)
    if op == "Xor":
        return _eval_expr(expr[1], assignment) ^ _eval_expr(expr[2], assignment)
    if op == "Eq":
        return 1 if _eval_expr(expr[1], assignment) == _eval_expr(expr[2], assignment) else 0
    if op == "NotEq":
        return 1 if _eval_expr(expr[1], assignment) != _eval_expr(expr[2], assignment) else 0
    if op == "Nand":
        return 1 - (_eval_expr(expr[1], assignment) & _eval_expr(expr[2], assignment))
    if op == "Nor":
        return 1 - (_eval_expr(expr[1], assignment) | _eval_expr(expr[2], assignment))
    if op == "Cond":
        return _eval_expr(expr[2], assignment) if _eval_expr(expr[1], assignment) else _eval_expr(expr[3], assignment)
    if op == "EqVec":
        return 1 if all(_eval_expr(a, assignment) == _eval_expr(b, assignment) for a, b in zip(expr[1], expr[2])) else 0
    if op == "EqBus":
        bits_a = _bits_from_bus(expr[1])
        rhs = expr[2]
        bits_b = _bits_from_bus(rhs) if isinstance(rhs, str) else None
        if bits_a is None:
            return 0
        for idx, abit in enumerate(bits_a):
            aval = _eval_expr(abit, assignment)
            if bits_b is not None:
                bval = _eval_expr(bits_b[idx], assignment)
            elif isinstance(rhs, int):
                bval = (rhs >> idx) & 1
            else:
                bval = _eval_expr(rhs, assignment)
            if aval != bval:
                return 0
        return 1
    if op == "LutConstBit":
        bus = expr[1] if len(expr) > 1 else ""
        default_bit = int(expr[2]) if len(expr) > 2 else 0
        exception_keys = set(int(v) for v in (expr[3] if len(expr) > 3 else []))
        clk_name = expr[4] if len(expr) > 4 else None
        bits = _bits_from_bus(bus)
        if bits is None:
            return default_bit
        value = 0
        for idx, bit_name in enumerate(bits):
            value |= (_eval_expr(bit_name, assignment) & 1) << idx
        out = 1 - default_bit if value in exception_keys else default_bit
        if isinstance(clk_name, str):
            out &= _eval_expr(clk_name, assignment)
        return out
    if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
        return _eval_expr(expr[1], assignment)
    raise ValueError(f"Unsupported operator: {op}")


def _compute_secret_support(truth_table_map, ref_bits):
    ref_set = set(ref_bits)
    cache = {}
    visiting = set()

    def _support(node):
        key = _freeze_expr(node)
        if key in cache:
            return cache[key]
        if key in visiting:
            return set()
        visiting.add(key)
        if isinstance(node, int):
            out = set()
        elif isinstance(node, str):
            if node in ref_set:
                out = {node}
            elif node in truth_table_map:
                out = _support(truth_table_map[node])
            else:
                out = set()
        elif isinstance(node, list):
            out = set()
            for part in node[1:]:
                out |= _support(part)
        else:
            out = set()
        cache[key] = out
        visiting.discard(key)
        return out

    for ref in ref_bits:
        cache[ref] = {ref}
    for sig, expr in truth_table_map.items():
        cache[sig] = _support(expr)
    return cache


def _collect_reachable_signals(graph, sinks):
    reachable = set()
    stack = [sig for sig in sinks if isinstance(sig, str)]
    while stack:
        sig = stack.pop()
        if sig in reachable:
            continue
        reachable.add(sig)
        for parent in graph["parents"].get(sig, ()):
            if isinstance(parent, str) and parent not in reachable:
                stack.append(parent)
    return reachable


def _build_channel_merger(truth_table_map, protected_atoms, max_channel_inputs):
    merge_cache = {}
    in_progress = set()

    def _refs_for_term(term):
        if isinstance(term, int):
            return set()
        if isinstance(term, str):
            return {term}
        return _expr_signal_refs(term)

    def _merge_signal(sig):
        if sig in merge_cache:
            return merge_cache[sig]
        if sig in in_progress:
            return sig, {sig}
        if sig in protected_atoms or sig not in truth_table_map:
            merge_cache[sig] = (sig, {sig})
            return merge_cache[sig]

        in_progress.add(sig)
        expr = truth_table_map[sig]
        if isinstance(expr, int):
            merge_cache[sig] = (expr, set())
            in_progress.discard(sig)
            return merge_cache[sig]
        if isinstance(expr, str):
            if expr in protected_atoms or expr not in truth_table_map:
                merge_cache[sig] = (expr, {expr})
            else:
                merged_child, child_refs = _merge_signal(expr)
                merge_cache[sig] = (merged_child, set(child_refs)) if len(child_refs) <= max_channel_inputs else (expr, {expr})
            in_progress.discard(sig)
            return merge_cache[sig]
        if not isinstance(expr, list) or not expr:
            merge_cache[sig] = (sig, {sig})
            in_progress.discard(sig)
            return merge_cache[sig]

        items = []
        for arg in expr[1:]:
            if isinstance(arg, str) and arg not in protected_atoms and arg in truth_table_map:
                merged_arg, arg_refs = _merge_signal(arg)
                items.append({
                    "original": arg,
                    "inline": merged_arg,
                    "refs_inline": set(arg_refs),
                    "can_collapse": True,
                    "use_inline": True,
                })
            else:
                items.append({
                    "original": arg,
                    "inline": arg,
                    "refs_inline": _expr_signal_refs(arg) if not isinstance(arg, int) else set(),
                    "can_collapse": False,
                    "use_inline": True,
                })

        while True:
            total_refs = set()
            for item in items:
                total_refs |= item["refs_inline"] if item["use_inline"] else {item["original"]}
            if len(total_refs) <= max_channel_inputs:
                merged_expr = [expr[0]] + [item["inline"] if item["use_inline"] else item["original"] for item in items]
                merge_cache[sig] = (merged_expr, total_refs)
                in_progress.discard(sig)
                return merge_cache[sig]

            collapsible = [item for item in items if item["can_collapse"] and item["use_inline"] and len(item["refs_inline"]) > 1]
            if not collapsible:
                merged_expr = [expr[0]]
                total_refs = set()
                for item in items:
                    term = item["inline"] if item["use_inline"] else item["original"]
                    merged_expr.append(term)
                    if isinstance(term, str):
                        total_refs.add(term)
                    elif isinstance(term, list):
                        total_refs |= _expr_signal_refs(term)
                merge_cache[sig] = (merged_expr, total_refs)
                in_progress.discard(sig)
                return merge_cache[sig]

            max(collapsible, key=lambda item: len(item["refs_inline"]) - 1)["use_inline"] = False

    return _merge_signal


def _base_unconditional_prob(sig, input_bits, ref_bits):
    if sig in input_bits or sig in ref_bits:
        return 0.5
    return 0.5


def _base_conditional_prob(sig, ref_bit, ref_val, input_bits, ref_bits):
    if sig in input_bits:
        return 0.5
    if sig in ref_bits:
        if sig == ref_bit:
            return 1.0 if ref_val else 0.0
        return 0.5
    return 0.5


def _channel_truth_table(merged_expr, leaves_sorted):
    rows = []
    for bits in product((0, 1), repeat=len(leaves_sorted)):
        assignment = dict(zip(leaves_sorted, bits))
        rows.append((bits, _eval_expr(merged_expr, assignment)))
    return rows


def _approx_expr_prob(expr, prob_lookup):
    if isinstance(expr, int):
        return float(expr)
    if isinstance(expr, str):
        return float(prob_lookup(expr))
    if not isinstance(expr, list) or not expr:
        return 0.5

    op = expr[0]
    if op == "Not":
        return 1.0 - _approx_expr_prob(expr[1], prob_lookup)
    if op == "And":
        a = _approx_expr_prob(expr[1], prob_lookup)
        b = _approx_expr_prob(expr[2], prob_lookup)
        return a * b
    if op == "Or":
        a = _approx_expr_prob(expr[1], prob_lookup)
        b = _approx_expr_prob(expr[2], prob_lookup)
        return a + b - a * b
    if op == "Xor" or op == "NotEq":
        a = _approx_expr_prob(expr[1], prob_lookup)
        b = _approx_expr_prob(expr[2], prob_lookup)
        return a + b - 2.0 * a * b
    if op == "Eq":
        a = _approx_expr_prob(expr[1], prob_lookup)
        b = _approx_expr_prob(expr[2], prob_lookup)
        return a * b + (1.0 - a) * (1.0 - b)
    if op == "Nand":
        a = _approx_expr_prob(expr[1], prob_lookup)
        b = _approx_expr_prob(expr[2], prob_lookup)
        return 1.0 - a * b
    if op == "Nor":
        a = _approx_expr_prob(expr[1], prob_lookup)
        b = _approx_expr_prob(expr[2], prob_lookup)
        return (1.0 - a) * (1.0 - b)
    if op == "Cond":
        pc = _approx_expr_prob(expr[1], prob_lookup)
        pt = _approx_expr_prob(expr[2], prob_lookup)
        pf = _approx_expr_prob(expr[3], prob_lookup)
        return pc * pt + (1.0 - pc) * pf
    if op == "EqVec":
        bits_a = expr[1]
        bits_b = expr[2]
        floor = expr[3] if len(expr) > 3 else 0.0
        eqps = []
        for a_elem, b_elem in zip(bits_a, bits_b):
            pa = _approx_expr_prob(a_elem, prob_lookup)
            pb = float(b_elem) if isinstance(b_elem, int) else _approx_expr_prob(b_elem, prob_lookup)
            eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
        return max(min(eqps) if eqps else 0.5, floor)
    if op == "EqBus":
        a = expr[1]
        b = expr[2]
        floor = expr[3] if len(expr) > 3 else 0.0
        abits = _bits_from_bus(a)
        bbits = _bits_from_bus(b) if isinstance(b, str) else None
        if abits is None:
            return floor
        eqps = []
        for idx, abit in enumerate(abits):
            bbit = bbits[idx] if bbits and idx < len(bbits) else ((b >> idx) & 1 if isinstance(b, int) else b)
            pa = _approx_expr_prob(abit, prob_lookup)
            pb = float(bbit) if isinstance(bbit, int) else _approx_expr_prob(bbit, prob_lookup)
            eqps.append(pa * pb + (1.0 - pa) * (1.0 - pb))
        return max(min(eqps) if eqps else 0.5, floor)
    if op == "LutConstBit":
        bus = expr[1] if len(expr) > 1 else ""
        default_bit = int(expr[2]) if len(expr) > 2 else 0
        exception_keys = set(int(v) for v in (expr[3] if len(expr) > 3 else []))
        clk_name = expr[4] if len(expr) > 4 else None
        bits = _bits_from_bus(bus)
        if bits is None:
            return float(default_bit)
        p_out = 0.0
        for value_bits in product((0, 1), repeat=len(bits)):
            p_val = 1.0
            value = 0
            for idx, (bit_name, bit_val) in enumerate(zip(bits, value_bits)):
                p_val *= _bool_prob(_approx_expr_prob(bit_name, prob_lookup), bit_val)
                value |= (bit_val & 1) << idx
            out = 1 - default_bit if value in exception_keys else default_bit
            if out:
                p_out += p_val
        if isinstance(clk_name, str):
            p_out *= _approx_expr_prob(clk_name, prob_lookup)
        return p_out
    if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
        return _approx_expr_prob(expr[1], prob_lookup)
    return 0.5


def _channel_output_prob(channel_rows, prob_values):
    p_y1 = 0.0
    for bits, y in channel_rows:
        p_assign = 1.0
        for prob_one, bit in zip(prob_values, bits):
            p_assign *= _bool_prob(prob_one, bit)
            if p_assign == 0.0:
                break
        if p_assign == 0.0:
            continue
        if y:
            p_y1 += p_assign
    return p_y1


def _channel_pbv(channel_rows, public_indices, secret_indices, ref_sensitive_secret_indices, prob_u_tuple, prob_c0_tuple, prob_c1_tuple):
    joint = defaultdict(float)
    for ref_value in (0, 1):
        for bits, y in channel_rows:
            prob = 0.5
            for idx in public_indices:
                prob *= _bool_prob(prob_u_tuple[idx], bits[idx])
            for idx in secret_indices:
                if idx in ref_sensitive_secret_indices:
                    cond_prob = prob_c0_tuple[idx] if ref_value == 0 else prob_c1_tuple[idx]
                else:
                    cond_prob = prob_u_tuple[idx]
                prob *= _bool_prob(cond_prob, bits[idx])
            if prob == 0.0:
                continue
            public_key = tuple(bits[idx] for idx in public_indices)
            joint[(public_key, y, ref_value)] += prob

    pbv = 0.0
    for public_key, y in {(k[0], k[1]) for k in joint.keys()}:
        pbv += max(joint.get((public_key, y, 0), 0.0), joint.get((public_key, y, 1), 0.0))
    return pbv


def _compute_qflow_metrics(order, truth_table_map, ref_bits, input_bits, secret_support, max_channel_inputs):
    protected_atoms = set(input_bits) | set(ref_bits)
    ref_bit_set = set(ref_bits)
    merge_signal = _build_channel_merger(truth_table_map, protected_atoms, max_channel_inputs)
    channel_rows_cache = {}
    channel_prob_cache = {}
    channel_pbv_cache = {}
    approx_prob_cache = {}
    signal_prob = {}
    signal_prob_cond = {}
    signal_leak = {}
    signal_pbv = {}

    for sig in input_bits:
        signal_prob[sig] = 0.5
        signal_prob_cond[sig] = {}
        signal_leak[sig] = {}
        signal_pbv[sig] = {}

    for sig in ref_bits:
        signal_prob[sig] = 0.5
        signal_prob_cond[sig] = {sig: {0: 0.0, 1: 1.0}}
        signal_leak[sig] = {sig: 1.0}
        signal_pbv[sig] = {sig: 1.0}

    for sig in tqdm(order, desc="QFlow Channel Processing"):
        if not isinstance(sig, str) or sig in signal_leak:
            continue
        if sig not in truth_table_map:
            relevant_refs = sorted(secret_support.get(sig, set()) & ref_bit_set)
            signal_prob[sig] = _base_unconditional_prob(sig, input_bits, ref_bits)
            signal_prob_cond[sig] = {}
            signal_leak[sig] = {}
            signal_pbv[sig] = {}
            continue

        channel_input_nodes = tuple(_direct_signal_inputs(truth_table_map[sig]))
        merged_expr, leaves = merge_signal(sig)
        leaf_refs = set()
        for leaf in leaves:
            if isinstance(leaf, str):
                leaf_refs |= (secret_support.get(leaf, set()) & ref_bit_set)
        relevant_refs = sorted((secret_support.get(sig, set()) & ref_bit_set) | leaf_refs)
        if not isinstance(merged_expr, list):
            signal_prob[sig] = signal_prob.get(merged_expr, _base_unconditional_prob(merged_expr, input_bits, ref_bits))
            signal_prob_cond[sig] = {}
            signal_leak[sig] = {}
            signal_pbv[sig] = {}
            for ref in relevant_refs:
                p0 = signal_prob_cond.get(merged_expr, {}).get(ref, {}).get(
                    0, _base_conditional_prob(merged_expr, ref, 0, input_bits, ref_bits)
                )
                p1 = signal_prob_cond.get(merged_expr, {}).get(ref, {}).get(
                    1, _base_conditional_prob(merged_expr, ref, 1, input_bits, ref_bits)
                )
                signal_prob_cond[sig][ref] = {0: p0, 1: p1}
                signal_pbv[sig][ref] = _pbv_from_conditionals(p0, p1, 0.5)
                signal_leak[sig][ref] = signal_leak.get(merged_expr, {}).get(ref, 0.0)
            continue

        leaves_sorted = tuple(sorted(leaves))
        leaf_index = {leaf: idx for idx, leaf in enumerate(leaves_sorted)}
        channel_key = (_freeze_expr(merged_expr), leaves_sorted)
        exact_channel = (
            len(leaves_sorted) <= max_channel_inputs
            and all(op in SUPPORTED_BOOL_OPS for op in _collect_ops(merged_expr))
        )

        if exact_channel:
            channel_rows = channel_rows_cache.get(channel_key)
            if channel_rows is None:
                channel_rows = _channel_truth_table(merged_expr, leaves_sorted)
                channel_rows_cache[channel_key] = channel_rows
        else:
            channel_rows = None

        prob_u_tuple = tuple(
            signal_prob.get(leaf, _base_unconditional_prob(leaf, input_bits, ref_bits))
            for leaf in leaves_sorted
        )
        if exact_channel:
            prob_key = (channel_key, prob_u_tuple)
            prob_val = channel_prob_cache.get(prob_key)
            if prob_val is None:
                prob_val = _channel_output_prob(
                    channel_rows,
                    prob_u_tuple,
                )
                channel_prob_cache[prob_key] = prob_val
            signal_prob[sig] = prob_val
        else:
            approx_key = ("u", channel_key, prob_u_tuple)
            approx_val = approx_prob_cache.get(approx_key)
            if approx_val is None:
                approx_val = _approx_expr_prob(
                    merged_expr,
                    lambda leaf, vals=prob_u_tuple, index=leaf_index: (
                        vals[index[leaf]]
                        if leaf in index
                        else signal_prob.get(leaf, _base_unconditional_prob(leaf, input_bits, ref_bits))
                    ),
                )
                approx_prob_cache[approx_key] = approx_val
            signal_prob[sig] = approx_val

        signal_prob_cond[sig] = {}
        signal_leak[sig] = {}
        signal_pbv[sig] = {}
        leaf_secret_supports = tuple(secret_support.get(leaf, set()) for leaf in leaves_sorted)
        public_indices = tuple(idx for idx, refs in enumerate(leaf_secret_supports) if not refs)
        secret_indices = tuple(idx for idx, refs in enumerate(leaf_secret_supports) if refs)
        for ref in relevant_refs:
            prob_c0_tuple = tuple(
                (
                    signal_prob.get(leaf, _base_unconditional_prob(leaf, input_bits, ref_bits))
                    if ref not in leaf_secret_supports[idx]
                    else signal_prob_cond.get(leaf, {}).get(ref, {}).get(
                        0, _base_conditional_prob(leaf, ref, 0, input_bits, ref_bits)
                    )
                )
                for idx, leaf in enumerate(leaves_sorted)
            )
            prob_c1_tuple = tuple(
                (
                    signal_prob.get(leaf, _base_unconditional_prob(leaf, input_bits, ref_bits))
                    if ref not in leaf_secret_supports[idx]
                    else signal_prob_cond.get(leaf, {}).get(ref, {}).get(
                        1, _base_conditional_prob(leaf, ref, 1, input_bits, ref_bits)
                    )
                )
                for idx, leaf in enumerate(leaves_sorted)
            )
            ref_sensitive_secret_indices = frozenset(
                idx for idx in secret_indices if ref in leaf_secret_supports[idx]
            )

            if exact_channel:
                prob0_key = (channel_key, prob_c0_tuple)
                p1_if_0 = channel_prob_cache.get(prob0_key)
                if p1_if_0 is None:
                    p1_if_0 = _channel_output_prob(
                        channel_rows,
                        prob_c0_tuple,
                    )
                    channel_prob_cache[prob0_key] = p1_if_0
                prob1_key = (channel_key, prob_c1_tuple)
                p1_if_1 = channel_prob_cache.get(prob1_key)
                if p1_if_1 is None:
                    p1_if_1 = _channel_output_prob(
                        channel_rows,
                        prob_c1_tuple,
                    )
                    channel_prob_cache[prob1_key] = p1_if_1
                pbv_key = (
                    channel_key,
                    public_indices,
                    secret_indices,
                    ref_sensitive_secret_indices,
                    prob_u_tuple,
                    prob_c0_tuple,
                    prob_c1_tuple,
                )
                pbv = channel_pbv_cache.get(pbv_key)
                if pbv is None:
                    pbv = _channel_pbv(
                        channel_rows,
                        public_indices,
                        secret_indices,
                        ref_sensitive_secret_indices,
                        prob_u_tuple,
                        prob_c0_tuple,
                        prob_c1_tuple,
                    )
                    channel_pbv_cache[pbv_key] = pbv
            else:
                approx0_key = ("c0", channel_key, ref, prob_c0_tuple)
                p1_if_0 = approx_prob_cache.get(approx0_key)
                if p1_if_0 is None:
                    p1_if_0 = _approx_expr_prob(
                        merged_expr,
                        lambda leaf, vals=prob_c0_tuple, ref=ref, index=leaf_index: (
                            vals[index[leaf]]
                            if leaf in index
                            else signal_prob_cond.get(leaf, {}).get(ref, {}).get(
                                0, _base_conditional_prob(leaf, ref, 0, input_bits, ref_bits)
                            )
                        ),
                    )
                    approx_prob_cache[approx0_key] = p1_if_0
                approx1_key = ("c1", channel_key, ref, prob_c1_tuple)
                p1_if_1 = approx_prob_cache.get(approx1_key)
                if p1_if_1 is None:
                    p1_if_1 = _approx_expr_prob(
                        merged_expr,
                        lambda leaf, vals=prob_c1_tuple, ref=ref, index=leaf_index: (
                            vals[index[leaf]]
                            if leaf in index
                            else signal_prob_cond.get(leaf, {}).get(ref, {}).get(
                                1, _base_conditional_prob(leaf, ref, 1, input_bits, ref_bits)
                            )
                        ),
                    )
                    approx_prob_cache[approx1_key] = p1_if_1
                pbv = _pbv_from_conditionals(p1_if_0, p1_if_1, 0.5)

            signal_prob_cond[sig][ref] = {0: p1_if_0, 1: p1_if_1}
            leak_sources = channel_input_nodes if channel_input_nodes else leaves
            leak_in = sum(signal_leak.get(src, {}).get(ref, 0.0) for src in leak_sources)
            signal_pbv[sig][ref] = pbv
            signal_leak[sig][ref] = min(1.0, pbv * leak_in) if leak_in > 0.0 else 0.0

    return signal_prob, signal_prob_cond, signal_leak, signal_pbv


def _discover_output_bits(top_module_name):
    output_bits = []
    outputs = module_maps.moduleOutputPortListMap.get(top_module_name, [])
    widths = module_maps.moduleOutputPortWidthListMap.get(top_module_name, [])
    for name, width in zip(outputs, widths):
        for bit in range(width):
            output_bits.append(f"{top_module_name}.{name}[{bit}:{bit}]")
            for t in range(UNROLL_DEPTH + 1):
                output_bits.append(f"{top_module_name}.{name}[{bit}:{bit}]@{t}")
    return output_bits


def _select_top_output_candidates(top_module_name, signal_names):
    output_bits = set(_discover_output_bits(top_module_name))
    return sorted(sig for sig in signal_names if isinstance(sig, str) and sig in output_bits)


def _write_results(results_dir, design, signal_leak, signal_pbv, ref_bits, candidate_signals, runtime_s):
    os.makedirs(results_dir, exist_ok=True)
    aggregated_rows = []
    per_secret_rows = []
    aggregated_by_base = {}
    for sig in sorted(candidate_signals):
        if sig not in signal_leak:
            continue
        best_ref = None
        best_leak = 0.0
        best_pbv = 0.5
        for ref in ref_bits:
            leak = signal_leak[sig].get(ref, 0.0)
            pbv = signal_pbv[sig].get(ref, 0.5)
            per_secret_rows.append((sig, ref, leak, pbv))
            if leak > best_leak:
                best_leak = leak
                best_ref = ref
                best_pbv = pbv
        aggregated_rows.append((sig, best_leak, best_ref, best_pbv))
        base_sig = sig.split("@", 1)[0]
        prev = aggregated_by_base.get(base_sig)
        if prev is None or best_leak > prev[1]:
            aggregated_by_base[base_sig] = (sig, best_leak, best_ref, best_pbv)

    aggregated_rows.sort(key=lambda row: row[1], reverse=True)
    per_secret_rows.sort(key=lambda row: row[2], reverse=True)
    aggregated_base_rows = [
        (base_sig, vals[1], vals[2], vals[3], vals[0])
        for base_sig, vals in aggregated_by_base.items()
    ]
    aggregated_base_rows.sort(key=lambda row: row[1], reverse=True)

    with open(os.path.join(results_dir, "leaks.txt"), "w", encoding="utf-8") as handle:
        handle.write("Signal,Leakage,BestRef,PBV,BestTimeSignal\n")
        for sig, leak, ref, pbv, best_time_sig in aggregated_base_rows:
            handle.write(f"{sig},{leak:.15f},{ref},{pbv:.15f},{best_time_sig}\n")

    with open(os.path.join(results_dir, "leaks_per_secret.txt"), "w", encoding="utf-8") as handle:
        handle.write("Signal,SecretBit,Leakage,PBV\n")
        for sig, ref, leak, pbv in per_secret_rows:
            handle.write(f"{sig},{ref},{leak:.15f},{pbv:.15f}\n")

    with open(os.path.join(results_dir, "leaks_per_time.txt"), "w", encoding="utf-8") as handle:
        handle.write("Signal,Leakage,BestRef,PBV\n")
        for sig, leak, ref, pbv in aggregated_rows:
            handle.write(f"{sig},{leak:.15f},{ref},{pbv:.15f}\n")

    with open(os.path.join(results_dir, "time.txt"), "w", encoding="utf-8") as handle:
        handle.write(f"Design: {design}\n")
        handle.write(f"Signals analysed: {len(aggregated_base_rows)}\n")
        handle.write(f"Runtime: {runtime_s:.4f}s\n")
        handle.write(f"Unroll depth: {UNROLL_DEPTH}\n")

    return aggregated_base_rows, per_secret_rows, aggregated_rows


def main(input_file_path, top_module_name, ref_module_name, ref_instance_name,
         ref_sig_name, ref_sig_width, design, results_dir, max_channel_inputs):
    start_time = time.time()
    os.environ["PATH"] = r"C:\iverilog\bin;" + os.environ["PATH"]

    print()
    print("******************************************************************")
    print("Design:", design)
    print(f"Temporal unroll depth: {UNROLL_DEPTH}")
    print()

    ref_bits = [f"{ref_sig_name}[{bit}:{bit}]" for bit in range(ref_sig_width)]
    input_names, input_widths, signal_names, _, truth_table_map = module_maps.subCircuitExtract(
        input_file_path, top_module_name, ref_module_name, ref_instance_name, ref_bits
    )

    truth_table_map, signal_names_unrolled = module_maps.build_time_unrolled_truth_table(
        truth_table_map, UNROLL_DEPTH
    )
    truth_table_map = lower_truth_table_map(truth_table_map)
    signal_names = set(signal_names_unrolled) | set(truth_table_map.keys()) | set(ref_bits)

    graph_truth_table_map = {
        sig: _string_only_expr(expr)
        for sig, expr in truth_table_map.items()
        if isinstance(sig, str)
    }
    graph_signal_names = {sig for sig in signal_names if isinstance(sig, str)}
    graph = build_recon_graph_artifacts(graph_signal_names, graph_truth_table_map)
    candidate_signals = _select_top_output_candidates(top_module_name, signal_names)
    reachable_signals = _collect_reachable_signals(graph, candidate_signals)
    truth_table_map = {sig: expr for sig, expr in truth_table_map.items() if sig in reachable_signals}
    signal_names = reachable_signals | set(ref_bits)
    graph_order = [sig for sig in graph["order"] if sig in reachable_signals or sig in ref_bits]

    input_bits = []
    for name, width in zip(input_names, input_widths):
        for t in range(UNROLL_DEPTH + 1):
            input_bits.extend(
                bit_name
                for bit_name in (f"{name}[{bit}:{bit}]@{t}" for bit in range(width))
                if bit_name in reachable_signals
            )

    secret_support = _compute_secret_support(truth_table_map, ref_bits)
    candidate_signals = _select_top_output_candidates(top_module_name, signal_names)
    print(f"Reachable signals in output cone: {len(reachable_signals)} / {len(graph_signal_names)}")
    active_ref_bits = sorted({
        ref
        for sig in candidate_signals
        for ref in secret_support.get(sig, set())
        if ref in ref_bits
    })
    print(f"Candidate-inferred active secret bits: {len(active_ref_bits)} / {len(ref_bits)}")
    _, _, signal_leak, signal_pbv = _compute_qflow_metrics(
        graph_order,
        truth_table_map,
        ref_bits,
        input_bits,
        secret_support,
        max_channel_inputs,
    )

    runtime_s = time.time() - start_time
    aggregated_rows, per_secret_rows, aggregated_time_rows = _write_results(
        results_dir, design, signal_leak, signal_pbv, ref_bits, candidate_signals, runtime_s
    )

    print("Top 20 top-module output signals by QFlow leakage:")
    for sig, leak, ref, pbv, best_time_sig in aggregated_rows[:20]:
        print(f"Signal: {sig}, Leakage: {leak:.15f}, Ref: {ref}, PBV: {pbv:.15f}, BestTimeSignal: {best_time_sig}")

    print()
    print("Top 20 top-module output signal/secret pairs by QFlow leakage:")
    for sig, ref, leak, pbv in per_secret_rows[:20]:
        print(f"Signal: {sig}, Secret: {ref}, Leakage: {leak:.15f}, PBV: {pbv:.15f}")

    print()
    print("Top 20 top-module output time-slice signals by QFlow leakage:")
    for sig, leak, ref, pbv in aggregated_time_rows[:20]:
        print(f"Signal: {sig}, Leakage: {leak:.15f}, Ref: {ref}, PBV: {pbv:.15f}")

    print()
    print(f"Signals analysed: {len(aggregated_rows)}")
    print(f"Total time taken: {runtime_s:.4f}s")
    print("Completed!")
    print("******************************************************************")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Isolated QFlow implementation using FORTIFY parser infrastructure without modifying existing files"
    )
    parser.add_argument("InputFilePath", metavar="input_file_path", type=str)
    parser.add_argument("TopModuleName", metavar="top_module_name", type=str)
    parser.add_argument("RefModuleName", metavar="ref_module_name", type=str)
    parser.add_argument("RefInstanceName", metavar="ref_instance_name", type=str)
    parser.add_argument("RefSigName", metavar="ref_sig_name", type=str)
    parser.add_argument("RefSigWidth", metavar="ref_sig_width", type=int)
    parser.add_argument("Design", metavar="design", type=str)
    parser.add_argument("-r", "--results-path", type=str, action="store")
    parser.add_argument("--max-channel-inputs", type=int, default=5)
    args = parser.parse_args()

    if args.results_path:
        results_dir = os.path.join("results", args.results_path, args.Design)
    else:
        results_dir = os.path.join("results", datetime.today().strftime("%Y-%m-%d-%H-%M-%S"), args.Design)

    main(
        args.InputFilePath,
        args.TopModuleName,
        args.RefModuleName,
        args.RefInstanceName,
        args.RefSigName,
        args.RefSigWidth,
        args.Design,
        results_dir,
        args.max_channel_inputs,
    )
