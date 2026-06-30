Here is what the added statistics are telling you and how to use them.

## 1. Basic graph statistics

These are printed in `analyze_design(...)`:

```python
print(f"nodes={G.number_of_nodes()}, edges={G.number_of_edges()}, DAG={nx.is_directed_acyclic_graph(G)}")
print(f"topological levels={node_df['topological_level'].max() + 1 if len(node_df) else 0}")
print(f"leakage nodes={int(node_df['has_leakage'].sum())}")
print(f"selected targets={len(targets)}, cone_nodes={len(cone_nodes)}, cone_edges={len(cone_edges)}")
```

### What they mean

`nodes` is the number of signals in the graph.

`edges` is the number of dependency edges. In your graph, an edge means:

```text
parent signal -> child signal
```

So if `A` influences `B`, there is an edge `A -> B`.

`DAG` tells whether the raw graph has cycles. If it is `False`, the notebook uses strongly connected components and a condensed DAG to assign topological levels.

`topological levels` tells how many logic-depth-like stages NetworkX found.

`leakage nodes` is the number of nodes that have a leakage value from `all_signal_leakage.csv`.

`selected targets` is the number of high-leakage output-like nodes selected for fanin-cone analysis.

`cone_nodes` is the number of nodes in the transitive fanin of those selected targets.

`cone_edges` is the number of edges inside that fanin cone.

### How to use them

Start here to check whether the analysis is meaningful. For example:

```text
selected targets = 0
```

means your threshold or output prefix is too restrictive.

```text
cone_nodes = 1
cone_edges = 0
```

means the selected output node has no parents in the parsed graph, or its name does not match the graph naming.

```text
DAG = False
```

is not necessarily bad. It just means the circuit has cycles or time-unrolled dependencies. The notebook handles this by using `nx.condensation(G)` before computing topological generations.

---

## 2. Topological level

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
topological_level
```

### What it means

This is the level of each signal in the graph, computed using:

```python
nx.topological_generations(...)
```

If the raw graph is cyclic, the notebook first computes:

```python
nx.condensation(G)
```

and then assigns each original node the level of its strongly connected component.

### Why it is useful

A Trojan path may not only have high leakage at the final output. It may show a shape across levels, for example:

```text
low leakage early -> sudden rise near trigger/payload logic -> high output leakage
```

or:

```text
consistently high same-level percentile across many levels
```

So topological level lets you compare signals at similar structural depth, instead of comparing an input-like signal with a deep internal signal.

---

## 3. `in_selected_fanin_cone`

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
in_selected_fanin_cone
```

### What it means

This is `True` if the node lies in the fanin cone of the selected high-leakage output targets.

The fanin cone is computed in:

```python
compute_fanin_artifacts(G, targets)
```

Then attached to the node table in:

```python
annotate_fanin_on_node_df(...)
```

### Why it is useful

This splits the graph into two groups:

```text
Group 1: nodes on suspicious/high-leakage output paths
Group 2: all other leakage-valued nodes
```

Then the notebook compares leakage distributions between those groups.

### How to use it

Open:

```text
<design>_nodes_levels_leakage_fanin.csv
```

and filter:

```text
in_selected_fanin_cone == True
```

This gives the actual internal signals that feed the suspicious output path.

---

## 4. `is_selected_target`

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
is_selected_target
```

### What it means

This marks the final high-leakage output-like signals that were selected as fanin targets.

Selection happens in:

```python
select_high_leakage_targets(...)
```

Controlled by:

```python
TARGET_SELECTION_MODE
LEAKAGE_THRESHOLD
TARGET_OUTPUT_PREFIXES
MAX_TARGETS_PER_DESIGN
```

### Why it is useful

This separates:

```text
final suspicious outputs
```

from:

```text
internal nodes in their fanin cone
```

The output target itself may be obvious, but the important question is whether the path feeding it has a distinct leakage pattern.

---

## 5. `fanin_min_distance_to_target`

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
fanin_min_distance_to_target
```

### What it means

This is the shortest reverse distance from a node to any selected high-leakage target.

```text
0 = selected target itself
1 = direct parent of selected target
2 = parent of parent
...
```

### Why it is useful

This tells you how leakage changes as you move backward from the suspicious output.

For example, if you see:

```text
distance 0: high leakage
distance 1: high leakage
distance 2: high leakage
distance 3: normal leakage
```

then the suspicious behavior may start around distance 1 or 2 from the output.

### Where it is summarized

The distance profile is saved as:

```text
<design>_distance_profile_inside_fanin.csv
```

and plotted as:

```text
<design>_distance_profile_inside_fanin.png
```

Computed in:

```python
compute_distance_profile(node_df)
```

### How to use it

Look for whether leakage is concentrated only at the output or whether it persists upstream.

A Trojan-like pattern may show a localized high-leakage region close to the payload output. A normal design path may show leakage more gradually or inconsistently.

---

## 6. `fanin_targets_reached`

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
fanin_targets_reached
```

### What it means

This tells how many selected targets a node can reach.

For example:

```text
fanin_targets_reached = 1
```

means the node feeds one selected high-leakage output.

```text
fanin_targets_reached = 8
```

means the node feeds eight selected high-leakage outputs.

### Why it is useful

If one internal node feeds many high-leakage outputs, it may be a shared suspicious source.

For your log, many bits of `top.Capacitance` have very similar leakage. If their fanin cones share a common internal source, `fanin_targets_reached` helps identify that shared point.

### How to use it

Sort nodes by:

```text
fanin_targets_reached descending
```

and then by:

```text
leakage descending
```

Nodes high in both are worth inspecting.

---

## 7. `leakage_percentile_global`

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
leakage_percentile_global
```

### What it means

This is the percentile rank of the node leakage compared to all leakage-valued nodes in the design.

```text
0.99 = this node is higher than about 99% of leakage-valued nodes
0.50 = near median
0.10 = low leakage compared to most nodes
```

### Why it is useful

It is threshold-free.

Instead of saying:

```text
leakage > 1e-8
```

you can say:

```text
this node is in the top 1% of leakage nodes
```

That is often more robust across designs because absolute leakage values may vary between AES100, AES200, AES700, etc.

### How to use it

Look at whether selected fanin-cone nodes have systematically high percentiles.

If many fanin nodes have:

```text
leakage_percentile_global > 0.95
```

then the path is globally unusual.

---

## 8. `leakage_percentile_same_level`

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
leakage_percentile_same_level
```

### What it means

This compares a node only against other nodes at the same topological level.

So if a node has:

```text
topological_level = 45
leakage_percentile_same_level = 0.98
```

it means:

```text
among level-45 nodes, this node is higher leakage than about 98% of them
```

### Why it is important

This is probably one of the most useful statistics for Trojan analysis.

Global leakage can be misleading because later logic levels may naturally have different leakage distributions than early levels. Same-level percentile asks a fairer question:

```text
Is this node unusually leaky compared to structurally similar nodes?
```

### How to use it

If a high-leakage path has many nodes with high same-level percentile, that suggests the path is not just deep or output-adjacent. It is unusual relative to neighboring logic at similar depth.

A useful suspicious pattern is:

```text
selected fanin nodes have high same-level percentile across several levels
```

rather than only the final output being high.

---

## 9. `output_like_robust_z`

Stored in:

```text
<design>_nodes_levels_leakage_fanin.csv
```

Column:

```text
output_like_robust_z
```

### What it means

This is a robust z-score for output-like signals. It compares output-like signal leakage using median and median absolute deviation.

Roughly:

```text
0 = typical output-like leakage
3 = much higher than typical
5+ = very unusual
```

### Why it is useful

It helps select suspicious outputs without choosing a fixed absolute threshold.

Instead of:

```python
Leakage_PBV > 1e-8
```

you can look for:

```python
output_like_robust_z > 3
```

This is more adaptive per design.

### How to use it

Sort output-like signals by:

```text
output_like_robust_z descending
```

If the Trojan payload output bits are truly unusual, they should stand out here.

---

## 10. `fanin median leakage` and `rest median leakage`

Printed by:

```python
print_design_statistics(stats)
```

Saved in:

```text
<design>_distribution_stats.csv
```

Columns:

```text
fanin_median
rest_median
```

### What they mean

`fanin_median` is the median leakage among leakage-valued nodes inside the selected fanin cones.

`rest_median` is the median leakage among leakage-valued nodes outside those fanin cones.

### Why it is useful

It answers:

```text
Is the suspicious path generally more leaky than the rest of the design?
```

### How to use it

If:

```text
fanin_median >> rest_median
```

then the whole path distribution is shifted upward.

If:

```text
fanin_median ≈ rest_median
```

then only a few nodes may be high, or the final output is high but the path is not broadly unusual.

---

## 11. `fanin p90 leakage` and `rest p90 leakage`

Printed by:

```python
print_design_statistics(stats)
```

Saved in:

```text
<design>_distribution_stats.csv
```

Columns:

```text
fanin_p90
rest_p90
```

### What they mean

The 90th percentile leakage inside the selected fanin cone and outside it.

### Why it is useful

Median can hide rare high-leakage nodes. P90 checks the upper tail.

A Trojan path may not make every node leaky, but it may create a heavier high-leakage tail.

### How to use it

If:

```text
fanin_median ≈ rest_median
```

but:

```text
fanin_p90 >> rest_p90
```

then only the upper tail of the suspicious path is abnormal.

That can still be meaningful.

---

## 12. `AUC P(fanin leakage > rest leakage)`

Printed as:

```text
AUC P(fanin leakage > rest leakage)
```

Saved as:

```text
auc_fanin_leakage_gt_rest
```

Computed in:

```python
rank_auc_probability_greater(cone["leakage"], rest["leakage"])
```

### What it means

This is a threshold-free effect size.

It estimates:

```text
Probability that a random fanin-cone node has higher leakage
than a random non-fanin node.
```

Interpretation:

```text
0.50  -> fanin and rest are similar
0.60  -> fanin tends to be somewhat higher
0.70+ -> fanin is clearly shifted higher
<0.50 -> fanin tends to be lower
```

### Why it is useful

This is very useful for your goal because you said you do not want to rely only on a precomputed threshold.

AUC compares distributions directly, not absolute leakage values.

### How to use it

For each design, check:

```text
auc_fanin_leakage_gt_rest
```

If Trojan designs consistently show higher AUC than clean designs, this becomes a better detection feature than a fixed leakage threshold.

Example interpretation:

```text
AES100: AUC = 0.52 -> suspicious path not very different
AES700: AUC = 0.81 -> suspicious path leakage distribution strongly shifted upward
```

---

## 13. `median_global_percentile_fanin`

Saved in:

```text
<design>_distribution_stats.csv
```

Column:

```text
median_global_percentile_fanin
```

### What it means

Among fanin-cone nodes, take their global leakage percentiles and compute the median.

### Why it is useful

It answers:

```text
Are fanin-cone nodes usually high-ranking globally?
```

### How to use it

If this is around:

```text
0.50
```

then fanin nodes are typical.

If it is around:

```text
0.80 or 0.90
```

then many fanin nodes are globally high leakage.

---

## 14. `median_same_level_percentile_fanin`

Printed as:

```text
median same-level percentile of fanin nodes
```

Saved as:

```text
median_same_level_percentile_fanin
```

### What it means

This is the median same-level percentile among fanin-cone nodes.

### Why it is especially useful

This tells whether the suspicious fanin path is unusual **after controlling for topological depth**.

This is better than just raw leakage because it asks:

```text
At each level, are the nodes on this path unusually leaky compared to other nodes at that same level?
```

### How to use it

This is a strong candidate feature for threshold-free Trojan identification.

If:

```text
median_same_level_percentile_fanin > 0.75
```

that means a typical node on the selected fanin path is higher than 75% of same-level nodes.

That is much more meaningful than saying only the output crossed `1e-8`.

---

## 15. `median_same_level_percentile_targets`

Printed as:

```text
median same-level percentile of targets
```

Saved as:

```text
median_same_level_percentile_targets
```

### What it means

This is same-level percentile, but only for selected high-leakage target outputs.

### Why it is useful

It tells whether the final output bits are unusual compared to other nodes at the same topological level.

### How to use it

If selected targets have high raw leakage but low same-level percentile, that means other nodes at that level are also similarly leaky.

If selected targets have high same-level percentile, then they are structurally unusual.

---

## 16. Level profile: cone vs rest

Saved as:

```text
<design>_level_profile_cone_vs_rest.csv
```

Plotted as:

```text
<design>_level_profile_cone_vs_rest.png
```

Computed in:

```python
compute_level_profile(node_df)
```

Important columns include:

```text
topological_level
mean_fanin_cone
mean_rest
median_fanin_cone
median_rest
max_fanin_cone
max_rest
mean_lift_cone_over_rest
median_lift_cone_over_rest
max_lift_cone_over_rest
```

### What it means

This compares fanin-cone nodes to the rest of the graph at each topological level.

For example:

```text
median_lift_cone_over_rest = 10
```

means:

```text
At that level, median fanin-cone leakage is 10x median rest leakage.
```

### Why it is useful

This reveals where the suspicious path becomes different.

You may see:

```text
levels 0-20: fanin similar to rest
levels 21-28: fanin much higher than rest
levels 29+: target/output region high
```

That gives you a localization clue.

### How to use it

Open the CSV and look for levels where:

```text
median_lift_cone_over_rest
```

or:

```text
mean_lift_cone_over_rest
```

becomes large.

Those levels may correspond to trigger/payload logic.

---

## 17. Distance profile inside fanin

Saved as:

```text
<design>_distance_profile_inside_fanin.csv
```

Plotted as:

```text
<design>_distance_profile_inside_fanin.png
```

Computed in:

```python
compute_distance_profile(node_df)
```

Columns:

```text
distance_to_selected_target
count
mean
median
max
```

### What it means

This groups fanin-cone nodes by distance from the selected target.

```text
distance 0 = selected target
distance 1 = immediate parents
distance 2 = grandparents
...
```

### Why it is useful

It shows how leakage changes as you move upstream from the suspicious output.

### How to use it

Look for shape:

```text
high only at distance 0
```

means the output itself is high, but the path may not be generally suspicious.

```text
high from distance 0 to 5
```

means leakage is concentrated near the output logic.

```text
periodic spikes at certain distances
```

may indicate repeated structure or replicated logic.

---

## 18. ECDF plot: cone vs rest

Saved as:

```text
<design>_ecdf_cone_vs_rest.png
```

Created by:

```python
plot_leakage_ecdf(bundle)
```

### What it means

The ECDF shows the full leakage distribution of:

```text
fanin-cone nodes
```

versus:

```text
rest of graph
```

### Why it is useful

This is more informative than a single threshold.

If the fanin curve is shifted to the right, then the suspicious path generally has higher leakage.

### How to use it

If the fanin ECDF curve is clearly to the right of the rest ECDF curve, then the selected path has a higher leakage distribution.

If the curves overlap, then the selected path is not distributionally different.

---

# Where these statistics are in the code

## Target selection

Look in the cell titled:

```python
# Fanin target selection and fanin utilities
```

The target selection happens here:

```python
def select_high_leakage_targets(...):
```

This decides which high-leakage output signals get fanin cones.

Controlled by config:

```python
TARGET_SELECTION_MODE
LEAKAGE_THRESHOLD
TARGET_OUTPUT_PREFIXES
MAX_TARGETS_PER_DESIGN
```

---

## Fanin computation

Same cell:

```python
def compute_fanin_artifacts(G, targets):
```

This computes:

```python
cone_nodes
cone_edges
min_distance_to_target
targets_reached
```

Then this function adds those values to the node dataframe:

```python
def annotate_fanin_on_node_df(...):
```

---

## Percentiles and node-level annotations

Look in the graph-building cell:

```python
def add_leakage_percentiles(df):
```

This adds:

```text
leakage_percentile_global
leakage_percentile_same_level
```

Also check:

```python
def build_node_dataframe(G, leakage_by_node, design):
```

This creates the main node table.

---

## Distribution statistics

Look in the cell titled:

```python
# Distribution statistics and profile utilities
```

The main functions are:

```python
def summarize_values(series, prefix):
def rank_auc_probability_greater(a, b):
def compute_level_profile(node_df):
def compute_distance_profile(node_df):
def compute_design_statistics(bundle):
def print_design_statistics(stats):
```

Most of the statistics you asked about are computed in:

```python
compute_design_statistics(bundle)
```

This is the best place to add new statistics.

---

## Saving CSVs

Look in:

```python
def analyze_design(spec, design_index=0):
```

Specifically this block:

```python
if SAVE_CSVS:
    node_df.to_csv(...)
    target_df.to_csv(...)
    pd.DataFrame({"node": sorted(cone_nodes)}).to_csv(...)
    fanin_edges_df.to_csv(...)
    level_profile.to_csv(...)
    distance_profile.to_csv(...)
    pd.DataFrame([stats]).to_csv(...)
```

If you add a new statistic to the `stats` dictionary, it automatically gets saved into:

```text
<design>_distribution_stats.csv
```

because of:

```python
pd.DataFrame([stats]).to_csv(out_dir / f"{design}_distribution_stats.csv", index=False)
```

---

# How to add a new statistic

The easiest way is to edit:

```python
def compute_design_statistics(bundle):
```

Inside it, you already have:

```python
df = bundle["node_df"]
leak_df = df[df["has_leakage"] == True].copy()
cone = leak_df[leak_df["in_selected_fanin_cone"] == True]
rest = leak_df[leak_df["in_selected_fanin_cone"] == False]
targets = leak_df[leak_df["is_selected_target"] == True]
```

Add new stats after that.

For example, to add the fraction of fanin nodes that are in the top 5% globally:

```python
stats["fanin_frac_global_top5_percent"] = float(
    (cone["leakage_percentile_global"] >= 0.95).mean()
) if len(cone) else math.nan
```

To compare against the rest:

```python
stats["rest_frac_global_top5_percent"] = float(
    (rest["leakage_percentile_global"] >= 0.95).mean()
) if len(rest) else math.nan
```

To add same-level top 5% fraction:

```python
stats["fanin_frac_same_level_top5_percent"] = float(
    (cone["leakage_percentile_same_level"] >= 0.95).mean()
) if len(cone) else math.nan

stats["rest_frac_same_level_top5_percent"] = float(
    (rest["leakage_percentile_same_level"] >= 0.95).mean()
) if len(rest) else math.nan
```

These are useful because they are threshold-free.

---

# Useful new statistics I would add next

## A. Path concentration score

Question answered:

```text
Is high leakage concentrated in the selected fanin cone?
```

Add inside `compute_design_statistics`:

```python
top5 = leak_df["leakage_percentile_global"] >= 0.95

stats["top5_nodes_total"] = int(top5.sum())
stats["top5_nodes_in_fanin"] = int((top5 & leak_df["in_selected_fanin_cone"]).sum())
stats["top5_fanin_capture_rate"] = (
    stats["top5_nodes_in_fanin"] / stats["top5_nodes_total"]
    if stats["top5_nodes_total"] else math.nan
)
```

Interpretation:

```text
High value = many globally high-leakage nodes lie on selected fanin paths.
```

---

## B. Same-level anomaly fraction

Question answered:

```text
Is the suspicious path unusual relative to same-level alternatives?
```

Add:

```python
stats["fanin_same_level_top1_frac"] = float(
    (cone["leakage_percentile_same_level"] >= 0.99).mean()
) if len(cone) else math.nan

stats["fanin_same_level_top5_frac"] = float(
    (cone["leakage_percentile_same_level"] >= 0.95).mean()
) if len(cone) else math.nan
```

Interpretation:

```text
High value = the path repeatedly contains nodes that are unusually leaky for their topological level.
```

---

## C. Leakage rise toward target

Question answered:

```text
Does leakage increase as we move toward the selected output?
```

Add after `distance_profile = compute_distance_profile(df)` is created, or inside `compute_design_statistics` after:

```python
level_profile = compute_level_profile(df)
```

Use:

```python
distance_profile = compute_distance_profile(df)

if not distance_profile.empty and len(distance_profile) >= 2:
    tmp = distance_profile.sort_values("distance_to_selected_target")
    x = tmp["distance_to_selected_target"]
    y = tmp["median"]

    if y.notna().sum() >= 2:
        stats["distance_median_leakage_corr"] = float(x.corr(y, method="spearman"))
    else:
        stats["distance_median_leakage_corr"] = math.nan
else:
    stats["distance_median_leakage_corr"] = math.nan
```

Interpretation:

Because distance 0 is the output, a **negative** correlation means leakage tends to be higher near the target and lower farther away.

That could be a useful Trojan-path signature.

---

## D. Level-lift area

Question answered:

```text
Across levels, how much higher is the fanin path than the rest?
```

After `level_profile` is computed:

```python
if not level_profile.empty and "median_lift_cone_over_rest" in level_profile.columns:
    lift = level_profile["median_lift_cone_over_rest"].replace([math.inf, -math.inf], math.nan).dropna()
    stats["median_lift_area_log10"] = float(
        lift.clip(lower=LEAKAGE_EPS).map(math.log10).sum()
    ) if len(lift) else math.nan
else:
    stats["median_lift_area_log10"] = math.nan
```

Interpretation:

Large positive value means the fanin cone is repeatedly higher than the rest across many levels, not just at one level.

---

# Practical analysis workflow

For each design, I would inspect these in this order:

1. Check `selected_targets`, `cone_nodes`, `cone_edges`.
   Make sure the fanin cone is real and nontrivial.

2. Check `median_same_level_percentile_targets`.
   This tells whether the suspicious outputs are unusual at their structural level.

3. Check `median_same_level_percentile_fanin`.
   This tells whether the whole path is unusual, not just the output.

4. Check `auc_fanin_leakage_gt_rest`.
   This tells whether the fanin distribution is shifted higher than the rest without using a threshold.

5. Look at `<design>_level_profile_cone_vs_rest.png`.
   This tells where in the circuit the suspicious path diverges.

6. Look at `<design>_distance_profile_inside_fanin.png`.
   This tells whether leakage grows near the output.

7. Use `fanin_targets_reached` to find common upstream sources feeding many suspicious outputs.

For Trojan identification without fixed thresholds, the best features are likely:

```text
auc_fanin_leakage_gt_rest
median_same_level_percentile_fanin
top5_fanin_capture_rate
fanin_same_level_top5_frac
distance_median_leakage_corr
median_lift_area_log10
```

These compare distributions and structural position instead of depending only on:

```text
Leakage_PBV > fixed threshold
```
