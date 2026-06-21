"""Build and cache static graph artifacts for reconvergence probability DP."""


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


def build_recon_graph_artifacts(signal_names, truth_table_map):
    """Return reusable graph artifacts for recon DP.

    Returns a dict with keys: universe, parents, children, order.
    """
    universe = set(signal_names) | set(truth_table_map.keys())
    for exp in truth_table_map.values():
        universe |= _extract_signal_names(exp)

    parents = {s: set() for s in universe}
    children = {s: set() for s in universe}

    for s in universe:
        exp = truth_table_map.get(s, None)
        if exp is None:
            continue
        if isinstance(exp, str) and isinstance(s, str) and s.endswith("@0") and exp == s[:-2]:
            # Treat @0 aliases as pure aliases (no dependency edge).
            continue
        ps = _extract_signal_names(exp, self_name=s)
        parents[s] = ps
        for p in ps:
            children.setdefault(p, set()).add(s)

    indeg = {s: len(parents.get(s, set())) for s in universe}
    q = [s for s, d in indeg.items() if d == 0]
    order = []
    levels = []
    while q:
        cur = list(q)
        q = []
        levels.append(cur)
        for n in cur:
            order.append(n)
            for ch in children.get(n, set()):
                indeg[ch] -= 1
                if indeg[ch] == 0:
                    q.append(ch)

    # In case any nodes remain due unresolved cycles, keep deterministic fallback.
    '''
    remaining = [s for s in universe if s not in set(order)]
    for n in remaining:
        levels.append([n])
        order.append(n)
    '''
    with open("parents.txt", "w", encoding="utf-8") as f:
        for sig in sorted(parents):
            f.write(f"{sig}: {sorted(parents[sig])}\n")
    return {
        "universe": universe,
        "parents": parents,
        "children": children,
        "order": order,
        "levels": levels,
    }