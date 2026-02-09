import sys
import argparse
from collections import defaultdict
import module_maps
import os
import sig_prob
import sig_prob_recon
from tqdm import tqdm
import time
from datetime import datetime
UNROLL_DEPTH = 32



sys.setrecursionlimit(100000)


def estimate_c_and_pbv_from_conditional_probs(s_hat_0, s_hat_1, s_hat,
                                              refSigBitNames, signalNames, target_signals=None):
    channel_C = defaultdict(lambda: defaultdict(float))  # C[h][y]
    joint_J   = defaultdict(lambda: defaultdict(float))  # J[y][h]
    results   = {}
    signals_to_check = target_signals if target_signals is not None else signalNames

    for sig in signals_to_check:
        if not isinstance(sig, str):
            continue
        if sig in refSigBitNames:
            continue
        for ref in refSigBitNames:
            if sig not in s_hat_0 or sig not in s_hat_1:
                continue
            if ref not in s_hat_0[sig] or ref not in s_hat_1[sig]:
                continue

            p_y1_h0 = s_hat_0[sig][ref]
            p_y1_h1 = s_hat_1[sig][ref]

            channel_C[0][1] = p_y1_h0
            channel_C[0][0] = 1 - p_y1_h0
            channel_C[1][1] = p_y1_h1
            channel_C[1][0] = 1 - p_y1_h1

            # prior for the ref bit (use s_hat if available; else 0.5)
            prior_0 = s_hat.get(ref, 0.5)
            prior_1 = 1 - prior_0

            for y in [0, 1]:
                joint_J[y][0] = prior_0 * channel_C[0][y]
                joint_J[y][1] = prior_1 * channel_C[1][y]

            pbv = sum(max(joint_J[y][0], joint_J[y][1]) for y in [0, 1])
            leakage = pbv / max(prior_0, prior_1)
            results[(sig, ref)] = {'PBV': pbv, 'Leakage': leakage, 'prior': max(prior_0, prior_1)}
            #print()
            #print('PBV', pbv, 'Leakage', leakage)

    return results

def main(input_file_path, top_module_name, ref_module_name, ref_instance_name,
         ref_sig_name, ref_sig_width, design, leaks_file_path, time_file_path,
         reconvergence_aware=False):
    startTime = time.time()

    print("\n ******************************************************************")
    print("Design:", design, "\n")
    os.environ["PATH"] = r"C:\iverilog\bin;" + os.environ["PATH"]

    # static analysis → graph + subcircuit
    (inputNames, inputWidths,
     signalNames, sigWidths,
     truthTableMap) = module_maps.subCircuitExtract(
        input_file_path, top_module_name,
        ref_module_name, ref_instance_name,
        [f'{ref_sig_name}[{j}:{j}]' for j in range(ref_sig_width)]
    )
    # time-unroll looped signals to depth UNROLL_DEPTH
    truthTableMap, signalNames_unrolled = module_maps.build_time_unrolled_truth_table(truthTableMap, H=UNROLL_DEPTH)

    # time-index reference bits (treated as looped secrets)
    refSigBitNames = []

    for j in range(ref_sig_width):
        refSigBitNames.append(f'{ref_sig_name}[{j}:{j}]')
    signalNames = set(signalNames_unrolled) | set(refSigBitNames)

    with open("truthTableMap.txt", "w") as f:
        print("truthTableMap 1", truthTableMap, file=f)

    # input signal bits names (time-indexed to match unrolled map)
    inputSigBitNames = []
    for inp, wid in zip(inputNames, inputWidths):
        for t in range(UNROLL_DEPTH + 1):
            inputSigBitNames.extend([f'{inp}[{i}:{i}]@{t}' for i in range(wid)])

    prior = {name: 0.5 for name in inputSigBitNames}

    s_hat = {}
    s_hat_0 = {}
    s_hat_1 = {}

    # initialise priors for input bits
    for sig in inputSigBitNames:
        base = sig.split("@")[0]
        s_hat[sig] = prior.get(base, 0.5)
        s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
        if "rst" in sig:
            s_hat[sig] = prior.get(base, 0.5)
            s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
            s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
    # print("signalNames ",signalNames)
    # initialise leakage scores of reference signal bits
    for sig in signalNames:
        if sig in refSigBitNames:
            s_hat[sig] = 0.5
            s_hat_0[sig] = {}
            s_hat_1[sig] = {}
            for ref in refSigBitNames:
                s_hat_0[sig][ref] = 0.5
                s_hat_1[sig][ref] = 0.5
                if ref == sig:
                    s_hat_0[sig][ref] = 0.0
                    s_hat_1[sig][ref] = 1.0


    done = 0

    if reconvergence_aware:
        print("reconnnnnn")
        sig_prob_recon.populateSigProbs_recon_dp(
            signalNames, s_hat, s_hat_0, s_hat_1,
            truthTableMap, refSigBitNames, inputSigBitNames
        )
    else:
        for sig in tqdm(signalNames, desc="Signal Probability Calculation"):
            if sig not in s_hat:
                sig_prob.populateSigProbs(
                    sig, set(), s_hat, s_hat_0, s_hat_1,
                    truthTableMap, refSigBitNames, inputSigBitNames)
        done += 1
    print("finished calc")
    #print("s_hat: ",s_hat)
    #print("s_hat0: ",s_hat_0)
    #print("s_hat1: ", s_hat_1)

    # print("s_hat: ",s_hat)
    with open("s_hat.txt", "w") as f:
        print("s_hat", s_hat, file=f)

    with open("s_hat_0.txt", "w") as f:
        print("s_hat_0", s_hat_0, file=f)

    with open("s_hat_1.txt", "w") as f:
        print("s_hat_1", s_hat_1, file=f)

        # print("s_hat0: ",s_hat_0)
        # print("s_hat1: ", s_hat_1)

        # build target signals: top-level outputs (out bits + Antena + others)
    outputSigBitNames = []
    if top_module_name in module_maps.moduleOutputPortListMap:
        outs = module_maps.moduleOutputPortListMap[top_module_name]
        outs_w = module_maps.moduleOutputPortWidthListMap[top_module_name]
        for oname, w in zip(outs, outs_w):
            for i in range(w):
                outputSigBitNames.append(f"{top_module_name}.{oname}[{i}:{i}]")
                for t in range(UNROLL_DEPTH + 1):
                    outputSigBitNames.append(f"{top_module_name}.{oname}[{i}:{i}]@{t}")

    print("outputSigBitNames ", outputSigBitNames)
    results = estimate_c_and_pbv_from_conditional_probs(
        s_hat_0, s_hat_1, s_hat, refSigBitNames, signalNames, target_signals=outputSigBitNames
    )
    # aggregate per base signal/ref (max over time slices)
    aggregated = {}
    for (sig, ref), metrics in results.items():
        base_sig = sig.split("@")[0]
        base_ref = ref.split("@")[0]
        key = (base_sig, base_ref)
        if key not in aggregated or metrics['Leakage'] > aggregated[key]['Leakage']:
            aggregated[key] = metrics

    top_10 = sorted(aggregated.items(), key=lambda x: x[1]['Leakage'], reverse=True)[:500]

    print("\nTop 10 signals with highest leakage:")
    for (sig, ref), metrics in top_10:
        print(f"Signal: {sig}, Ref: {ref}, "f"Leakage: {metrics['Leakage']:.15f}, PBV: {metrics['PBV']:.15f}")

    sigLeaks = {}
    sigLeaks_ext = {}

    print()
    import math
    baseLeak = 1.0 / math.sqrt(ref_sig_width)

    # leakage score calculation
    for sig in tqdm(sigWidths, desc="Leakage calculation"):
        width = sigWidths[sig]
        leakages = []
        flag = 1
        for j in range(width):
            sigName = '{}[{}:{}]'.format(sig, j, j)
            if sigName in signalNames:
                leakVal = 0
                for ref in refSigBitNames:
                    leak = (s_hat_0[sigName][ref] - s_hat_1[sigName][ref]) ** 2
                    if leak > 0:
                        y_bar = 2 * s_hat[sigName] * (1 - s_hat[sigName])
                        denom = 4 * y_bar * (1 - y_bar)
                        if denom != 0:
                            leak = leak / math.sqrt(denom)
                    leakVal += leak
                leakages.append(leakVal ** 2)
                sigLeaks_ext[sigName] = leakVal

            else:
                flag = 0
                break
        if flag:
            sigLeaks[sig] = math.sqrt(sum(leakages)) * baseLeak
            # leakage score may exceed 1 due to approximation of above leakage formula
            if sigLeaks[sig] > 1:
                sigLeaks[sig] = 1

    print()
    endTime = time.time()

    print("Number of signals: {}".format(len(sigLeaks)))
    print("Total time taken: {:.4f}s".format(endTime - startTime))

    print("\nCompleted!")
    print("******************************************************************\n")


if __name__ == '__main__':
    # creating the argument parser
    my_parser = argparse.ArgumentParser(
        description='Pre-silicon power side-channel analysis using FORTIFY'
    )

    my_parser.add_argument('InputFilePath',   metavar='input_file_path',   type=str)
    my_parser.add_argument('TopModuleName',   metavar='top_module_name',   type=str)
    my_parser.add_argument('RefModuleName',   metavar='ref_module_name',   type=str)
    my_parser.add_argument('RefInstanceName', metavar='ref_instance_name', type=str)
    my_parser.add_argument('RefSigName',      metavar='ref_sig_name',      type=str)
    my_parser.add_argument('RefSigWidth',     metavar='ref_sig_width',     type=int)
    my_parser.add_argument('Design',          metavar='design',            type=str)
    my_parser.add_argument('--reconvergence-aware', action='store_true',
                           help='enable reconvergence cone DP (collapse cones once resolved)')
    my_parser.add_argument('-r', '--results-path', type=str, action='store',
                           help='name of directory within results/ directory to store results')

    # parsing the arguments
    args = my_parser.parse_args()

    input_file_path = args.InputFilePath
    top_module_name = args.TopModuleName
    ref_module_name = args.RefModuleName
    ref_instance_name = args.RefInstanceName
    ref_sig_name = args.RefSigName
    ref_sig_width = args.RefSigWidth
    design = args.Design

    start = time.time()
    results_path = args.results_path
    if results_path:
        results_path = 'results/' + results_path + '/' + design + '/'
    else:
        results_path = 'results/' + datetime.today().strftime('%Y-%m-%d-%H:%M:%S') + '/' + design + '/'

    if not os.path.isdir(results_path):
        os.makedirs(results_path)

    leaks_file_path = '{}/leaks.txt'.format(results_path)
    time_file_path = '{}/time.txt'.format(results_path)
    main(args.InputFilePath, args.TopModuleName,
         args.RefModuleName, args.RefInstanceName,
         args.RefSigName, args.RefSigWidth, args.Design, leaks_file_path, time_file_path,
         reconvergence_aware=args.reconvergence_aware)
    print("Runtime:", time.time() - start, "seconds")
