"""Utilities to extract the reconvergence leakage subgraph.

This module mirrors the leakage subgraph extraction logic used in
`run_fortify_seq.py` and exposes it as a callable function.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Set, Tuple


def extract_leaky_outputs(
    results: Dict[Tuple[str, str], Dict[str, float]],
    leakage_threshold: float = 1.0,
) -> Set[str]:
    """Return output signals whose leakage exceeds threshold."""
    return {sig
        for (sig, _ref), metrics in results.items()
        if metrics.get("Leakage", 0.0) > leakage_threshold
        if metrics.get("Leakage", 0.0) > leakage_threshold
    }


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
            return (
                _extract_signal_names(cond, self_name=self_name)
                | _extract_signal_names(tval, self_name=self_name)
                | _extract_signal_names(fval, self_name=self_name)
            )
        out = set()
        for part in exp[1:]:
            out |= _extract_signal_names(part, self_name=self_name)
        return out
    return set()


def _build_parents_children(truth_table_map):
    parents = {}
    children = {}
    for sig, exp in truth_table_map.items():
        ps = _extract_signal_names(exp, self_name=sig)
        parents[sig] = ps
        for p in ps:
            children.setdefault(p, set()).add(sig)
    return parents, children


def _backward_cone(outputs, parents):
    stack = list(outputs)
    seen = set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for p in parents.get(n, set()):
            if p not in seen:
                stack.append(p)
    return seen


def _forward_reach(seeds, children):
    stack = list(seeds)
    seen = set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for ch in children.get(n, set()):
            if ch not in seen:
                stack.append(ch)
    return seen


def extract_sub_recon_graph(
    truth_table_map: Dict[str, object],
    ref_sig_bit_names: Iterable[str],
    signal_names: Iterable[str],
    results: Optional[Dict[Tuple[str, str], Dict[str, float]]] = None,
    leaky_outputs: Optional[Iterable[str]] = None,
    leakage_threshold: float = 1.0,
    unroll_depth: int = 32,
) -> Set[str]:
    """Return leakage subgraph nodes for reconvergence-aware analysis.

    Args:
        truth_table_map: Signal dependency map.
        ref_sig_bit_names: Secret/reference bit names.
        signal_names: All known signal names (including time-indexed ones).
        results: Optional leakage results map {(sig, ref): {"Leakage": ...}}.
        leaky_outputs: Optional explicit leaky output set. If provided, `results`
            and `leakage_threshold` are ignored for leaky-output selection.
        leakage_threshold: Leakage cutoff for selecting leaky outputs from
            `results` (default matches `run_fortify_seq.py` logic: > 1.0).
        unroll_depth: Max time index used to include `ref@t` seeds.
    """
    ref_sig_bit_names = set(ref_sig_bit_names)
    signal_names = set(signal_names)

    if leaky_outputs is None:
        if results is None:
            leaky_outputs_set = set()
        else:
            leaky_outputs_set = extract_leaky_outputs(
                results, leakage_threshold=leakage_threshold
            )

            #leaky_outputs_set.add("top.Antena[0:0]@0")
    else:
        print("good")
        leaky_outputs_set = set(leaky_outputs)

    parents, children = _build_parents_children(truth_table_map)

    ref_seeds = set(ref_sig_bit_names)
    for r in ref_sig_bit_names:
        for t in range(unroll_depth + 1):
            rt = f"{r}@{t}"
            if rt in signal_names:
                ref_seeds.add(rt)

    backcone = _backward_cone(leaky_outputs_set, parents)
    forward = _forward_reach(ref_sig_bit_names, children)
    leakage_subgraph = (backcone) | leaky_outputs_set | ref_seeds
    return leakage_subgraph
