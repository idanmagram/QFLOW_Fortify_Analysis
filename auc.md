AUC = Area Under the Curve.
**AUC metric** is being used as a **threshold-free distribution comparison** between two groups:

```text
Group A: leakage values of nodes inside the selected fanin cone
Group B: leakage values of nodes outside that fanin cone
```

So when the notebook reports:

```text
AUC P(fanin leakage > rest leakage)
```

it is asking:

```text
If I randomly pick one node from the suspicious fanin cone
and one node from the rest of the graph,
what is the probability that the fanin-cone node has higher leakage?
```

---

## Mathematical meaning

Let:

```text
X = leakage of a randomly chosen node from the fanin cone
Y = leakage of a randomly chosen node from the rest of the graph
```

Then the AUC used here is:

```text
AUC = P(X > Y) + 0.5 P(X = Y)
```

The `0.5 P(X = Y)` term handles ties fairly.

So if you have fanin leakage values:

```text
A = {a1, a2, ..., am}
```

and rest-of-graph leakage values:

```text
B = {b1, b2, ..., bn}
```

then:

```text
AUC = (1 / (m n)) * sum over i,j score(ai, bj)
```

where:

```text
score(ai, bj) = 1     if ai > bj
              = 0.5   if ai = bj
              = 0     if ai < bj
```

So explicitly:

```text
AUC =
1/(m n) * Σ_i Σ_j [ I(ai > bj) + 0.5 I(ai = bj) ]
```

---

## What it signifies

### AUC = 0.5

```text
fanin cone leakage looks similar to the rest of the graph
```

This means a random fanin-cone node is equally likely to have higher or lower leakage than a random non-fanin node.

So the selected path is not distributionally special.

---

### AUC > 0.5

```text
fanin cone leakage tends to be higher than the rest of the graph
```

For example:

```text
AUC = 0.70
```

means:

```text
There is a 70% chance that a randomly selected fanin-cone node
has higher leakage than a randomly selected node outside the cone.
```

That suggests the suspicious output is not isolated. Its upstream path also has elevated leakage.

---

### AUC close to 1.0

```text
fanin cone leakage is almost always higher than rest leakage
```

This would be very suspicious.

For example:

```text
AUC = 0.95
```

means fanin-cone nodes dominate the rest of the graph in leakage.

---

### AUC < 0.5

```text
fanin cone leakage tends to be lower than the rest of the graph
```

This can happen if the selected output is high leakage but its fanin path is mostly normal or low leakage, or if the selected output itself is an outlier but the path is not.

---

## Why this is useful for your Trojan analysis

A fixed threshold like:

```text
Leakage_PBV > 1e-8
```

can be brittle because different designs may have different leakage scales.

For example:

```text
AES100 may naturally have leakage values around 1e-9
AES700 may naturally have leakage values around 1e-6
```

A fixed threshold may over-flag one design and under-flag another.

AUC avoids that by comparing **relative distributions within the same design**.

It asks:

```text
Is the fanin path of this suspicious output unusually leaky compared to the rest of this design?
```

This is much better for cross-design comparison.

---

## Small example

Suppose the selected fanin cone has leakage:

```text
fanin = [0.8, 0.7, 0.6]
```

and the rest of the graph has:

```text
rest = [0.2, 0.3, 0.4]
```

Every fanin value is greater than every rest value.

There are:

```text
3 * 3 = 9
```

comparisons, and fanin wins all 9.

So:

```text
AUC = 9 / 9 = 1.0
```

Very suspicious.

---

Now suppose:

```text
fanin = [0.1, 0.5, 0.9]
rest  = [0.2, 0.6, 0.8]
```

Pairwise comparisons:

```text
0.1 > 0.2? no
0.1 > 0.6? no
0.1 > 0.8? no

0.5 > 0.2? yes
0.5 > 0.6? no
0.5 > 0.8? no

0.9 > 0.2? yes
0.9 > 0.6? yes
0.9 > 0.8? yes
```

Fanin wins 4 out of 9.

```text
AUC = 4 / 9 = 0.444
```

So the fanin distribution is not higher than the rest.

---

## How it is computed in the notebook

The function is probably named:

```python
rank_auc_probability_greater(a, b)
```

Conceptually, it does this:

```python
def auc_probability_greater(a, b):
    wins = 0
    ties = 0
    total = 0

    for x in a:
        for y in b:
            if x > y:
                wins += 1
            elif x == y:
                ties += 1
            total += 1

    return (wins + 0.5 * ties) / total
```

But doing the double loop is expensive for large graphs, so the notebook uses a **rank-based computation**.

The rank-based formula is equivalent to the Mann Whitney U statistic:

```text
AUC = U / (m n)
```

where:

```text
m = number of fanin leakage values
n = number of rest leakage values
```

and:

```text
U = rank_sum_fanin - m(m + 1)/2
```

Here `rank_sum_fanin` is the sum of ranks of fanin values after sorting all values from both groups together.

The implementation is faster because it sorts once instead of comparing every pair.

---

## How to interpret it in your candidate table

For each candidate output, you have something like:

```text
fanin_auc_vs_rest
```

Use it like this:

```text
0.50 to 0.55  -> weak/no evidence that the path is special
0.55 to 0.65  -> mild shift
0.65 to 0.75  -> noticeable path-level leakage shift
0.75+         -> strong evidence that the fanin path is distributionally unusual
```

But do not use AUC alone. Combine it with:

```text
output_same_level_percentile
fanin_median_same_level_percentile
fanin_same_level_top5_frac
near_to_far_log10_lift
fanin_leakage_nodes
```

A good Trojan-output candidate would look like:

```text
high output leakage percentile
high output same-level percentile
high fanin_auc_vs_rest
high fanin same-level percentile
nontrivial fanin cone size
```

In words:

```text
The output is unusual, and the logic feeding it is also unusually leaky compared to the rest of the design.
```

That is much stronger evidence than just saying:

```text
This output has Leakage_PBV > 1e-8.
```
