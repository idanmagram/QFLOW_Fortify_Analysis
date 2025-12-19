
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
    if key == "top.U_RSA.indata[1:1]":
        print("idna")

    # to avoid recomputation of already calculated signal probability values
    if key in s_hat:
        return

    #if not (isinstance(key, int)) and "state" in key:
    #    print("Lior")
    # to avoid infinite recursion caused by circular dependencies, assigning zero signal probabilities
    if key in encounteredSigs:
        print("Circular dependency:", sig)
        s_hat[key] = 0.5
        s_hat_0[key] = {}
        s_hat_1[key] = {}
        for ref in refSigBitNames:
            s_hat_0[key][ref] = 0.5
            s_hat_1[key][ref] = 0.5
        return

    encounteredSigs.add(key)

    # Expression nodes (e.g., Eq, Cond) that are not standalone signals
    if isinstance(sig, list):
        op = sig[0]
        known_ops = {"Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq", "Srl", "Sll", "Plus", "Times", "Minus"}
        if op not in known_ops:
            # Treat as pass-through of first element if unrecognized op (e.g., concatenation artifacts)
            target = op
            populateSigProbs(target, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = s_hat[_key(target)]
            s_hat_0[key] = {ref: s_hat_0[_key(target)][ref] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat_1[_key(target)][ref] for ref in refSigBitNames}
            return

        if op == "Cond":
            cond = sig[1]
            tval = sig[2] if len(sig) > 2 else 0
            fval = sig[3] if len(sig) > 3 else 0

            populateSigProbs(cond, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            populateSigProbs(tval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            # if fval is None (e.g., truncated unroll), treat as 0
            if fval is not None:
                populateSigProbs(fval, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            else:
                s_hat[_key(fval)] = 0
                s_hat_0[_key(fval)] = {ref: 0 for ref in refSigBitNames}
                s_hat_1[_key(fval)] = {ref: 0 for ref in refSigBitNames}

            p_cond = s_hat[_key(cond)]
            s_hat[key] = p_cond * s_hat[_key(tval)] + (1 - p_cond) * s_hat[_key(fval)]
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                p0 = s_hat_0[_key(cond)][ref]
                p1 = s_hat_1[_key(cond)][ref]
                #p0 = 0.5
                #p1 = 0.5
                s_hat_0[key][ref] = p0 * s_hat_0[_key(tval)][ref] + (1 - p0) * s_hat_0[_key(fval)][ref]
                s_hat_1[key][ref] = p1 * s_hat_1[_key(tval)][ref] + (1 - p1) * s_hat_1[_key(fval)][ref]
            return

        if op == "Not":
            a = sig[1] if len(sig) > 1 else 0
            populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = 1 - s_hat[_key(a)]
            s_hat_0[key] = {ref: 1 - s_hat_0[_key(a)][ref] for ref in refSigBitNames}
            s_hat_1[key] = {ref: 1 - s_hat_1[_key(a)][ref] for ref in refSigBitNames}
            return

        if op == "Mix":
            parts = sig[1:]
            if not parts:
                s_hat[key] = 0
                s_hat_0[key] = {ref: 0 for ref in refSigBitNames}
                s_hat_1[key] = {ref: 0 for ref in refSigBitNames}
                return
            for p in parts:
                populateSigProbs(p, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = sum(s_hat[_key(p)] for p in parts) / len(parts)
            s_hat_0[key] = {ref: sum(s_hat_0[_key(p)][ref] for p in parts) / len(parts) for ref in refSigBitNames}
            s_hat_1[key] = {ref: sum(s_hat_1[_key(p)][ref] for p in parts) / len(parts) for ref in refSigBitNames}
            return

        # Binary ops: And/Or/Xor/Eq/NotEq
        a = sig[1] if len(sig) > 1 else 0
        b = sig[2] if len(sig) > 2 else 0
        populateSigProbs(a, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
        populateSigProbs(b, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

        if op in ("Srl", "Sll", "Plus", "Times", "Minus"):
            # Approximate: treat as pass-through of left operand for probabilities
            s_hat[key] = s_hat[_key(a)]
            s_hat_0[key] = {ref: s_hat_0[_key(a)][ref] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat_1[_key(a)][ref] for ref in refSigBitNames}
            return

        s_hat[key] = incSigProb(s_hat[_key(a)], s_hat[_key(b)], op)
        s_hat_0[key] = {}
        s_hat_1[key] = {}
        for ref in refSigBitNames:
            s_hat_0[key][ref] = incSigProb(s_hat_0[_key(a)][ref], s_hat_0[_key(b)][ref], op)
            s_hat_1[key][ref] = incSigProb(s_hat_1[_key(a)][ref], s_hat_1[_key(b)][ref], op)
        return

    if sig in truthTableMap:
        # logical expression corresponding to this signal
        if not (isinstance(key, int)) and "Baud8GeneratorACC[25:25]" in key:
            print("Lior")
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
                if cond == 'top.TSC.MUX_Sel[0:0]':
                    print("idan")

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
            elif op == "Mix":
                parts = exp[1:]
                for p in parts:
                    populateSigProbs(p, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = sum(s_hat[_key(p)] for p in parts) / len(parts) if parts else 0
                s_hat_0[key] = {ref: sum(s_hat_0[_key(p)][ref] for p in parts) / len(parts) if parts else 0 for ref in refSigBitNames}
                s_hat_1[key] = {ref: sum(s_hat_1[_key(p)][ref] for p in parts) / len(parts) if parts else 0 for ref in refSigBitNames}
            elif op in ("Srl", "Sll", "Plus", "Times", "Minus"):
                # Approximate: treat as pass-through of left operand
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = s_hat[_key(exp[1])]
                s_hat_0[key] = {ref: s_hat_0[_key(exp[1])][ref] for ref in refSigBitNames}
                s_hat_1[key] = {ref: s_hat_1[_key(exp[1])][ref] for ref in refSigBitNames}
            else: # And, Or, Xor, Eq, NotEq
                populateSigProbs(exp[1], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                populateSigProbs(exp[2], encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                s_hat[key] = incSigProb(s_hat[_key(exp[1])], s_hat[_key(exp[2])], op)
                s_hat_0[key] = {}
                s_hat_1[key] = {}
                for ref in refSigBitNames:
                    #You should check why s_hat_0[_key(exp[1])][ref], is 0
                    s_hat_0[key][ref] = incSigProb(s_hat_0[_key(exp[1])][ref], s_hat_0[_key(exp[2])][ref], op)
                    s_hat_1[key][ref] = incSigProb(s_hat_1[_key(exp[1])][ref], s_hat_1[_key(exp[2])][ref], op)

    else:
        # missing definition; try per-bit product if this looks like a bus slice, else default to 0.5
        prod = None
        prod0 = {ref: None for ref in refSigBitNames}
        prod1 = {ref: None for ref in refSigBitNames}

        if isinstance(sig, str) and '[' in sig and ':' in sig:
            try:
                base = sig.split('[')[0]
                rng = sig.split('[')[1].split(']')[0]
                msb, lsb = map(int, rng.split(':'))
                bits = range(lsb, msb + 1)
                p = 1.0
                p0 = {ref: 1.0 for ref in refSigBitNames}
                p1 = {ref: 1.0 for ref in refSigBitNames}
                for i in bits:
                    bitname = f"{base}[{i}:{i}]"
                    populateSigProbs(bitname, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                    p *= s_hat.get(_key(bitname), 0.5)
                    for ref in refSigBitNames:
                        p0[ref] *= s_hat_0.get(_key(bitname), {}).get(ref, 0.5)
                        p1[ref] *= s_hat_1.get(_key(bitname), {}).get(ref, 0.5)
                prod = p
                prod0 = p0
                prod1 = p1
            except Exception:
                prod = None

        if prod is None:
            s_hat[key] = 0.5 if sig not in (0, 1) else sig
            s_hat_0[key] = {ref: s_hat[key] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat[key] for ref in refSigBitNames}
        else:
            pmin = 0.25
            s_hat[key] = max(prod, pmin)
            s_hat_0[key] = {ref: max(prod0[ref], pmin) for ref in refSigBitNames}
            s_hat_1[key] = {ref: max(prod1[ref], pmin) for ref in refSigBitNames}
