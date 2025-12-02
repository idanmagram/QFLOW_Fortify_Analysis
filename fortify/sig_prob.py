
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
    elif op == "Eq":
        return a * b + (1 - a) * (1 - b)
    elif op == "NotEq":
        return a + b - 2 * a * b
    else:
        raise ValueError("Unknown op: " + op)

def _key(sig):
    """
    Convert potentially nested list-based expressions into a hashable tuple
    so they can be used as dict/set keys.
    """
    if isinstance(sig, list):
        return tuple(_key(x) for x in sig)
    return sig

# recursive signal probability and conditional signal probability calculation
def populateSigProbs(sig, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames):
    key = _key(sig)

    # to avoid recomputation of already calculated signal probability values
    if key in s_hat:
        return

    # to avoid infinite recursion caused by circular dependencies, assigning zero signal probabilities
    if key in encounteredSigs:
        print("Circular dependency:", sig)
        s_hat[key] = 0
        s_hat_0[key] = {}
        s_hat_1[key] = {}
        for ref in refSigBitNames:
            s_hat_0[key][ref] = 0
            s_hat_1[key][ref] = 0
        return

    encounteredSigs.add(key)

    # Expression nodes (e.g., Eq, Cond) that are not standalone signals
    if isinstance(sig, list):
        op = sig[0]
        if op == "Cond":
            cond = sig[1]
            tval = sig[2]
            fval = sig[3]

            populateSigProbs(cond, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            populateSigProbs(tval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            populateSigProbs(fval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

            p_cond = s_hat[_key(cond)]
            s_hat[key] = p_cond * s_hat[_key(tval)] + (1 - p_cond) * s_hat[_key(fval)]
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                p0 = s_hat_0[_key(cond)][ref]
                p1 = s_hat_1[_key(cond)][ref]
                s_hat_0[key][ref] = p0 * s_hat_0[_key(tval)][ref] + (1 - p0) * s_hat_0[_key(fval)][ref]
                s_hat_1[key][ref] = p1 * s_hat_1[_key(tval)][ref] + (1 - p1) * s_hat_1[_key(fval)][ref]
            return

        # Binary ops: And/Or/Xor/Eq/NotEq
        a = sig[1]
        b = sig[2]
        populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
        populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

        s_hat[key] = incSigProb(s_hat[_key(a)], s_hat[_key(b)], op)
        s_hat_0[key] = {}
        s_hat_1[key] = {}
        for ref in refSigBitNames:
            s_hat_0[key][ref] = incSigProb(s_hat_0[_key(a)][ref], s_hat_0[_key(b)][ref], op)
            s_hat_1[key][ref] = incSigProb(s_hat_1[_key(a)][ref], s_hat_1[_key(b)][ref], op)
        return

    if sig in truthTableMap:
        # logical expression corresponding to this signal
        exp = truthTableMap[sig]

        if isinstance(exp, int):
            s_hat[key] = exp
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                s_hat_0[key][ref] = exp
                s_hat_1[key][ref] = exp

        elif isinstance(exp, str):
            populateSigProbs(exp, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = s_hat[exp]
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                s_hat_0[key][ref] = s_hat_0[exp][ref]
                s_hat_1[key][ref] = s_hat_1[exp][ref]

        # for expressions corresponding to a logical operation (gate) like Not, And, Or, Xor,
        # we calculate the signal probability recursively from the signal probabilities
        # of the inputs of the operation (gate)
        elif isinstance(exp, list):
            op = exp[0]
            if op == "Cond":
                cond = exp[1]
                tval = exp[2]
                fval = exp[3]

                populateSigProbs(cond, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                populateSigProbs(tval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                populateSigProbs(fval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

                p_cond = s_hat[_key(cond)]
                s_hat[key] = p_cond * s_hat[_key(tval)] + (1 - p_cond) * s_hat[_key(fval)]
                s_hat_0[key] = {}
                s_hat_1[key] = {}
                for ref in refSigBitNames:
                    p0 = s_hat_0[_key(cond)][ref]
                    p1 = s_hat_1[_key(cond)][ref]
                    s_hat_0[key][ref] = p0 * s_hat_0[_key(tval)][ref] + (1 - p0) * s_hat_0[_key(fval)][ref]
                    s_hat_1[key][ref] = p1 * s_hat_1[_key(tval)][ref] + (1 - p1) * s_hat_1[_key(fval)][ref]

            elif op == "Not":
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = 1 - s_hat[_key(exp[1])]
                s_hat_0[key] = {}
                s_hat_1[key] = {}
                for ref in refSigBitNames:
                    s_hat_0[key][ref] = 1 - s_hat_0[_key(exp[1])][ref]
                    s_hat_1[key][ref] = 1 - s_hat_1[_key(exp[1])][ref]
            else: # And, Or, Xor, Eq, NotEq
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                populateSigProbs(exp[2], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = incSigProb(s_hat[_key(exp[1])], s_hat[_key(exp[2])], op)
                s_hat_0[key] = {}
                s_hat_1[key] = {}
                for ref in refSigBitNames:
                    s_hat_0[key][ref] = incSigProb(s_hat_0[_key(exp[1])][ref], s_hat_0[_key(exp[2])][ref], op)
                    s_hat_1[key][ref] = incSigProb(s_hat_1[_key(exp[1])][ref], s_hat_1[_key(exp[2])][ref], op)

    else:
        # should never reach here; assigning zero signal probabilities as a corner case
        if not isinstance(sig,int):
            print("Should not reach this; check:", sig)
        s_hat[key] = 0
        s_hat_0[key] = {}
        s_hat_1[key] = {}
        for ref in refSigBitNames:
            s_hat_0[key][ref] = 0
            s_hat_1[key][ref] = 0
