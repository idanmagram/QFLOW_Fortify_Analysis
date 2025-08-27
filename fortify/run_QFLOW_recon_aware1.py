import sys
import argparse
from collections import defaultdict
import module_maps
import os
import sig_prob_recon
from tqdm import tqdm
import time

sys.setrecursionlimit(100000)


def estimate_c_and_pbv_from_conditional_probs(s_hat_0, s_hat_1, s_hat,
                                              refSigBitNames, signalNames):
    channel_C = defaultdict(lambda: defaultdict(float))  # C[h][y]
    joint_J   = defaultdict(lambda: defaultdict(float))  # J[y][h]
    results   = {}

    for sig in signalNames:
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
            results[(sig, ref)] = {'PBV': pbv, 'Leakage': leakage}

    return results


def main(input_file_path, top_module_name, ref_module_name, ref_instance_name,
         ref_sig_name, ref_sig_width, design):
    print("\n ******************************************************************")
    print("Design:", design, "\n")
    os.environ["PATH"] = r"C:\iverilog\bin;" + os.environ["PATH"]

    # reference signal bit names
    refSigBitNames = [f'{ref_sig_name}[{j}:{j}]' for j in range(ref_sig_width)]

    # static analysis → graph + subcircuit
    (inputNames, inputWidths,
     signalNames, sigWidths,
     truthTableMap) = module_maps.subCircuitExtract(
        input_file_path, top_module_name,
        ref_module_name, ref_instance_name,
        refSigBitNames
    )

    # input signal bits names
    inputSigBitNames = []
    for inp, wid in zip(inputNames, inputWidths):
        inputSigBitNames.extend([f'{inp}[{i}:{i}]' for i in range(wid)])

    # === Build depinfo (ancestors / fanout / depth) for reconvergence handling ===
    depinfo = sig_prob_recon.build_dependency_info(truthTableMap, list(set(signalNames) | set(inputNames)))

    # Simple factorized prior over inputs (customize if you have a better prior)
    prior = {name: 0.5 for name in inputSigBitNames}

    # maps to store signal probability and conditional signal probability values
    s_hat   = {}
    s_hat_0 = {}
    s_hat_1 = {}

    # initialise priors for input bits
    for sig in inputSigBitNames:
        s_hat[sig] = prior.get(sig, 0.5)
        s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
        s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}

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

    # signal probability and conditional probability calculation (dependency-aware)
    for sig in tqdm(signalNames, desc="Signal Probability Calculation"):
        if sig not in s_hat:
            sig_prob_recon.populateSigProbs(
                sig, set(), s_hat, s_hat_0, s_hat_1,
                truthTableMap, refSigBitNames, inputSigBitNames,
                depinfo=depinfo, prior_map_or_callable=prior, max_cut=3
            )

    print("s_hat: ",s_hat)
    print("s_hat0: ",s_hat_0)
    print("s_hat1: ", s_hat_1)

    results = estimate_c_and_pbv_from_conditional_probs(
        s_hat_0, s_hat_1, s_hat, refSigBitNames, signalNames
    )
    top_10 = sorted(results.items(), key=lambda x: x[1]['Leakage'], reverse=True)[:10]

    print("\nTop 10 signals with highest leakage:")
    for (sig, ref), metrics in top_10:
        print(f"Signal: {sig}, Leakage: {metrics['Leakage']:.4f}, PBV: {metrics['PBV']:.4f}")

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
    my_parser.add_argument('-r', '--results-path', type=str, action='store',
                           help='name of directory within results/ directory to store results')

    args = my_parser.parse_args()
    start = time.time()
    main(args.InputFilePath, args.TopModuleName,
         args.RefModuleName, args.RefInstanceName,
         args.RefSigName, args.RefSigWidth, args.Design)
    print("Runtime:", time.time() - start, "seconds")
