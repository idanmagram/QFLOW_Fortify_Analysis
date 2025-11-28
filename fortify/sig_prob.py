
import sys
sys.setrecursionlimit(100000)

# incremental signal probability formulae for the standard logic gates
def incSigProb(a, b, op):
    if op == "And":
        return a*b
    elif op == "Or":
        return a + b - a*b
    else: # op == "Xor"
        return a + b - 2*a*b

# recursive signal probability and conditional signal probability calculation
def populateSigProbs(sig, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames):
    # to avoid recomputation of already calculated signal probability values
    #print("truthTableMap ",truthTableMap)
    #if "top.tro.load[0:0]" in sig:
    #    print("idna")
    if sig in s_hat:
        return

    # to avoid infinite recursion caused by circular dependencies, assigning zero signal probabilities
    if sig in encounteredSigs:
        print("Circular dependency:", sig)
        s_hat[sig] = 0
        s_hat_0[sig] = {}
        s_hat_1[sig] = {}
        for ref in refSigBitNames:
            s_hat_0[sig][ref] = 0
            s_hat_1[sig][ref] = 0
        return

    encounteredSigs.add(sig)

    if sig in truthTableMap:
        # logical expression corresponding to this signal
        exp = truthTableMap[sig]

        if isinstance(exp, int):
            s_hat[sig] = exp
            s_hat_0[sig] = {}
            s_hat_1[sig] = {}
            for ref in refSigBitNames:
                s_hat_0[sig][ref] = exp
                s_hat_1[sig][ref] = exp

        elif isinstance(exp, str):
            populateSigProbs(exp, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[sig] = s_hat[exp]
            s_hat_0[sig] = {}
            s_hat_1[sig] = {}
            for ref in refSigBitNames:
                s_hat_0[sig][ref] = s_hat_0[exp][ref]
                s_hat_1[sig][ref] = s_hat_1[exp][ref]

        # for expressions corresponding to a logical operation (gate) like Not, And, Or, Xor,
        # we calculate the signal probability recursively from the signal probabilities
        # of the inputs of the operation (gate)
        elif isinstance(exp, list):
            if "top.tro.counter[0:0]" in sig:
                print("idna")
            op = exp[0]
            if op == "Not":
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[sig] = 1 - s_hat[exp[1]]
                s_hat_0[sig] = {}
                s_hat_1[sig] = {}
                for ref in refSigBitNames:
                    s_hat_0[sig][ref] = 1 - s_hat_0[exp[1]][ref]
                    s_hat_1[sig][ref] = 1 - s_hat_1[exp[1]][ref]
            else: # And, Or, Xor
                if "top.tro.counter[0:0]" in sig:
                    print("idna")
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                populateSigProbs(exp[2], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[sig] = incSigProb(s_hat[exp[1]], s_hat[exp[2]], op)
                s_hat_0[sig] = {}
                s_hat_1[sig] = {}
                for ref in refSigBitNames:
                    s_hat_0[sig][ref] = incSigProb(s_hat_0[exp[1]][ref], s_hat_0[exp[2]][ref], op)
                    s_hat_1[sig][ref] = incSigProb(s_hat_1[exp[1]][ref], s_hat_1[exp[2]][ref], op)

    else:
        # should never reach here; assigning zero signal probabilities as a corner case
        print("Should not reach this; check:", sig)
        s_hat[sig] = 0
        s_hat_0[sig] = {}
        s_hat_1[sig] = {}
        for ref in refSigBitNames:
            s_hat_0[sig][ref] = 0
            s_hat_1[sig][ref] = 0
'''
import sys
sys.setrecursionlimit(100000)

# incremental signal probability formulae for the standard logic gates
def incSigProb(a, b, op):
    if op == "And":
        return a * b
    elif op == "Or":
        return a + b - a * b
    elif op == "Xor":
        return a + b - 2 * a * b
    else:
        raise ValueError("Unknown op: " + op)

# helper to collect all transitive dependencies
def get_dependencies(sig, exp, sig_deps):
    if isinstance(exp, int):
        sig_deps[sig] = set()
    elif isinstance(exp, str):
        sig_deps[sig] = sig_deps.get(exp, set()).union({exp})
    elif isinstance(exp, list):
        _, a, b = exp
        deps_a = sig_deps.get(a, {a})
        deps_b = sig_deps.get(b, {b})
        sig_deps[sig] = deps_a.union(deps_b, {a, b})
    return sig_deps[sig]

# recursive signal probability computation, now reconvergence-aware
def populateSigProbs(sig, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                     truthTableMap, refSigBitNames, inputSigBitNames, sig_deps):
    if sig in s_hat:
        return

    if sig in encounteredSigs:
        print("Circular dependency:", sig)
        s_hat[sig] = 0
        s_hat_0[sig] = {ref: 0 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0 for ref in refSigBitNames}
        return

    encounteredSigs.add(sig)

    if sig not in truthTableMap:
        print("Missing definition for:", sig)
        s_hat[sig] = 0
        s_hat_0[sig] = {ref: 0 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0 for ref in refSigBitNames}
        return

    exp = truthTableMap[sig]

    if isinstance(exp, int):
        s_hat[sig] = exp
        s_hat_0[sig] = {ref: exp for ref in refSigBitNames}
        s_hat_1[sig] = {ref: exp for ref in refSigBitNames}
        sig_deps[sig] = set()

    elif isinstance(exp, str):
        populateSigProbs(exp, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                         truthTableMap, refSigBitNames, inputSigBitNames, sig_deps)
        s_hat[sig] = s_hat[exp]
        s_hat_0[sig] = s_hat_0[exp].copy()
        s_hat_1[sig] = s_hat_1[exp].copy()
        sig_deps[sig] = get_dependencies(sig, exp, sig_deps)

    elif isinstance(exp, list):
        op, a, b = exp[0], exp[1], exp[2]
        populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                         truthTableMap, refSigBitNames, inputSigBitNames, sig_deps)
        populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                         truthTableMap, refSigBitNames, inputSigBitNames, sig_deps)

        # track dependencies to detect reconvergence
        deps_a = sig_deps.get(a, {a})
        deps_b = sig_deps.get(b, {b})
        sig_deps[sig] = deps_a.union(deps_b, {a, b})

        # reconvergent fanout detection
        reconverges = bool(deps_a.intersection(deps_b))

        s_hat_0[sig] = {}
        s_hat_1[sig] = {}

        for ref in refSigBitNames:
            if reconverges:
                # conservative cofactoring estimate
                # instead of 0.5 fallback, we apply Shannon expansion:
                # P(f) = P(X=0)*P(f|X=0) + P(X=1)*P(f|X=1)
                p0 = incSigProb(s_hat_0[a][ref], s_hat_0[b][ref], op)
                p1 = incSigProb(s_hat_1[a][ref], s_hat_1[b][ref], op)
                s_hat[sig] = 0.5 * p0 + 0.5 * p1  # assumes P(ref)=0.5
                s_hat_0[sig][ref] = p0
                s_hat_1[sig][ref] = p1
            else:
                s_hat[sig] = incSigProb(s_hat[a], s_hat[b], op)
                s_hat_0[sig][ref] = incSigProb(s_hat_0[a][ref], s_hat_0[b][ref], op)
                s_hat_1[sig][ref] = incSigProb(s_hat_1[a][ref], s_hat_1[b][ref], op)
    else:
        raise ValueError(f"Unsupported expression type for signal {sig}")
'''