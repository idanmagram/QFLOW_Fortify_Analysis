# Main script to run the FORTIFY tool

import argparse
from collections import defaultdict
import module_maps
import os
import sig_prob
from tqdm import tqdm


def estimate_c_and_pbv_from_conditional_probs(s_hat_0, s_hat_1, s_hat, refSigBitNames, signalNames):
    channel_C = defaultdict(lambda: defaultdict(float))  # C[h][y]
    joint_J = defaultdict(lambda: defaultdict(float))     # J[y][h]
    results = {}

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

            for y in [0, 1]:
                # Use true prior: assume uniform for ref bits, else use s_hat if available
                prior_0 = s_hat.get(ref, 0.5)
                prior_1 = 1 - prior_0
                joint_J[y][0] = prior_0 * channel_C[0][y]
                joint_J[y][1] = prior_1 * channel_C[1][y]

            pbv = sum(max(joint_J[y][0], joint_J[y][1]) for y in [0, 1])
            leakage = pbv / max(prior_0, prior_1)
            results[(sig, ref)] = {'PBV': pbv, 'Leakage': leakage}

    return results


def main(input_file_path, top_module_name, ref_module_name, ref_instance_name, ref_sig_name, ref_sig_width, design):
    print()
    print(" ******************************************************************")

    print("Design:", design)
    print()
    os.environ["PATH"] = r"C:\iverilog\bin;" + os.environ["PATH"]

    # reference signal bit names
    refSigBitNames = ['{}[{}:{}]'.format(ref_sig_name, j, j) for j in range(ref_sig_width)]

    # performing static analysis to convert into directed graph representation and extracting subcircuit
    inputNames, inputWidths, signalNames, sigWidths, truthTableMap = module_maps.subCircuitExtract(input_file_path, top_module_name, ref_module_name, ref_instance_name, refSigBitNames)

    # input signal bits names
    inputSigBitNames = []
    for inp, wid in zip(inputNames, inputWidths):
        inputSigBitNames.extend(['{}[{}:{}]'.format(inp, i, i) for i in range(wid)])

    # maps to store signal probability and conditional signal probability values
    s_hat = {}
    s_hat_0 = {}
    s_hat_1 = {}


    # initialise leakage scores of input bits
    for sig in inputSigBitNames:
        s_hat[sig] = 0.5
        s_hat_0[sig] = {}
        s_hat_1[sig] = {}
        for ref in refSigBitNames:
            s_hat_0[sig][ref] = 0.5
            s_hat_1[sig][ref] = 0.5

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
                    s_hat_0[sig][ref] = 0
                    s_hat_1[sig][ref] = 1

    # signal probability and consitional signal probability calculation
    for sig in tqdm(signalNames, "Signal Probability Calculation"):
        if not sig in s_hat:
            sig_prob.populateSigProbs(sig, set(), s_hat, s_hat_0, s_hat_1, truthTableMap, refSigBitNames, inputSigBitNames)

    print()
    results = estimate_c_and_pbv_from_conditional_probs(s_hat_0, s_hat_1, s_hat, refSigBitNames, signalNames)
    top_5 = sorted(results.items(), key=lambda x: x[1]['Leakage'], reverse=True)[:10]

    print("\nTop 5 signals with highest leakage:")
    for (sig, ref), metrics in top_5:
        print(f"Signal: {sig}, Secret: {ref}, Leakage: {metrics['Leakage']:.4f}, PBV: {metrics['PBV']:.4f}")

    print()
    print("Completed!")
    print("******************************************************************")
    print()

if __name__ == '__main__':
    # creating the argument parser
    my_parser = argparse.ArgumentParser(description='Pre-silicon power side-channel analysis using FORTIFY')

    # adding the arguments
    my_parser.add_argument('InputFilePath',
                           metavar='input_file_path',
                           type=str,
                           help='path to the input Verilog file to be analyzed')
    my_parser.add_argument('TopModuleName',
                           metavar='top_module_name',
                           type=str,
                           help='name of the top module in the input Verilog file')
    my_parser.add_argument('RefModuleName',
                           metavar='ref_module_name',
                           type=str,
                           help='name of the module in the input Verilog file containing the reference signal')
    my_parser.add_argument('RefInstanceName',
                           metavar='ref_instance_name',
                           type=str,
                           help='name of the instance in the input Verilog file containing the reference signal')
    my_parser.add_argument('RefSigName',
                           metavar='ref_sig_name',
                           type=str,
                           help='name of the reference signal')
    my_parser.add_argument('RefSigWidth',
                           metavar='ref_sig_width',
                           type=int,
                           help='width of the reference signal')
    my_parser.add_argument('Design',
                           metavar='design',
                           type=str,
                           help='name of the design being analysed')
    my_parser.add_argument('-r',
                           '--results-path',
                           type=str,
                           action='store',
                           help='name of directory within results/ directory to store results, default value = current timestamp')

    # parsing the arguments
    args = my_parser.parse_args()

    input_file_path = args.InputFilePath
    top_module_name = args.TopModuleName
    ref_module_name = args.RefModuleName
    ref_instance_name = args.RefInstanceName
    ref_sig_name = args.RefSigName
    ref_sig_width = args.RefSigWidth
    design = args.Design



    main(input_file_path, top_module_name, ref_module_name, ref_instance_name, ref_sig_name, ref_sig_width, design)
