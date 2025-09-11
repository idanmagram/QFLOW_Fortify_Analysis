# sig_prob_recon_fast.py
# High-performance, reconvergence-aware signal probability engine
# - Fixes self-conditioning identity: P(ref=1|ref=0)=0, P(ref=1|ref=1)=1
# - Corrects cutset mixing under clamps: uses P(Z | clamps) with normalization
# - Fast graph ops via bitset ancestors + integer clamp contexts
# - Computes only relevant conditionals (refs in a signal's cone-of-influence)
# - NEW: Unconditional evaluation is now **dependency-aware** at every binary gate
#        (uses cutset enumeration when two child nets share ancestors), so
#        reconvergence like N23 = AND(NAND(N2,N11), NAND(N11,N7)) yields P=0.625
#        under uniform priors (instead of 0.5625 from a naive independence assumption).

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional, Callable, Set

# ------------------------------------------------------------
# Basic gate probability combiners (assuming independence)
# ------------------------------------------------------------
def incSigProb(a: float, b: float, op: str) -> float:
    op = op.capitalize()
    if op == "And":
        return a * b
    elif op == "Or":
        return a + b - a * b
    elif op == "Xor":
        return a + b - 2.0 * a * b
    elif op == "Nand":
        return 1.0 - a * b
    elif op == "Nor":
        return (1.0 - a) * (1.0 - b)
    else:
        raise ValueError(f"Unsupported op {op}")

# ------------------------------------------------------------
# Compile expressions to tuples over integer IDs
# Supported forms in truthTableMap values:
#   - int constant 0/1
#   - str alias (e.g., 'mod.sig[0:0]')
#   - list gate:
#       ['Not', x] or [op, a, b] where a,b are aliases or constants
#   (Nested lists are supported if they reduce to alias/const at leaves)
# ------------------------------------------------------------
Exp = Tuple  # ("const", val) | ("alias", id) | ("not", Exp) | (op, Exp, Exp)

def _collect_signal_names(exp: Any, out: Set[str]) -> None:
    if isinstance(exp, int):
        return
    if isinstance(exp, str):
        out.add(exp); return
    if isinstance(exp, list):
        if not exp: return
        op = exp[0]
        if op == "Not":
            _collect_signal_names(exp[1], out)
        else:
            _collect_signal_names(exp[1], out)
            _collect_signal_names(exp[2], out)

def _compile_exp(exp: Any, name2id: Dict[str, int]) -> Exp:
    if isinstance(exp, int):
        return ("const", float(exp))
    if isinstance(exp, str):
        return ("alias", name2id[exp])
    if isinstance(exp, list):
        op = exp[0]
        if op == "Not":
            c = _compile_exp(exp[1], name2id)
            if c[0] == "const":
                return ("const", 1.0 - float(c[1]))
            return ("not", c)
        # binary
        a = _compile_exp(exp[1], name2id)
        b = _compile_exp(exp[2], name2id)
        if a[0] == "const" and b[0] == "const":
            return ("const", incSigProb(float(a[1]), float(b[1]), op))
        return (op, a, b)
    # Unknown leaf → treat as unbiased constant
    return ("const", 0.5)

def _exp_parents(exp: Exp, parents: Set[int]) -> None:
    k = exp[0]
    if k == "const": return
    if k == "alias":
        parents.add(exp[1]); return
    if k == "not":
        _exp_parents(exp[1], parents); return
    # binary
    _exp_parents(exp[1], parents)
    _exp_parents(exp[2], parents)

@dataclass
class DepInfo:
    ancestors_mask: List[int]      # bitset of transitive ancestors for each node id
    parents:        List[List[int]]
    fanout:         List[int]
    depth:          List[int]
    id2name:        List[str]
    name2id:        Dict[str, int]

    def indep(self, a: int, b: int) -> bool:
        A = self.ancestors_mask[a] | (1 << a)
        B = self.ancestors_mask[b] | (1 << b)
        return (A & B) == 0

    def indep_given_mask(self, a: int, b: int, clamp_mask: int) -> bool:
        A = (self.ancestors_mask[a] | (1 << a)) & ~clamp_mask
        B = (self.ancestors_mask[b] | (1 << b)) & ~clamp_mask
        return (A & B) == 0

class _Engine:
    def __init__(self,
                 truthTableMap: Dict[str, Any],
                 signalNames: List[str],
                 refSigBitNames: List[str],
                 prior_map_or_callable: Optional[Callable[[Dict[str,int]], float] | Dict[str, float]] = None,
                 unknown_leaf_prob: float = 0.5):
        # Universe of names
        names = set(signalNames) | set(truthTableMap.keys()) | set(refSigBitNames)
        for e in truthTableMap.values():
            _collect_signal_names(e, names)
        names = sorted(names)
        self.name2id = {n: i for i, n in enumerate(names)}
        self.id2name = names
        self.N = len(names)
        self.unknown_leaf_prob = float(unknown_leaf_prob)

        # Compile expressions
        self.comp_map: Dict[int, Exp] = {}
        for s_name, exp in truthTableMap.items():
            sid = self.name2id[s_name]
            self.comp_map[sid] = _compile_exp(exp, self.name2id)

        # Parents, fanout, depth
        parents: List[List[int]] = [[] for _ in range(self.N)]
        for sid, exp in self.comp_map.items():
            ps: Set[int] = set()
            _exp_parents(exp, ps)
            parents[sid] = sorted(ps)

        fanout = [0] * self.N
        for ps in parents:
            for p in ps:
                fanout[p] += 1

        depth = [-1] * self.N
        visiting = [False] * self.N

        def _depth(u: int) -> int:
            if depth[u] != -1: return depth[u]
            if visiting[u]:
                depth[u] = 0; return 0
            visiting[u] = True
            ps = parents[u]
            d = 0 if not ps else 1 + max(_depth(p) for p in ps)
            visiting[u] = False
            depth[u] = d
            return d

        for i in range(self.N):
            _depth(i)

        # Ancestors masks (DP in topo order)
        order = sorted(range(self.N), key=lambda i: depth[i])
        anc_mask = [0] * self.N
        for u in order:
            m = 0
            for p in parents[u]:
                m |= anc_mask[p] | (1 << p)
            anc_mask[u] = m

        self.depinfo = DepInfo(ancestors_mask=anc_mask,
                               parents=parents,
                               fanout=fanout,
                               depth=depth,
                               id2name=self.id2name,
                               name2id=self.name2id)

        # Ref ids + mask
        self.ref_ids = [self.name2id[r] for r in refSigBitNames if r in self.name2id]
        self.ref_mask = 0
        for rid in self.ref_ids: self.ref_mask |= (1 << rid)

        # Prior (optional; typically unused because we compute via network)
        self.prior = prior_map_or_callable

        # Unconditional probabilities (filled later)
        self.P_uncond: List[float] = [self.unknown_leaf_prob] * self.N

        # Runtime config
        self.max_cut: int = 3

    # --------- Clamp-aware eval with caching (keyed by (sig,mask,bits)) ---------
    def _prob_with_clamps_mask(self,
                               sig: int,
                               clamp_mask: int,
                               clamp_bits: int,
                               cache: Dict[Tuple[int, int, int], float]) -> float:
        key = (sig, clamp_mask, clamp_bits)
        if key in cache: return cache[key]

        # If clamped at leaf
        if (clamp_mask >> sig) & 1:
            v = 1.0 if ((clamp_bits >> sig) & 1) else 0.0
            cache[key] = v
            return v

        exp = self.comp_map.get(sig)
        if exp is None:
            # Optional prior override for unknown leaves
            if isinstance(self.prior, dict):
                pname = self.id2name[sig]
                if pname in self.prior:
                    cache[key] = float(self.prior[pname])
                    return cache[key]
            cache[key] = self.unknown_leaf_prob
            return cache[key]

        def _eval(e: Exp) -> float:
            k = e[0]
            if k == "const":
                return float(e[1])
            if k == "alias":
                sid = e[1]
                if (clamp_mask >> sid) & 1:
                    return 1.0 if ((clamp_bits >> sid) & 1) else 0.0
                return self._prob_with_clamps_mask(sid, clamp_mask, clamp_bits, cache)
            if k == "not":
                return 1.0 - _eval(e[1])
            # binary: if both children are aliases, do dep-aware combination
            a_exp, b_exp = e[1], e[2]
            if a_exp[0] == "alias" and b_exp[0] == "alias":
                a, b = a_exp[1], b_exp[1]
                return self._gate_prob_depaware_mask(k, a, b, clamp_mask, clamp_bits)
            # otherwise, fall back to combining evaluated probabilities
            pA = _eval(a_exp)
            pB = _eval(b_exp)
            return incSigProb(pA, pB, k)

        v = _eval(exp)
        cache[key] = v
        return v

    # --------- P(Z | clamps) ≈ ∏ P(z_i = assign_i | clamps) (factorized) ---------
    def _pz_given_clamps_mask(self,
                              Z: List[int],
                              assign_bits_local: int,  # i-th bit corresponds to Z[i]
                              clamp_mask: int,
                              clamp_bits: int,
                              cache: Dict[Tuple[int, int, int], float]) -> float:
        p = 1.0
        for i, z in enumerate(Z):
            p1 = self._prob_with_clamps_mask(z, clamp_mask, clamp_bits, cache)
            bit = (assign_bits_local >> i) & 1
            p *= p1 if bit else (1.0 - p1)
        return p

    # --------- Pick a small cutset (shared ancestors \ {a,b} \ clamps), by fanout/depth ---------
    def _pick_cutset_ids(self, a: int, b: int, clamp_mask: int) -> List[int]:
        shared_mask = ((self.depinfo.ancestors_mask[a] | (1 << a)) &
                       (self.depinfo.ancestors_mask[b] | (1 << b)))
        shared_mask &= ~((1 << a) | (1 << b) | clamp_mask)
        if shared_mask == 0:
            return []
        ids: List[int] = []
        m = shared_mask
        while m:
            lsb = m & -m
            idx = lsb.bit_length() - 1
            ids.append(idx)
            m ^= lsb
        ids.sort(key=lambda z: (-self.depinfo.fanout[z], -self.depinfo.depth[z]))
        return ids[: self.max_cut]

    # --------- Gate probability with dependency awareness under clamps ---------
    def _gate_prob_depaware_mask(self,
                                 op: str, a: int, b: int,
                                 clamp_mask: int, clamp_bits: int) -> float:
        # Independence under clamps → fast combine
        if self.depinfo.indep_given_mask(a, b, clamp_mask):
            cache = {}
            pA = self._prob_with_clamps_mask(a, clamp_mask, clamp_bits, cache)
            pB = self._prob_with_clamps_mask(b, clamp_mask, clamp_bits, cache)
            return incSigProb(pA, pB, op)

        Z = self._pick_cutset_ids(a, b, clamp_mask)
        # No useful Z → just combine under current clamps
        if not Z:
            cache = {}
            pA = self._prob_with_clamps_mask(a, clamp_mask, clamp_bits, cache)
            pB = self._prob_with_clamps_mask(b, clamp_mask, clamp_bits, cache)
            return incSigProb(pA, pB, op)

        # Enumerate Z, weight by P(Z | clamps), normalize
        cache: Dict[Tuple[int,int,int], float] = {}
        pY_num, w_sum = 0.0, 0.0
        Z_mask = 0
        for z in Z: Z_mask |= (1 << z)

        for assign in range(1 << len(Z)):
            # absolute bits for Z assignments
            z_bits_abs = 0
            for i, z_id in enumerate(Z):
                if (assign >> i) & 1: z_bits_abs |= (1 << z_id)

            cm = clamp_mask | Z_mask
            cb = (clamp_bits & ~Z_mask) | z_bits_abs

            w = self._pz_given_clamps_mask(Z, assign, clamp_mask, clamp_bits, cache)
            if w == 0.0:
                continue

            pA = self._prob_with_clamps_mask(a, cm, cb, cache)
            pB = self._prob_with_clamps_mask(b, cm, cb, cache)
            pY_num += incSigProb(pA, pB, op) * w
            w_sum  += w

        return (pY_num / w_sum) if w_sum > 0 else 0.5

    # --------- Compute all unconditional & conditional probabilities ---------
    def compute_all(self, max_cut: int = 3) -> Tuple[
        Dict[str, float], Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]
    ]:
        self.max_cut = max_cut

        # Unconditional via clamp-aware engine with (mask=0,bits=0) and caching;
        # because _prob_with_clamps_mask now calls _gate_prob_depaware_mask at
        # every binary gate with alias children, reconvergence is handled.
        cache_uncond: Dict[Tuple[int,int,int], float] = {}
        order = sorted(range(self.N), key=lambda i: self.depinfo.depth[i])
        for u in order:
            self.P_uncond[u] = self._prob_with_clamps_mask(u, 0, 0, cache_uncond)

        # Build name → value map
        s_hat: Dict[str, float] = {self.id2name[i]: self.P_uncond[i] for i in range(self.N)}

        # Conditional per reference, computed only for refs in the cone of influence
        s_hat_0: Dict[str, Dict[str, float]] = {}
        s_hat_1: Dict[str, Dict[str, float]] = {}
        ref_names = {rid: self.id2name[rid] for rid in self.ref_ids}

        for u in order:
            name_u = self.id2name[u]
            d0: Dict[str, float] = {}
            d1: Dict[str, float] = {}

            exp = self.comp_map.get(u)
            # Which refs can influence u?
            relevant: List[int] = []
            m = self.depinfo.ancestors_mask[u] & self.ref_mask
            while m:
                lsb = m & -m
                rid = lsb.bit_length() - 1
                relevant.append(rid)
                m ^= lsb

            if exp is None:
                for rid in relevant:
                    rn = ref_names[rid]
                    d0[rn] = self.P_uncond[u]
                    d1[rn] = self.P_uncond[u]
            else:
                k = exp[0]
                if k == "const":
                    v = float(exp[1])
                    for rid in relevant:
                        rn = ref_names[rid]
                        d0[rn] = v
                        d1[rn] = v

                elif k == "alias":
                    a = exp[1]
                    src0 = s_hat_0.get(self.id2name[a], {})
                    src1 = s_hat_1.get(self.id2name[a], {})
                    for rid in relevant:
                        rn = ref_names[rid]
                        d0[rn] = src0.get(rn, self.P_uncond[a])
                        d1[rn] = src1.get(rn, self.P_uncond[a])

                elif k == "not":
                    c_exp = exp[1]
                    if c_exp[0] == "alias":
                        c = c_exp[1]
                        src0 = s_hat_0.get(self.id2name[c], {})
                        src1 = s_hat_1.get(self.id2name[c], {})
                        for rid in relevant:
                            rn = ref_names[rid]
                            v0 = src0.get(rn, self.P_uncond[c])
                            v1 = src1.get(rn, self.P_uncond[c])
                            d0[rn] = 1.0 - v0
                            d1[rn] = 1.0 - v1
                    else:
                        for rid in relevant:
                            rn = ref_names[rid]
                            cm = (1 << rid)
                            v0 = 1.0 - self._prob_with_clamps_mask(u, cm, 0, {})
                            v1 = 1.0 - self._prob_with_clamps_mask(u, cm, cm, {})
                            d0[rn] = v0
                            d1[rn] = v1

                else:
                    # Binary op
                    a_exp, b_exp = exp[1], exp[2]
                    if a_exp[0] == "alias" and b_exp[0] == "alias":
                        a, b = a_exp[1], b_exp[1]
                        for rid in relevant:
                            rn = ref_names[rid]
                            cm = (1 << rid)
                            if self.depinfo.indep_given_mask(a, b, cm):
                                a0 = s_hat_0.get(self.id2name[a], {}).get(rn, self.P_uncond[a])
                                b0 = s_hat_0.get(self.id2name[b], {}).get(rn, self.P_uncond[b])
                                a1 = s_hat_1.get(self.id2name[a], {}).get(rn, self.P_uncond[a])
                                b1 = s_hat_1.get(self.id2name[b], {}).get(rn, self.P_uncond[b])
                                d0[rn] = incSigProb(a0, b0, k)
                                d1[rn] = incSigProb(a1, b1, k)
                            else:
                                d0[rn] = self._gate_prob_depaware_mask(k, a, b, cm, 0)
                                d1[rn] = self._gate_prob_depaware_mask(k, a, b, cm, cm)
                    else:
                        for rid in relevant:
                            rn = ref_names[rid]
                            cm = (1 << rid)
                            v0 = self._prob_with_clamps_mask(u, cm, 0, {})
                            v1 = self._prob_with_clamps_mask(u, cm, cm, {})
                            d0[rn] = v0
                            d1[rn] = v1

            # Enforce self-conditioning identity if this node is itself a reference
            u_is_ref = ( (1 << u) & self.ref_mask ) != 0
            if u_is_ref:
                rn = self.id2name[u]
                d0[rn] = 0.0
                d1[rn] = 1.0

            s_hat_0[name_u] = d0
            s_hat_1[name_u] = d1

        return s_hat, s_hat_0, s_hat_1


# ------------------------------------------------------------------------------------
# Public API 1: one-shot computation (recommended)
# ------------------------------------------------------------------------------------
def compute_sig_probs(truthTableMap: Dict[str, Any],
                      signalNames: List[str],
                      refSigBitNames: List[str],
                      prior_map_or_callable: Optional[Callable[[Dict[str,int]], float] | Dict[str, float]] = None,
                      max_cut: int = 3,
                      unknown_leaf_prob: float = 0.5
                      ) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
    eng = _Engine(truthTableMap, signalNames, refSigBitNames, prior_map_or_callable, unknown_leaf_prob)
    return eng.compute_all(max_cut=max_cut)

# ------------------------------------------------------------------------------------
# Public API 2: Compatibility shim for your existing recursive driver
#   This fills s_hat/s_hat_0/s_hat_1 on the first call (for all signals),
#   then subsequent calls return immediately because the dicts are already populated.
# ------------------------------------------------------------------------------------
def populateSigProbs(sig: Any,
                     encounteredSigs: Set[Any],
                     s_hat: Dict[str, float],
                     s_hat_0: Dict[str, Dict[str, float]],
                     s_hat_1: Dict[str, Dict[str, float]],
                     truthTableMap: Dict[str, Any],
                     refSigBitNames: List[str],
                     inputSigBitNames: List[str],
                     depinfo=None,                       # ignored: we build our own optimized depinfo
                     prior_map_or_callable=None,
                     max_cut: int = 3) -> None:
    # If already computed once, keep behavior: do nothing
    if s_hat:
        return

    # Build a superset of names (signals you care about)
    signalNames = sorted(set(inputSigBitNames) | set(truthTableMap.keys()) | set(refSigBitNames))

    sh, sh0, sh1 = compute_sig_probs(
        truthTableMap=truthTableMap,
        signalNames=signalNames,
        refSigBitNames=refSigBitNames,
        prior_map_or_callable=prior_map_or_callable,
        max_cut=max_cut,
        unknown_leaf_prob=0.5,
    )

    s_hat.update(sh)
    s_hat_0.update(sh0)
    s_hat_1.update(sh1)
