
import sys
sys.setrecursionlimit(100000)

AES_SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)

_BYTE_BIT_PROFILES = tuple(
    tuple((value >> bit) & 1 for bit in range(8))
    for value in range(256)
)
_AES_SBOX_OUTPUT_BITS = tuple(
    tuple((AES_SBOX[value] >> bit) & 1 for bit in range(8))
    for value in range(256)
)
REPRESENTATIVE_LUT_INDICATOR_MEAN = 0.5

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


def _bits_from_bus(bus):
    if not isinstance(bus, str):
        return None
    if "[" in bus and ":" in bus and bus.endswith("]"):
        try:
            base = bus.split("[", 1)[0]
            rng = bus.split("[", 1)[1].split("]")[0]
            msb, lsb = map(int, rng.split(":"))
            return [f"{base}[{i}:{i}]" for i in range(lsb, msb + 1)]
        except Exception:
            return None
    return None


def _lut_const_bit_prob(bus, default_bit, exception_keys, bit_prob_fn):
    abits = _bits_from_bus(bus)
    if abits is None:
        return 0.5

    # Fast approximation requested by user:
    # choose a single representative input byte x* from the marginals,
    # estimate P(in=x*), and approximate the LUT output-bit indicator as a
    # Bernoulli(0.5) random variable representative of the table outputs.
    p_x = 1.0
    for abit in abits:
        pa = bit_prob_fn(abit)
        if pa >= 0.5:
            p_x *= pa
        else:
            p_x *= (1.0 - pa)

    approx = (1 << len(abits)) * p_x * REPRESENTATIVE_LUT_INDICATOR_MEAN
    return min(max(approx, 0.0), 1.0)


def _lut_const_bit_prob_with_clk(bus, default_bit, exception_keys, bit_prob_fn, clk_name=None):
    p = _lut_const_bit_prob(bus, default_bit, exception_keys, bit_prob_fn)
    if isinstance(clk_name, str):
        return p * bit_prob_fn(clk_name)
    return p


def compute_sbox_output_probs(input_bit_probs):
    """
    Exact AES S-box output bit probabilities from independent input bit probabilities.

    input_bit_probs[k] = P(in[k] = 1 | key_i = b), for k=0..7 (LSB-first).
    Returns out_probs[j] = P(out[j] = 1 | key_i = b), for j=0..7 (LSB-first).
    """
    if len(input_bit_probs) != 8:
        raise ValueError("input_bit_probs must have length 8")

    probs = [float(p) for p in input_bit_probs]
    for p in probs:
        if p < 0.0 or p > 1.0:
            raise ValueError("input bit probabilities must be in [0, 1]")

    out_probs = [0.0] * 8
    for x in range(256):
        p_x = 1.0
        x_bits = _BYTE_BIT_PROFILES[x]
        for bit_idx in range(8):
            p_bit = probs[bit_idx]
            p_x *= p_bit if x_bits[bit_idx] else (1.0 - p_bit)
            if p_x == 0.0:
                break
        if p_x == 0.0:
            continue

        out_bits = _AES_SBOX_OUTPUT_BITS[x]
        for out_idx in range(8):
            if out_bits[out_idx]:
                out_probs[out_idx] += p_x

    return out_probs


def compute_both_conditions(p_in_given_key0, p_in_given_key1):
    """
    Compute exact AES S-box output probabilities under both secret conditions.

    Returns:
      (out_probs_given_key0, out_probs_given_key1)
    """
    return (
        compute_sbox_output_probs(p_in_given_key0),
        compute_sbox_output_probs(p_in_given_key1),
    )

# recursive signal probability and conditional signal probability calculation
def populateSigProbs(sig, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames):
    key = _key(sig)
    fallback = 0.5
    if key == "top.Tj_Trigger.Detected[0:0]":
        print("idna")

    # to avoid recomputation of already calculated signal probability values
    if key in s_hat:
        return
    #print("key = ", key)
    if key == "top.TSC.Tj_Trig[0:0]@1":
        print("Tali")

    if isinstance(key, str) and "Tj_Trig[0:0]" in key:
        s_hat[key] = 0
        s_hat_0[key] = {ref: 0 for ref in refSigBitNames}
        s_hat_1[key] = {ref: 0 for ref in refSigBitNames}
        return

    # to avoid infinite recursion caused by circular dependencies, assigning zero signal probabilities
    if key in encounteredSigs:
        s_hat[key] = fallback
        s_hat_0[key] = {ref: fallback for ref in refSigBitNames}
        s_hat_1[key] = {ref: fallback for ref in refSigBitNames}
        return

    encounteredSigs.add(key)

    # Expression nodes (e.g., Eq, Cond) that are not standalone signals
    if isinstance(sig, list):
        op = sig[0]
        known_ops = {"Cond", "Not", "Mix", "And", "Or", "Xor", "Eq", "NotEq", "Srl", "Sll", "Plus", "Times", "Minus", "EqVec", "EqBus", "LutConstBit"}
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
        if op == "EqVec":
            bits_a = sig[1]
            bits_b = sig[2]
            floor = sig[3] if len(sig) > 3 else 0.0
            eqps = []
            eqps0 = {ref: [] for ref in refSigBitNames}
            eqps1 = {ref: [] for ref in refSigBitNames}
            for a_elem, b_elem in zip(bits_a, bits_b):
                populateSigProbs(a_elem, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                if not isinstance(b_elem, int):
                    populateSigProbs(b_elem, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                pa = s_hat.get(_key(a_elem), 0.5)
                pb = b_elem if isinstance(b_elem, int) else s_hat.get(_key(b_elem), 0.5)
                eqp = pa * pb + (1 - pa) * (1 - pb)
                eqps.append(eqp)
                for ref in refSigBitNames:
                    pa0 = s_hat_0.get(_key(a_elem), {}).get(ref, 0.5)
                    pa1 = s_hat_1.get(_key(a_elem), {}).get(ref, 0.5)
                    pb0 = b_elem if isinstance(b_elem, int) else s_hat_0.get(_key(b_elem), {}).get(ref, 0.5)
                    pb1 = b_elem if isinstance(b_elem, int) else s_hat_1.get(_key(b_elem), {}).get(ref, 0.5)
                    eqp0 = pa0 * pb0 + (1 - pa0) * (1 - pb0)
                    eqp1 = pa1 * pb1 + (1 - pa1) * (1 - pb1)
                    eqps0[ref].append(eqp0)
                    eqps1[ref].append(eqp1)
            val = min(eqps) if eqps else 0.5
            val = max(val, floor)
            s_hat[key] = val
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                v0 = min(eqps0[ref]) if eqps0[ref] else 0.5
                v1 = min(eqps1[ref]) if eqps1[ref] else 0.5
                s_hat_0[key][ref] = max(v0, floor)
                s_hat_1[key][ref] = max(v1, floor)
            return
        if op == "EqBus":
            a = sig[1]
            b = sig[2]
            floor = sig[3] if len(sig) > 3 else 0.0
            def _bits(x):
                if isinstance(x, str) and "[" in x and ":" in x and x.endswith("]"):
                    try:
                        base = x.split("[",1)[0]
                        rng = x.split("[",1)[1].split("]")[0]
                        msb, lsb = map(int, rng.split(":"))
                        return [f"{base}[{i}:{i}]" for i in range(lsb, msb+1)]
                    except Exception:
                        return None
                return None
            abits = _bits(a)
            bbits = _bits(b) if isinstance(b, str) else None
            if abits is None:
                s_hat[key] = floor
                s_hat_0[key] = {ref: floor for ref in refSigBitNames}
                s_hat_1[key] = {ref: floor for ref in refSigBitNames}
                return
            eqps = []
            eqps0 = {ref: [] for ref in refSigBitNames}
            eqps1 = {ref: [] for ref in refSigBitNames}
            for idx, abit in enumerate(abits):
                bbit = bbits[idx] if bbits and idx < len(bbits) else ((b >> idx) & 1 if isinstance(b, int) else b)
                populateSigProbs(abit, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                if isinstance(bbit, str):
                    populateSigProbs(bbit, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
                pa = s_hat.get(_key(abit), 0.5)
                pb = bbit if isinstance(bbit, int) else s_hat.get(_key(bbit), 0.5)
                eqp = pa * pb + (1 - pa) * (1 - pb)
                eqps.append(eqp)
                for ref in refSigBitNames:
                    pa0 = s_hat_0.get(_key(abit), {}).get(ref, 0.5)
                    pa1 = s_hat_1.get(_key(abit), {}).get(ref, 0.5)
                    pb0 = bbit if isinstance(bbit, int) else s_hat_0.get(_key(bbit), {}).get(ref, 0.5)
                    pb1 = bbit if isinstance(bbit, int) else s_hat_1.get(_key(bbit), {}).get(ref, 0.5)
                    eqp0 = pa0 * pb0 + (1 - pa0) * (1 - pb0)
                    eqp1 = pa1 * pb1 + (1 - pa1) * (1 - pb1)
                    eqps0[ref].append(eqp0)
                    eqps1[ref].append(eqp1)
            val = min(eqps) if eqps else 0.5
            val = max(val, floor)
            s_hat[key] = val
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                v0 = min(eqps0[ref]) if eqps0[ref] else 0.5
                v1 = min(eqps1[ref]) if eqps1[ref] else 0.5
                s_hat_0[key][ref] = max(v0, floor)
                s_hat_1[key][ref] = max(v1, floor)
            return
        if op == "LutConstBit":
            bus = sig[1] if len(sig) > 1 else ""
            default_bit = sig[2] if len(sig) > 2 else 0
            exception_keys = sig[3] if len(sig) > 3 else []
            clk_name = sig[4] if len(sig) > 4 else None
            for abit in _bits_from_bus(bus) or []:
                populateSigProbs(abit, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            if isinstance(clk_name, str):
                populateSigProbs(clk_name, encounteredSigs, s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)
            s_hat[key] = _lut_const_bit_prob_with_clk(
                bus,
                default_bit,
                exception_keys,
                lambda bit_name: s_hat.get(_key(bit_name), 0.5),
                clk_name=clk_name,
            )
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                s_hat_0[key][ref] = _lut_const_bit_prob_with_clk(
                    bus,
                    default_bit,
                    exception_keys,
                    lambda bit_name, ref=ref: s_hat_0.get(_key(bit_name), {}).get(ref, s_hat.get(_key(bit_name), 0.5)),
                    clk_name=clk_name,
                )
                s_hat_1[key][ref] = _lut_const_bit_prob_with_clk(
                    bus,
                    default_bit,
                    exception_keys,
                    lambda bit_name, ref=ref: s_hat_1.get(_key(bit_name), {}).get(ref, s_hat.get(_key(bit_name), 0.5)),
                    clk_name=clk_name,
                )
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
            s_hat[key] = s_hat.get(exp, 0.5)
            s_hat_0[key] = {}
            s_hat_1[key] = {}
            for ref in refSigBitNames:
                s_hat_0[key][ref] = s_hat_0.get(exp, {}).get(ref, 0.5)
                s_hat_1[key][ref] = s_hat_1.get(exp, {}).get(ref, 0.5)

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
                # Separate the base name and the suffix (if any)
                # Example: 'top.U_RSA.exp[31:0]@22' -> base_part='top.U_RSA.exp', rest='31:0]@22'
                base_part, rest = sig.split('[', 1)

                # Split the range from the suffix
                # Example: '31:0]@22' -> rng_str='31:0', suffix='@22'
                rng_str, suffix = rest.split(']', 1)

                msb, lsb = map(int, rng_str.split(':'))
                bits = range(lsb, msb + 1)
                num_bits = len(bits)  # Store the count for averaging
                p = 1.0
                p0 = {ref: 1 for ref in refSigBitNames}
                p1 = {ref: 1 for ref in refSigBitNames}

                for i in bits:
                    # Reconstruct the bitname including the suffix
                    # Example: 'top.U_RSA.exp[0:0]@22'
                    bitname = f"{base_part}[{i}:{i}]{suffix}"

                    populateSigProbs(bitname, encounteredSigs, s_hat, s_hat_0, s_hat_1,
                                     truthTableMap, refSigBitNames, inputSigBitNames)
                    #print("bitname 1:", bitname, " p = ",s_hat.get(_key(bitname), 0.5))

                    p *= s_hat.get(_key(bitname), 0.5)
                    for ref in refSigBitNames:
                        p0[ref] = min(s_hat_0.get(_key(bitname), {}).get(ref, 0.5),p0[ref])
                        p1[ref] = min(s_hat_1.get(_key(bitname), {}).get(ref, 0.5),p1[ref])
                    #for ref in refSigBitNames:
                        # Summing the probabilities
                    #    p0[ref] += s_hat_0.get(_key(bitname), {}).get(ref, 0.5)
                    #    p1[ref] += s_hat_1.get(_key(bitname), {}).get(ref, 0.5)

                #prod = p


                #if num_bits > 0:
                #    for ref in refSigBitNames:
                #        p0[ref] /= num_bits
                #        p1[ref] /= num_bits

                prod0 = p0
                prod1 = p1
                bitname = f"{base_part}[{0}:{0}]{suffix}"
                prod = s_hat.get(_key(bitname), 0.5)

                #print("final prod:", prod, "s_hat_0 ",prod0, "s_hat_1 ",s_hat_1)

            except Exception:
                prod = None

        #print("final prod:", prod, " for ", sig)
        if prod is None:
            s_hat[key] = 0.5 if sig not in (0, 1) else sig
            s_hat_0[key] = {ref: s_hat[key] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat[key] for ref in refSigBitNames}
        else:
            pmin = 0.0000000001
            s_hat[key] = max(prod, pmin)
            #s_hat_0[key] = {ref: max(prod0[ref], pmin) for ref in refSigBitNames}
            #s_hat_1[key] = {ref: max(prod1[ref], pmin) for ref in refSigBitNames}
            s_hat_0[key] = {ref: s_hat[key] for ref in refSigBitNames}
            s_hat_1[key] = {ref: s_hat[key] for ref in refSigBitNames}
