
#!/usr/bin/env python3
"""FORTIFY leakage based Trojan-output miner."""

from __future__ import annotations

import argparse
import ast
import math
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import networkx as nx
import pandas as pd


# -----------------------------
# Default parameters
# -----------------------------

PROJECT_ROOT = Path("fortify")
RESULTS_ROOT = PROJECT_ROOT / "results"
RUN_NAME = "aes"
OUTPUT_ROOT = PROJECT_ROOT / "analysis_outputs" / "leakage_topology"

AES_SIZES = [100, 200, 700, 800, 1100, 1200, 1600, 1700]
DESIGN_NAME_TEMPLATE = "aes{size}"
RESULT_SUBDIR_TEMPLATE = "{design}_recon"
VERILOG_FILE_TEMPLATE = "AES{size}.v"

PARENTS_FILENAME = "parents.txt"
LEAKAGE_FILENAME = "all_signal_leakage.csv"

PRIMARY_OUTPUTS_ONLY = True
PRIMARY_OUTPUT_BASES = ["top.Capacitance", "top.out"]
OUTPUT_NODE_MODE = "primary_bases"
OUTPUT_PREFIXES = ["top.Capacitance", "top.out"]
EXCLUDE_INPUT_LIKE_CANDIDATES = True

GROUP_TIME_INSTANCES = True
TROJAN_MAX_OUTPUT_CANDIDATES_TO_SCORE = None
TROJAN_MIN_FANIN_LEAKAGE_NODES = 5
TROJAN_NEAR_TARGET_DISTANCE = 3
LEAKAGE_EPS = 1e-30

PRINT_TOP_N = None
CACHE_VERSION = "fortify_trojan_miner_v2"


# -----------------------------
# Small naming helpers
# -----------------------------

INPUT_LIKE_RE = re.compile(r"(^|\.)in(?:\[\d+(?::\d+)?\])?$")


def strip_time_suffix(node: str) -> str:
    """Remove a trailing time suffix such as @0 or @17."""
    return str(node).split("@", 1)[0]


def canonical_signal(node: str, group_time: bool = True) -> str:
    """Return the candidate identity used for de-duplicating time instances."""
    return strip_time_suffix(node) if group_time else str(node)


def bus_base(node: str) -> str:
    """Return the bus/base name without bit range or time suffix."""
    return strip_time_suffix(str(node)).split("[", 1)[0]


def is_input_like_node(node: str) -> bool:
    """Detect module input pins named .in, .in[3], or .in[3:3]."""
    return bool(INPUT_LIKE_RE.search(strip_time_suffix(str(node))))


def matches_prefix(node: str, prefixes: Optional[Sequence[str]]) -> bool:
    """Check whether a signal begins with one of the allowed prefixes."""
    if prefixes is None:
        return True
    return any(str(node).startswith(prefix) for prefix in prefixes)


def is_primary_output_node(node: str, primary_bases: Sequence[str]) -> bool:
    """Check whether a node belongs to a configured primary-output bus."""
    return bus_base(node) in set(primary_bases)


# -----------------------------
# Design specification
# -----------------------------

@dataclass
class DesignSpec:
    """Filesystem description of one design instance."""
    design: str
    size: Optional[int]
    result_dir: Path
    verilog_file: Path


def make_design_specs(
    sizes: Sequence[int] = AES_SIZES,
    project_root: Path = PROJECT_ROOT,
    run_name: str = RUN_NAME,
    design_name_template: str = DESIGN_NAME_TEMPLATE,
    result_subdir_template: str = RESULT_SUBDIR_TEMPLATE,
    verilog_file_template: str = VERILOG_FILE_TEMPLATE,
) -> List[DesignSpec]:
    """Create design specs from the configured AES sizes."""
    specs = []
    for size in sizes:
        design = design_name_template.format(size=size)
        result_subdir = result_subdir_template.format(size=size, design=design)
        verilog_name = verilog_file_template.format(size=size, design=design)
        specs.append(
            DesignSpec(
                design=design,
                size=size,
                result_dir=project_root / "results" / run_name / result_subdir,
                verilog_file=project_root / "Benchmarks" / verilog_name,
            )
        )
    return specs


# -----------------------------
# Input parsers
# -----------------------------

def read_parents_txt(path: Path) -> Dict[str, List[str]]:
    """Parse parents.txt where each row is child: [parents]."""
    path = Path(path)
    parents: Dict[str, List[str]] = {}

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            if ": " in line:
                child, rhs = line.split(": ", 1)
            elif ": [" in line:
                idx = line.find(": [")
                child, rhs = line[:idx], line[idx + 2:].strip()
            else:
                child, rhs = line.rsplit(":", 1)
                rhs = rhs.strip()

            try:
                plist = ast.literal_eval(rhs)
            except Exception:
                continue

            if isinstance(plist, (list, tuple, set)):
                parents[str(child)] = [str(p) for p in plist if isinstance(p, str)]

    return parents


def load_leakage_csv(path: Path) -> Tuple[Dict[str, dict], pd.DataFrame]:
    """Load all_signal_leakage.csv and keep the maximum leakage row per node."""
    path = Path(path)
    df = pd.read_csv(path)

    if "Leakage_PBV" not in df.columns:
        raise ValueError(f"{path} is missing Leakage_PBV")

    if "ActualSignal" in df.columns:
        node_col = "ActualSignal"
    elif "Signal" in df.columns:
        node_col = "Signal"
    elif "signal" in df.columns:
        node_col = "signal"
    else:
        raise ValueError(f"{path} has no Signal/ActualSignal column")

    if "ActualRef" in df.columns:
        ref_col = "ActualRef"
    elif "Ref" in df.columns:
        ref_col = "Ref"
    elif "ref" in df.columns:
        ref_col = "ref"
    else:
        ref_col = None

    df = df.copy()
    df["node_for_graph"] = df[node_col].astype(str)
    df["Leakage_PBV"] = pd.to_numeric(df["Leakage_PBV"], errors="coerce")
    df = df.dropna(subset=["Leakage_PBV"])

    best = df.sort_values("Leakage_PBV", ascending=False).drop_duplicates("node_for_graph")
    leakage_by_node = {}

    for _, row in best.iterrows():
        node = row["node_for_graph"]
        leakage_by_node[node] = {
            "leakage": float(row["Leakage_PBV"]),
            "best_ref": str(row[ref_col]) if ref_col else "",
            "pbv": float(row["PBV"]) if "PBV" in row and pd.notna(row["PBV"]) else math.nan,
            "raw_row": row.to_dict(),
        }

    return leakage_by_node, df


# -----------------------------
# Graph and node tables
# -----------------------------

def build_networkx_graph_from_parents(
    parents: Dict[str, Sequence[str]],
    extra_nodes: Optional[Iterable[str]] = None,
) -> nx.DiGraph:
    """Build a raw dependency graph with edges parent -> child."""
    G = nx.DiGraph()

    for child, plist in parents.items():
        G.add_node(child)
        for parent in plist or []:
            G.add_edge(parent, child)

    if extra_nodes is not None:
        G.add_nodes_from([str(n) for n in extra_nodes])

    return G


def topological_levels_networkx(G: nx.DiGraph) -> Tuple[Dict[str, int], List[List[str]], bool, int]:
    """Assign levels using nx.topological_generations on a DAG or condensation DAG."""
    if nx.is_directed_acyclic_graph(G):
        generations = [list(gen) for gen in nx.topological_generations(G)]
        return {node: level for level, gen in enumerate(generations) for node in gen}, generations, True, 0

    C = nx.condensation(G)
    component_level = {}
    generations_c = [list(gen) for gen in nx.topological_generations(C)]

    for level, gen in enumerate(generations_c):
        for comp in gen:
            component_level[comp] = level

    levels = {}
    for comp, data in C.nodes(data=True):
        for member in data.get("members", []):
            levels[member] = component_level[comp]

    generations_by_level: Dict[int, List[str]] = {}
    for node, level in levels.items():
        generations_by_level.setdefault(level, []).append(node)

    generations = [sorted(generations_by_level[k]) for k in sorted(generations_by_level)]

    return levels, generations, False, nx.number_strongly_connected_components(G)


def add_leakage_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """Add global and same-level leakage percentile ranks."""
    df = df.copy()
    leak_mask = df["has_leakage"] == True

    df["leakage_percentile_global"] = math.nan
    if leak_mask.any():
        df.loc[leak_mask, "leakage_percentile_global"] = df.loc[leak_mask, "leakage"].rank(pct=True, method="average")

    df["leakage_percentile_same_level"] = math.nan
    for level, group in df[leak_mask].groupby("topological_level"):
        df.loc[group.index, "leakage_percentile_same_level"] = group["leakage"].rank(pct=True, method="average")

    return df


def build_node_dataframe(G: nx.DiGraph, leakage_by_node: Dict[str, dict], design: str) -> Tuple[pd.DataFrame, List[List[str]], bool, int]:
    """Create the main per-node table with graph and leakage features."""
    level_by_node, generations, is_dag, n_scc = topological_levels_networkx(G)

    rows = []
    for node in G.nodes():
        info = leakage_by_node.get(node, {})
        leakage = info.get("leakage", math.nan)
        rows.append(
            {
                "design": design,
                "node": node,
                "canonical_node": canonical_signal(node, True),
                "bus_base": bus_base(node),
                "topological_level": int(level_by_node.get(node, -1)),
                "indegree": int(G.in_degree(node)),
                "outdegree": int(G.out_degree(node)),
                "has_leakage": not pd.isna(leakage),
                "leakage": float(leakage) if not pd.isna(leakage) else math.nan,
                "plot_leakage": math.log10(float(leakage) + LEAKAGE_EPS) if not pd.isna(leakage) else math.nan,
                "best_ref": info.get("best_ref", ""),
                "is_input_like": is_input_like_node(node),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = add_leakage_percentiles(df)
        df = df.sort_values(["topological_level", "node"]).reset_index(drop=True)

    return df, generations, is_dag, n_scc


# -----------------------------
# Candidate features
# -----------------------------

def robust_z_scores(values: pd.Series) -> pd.Series:
    """Compute robust z-scores with median and MAD fallback."""
    s = pd.to_numeric(pd.Series(values), errors="coerce")
    med = s.median()
    mad = (s - med).abs().median()

    if pd.isna(mad) or mad <= 0:
        std = s.std(ddof=0)
        denom = std if std and std > 0 else 1.0
    else:
        denom = 1.4826 * mad

    return (s - med) / denom


def rank_auc_probability_greater(a: Iterable[float], b: Iterable[float]) -> float:
    """Compute P(a > b) + 0.5P(a = b) using rank statistics."""
    a = pd.to_numeric(pd.Series(list(a)), errors="coerce").dropna()
    b = pd.to_numeric(pd.Series(list(b)), errors="coerce").dropna()

    n = len(a)
    m = len(b)
    if n == 0 or m == 0:
        return math.nan

    values = pd.concat(
        [
            pd.DataFrame({"value": a, "group": "a"}),
            pd.DataFrame({"value": b, "group": "b"}),
        ],
        ignore_index=True,
    )

    values["rank"] = values["value"].rank(method="average")
    rank_sum_a = values.loc[values["group"] == "a", "rank"].sum()
    u = rank_sum_a - n * (n + 1) / 2.0

    return float(u / (n * m))


def ensure_candidate_features(
    node_df: pd.DataFrame,
    G: nx.DiGraph,
    primary_output_bases: Sequence[str] = PRIMARY_OUTPUT_BASES,
    output_mode: str = OUTPUT_NODE_MODE,
    output_prefixes: Optional[Sequence[str]] = OUTPUT_PREFIXES,
    primary_outputs_only: bool = PRIMARY_OUTPUTS_ONLY,
    exclude_input_like: bool = EXCLUDE_INPUT_LIKE_CANDIDATES,
) -> pd.DataFrame:
    """Mark candidate output nodes and add output-relative statistics."""
    df = node_df.copy()
    leak_mask = df["has_leakage"] == True

    primary_mask = df["node"].map(lambda n: is_primary_output_node(n, primary_output_bases))
    prefix_mask = df["node"].map(lambda n: matches_prefix(n, output_prefixes))
    sink_mask = df["node"].map(lambda n: G.out_degree(n) == 0 if n in G else False)

    if primary_outputs_only or output_mode == "primary_bases":
        output_mask = primary_mask
    elif output_mode == "prefix":
        output_mask = prefix_mask
    elif output_mode == "sinks":
        output_mask = sink_mask
    elif output_mode == "prefix_or_sinks":
        output_mask = prefix_mask | sink_mask
    elif output_mode == "all_leakage":
        output_mask = pd.Series(True, index=df.index)
    else:
        raise ValueError("OUTPUT_NODE_MODE must be primary_bases, prefix, sinks, prefix_or_sinks, or all_leakage")

    output_mask = output_mask & leak_mask
    if exclude_input_like:
        output_mask = output_mask & ~df["is_input_like"]

    df["is_output_candidate"] = output_mask

    df["output_leakage_percentile"] = math.nan
    df["output_robust_z"] = math.nan

    output_idx = df.index[df["is_output_candidate"] == True]
    if len(output_idx):
        df.loc[output_idx, "output_leakage_percentile"] = df.loc[output_idx, "leakage"].rank(pct=True, method="average")
        df.loc[output_idx, "output_robust_z"] = robust_z_scores(df.loc[output_idx, "leakage"]).to_numpy()

    return df


def fanin_cone_for_target(G: nx.DiGraph, target: str) -> Tuple[set, set, Dict[str, int]]:
    """Return fanin nodes, fanin edges, and shortest reverse distance to target."""
    if target not in G:
        return set(), set(), {}

    cone_nodes = nx.ancestors(G, target) | {target}
    sub = G.subgraph(cone_nodes)
    cone_edges = set(sub.edges())

    reverse_G = G.reverse(copy=False)
    distances = nx.single_source_shortest_path_length(reverse_G, target)

    distances = {n: int(d) for n, d in distances.items() if n in cone_nodes}

    return cone_nodes, cone_edges, distances


def level_log_lift(cone_df: pd.DataFrame, rest_df: pd.DataFrame) -> float:
    """Compute median log10 lift of fanin leakage over rest at matching levels."""
    lifts = []

    for level, g_cone in cone_df.groupby("topological_level"):
        g_rest = rest_df[rest_df["topological_level"] == level]
        if g_rest.empty:
            continue

        med_cone = g_cone["leakage"].median()
        med_rest = g_rest["leakage"].median()
        lifts.append(math.log10((med_cone + LEAKAGE_EPS) / (med_rest + LEAKAGE_EPS)))

    return float(pd.Series(lifts).median()) if lifts else math.nan


def distance_shape_metrics(cone_df: pd.DataFrame, near_distance: int = TROJAN_NEAR_TARGET_DISTANCE) -> Tuple[float, float]:
    """Summarize whether leakage rises near the candidate output."""
    if cone_df.empty or "distance_to_target" not in cone_df:
        return math.nan, math.nan

    prof = (
        cone_df.dropna(subset=["distance_to_target", "leakage"])
        .groupby("distance_to_target")["leakage"]
        .median()
        .reset_index()
        .sort_values("distance_to_target")
    )

    if len(prof) >= 2:
        corr = prof["distance_to_target"].corr(prof["leakage"], method="spearman")
    else:
        corr = math.nan

    near = cone_df[cone_df["distance_to_target"] <= near_distance]["leakage"].dropna()
    far = cone_df[cone_df["distance_to_target"] > near_distance]["leakage"].dropna()

    if len(near) and len(far):
        near_far = math.log10((near.median() + LEAKAGE_EPS) / (far.median() + LEAKAGE_EPS))
    else:
        near_far = math.nan

    return float(corr) if not pd.isna(corr) else math.nan, float(near_far) if not pd.isna(near_far) else math.nan


def clip01(x: float) -> float:
    """Clip a score component into [0, 1]."""
    if x is None or pd.isna(x):
        return 0.0
    return float(max(0.0, min(1.0, x)))


def score_candidate_output(
    candidate: str,
    node_df: pd.DataFrame,
    G: nx.DiGraph,
    min_fanin_leakage_nodes: int = TROJAN_MIN_FANIN_LEAKAGE_NODES,
    near_distance: int = TROJAN_NEAR_TARGET_DISTANCE,
) -> dict:
    """Score one candidate output using output and fanin distribution evidence."""
    row = node_df[node_df["node"] == candidate]
    if row.empty:
        raise ValueError(f"candidate not found in node table: {candidate}")

    r = row.iloc[0]
    cone_nodes, cone_edges, distances = fanin_cone_for_target(G, candidate)

    leak_df = node_df[node_df["has_leakage"] == True].copy()
    cone_df = leak_df[leak_df["node"].isin(cone_nodes)].copy()
    rest_df = leak_df[~leak_df["node"].isin(cone_nodes)].copy()

    cone_df["distance_to_target"] = cone_df["node"].map(distances)

    auc = rank_auc_probability_greater(cone_df["leakage"], rest_df["leakage"])
    fanin_med_level_pct = float(cone_df["leakage_percentile_same_level"].median()) if len(cone_df) else math.nan
    fanin_top5_frac = float((cone_df["leakage_percentile_same_level"] >= 0.95).mean()) if len(cone_df) else math.nan
    lift = level_log_lift(cone_df, rest_df)
    dist_corr, near_far = distance_shape_metrics(cone_df, near_distance)

    fanin_leakage_nodes = int(len(cone_df))
    size_conf = min(1.0, fanin_leakage_nodes / max(1, min_fanin_leakage_nodes))

    output_pct = clip01(r.get("output_leakage_percentile", math.nan))
    output_level_pct = clip01(r.get("leakage_percentile_same_level", math.nan))
    output_z_comp = clip01(float(r.get("output_robust_z", 0.0)) / 6.0)
    auc_comp = clip01((auc - 0.5) / 0.5)
    fanin_level_comp = clip01(fanin_med_level_pct)
    fanin_top5_comp = clip01(fanin_top5_frac / 0.25)
    lift_comp = clip01(lift / 2.0)
    distance_comp = clip01(-dist_corr)

    weighted = (
        0.16 * output_pct
        + 0.16 * output_level_pct
        + 0.14 * output_z_comp
        + 0.20 * auc_comp
        + 0.16 * fanin_level_comp
        + 0.10 * fanin_top5_comp
        + 0.04 * lift_comp
        + 0.04 * distance_comp
    )

    score = 100.0 * size_conf * weighted

    reasons = []
    if output_level_pct >= 0.95:
        reasons.append("output same-level outlier")
    if output_z_comp >= 0.5:
        reasons.append("output robust-z high")
    if auc >= 0.70:
        reasons.append("fanin distribution shifted")
    if fanin_med_level_pct >= 0.75:
        reasons.append("fanin high for levels")
    if fanin_top5_frac >= 0.10:
        reasons.append("many fanin level-outliers")
    if not pd.isna(near_far) and near_far > 0:
        reasons.append("near-output lift")

    return {
        "candidate_output": candidate,
        "canonical_output": canonical_signal(candidate, True),
        "leakage": float(r["leakage"]),
        "best_ref": r.get("best_ref", ""),
        "topological_level": int(r["topological_level"]),
        "output_leakage_percentile": output_pct,
        "output_same_level_percentile": output_level_pct,
        "output_robust_z": float(r.get("output_robust_z", math.nan)),
        "fanin_nodes": int(len(cone_nodes)),
        "fanin_edges": int(len(cone_edges)),
        "fanin_leakage_nodes": fanin_leakage_nodes,
        "path_size_confidence": size_conf,
        "fanin_auc_vs_rest": float(auc) if not pd.isna(auc) else math.nan,
        "fanin_median_same_level_percentile": fanin_med_level_pct,
        "fanin_same_level_top5_frac": fanin_top5_frac,
        "median_level_log10_lift_cone_over_rest": float(lift) if not pd.isna(lift) else math.nan,
        "distance_spearman_corr": float(dist_corr) if not pd.isna(dist_corr) else math.nan,
        "near_to_far_log10_lift": float(near_far) if not pd.isna(near_far) else math.nan,
        "trojan_candidate_score": float(score),
        "reason": "; ".join(reasons) if reasons else "weak or mixed evidence",
    }


def select_candidate_outputs(
    node_df: pd.DataFrame,
    max_candidates: Optional[int] = TROJAN_MAX_OUTPUT_CANDIDATES_TO_SCORE,
    exclude_input_like: bool = EXCLUDE_INPUT_LIKE_CANDIDATES,
) -> List[str]:
    """Select output candidates without using an absolute leakage threshold."""
    cand = node_df[node_df["is_output_candidate"] == True].copy()

    if exclude_input_like:
        cand = cand[~cand["is_input_like"]].copy()

    cand = cand.sort_values(["output_leakage_percentile", "leakage"], ascending=False)

    if max_candidates is not None:
        cand = cand.head(int(max_candidates))

    return cand["node"].tolist()


def group_time_instances(score_df: pd.DataFrame, group_time: bool = GROUP_TIME_INSTANCES) -> pd.DataFrame:
    """Group candidate_output@t rows and keep the maximum score representative."""
    if not group_time or score_df.empty:
        out = score_df.copy()
        out["grouped_instance_count"] = 1
        return out

    df = score_df.copy()
    df["canonical_output"] = df["candidate_output"].map(lambda n: canonical_signal(n, True))
    df = df.sort_values("trojan_candidate_score", ascending=False)

    counts = df.groupby("canonical_output")["candidate_output"].count().rename("grouped_instance_count")
    best = df.drop_duplicates("canonical_output").copy()
    best = best.merge(counts, on="canonical_output", how="left")
    best["representative_output"] = best["candidate_output"]
    best["candidate_output"] = best["canonical_output"]

    return best.sort_values("trojan_candidate_score", ascending=False).reset_index(drop=True)


# -----------------------------
# Per-design analysis and cache
# -----------------------------

def cache_paths(out_dir: Path, design: str) -> dict:
    """Return cache and result paths for one design."""
    return {
        "bundle": out_dir / f"{design}_analysis_bundle.pkl",
        "nodes": out_dir / f"{design}_nodes_levels_leakage.csv",
        "raw_candidates": out_dir / f"{design}_trojan_output_candidates_raw.csv",
        "grouped_candidates": out_dir / f"{design}_trojan_output_candidates.csv",
        "stats": out_dir / f"{design}_analysis_stats.csv",
        "graph_edges": out_dir / f"{design}_graph_edges.csv",
        "top_generations": out_dir / f"{design}_networkx_topological_generations.csv",
    }


def analyze_design(
    spec: DesignSpec,
    force: bool = False,
    group_time: bool = GROUP_TIME_INSTANCES,
    save: bool = True,
) -> dict:
    """Analyze one design, using cached results unless force=True."""
    out_dir = OUTPUT_ROOT / spec.design
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = cache_paths(out_dir, spec.design)

    if not force and paths["bundle"].exists() and paths["grouped_candidates"].exists():
        with paths["bundle"].open("rb") as f:
            bundle = pickle.load(f)
        bundle["candidates_df"] = pd.read_csv(paths["grouped_candidates"])
        bundle["raw_candidates_df"] = pd.read_csv(paths["raw_candidates"]) if paths["raw_candidates"].exists() else bundle["candidates_df"]
        bundle["loaded_from_cache"] = True
        return bundle

    parents_path = spec.result_dir / PARENTS_FILENAME
    leakage_path = spec.result_dir / LEAKAGE_FILENAME

    if not parents_path.exists():
        raise FileNotFoundError(f"Missing parents file: {parents_path}")
    if not leakage_path.exists():
        raise FileNotFoundError(f"Missing leakage CSV: {leakage_path}")

    parents = read_parents_txt(parents_path)
    leakage_by_node, leakage_raw_df = load_leakage_csv(leakage_path)
    G = build_networkx_graph_from_parents(parents, extra_nodes=leakage_by_node.keys())

    node_df, generations, is_dag, n_scc = build_node_dataframe(G, leakage_by_node, spec.design)
    node_df = ensure_candidate_features(node_df, G)

    candidates = select_candidate_outputs(node_df)
    score_rows = [score_candidate_output(c, node_df, G) for c in candidates]

    raw_candidates_df = pd.DataFrame(score_rows)
    grouped_candidates_df = group_time_instances(raw_candidates_df, group_time)

    if not grouped_candidates_df.empty:
        grouped_candidates_df = grouped_candidates_df.sort_values("trojan_candidate_score", ascending=False).reset_index(drop=True)
        grouped_candidates_df.insert(0, "rank", range(1, len(grouped_candidates_df) + 1))

    if not raw_candidates_df.empty:
        raw_candidates_df = raw_candidates_df.sort_values("trojan_candidate_score", ascending=False).reset_index(drop=True)
        raw_candidates_df.insert(0, "raw_rank", range(1, len(raw_candidates_df) + 1))

    stats = {
        "design": spec.design,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "is_dag": is_dag,
        "strongly_connected_components": n_scc,
        "topological_levels": int(node_df["topological_level"].max() + 1) if len(node_df) else 0,
        "leakage_nodes": int(node_df["has_leakage"].sum()),
        "output_candidates_scored": int(len(raw_candidates_df)),
        "output_candidates_reported": int(len(grouped_candidates_df)),
        "max_candidate_score": float(grouped_candidates_df["trojan_candidate_score"].max()) if len(grouped_candidates_df) else math.nan,
    }

    bundle = {
        "cache_version": CACHE_VERSION,
        "design": spec.design,
        "spec": spec,
        "out_dir": out_dir,
        "graph": G,
        "node_df": node_df,
        "generations": generations,
        "is_dag": is_dag,
        "n_scc": n_scc,
        "leakage_raw_df": leakage_raw_df,
        "raw_candidates_df": raw_candidates_df,
        "candidates_df": grouped_candidates_df,
        "stats": stats,
        "loaded_from_cache": False,
    }

    if save:
        node_df.to_csv(paths["nodes"], index=False)
        raw_candidates_df.to_csv(paths["raw_candidates"], index=False)
        grouped_candidates_df.to_csv(paths["grouped_candidates"], index=False)
        pd.DataFrame([stats]).to_csv(paths["stats"], index=False)
        pd.DataFrame(list(G.edges()), columns=["parent", "child"]).to_csv(paths["graph_edges"], index=False)
        save_generations_csv(generations, paths["top_generations"])
        with paths["bundle"].open("wb") as f:
            pickle.dump({k: v for k, v in bundle.items() if k not in {"raw_candidates_df", "candidates_df"}}, f)

    return bundle


def save_generations_csv(generations: List[List[str]], path: Path) -> None:
    """Save NetworkX topological generations in a long CSV table."""
    rows = []
    for level, nodes in enumerate(generations):
        for node in nodes:
            rows.append({"topological_level": level, "node": node})
    pd.DataFrame(rows).to_csv(path, index=False)


def analyze_all_designs(
    specs: Sequence[DesignSpec],
    force: bool = False,
    group_time: bool = GROUP_TIME_INSTANCES,
) -> Tuple[List[dict], List[Tuple[str, str]]]:
    """Analyze all designs and collect recoverable errors."""
    bundles = []
    errors = []

    for spec in specs:
        try:
            bundle = analyze_design(spec, force=force, group_time=group_time)
            bundles.append(bundle)
        except Exception as exc:
            errors.append((spec.design, str(exc)))

    return bundles, errors


def combined_candidates(bundles: Sequence[dict], group_time: bool = GROUP_TIME_INSTANCES) -> pd.DataFrame:
    """Combine candidate tables from all designs."""
    tables = []

    for bundle in bundles:
        df = bundle["candidates_df"].copy()
        if df.empty:
            continue
        df["design"] = bundle["design"]
        tables.append(df)

    if not tables:
        return pd.DataFrame()

    combined = pd.concat(tables, ignore_index=True)
    combined = combined.sort_values(["trojan_candidate_score", "leakage"], ascending=False).reset_index(drop=True)
    combined.insert(0, "combined_rank", range(1, len(combined) + 1))

    return combined


def save_combined_candidates(df: pd.DataFrame, output_root: Path = OUTPUT_ROOT) -> Path:
    """Save the cross-design candidate ranking."""
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "combined_trojan_output_candidates.csv"
    df.to_csv(path, index=False)
    return path


# -----------------------------
# CLI printing
# -----------------------------

def print_candidate_table(df: pd.DataFrame, top_n: Optional[int] = PRINT_TOP_N) -> None:
    """Print candidate outputs and scores in a compact table."""
    if df.empty:
        print("No candidate outputs found.")
        return

    cols = [
        "combined_rank" if "combined_rank" in df.columns else "rank",
        "design",
        "candidate_output",
        "representative_output",
        "trojan_candidate_score",
        "leakage",
        "best_ref",
        "output_same_level_percentile",
        "output_robust_z",
        "fanin_auc_vs_rest",
        "fanin_median_same_level_percentile",
        "fanin_same_level_top5_frac",
        "reason",
    ]

    cols = [c for c in cols if c in df.columns]
    out = df[cols].copy()

    if top_n is not None:
        out = out.head(int(top_n))

    with pd.option_context("display.max_rows", None, "display.max_colwidth", 120, "display.width", 220):
        print(out.to_string(index=False))


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for candidate mining."""
    p = argparse.ArgumentParser(description="Identify likely Trojan output candidates from FORTIFY leakage artifacts.")
    p.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    p.add_argument("--run-name", type=str, default=RUN_NAME)
    p.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    p.add_argument("--sizes", type=str, default=",".join(map(str, AES_SIZES)))
    p.add_argument("--force", action="store_true", help="recompute even if cached results exist")
    p.add_argument("--no-group-time", action="store_true", help="do not group signal@t instances")
    p.add_argument("--top-n", type=int, default=None, help="print only the top N candidates")
    p.add_argument("--primary-bases", type=str, default=",".join(PRIMARY_OUTPUT_BASES))
    p.add_argument("--output-mode", type=str, default=OUTPUT_NODE_MODE)
    return p.parse_args()


def main() -> None:
    """Run the miner and print candidate outputs."""
    global PROJECT_ROOT, RESULTS_ROOT, OUTPUT_ROOT, RUN_NAME, PRIMARY_OUTPUT_BASES, OUTPUT_NODE_MODE

    args = parse_args()

    PROJECT_ROOT = args.project_root
    RESULTS_ROOT = PROJECT_ROOT / "results"
    OUTPUT_ROOT = args.output_root
    RUN_NAME = args.run_name
    PRIMARY_OUTPUT_BASES = [x.strip() for x in args.primary_bases.split(",") if x.strip()]
    OUTPUT_NODE_MODE = args.output_mode

    sizes = [int(x.strip()) for x in args.sizes.split(",") if x.strip()]
    specs = make_design_specs(sizes=sizes, project_root=PROJECT_ROOT, run_name=RUN_NAME)

    bundles, errors = analyze_all_designs(specs, force=args.force, group_time=not args.no_group_time)
    combined = combined_candidates(bundles, group_time=not args.no_group_time)
    saved_path = save_combined_candidates(combined, OUTPUT_ROOT)

    if errors:
        print("Errors:")
        for design, msg in errors:
            print(f"  {design}: {msg}")

    print(f"\nSaved combined candidates to: {saved_path}\n")
    print_candidate_table(combined, top_n=args.top_n)


if __name__ == "__main__":
    main()
