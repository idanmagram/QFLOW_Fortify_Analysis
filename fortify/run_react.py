import math
import sys
import argparse
from collections import defaultdict
import module_maps
import os
import sig_prob_recon
from tqdm import tqdm
import time
from datetime import datetime
from extract_sub_recon_graph import extract_sub_recon_graph, extract_leaky_outputs
from recon_graph_artifacts import build_recon_graph_artifacts
UNROLL_DEPTH = 32



sys.setrecursionlimit(100000)

def estimate_c_and_pbv_from_conditional_probs(s_hat_0, s_hat_1, s_hat,
                                              refSigBitNames, signalNames,
                                              target_signals=None):

    channel_C = defaultdict(lambda: defaultdict(float))
    joint_J   = defaultdict(lambda: defaultdict(float))
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

            # channel probabilities
            channel_C[0][1] = p_y1_h0
            channel_C[0][0] = 1 - p_y1_h0
            channel_C[1][1] = p_y1_h1
            channel_C[1][0] = 1 - p_y1_h1

            prior_0 = s_hat.get(ref, 0.5)
            prior_1 = 1 - prior_0

            # joint distribution
            for y in [0,1]:
                joint_J[y][0] = prior_0 * channel_C[0][y]
                joint_J[y][1] = prior_1 * channel_C[1][y]

            # -----------------------------
            # PBV (what you already compute)
            # -----------------------------

            pbv = sum(max(joint_J[y][0], joint_J[y][1]) for y in [0,1])
            leakage_pbv = pbv / max(prior_0, prior_1)

            # -----------------------------
            # Posterior probabilities
            # -----------------------------

            denom1 = p_y1_h0 + p_y1_h1
            denom0 = (1 - p_y1_h0) + (1 - p_y1_h1)

            if denom1 > 0:
                p_s1_y1 = p_y1_h1 / denom1
            else:
                p_s1_y1 = 0.5

            if denom0 > 0:
                p_s1_y0 = (1 - p_y1_h1) / denom0
            else:
                p_s1_y0 = 0.5

            # -----------------------------
            # Posterior gap (strong simple metric)
            # -----------------------------

            posterior_gap = abs(p_s1_y1 - p_s1_y0)

            # -----------------------------
            # Mutual information
            # -----------------------------

            def h(x):
                if x <= 0 or x >= 1:
                    return 0
                return -(x*math.log2(x) + (1-x)*math.log2(1-x))

            q = prior_0*p_y1_h0 + prior_1*p_y1_h1

            mutual_info = h(q) - prior_0*h(p_y1_h0) - prior_1*h(p_y1_h1)

            # -----------------------------
            # Likelihood ratio (rare-event sensitive)
            # -----------------------------

            eps = 1e-15
            lr1 = (p_y1_h1 + eps) / (p_y1_h0 + eps)
            lr0 = ((1-p_y1_h1) + eps) / ((1-p_y1_h0) + eps)

            log_lr_gap = abs(math.log(lr1) - math.log(lr0))
            log_lr_gap = math.tanh(log_lr_gap / 2)

            # -----------------------------
            # Log-odds gap of posteriors
            # -----------------------------

            def logit(x):
                x = min(max(x, 1e-12), 1-1e-12)
                return math.log(x/(1-x))

            logit_gap = abs(logit(p_s1_y1) - logit(p_s1_y0))

            results[(sig,ref)] = {
                'PBV': pbv,
                'Leakage_PBV': leakage_pbv,
                'Posterior_gap': posterior_gap,
                'Logit_gap': logit_gap,
                'Log_LR_gap': log_lr_gap,
                'Mutual_information': mutual_info,
                'Posterior_Y1': p_s1_y1,
                'Posterior_Y0': p_s1_y0,
                'prior': max(prior_0, prior_1)
            }

    return results

def estimate_c_and_pbv_from_conditional_probs1(s_hat_0, s_hat_1, s_hat,
                                              refSigBitNames, signalNames, target_signals=None):
    channel_C = defaultdict(lambda: defaultdict(float))  # C[h][y]
    joint_J   = defaultdict(lambda: defaultdict(float))  # J[y][h]
    results   = {}
    signals_to_check = target_signals if target_signals is not None else signalNames
    #print("signalNames", signalNames)

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
            print("p_y1_h0 ",p_y1_h0," for sig ",sig)
            print("p_y1_h1 ",p_y1_h1," for sig ",sig)

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
         reconvergence_aware=False, subgraph_path=None):
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
    # time-unroll for looped signals
    truthTableMap, signalNames_unrolled = module_maps.build_time_unrolled_truth_table(truthTableMap, UNROLL_DEPTH)

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
        inputSigBitNames.extend([f'{inp}[{i}:{i}]' for i in range(wid)])

    graph_artifacts = build_recon_graph_artifacts(signalNames, truthTableMap)

    def _init_prob_tables_second_pass(s_hat, s_hat_0, s_hat_1):

        # initialise priors for input bits
        for sig in inputSigBitNames:
            base = sig.split("@")[0]
            s_hat[sig] = 0.5
            s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
            s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
            if "rst" in sig:
                s_hat[sig] = 0.1
                s_hat_0[sig] = {ref: 0.1 for ref in refSigBitNames}
                s_hat_1[sig] = {ref: 0.1 for ref in refSigBitNames}

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
        return s_hat, s_hat_0, s_hat_1


    def _init_prob_tables():
        s_hat = {}
        s_hat_0 = {}
        s_hat_1 = {}

        # initialise priors for input bits
        for sig in inputSigBitNames:
            base = sig.split("@")[0]
            s_hat[sig] = 0.5
            s_hat_0[sig] = {ref: 0.5 for ref in refSigBitNames}
            s_hat_1[sig] = {ref: 0.5 for ref in refSigBitNames}
            if "rst" in sig:
                s_hat[sig] = 0.1
                s_hat_0[sig] = {ref: 0.1 for ref in refSigBitNames}
                s_hat_1[sig] = {ref: 0.1 for ref in refSigBitNames}

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
        return s_hat, s_hat_0, s_hat_1

    s_hat, s_hat_0, s_hat_1 = _init_prob_tables()


    done = 0

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

    if reconvergence_aware:
        print("Reconvergance aware calculation")
        loaded_subgraph = None
        if subgraph_path:
            try:
                with open(subgraph_path, "r") as f:
                    loaded_subgraph = {line.strip() for line in f if line.strip()}
                print(f"Loaded Reconvergence subgraph: {len(loaded_subgraph)} nodes")
            except Exception as e:
                print(f"Failed to read subgraph file {subgraph_path}: {e}")
                loaded_subgraph = None

        if loaded_subgraph is None:
            print("Pass 1")
            # Pass 1: full graph probabilities to identify leaky outputs.
            sig_prob_recon.populateSigProbs_recon_dp(
                signalNames, s_hat, s_hat_0, s_hat_1,
                truthTableMap, refSigBitNames, inputSigBitNames, sigWidths,
                graph_artifacts=graph_artifacts
            )
            first_pass_results = estimate_c_and_pbv_from_conditional_probs(
                s_hat_0, s_hat_1, s_hat, refSigBitNames, signalNames,
                target_signals=outputSigBitNames)

            leaky_outputs = extract_leaky_outputs(first_pass_results, leakage_threshold=1.0)
            recon_only_set = extract_sub_recon_graph(
                truth_table_map=truthTableMap,
                ref_sig_bit_names=refSigBitNames,
                signal_names=signalNames,
                results=first_pass_results,
                leaky_outputs=leaky_outputs,
                leakage_threshold=1.0,
                unroll_depth=UNROLL_DEPTH,
            )
            print(f"First-pass leaky outputs: {len(leaky_outputs)}")
            print(f"Extracted Reconvergence subgraph: {len(recon_only_set)} nodes")

            results_dir = os.path.dirname(leaks_file_path)
            leaky_outputs_path = os.path.join(results_dir, "leaky_outputs_auto.txt")
            with open(leaky_outputs_path, "w") as f:
                for s in sorted(leaky_outputs):
                    f.write(f"{s}\n")
            print(f"Saved first-pass leaky outputs to: {leaky_outputs_path}")
        else:
            recon_only_set = loaded_subgraph

        # Optional artifact to reuse in future runs.
        results_dir = os.path.dirname(leaks_file_path)
        auto_subgraph_path = os.path.join(results_dir, "recon_subgraph_auto.txt")
        with open(auto_subgraph_path, "w") as f:
            for s in sorted(recon_only_set):
                f.write(f"{s}\n")
        print(f"Saved Reconvergence subgraph to: {auto_subgraph_path}")

        # Pass 2: recompute only the recon subgraph and keep Pass-1 values outside it.
        # This avoids recalculating unaffected nodes.
        print("Pass 2")
        for sig in recon_only_set:
            s_hat.pop(sig, None)
            s_hat_0.pop(sig, None)
            s_hat_1.pop(sig, None)
        _init_prob_tables_second_pass(s_hat, s_hat_0, s_hat_1)
        sig_prob_recon.populateSigProbs_recon_dp(
            signalNames, s_hat, s_hat_0, s_hat_1,
            truthTableMap, refSigBitNames, inputSigBitNames, sigWidths,
            recon_only_set=recon_only_set, graph_artifacts=graph_artifacts
        )
    else:
        for sig in tqdm(signalNames, desc="Signal Probability Calculation"):
            if sig not in s_hat:
                sig_prob_recon.populateSigProbs_recon_dp(
                    signalNames, s_hat, s_hat_0, s_hat_1,
                    truthTableMap, refSigBitNames, inputSigBitNames, sigWidths,
                    graph_artifacts=graph_artifacts)
            done += 1
    print("finished calc")

    with open("s_hat.txt", "w") as f:
        print("s_hat", s_hat, file=f)

    #with open("s_hat_0.txt", "w") as f:
    #    print("s_hat_0", s_hat_0, file=f)

    #with open("s_hat_1.txt", "w") as f:
    #    print("s_hat_1", s_hat_1, file=f)

        # print("s_hat0: ",s_hat_0)
        # print("s_hat1: ", s_hat_1)

    results = estimate_c_and_pbv_from_conditional_probs(
        s_hat_0, s_hat_1, s_hat, refSigBitNames, signalNames, target_signals=outputSigBitNames)
    # aggregate per base signal/ref (max over time slices)

    '''
    top_150 = sorted(results.items(), key=lambda x: x[1]['Leakage'], reverse=True)[:500]

    print("\nTop 150 signals with highest leakage: not aggregated")
    for (sig, ref), metrics in top_150:
        print(f"Signal: {sig}, Ref: {ref}, "f"Leakage: {metrics['Leakage']:.15f}, PBV: {metrics['PBV']:.15f}")
    '''

    aggregated = {}
    for (sig, ref), metrics in results.items():
        base_sig = sig.split("@")[0]
        base_ref = ref.split("@")[0]
        key = (base_sig, base_ref)
        if key not in aggregated or metrics['Leakage_PBV'] > aggregated[key]['Leakage_PBV']:
            aggregated[key] = metrics

    top_150 = sorted(aggregated.items(),key=lambda x: x[1]['Leakage_PBV'],reverse=True)[:150]
    print("\nTop 150 signals with highest leakage:\n")
    for (sig, ref), metrics in top_150:
        print(
            f"Signal: {sig}, Ref: {ref}, "
            f"Log_LR_gap: {metrics['Log_LR_gap']:.15f}, "
            f"Leakage_PBV: {metrics['Leakage_PBV']:.15f}")
    print()
    endTime = time.time()
    print("Total time taken: {:.4f}s".format(endTime - startTime))
    print("\nCompleted!")
    print("******************************************************************\n")
    return


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
    my_parser.add_argument('--subgraph-path', type=str, action='store',
                           help='path to subgraph nodes (one per line) to limit reconvergence')
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
         reconvergence_aware=args.reconvergence_aware, subgraph_path=args.subgraph_path)
    print("Runtime:", time.time() - start, "seconds")
