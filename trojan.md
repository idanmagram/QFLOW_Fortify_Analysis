Below is the theory behind the Trojan-output mining logic in a clean mathematical way. The most important point is this:

**We are not saying “high leakage means Trojan.”**
AES itself naturally leaks because cryptographic computations intentionally depend on key/state bits. The goal is more subtle:

> A Trojan output should not only have high leakage. Its **structural fanin path** should show a leakage distribution that is statistically different from ordinary AES datapath leakage.

So the logic is:

```text
Fortify gives leakage per signal.
Normal AES has leakage, but spread through normal algorithmic datapath structure.
A Trojan output is suspicious if:
    1. the output itself is unusually leaky, and
    2. the signals feeding that output are also statistically unusual
       compared to the rest of the design and compared to signals at similar graph depth.
```

---

# 1. Signal graph model

From `parents.txt`, we build a directed graph:

```text
G = (V, E)
```

where:

```text
V = set of signals/nodes
E = dependency edges
```

If signal `u` is a parent of signal `v`, then:

```text
u -> v
```

means:

```text
u influences v
```

For a candidate output signal `y`, its fanin cone is:

```text
F(y) = { v in V : there exists a path v -> ... -> y }
```

This is the set of all signals that can influence `y`.

The rest of the graph is:

```text
R(y) = V \ F(y)
```

So for every candidate output `y`, we compare:

```text
signals feeding y
```

against:

```text
signals not feeding y
```

This is the core idea.

---

# 2. Fortify leakage metric

For every signal `v` and reference secret bit `h`, Fortify estimates:

```text
P(v = 1 | h = 0)
P(v = 1 | h = 1)
```

Let:

```text
p0(v,h) = P(v = 1 | h = 0)
p1(v,h) = P(v = 1 | h = 1)
```

The code computes Bayesian vulnerability after observing the signal value.

For a binary secret bit with uniform prior:

```text
P(h = 0) = P(h = 1) = 1/2
```

The prior Bayes vulnerability is:

```text
V_prior = max(P(h=0), P(h=1)) = 1/2
```

After observing signal value `v`, the posterior Bayes vulnerability is:

```text
PBV(v,h)
= sum over s in {0,1} max_b P(v=s, h=b)
```

Expanding this:

```text
PBV(v,h)
= max(P(v=0,h=0), P(v=0,h=1))
  + max(P(v=1,h=0), P(v=1,h=1))
```

Using the conditional probabilities:

```text
P(v=0,h=0) = 1/2 * (1 - p0)
P(v=0,h=1) = 1/2 * (1 - p1)

P(v=1,h=0) = 1/2 * p0
P(v=1,h=1) = 1/2 * p1
```

So:

```text
PBV(v,h)
= max(1/2(1-p0), 1/2(1-p1))
  + max(1/2 p0, 1/2 p1)
```

The leakage value used in your code is:

```text
Leakage_PBV(v,h) = PBV(v,h) / V_prior - 1
```

Since `V_prior = 1/2`, this simplifies to:

```text
Leakage_PBV(v,h) = |p1(v,h) - p0(v,h)|
```

So intuitively:

> A signal leaks a reference bit if its probability of being 1 changes depending on whether that reference bit is 0 or 1.

For each signal, the notebook uses the strongest reference bit:

```text
L(v) = max_h Leakage_PBV(v,h)
```

and records:

```text
h*(v) = argmax_h Leakage_PBV(v,h)
```

So every node gets one main leakage score:

```text
L(v)
```

This is the value plotted and analyzed.

---

# 3. Why high leakage alone is not enough

AES is a cryptographic circuit. Normal AES signals depend on key bits and plaintext/state bits. Therefore, many normal AES internal nodes can have nonzero leakage.

So this rule is weak:

```text
if L(v) > threshold:
    suspicious
```

because it will flag normal AES datapath nodes too.

Instead, we ask a stronger structural question:

> Is this output and the path feeding it statistically different from ordinary AES leakage behavior?

That is why the notebook does path-based analysis.

For each candidate output `y`, it checks:

```text
output anomaly:
    Is y unusually leaky as an output?

path anomaly:
    Is F(y), the fanin cone of y, unusually leaky compared to the rest of the graph?

depth-normalized anomaly:
    Are nodes in F(y) unusually leaky compared to other nodes at the same topological level?
```

This is much stronger than just checking absolute leakage.

---

# 4. Candidate output model

For every output-like candidate `y`, the notebook computes a score:

```text
TrojanScore(y)
```

A high score means:

```text
y is a possible Trojan output
```

not a proof.

The score combines output-level evidence and path-level evidence.

The actual scoring formula in the notebook is:

```text
TrojanScore(y) = 100 * C_size(y) *
[
    0.18 * C_output_percentile(y)
  + 0.17 * C_output_same_level(y)
  + 0.15 * C_output_robust_z(y)
  + 0.20 * C_auc(y)
  + 0.15 * C_fanin_same_level(y)
  + 0.10 * C_fanin_top5(y)
  + 0.05 * C_distance(y)
]
```

where `C_size(y)` penalizes tiny fanin cones:

```text
C_size(y) = min(1, number_of_leakage_nodes_in_F(y) / TROJAN_MIN_FANIN_LEAKAGE_NODES)
```

So if a candidate output has almost no fanin cone, it is not trusted much because there is not enough path evidence.

---

# 5. Output leakage percentile

For candidate output `y`, the notebook computes:

```text
output_leakage_percentile(y)
```

Mathematically, among all output-like candidates `O`:

```text
output_leakage_percentile(y)
= rank of L(y) among {L(o) : o in O} / |O|
```

If this is `0.99`, then `y` is higher leakage than about 99% of output-like signals.

### Why this matters

This checks:

> Is this output unusually leaky compared to other outputs?

This is important because different designs may have different absolute leakage scales. A percentile is relative to the design.

### Limitation

This alone is not enough. AES output/state bits can naturally be high leakage. So we combine it with path statistics.

---

# 6. Same-level output percentile

Each node has a topological level:

```text
level(v)
```

computed from the graph. Nodes at similar levels are roughly at similar structural depth.

For a node `v`, the same-level percentile is:

```text
P_level(v)
= rank of L(v) among {L(u) : level(u) = level(v)} / |{u : level(u)=level(v)}|
```

For the output candidate `y`:

```text
output_same_level_percentile(y) = P_level(y)
```

### Why this matters

Raw leakage can naturally vary by depth. Deep combinational logic or output-adjacent logic may have different leakage behavior from input-adjacent nodes.

Same-level percentile asks the fairer question:

> Among nodes at the same structural depth, is this output unusually leaky?

If yes, that is stronger evidence.

A suspicious output should not only be high globally. It should also stand out among nodes at comparable graph position.

---

# 7. Robust z-score for outputs

For output-like nodes, the notebook computes a robust z-score.

Let:

```text
O = set of output-like candidate nodes
```

Let:

```text
median_O = median { L(o) : o in O }
MAD_O = median { |L(o) - median_O| : o in O }
```

The robust z-score is:

```text
Z_robust(y) = (L(y) - median_O) / (1.4826 * MAD_O)
```

The constant `1.4826` makes MAD comparable to standard deviation under a normal distribution.

### Why robust z-score?

Normal mean/std z-scores can be distorted by a few extreme leakage values. But Trojan candidates are exactly the kind of extreme values we are looking for. So median/MAD is more stable.

### Interpretation

```text
Z_robust(y) ≈ 0
```

means typical output leakage.

```text
Z_robust(y) >= 3
```

means highly unusual output leakage.

The notebook turns this into a bounded score component:

```text
C_output_robust_z(y) = min(1, max(0, Z_robust(y) / 6))
```

So very large z-scores saturate.

---

# 8. Fanin-cone AUC versus rest of graph

This is one of the most important statistics.

For a candidate output `y`, define:

```text
F(y) = fanin cone of y
R(y) = rest of graph
```

Let:

```text
X = L(v), where v is randomly sampled from F(y)
Y = L(u), where u is randomly sampled from R(y)
```

The AUC statistic is:

```text
AUC(y) = P(X > Y) + 0.5 P(X = Y)
```

Equivalently, if:

```text
F_L = {L(v) : v in F(y)}
R_L = {L(u) : u in R(y)}
```

then:

```text
AUC(y)
= 1 / (|F_L| |R_L|)
  * sum_{x in F_L} sum_{r in R_L}
      [ I(x > r) + 0.5 I(x = r) ]
```

### Interpretation

```text
AUC = 0.5
```

The fanin cone leakage distribution looks like the rest of the graph.

```text
AUC > 0.5
```

A random fanin-cone node tends to have higher leakage than a random rest-of-graph node.

```text
AUC = 0.8
```

There is an 80% chance that a randomly chosen fanin node has higher leakage than a randomly chosen non-fanin node.

### Why this matters for Trojan detection

This is threshold-free.

It does not ask:

```text
Is leakage above 1e-8?
```

It asks:

```text
Is the whole path feeding this output shifted toward higher leakage compared to the rest of this same design?
```

That is exactly the kind of logic you want.

Normal AES can leak, but if a particular output’s entire fanin path has a leakage distribution shifted above the rest, that path is structurally unusual.

The notebook maps AUC to a score component:

```text
C_auc(y) = min(1, max(0, (AUC(y) - 0.5) / 0.5))
```

So:

```text
AUC = 0.5 -> C_auc = 0
AUC = 0.75 -> C_auc = 0.5
AUC = 1.0 -> C_auc = 1
```

---

# 9. Fanin median same-level percentile

For every node `v` in the fanin cone, we already have:

```text
P_level(v)
```

the leakage percentile of `v` among nodes at the same topological level.

Then for output `y`:

```text
fanin_median_same_level_percentile(y)
= median { P_level(v) : v in F(y) }
```

### Why this is important

This answers:

> Is the path feeding the output consistently unusual at its respective levels?

This is stronger than AUC in one way: it controls for structural depth.

A Trojan path may not dominate the whole graph globally, but it may repeatedly stand out compared to same-level alternatives.

### Interpretation

```text
0.50
```

The typical fanin node is average for its level.

```text
0.75
```

The typical fanin node is higher leakage than 75% of same-level nodes.

```text
0.90
```

The path is very unusual across levels.

This is one of the best features for distinguishing:

```text
normal AES leakage
```

from:

```text
structurally abnormal leakage path
```

---

# 10. Fanin same-level top-5 fraction

The notebook also computes:

```text
fanin_same_level_top5_frac(y)
```

This is:

```text
fanin_same_level_top5_frac(y)
= |{ v in F(y) : P_level(v) >= 0.95 }| / |F(y)|
```

So it measures the fraction of fanin-cone nodes that are in the top 5% of leakage among their own level.

### Why this matters

Median can hide a small number of very anomalous nodes. This statistic asks:

> Does this path contain many level-wise outliers?

A Trojan path might have a small trigger or payload region. Not every fanin node needs to be abnormal, but if many nodes on the path are top-level outliers, the path is suspicious.

The notebook maps it as:

```text
C_fanin_top5(y) = min(1, fanin_same_level_top5_frac(y) / 0.25)
```

So if 25% or more of the path is top-5% at its level, this component saturates.

---

# 11. Level lift: cone versus rest at each level

For each topological level `k`, define:

```text
F_k(y) = { v in F(y) : level(v) = k }
R_k(y) = { u in R(y) : level(u) = k }
```

Then compare median leakage:

```text
median_F(k) = median { L(v) : v in F_k(y) }
median_R(k) = median { L(u) : u in R_k(y) }
```

The notebook computes a log lift:

```text
lift_k(y) = log10( (median_F(k) + eps) / (median_R(k) + eps) )
```

Then summarizes across levels:

```text
median_level_log10_lift_cone_over_rest(y)
= median_k lift_k(y)
```

### Why this matters

This checks whether the fanin cone is elevated level by level.

This avoids a misleading case where the fanin cone looks high only because it contains many late-level nodes.

### Interpretation

```text
lift_k = 0
```

Fanin and rest have similar median leakage at level `k`.

```text
lift_k = 1
```

Fanin median leakage is 10x the rest median at that level.

```text
lift_k = -1
```

Fanin median leakage is 10x lower than the rest.

For Trojan analysis, a useful signal is:

```text
several consecutive levels where fanin cone has positive lift
```

That means the suspicious path is not just an isolated output anomaly.

---

# 12. Distance-to-target leakage shape

For a candidate output `y`, define reverse distance:

```text
d_y(v) = shortest path distance from v to y
```

where:

```text
d_y(y) = 0
```

direct parents have distance 1, grandparents distance 2, and so on.

For every distance `d`, define:

```text
M_d(y) = median { L(v) : v in F(y), d_y(v) = d }
```

The notebook computes Spearman correlation between:

```text
distance d
```

and:

```text
median leakage M_d
```

So:

```text
rho_y = SpearmanCorr(d, M_d)
```

Because distance 0 is the output:

```text
rho_y < 0
```

means leakage tends to be higher near the output and lower farther away.

The notebook uses:

```text
distance_rise_toward_target(y) = max(0, -rho_y)
```

### Why this matters

A Trojan payload output may show leakage concentrated near the output side of its path.

For example:

```text
far upstream: normal AES-like leakage
near output: abnormal leakage rise
target output: very high leakage
```

That gives:

```text
negative rho
```

The notebook also computes near-to-far lift:

```text
near_to_far_log10_lift(y)
= log10(
    (median leakage for d <= D_near + eps)
    /
    (median leakage for d > D_near + eps)
  )
```

where `D_near = TROJAN_NEAR_TARGET_DISTANCE`.

### Interpretation

```text
near_to_far_log10_lift = 1
```

means near-output fanin nodes have 10x median leakage compared to farther fanin nodes.

This is useful when the Trojan effect is localized near the payload output.

---

# 13. Full scoring equation

The actual notebook scoring equation is:

```text
TrojanScore(y) = 100 * C_size(y) * E(y)
```

where:

```text
E(y)
= 0.18 C_output_percentile(y)
+ 0.17 C_output_same_level(y)
+ 0.15 C_output_robust_z(y)
+ 0.20 C_auc(y)
+ 0.15 C_fanin_same_level(y)
+ 0.10 C_fanin_top5(y)
+ 0.05 C_distance(y)
```

The components are:

```text
C_output_percentile(y)
= output_leakage_percentile(y)
```

```text
C_output_same_level(y)
= output_same_level_percentile(y)
```

```text
C_output_robust_z(y)
= min(1, max(0, Z_robust(y) / 6))
```

```text
C_auc(y)
= min(1, max(0, (AUC(y) - 0.5) / 0.5))
```

```text
C_fanin_same_level(y)
= fanin_median_same_level_percentile(y)
```

```text
C_fanin_top5(y)
= min(1, fanin_same_level_top5_frac(y) / 0.25)
```

```text
C_distance(y)
= max(0, -SpearmanCorr(distance_to_target, median_leakage_at_distance))
```

and:

```text
C_size(y)
= min(1, fanin_leakage_nodes(y) / TROJAN_MIN_FANIN_LEAKAGE_NODES)
```

So the score rewards outputs that satisfy both:

```text
output is unusual
```

and:

```text
fanin path is unusual
```

---

# 14. Logical interpretation

The full logical argument is:

## Step 1: Leakage dependency exists

Fortify estimates whether a signal statistically depends on a secret/reference bit.

```text
L(v) = max_h |P(v=1|h=1) - P(v=1|h=0)|
```

This tells us how strongly the signal reveals a secret/reference bit.

## Step 2: But AES normally leaks

AES computation naturally depends on the key. Therefore:

```text
L(v) > 0
```

does not imply Trojan.

Normal AES leakage should be associated with normal AES datapath structure.

## Step 3: Trojan output should be structurally abnormal

A Trojan output, especially one designed to leak key bits through an added output or payload signal, should create an output whose fanin path is not statistically typical of normal AES logic.

So we inspect each candidate output `y` and ask:

```text
Is y unusual among outputs?
Is y unusual among nodes at its level?
Is F(y) unusual compared to R(y)?
Is F(y) unusual level-by-level?
Does leakage concentrate near y?
```

## Step 4: Distributional comparison avoids fixed thresholds

Instead of saying:

```text
leakage > fixed threshold
```

we compare distributions:

```text
F(y) leakage distribution versus R(y) leakage distribution
```

using AUC, percentiles, robust z-score, and level-normalized ranks.

This is better because leakage scale can differ between AES variants.

## Step 5: Candidate ranking

A node is a stronger Trojan-output candidate if:

```text
high output percentile
+ high same-level percentile
+ high robust z
+ high fanin-vs-rest AUC
+ high fanin same-level percentile
+ many fanin nodes are same-level outliers
+ leakage rises toward the output
```

The output is not declared a Trojan by one statistic. It is ranked high when multiple independent statistical views agree.

---

# 15. What distinguishes normal AES leakage from Trojan-like leakage

A normal AES path may have:

```text
some high leakage nodes
```

but those nodes may be spread across normal S-box, XOR, key schedule, and round logic.

A Trojan output path is suspicious if it shows:

```text
localized or path-concentrated leakage anomaly
```

That means:

```text
the output is high
the fanin cone is shifted high
many fanin nodes are top outliers at their own levels
the path differs from same-depth AES nodes
the leakage shape changes as we approach the output
```

In plain words:

> Normal AES leakage is expected, but it should look like the rest of the AES datapath. A Trojan output is suspicious when its leakage is not only high, but its entire dependency path behaves differently from the rest of the design.

---

# 16. What each statistic contributes

| Statistic                                | Question it answers                                            | Why useful                                |
| ---------------------------------------- | -------------------------------------------------------------- | ----------------------------------------- |
| `output_leakage_percentile`              | Is the output high leakage among outputs?                      | Finds direct suspicious outputs           |
| `output_same_level_percentile`           | Is the output high compared to same-depth nodes?               | Controls for graph depth                  |
| `output_robust_z`                        | Is the output an outlier among outputs?                        | Robust to extreme values                  |
| `fanin_auc_vs_rest`                      | Is the fanin cone leakage distribution shifted above the rest? | Threshold-free path anomaly               |
| `fanin_median_same_level_percentile`     | Are fanin nodes typically high for their levels?               | Strong structural anomaly indicator       |
| `fanin_same_level_top5_frac`             | Does the path contain many level-wise outliers?                | Detects concentrated suspicious regions   |
| `median_level_log10_lift_cone_over_rest` | Is fanin higher than rest level-by-level?                      | Avoids depth bias                         |
| `distance_rise_toward_target`            | Does leakage increase near output?                             | Finds payload-side concentration          |
| `near_to_far_log10_lift`                 | Is near-output leakage higher than far-upstream leakage?       | Localizes suspicious output-side behavior |

---

# 17. How you should use this practically

For each design, inspect:

```text
combined_trojan_output_candidates.csv
```

Sort by:

```text
trojan_candidate_score
```

Then do not blindly trust the top score. Look at the evidence columns.

A strong candidate should have something like:

```text
output_same_level_percentile >= 0.95
output_robust_z >= 3
fanin_auc_vs_rest >= 0.70
fanin_median_same_level_percentile >= 0.75
fanin_same_level_top5_frac noticeably high
fanin_leakage_nodes nontrivial
```

A weaker candidate may have high output leakage but:

```text
fanin_auc_vs_rest ≈ 0.5
fanin_median_same_level_percentile ≈ 0.5
```

That means:

```text
the output leaks, but its path does not look unusual
```

which may be normal AES behavior.

The most interesting candidates are the ones where:

```text
output is anomalous
AND
fanin cone is anomalous
AND
same-level comparison says the path is unusual
```

That is the central logic of the approach.
