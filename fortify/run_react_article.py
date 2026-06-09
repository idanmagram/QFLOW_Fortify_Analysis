import math
import sys
import argparse
import random
import module_maps
import os
import sig_prob_recon
import sig_prob_recon_t
import sig_prob_recon_article
from tqdm import tqdm
import time
from datetime import datetime
from extract_sub_recon_graph import extract_sub_recon_graph, extract_leaky_outputs
from recon_graph_artifacts import build_recon_graph_artifacts

UNROLL_DEPTH = 32


sys.setrecursionlimit(100000)


def _get_cached_uniform(cache, key):
    if key not in cache:
        cache[key] = random.choice([0.0, 1.0])
    return cache[key]


def estimate_c_and_pbv_from_conditional_probs(
    s_hat_0,
    s_hat_1,
    s_hat,
    refSigBitNames,
    signalNames,
    target_signals=None,
):
    per_output_results = {}
    max_per_secret_results = {}

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

            p1_if_0 = s_hat_0[sig][ref]
            p1_if_1 = s_hat_1[sig][ref]

            p_secret_1 = 0.5
            p_secret_0 = 1 - p_secret_1

            p_s0_h0 = p_secret_0 * (1 - p1_if_0)
            p_s0_h1 = p_secret_1 * (1 - p1_if_1)
            p_s1_h0 = p_secret_0 * p1_if_0
            p_s1_h1 = p_secret_1 * p1_if_1

            prior = max(p_secret_0, p_secret_1)

            best_if_s0 = max(p_s0_h0, p_s0_h1)
            best_if_s1 = max(p_s1_h0, p_s1_h1)
            pbv = best_if_s0 + best_if_s1
            leakage_pbv = pbv / prior

            per_output_results[(sig, ref)] = {
                "PBV": pbv,
                "Leakage_PBV": leakage_pbv,
                "prior": prior,
            }

    for ref in refSigBitNames:
        ref_metrics = [
            (sig, metrics)
            for (sig, metric_ref), metrics in per_output_results.items()
            if metric_ref == ref
        ]
        if not ref_metrics:
            continue

        max_sig, max_metrics = max(
            ref_metrics,
            key=lambda item: item[1]["Leakage_PBV"],
        )
        max_per_secret_results[ref] = {
            "Leakage_PBV": max_metrics["Leakage_PBV"],
            "PBV": max_metrics["PBV"],
            "prior": max_metrics["prior"],
            "argmax_output": max_sig,
            "num_outputs": len(ref_metrics),
            "summary": "max_per_output_leakage",
        }

    return {
        "per_output": per_output_results,
        "max_per_secret": max_per_secret_results,
    }


def _base_name(sig_name):
    return sig_name.split("@")[0] if isinstance(sig_name, str) else sig_name


def _aggregate_per_output_rows(per_output_results, score_key="Leakage_PBV"):
    aggregated = {}
    for (sig, ref), metrics in per_output_results.items():
        base_sig = _base_name(sig)
        base_ref = _base_name(ref)
        row = {
            "signal": base_sig,
            "ref": base_ref,
            "actual_signal": sig,
            "actual_ref": ref,
            "PBV": metrics.get("PBV", 0.0),
            "Leakage_PBV": metrics.get("Leakage_PBV", 0.0),
            "prior": metrics.get("prior", 0.0),
        }
        key = (base_sig, base_ref)
        if key not in aggregated or row[score_key] > aggregated[key][score_key]:
            aggregated[key] = row
    return aggregated


def _snapshot_output_signal_probabilities(s_hat, outputSigBitNames):
    aggregated = {}
    for sig in outputSigBitNames:
        if sig not in s_hat:
            continue
        base_sig = _base_name(sig)
        row = {
            "signal": base_sig,
            "actual_signal": sig,
            "probability": float(s_hat[sig]),
        }
        if (
            base_sig not in aggregated
            or row["probability"] > aggregated[base_sig]["probability"]
        ):
            aggregated[base_sig] = row
    return aggregated


def _snapshot_output_conditional_probabilities(
    s_hat_0, s_hat_1, outputSigBitNames, refSigBitNames
):
    aggregated = {}
    for sig in outputSigBitNames:
        if sig not in s_hat_0 or sig not in s_hat_1:
            continue
        for ref in refSigBitNames:
            if ref not in s_hat_0[sig] or ref not in s_hat_1[sig]:
                continue
            base_sig = _base_name(sig)
            base_ref = _base_name(ref)
            row = {
                "signal": base_sig,
                "ref": base_ref,
                "actual_signal": sig,
                "actual_ref": ref,
                "p_if_ref0": float(s_hat_0[sig][ref]),
                "p_if_ref1": float(s_hat_1[sig][ref]),
            }
            key = (base_sig, base_ref)
            score = max(row["p_if_ref0"], row["p_if_ref1"])
            if (
                key not in aggregated
                or score > max(
                    aggregated[key]["p_if_ref0"], aggregated[key]["p_if_ref1"]
                )
            ):
                aggregated[key] = row
    return aggregated


def _write_probability_comparison_csv(path, before_probs, after_probs):
    keys = sorted(set(before_probs.keys()) | set(after_probs.keys()))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("signal,before_probability,after_probability,delta\n")
        for key in keys:
            before_val = before_probs.get(key, {}).get("probability", 0.0)
            after_val = after_probs.get(key, {}).get("probability", 0.0)
            handle.write(
                f"{key},{before_val:.15f},{after_val:.15f},{(after_val - before_val):.15f}\n"
            )


def _write_conditional_probability_csv(path, before_cond, after_cond):
    keys = sorted(set(before_cond.keys()) | set(after_cond.keys()))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "signal,ref,before_p_if_ref0,after_p_if_ref0,delta_ref0,"
            "before_p_if_ref1,after_p_if_ref1,delta_ref1\n"
        )
        for signal, ref in keys:
            before_row = before_cond.get((signal, ref), {})
            after_row = after_cond.get((signal, ref), {})
            before_0 = before_row.get("p_if_ref0", 0.0)
            after_0 = after_row.get("p_if_ref0", 0.0)
            before_1 = before_row.get("p_if_ref1", 0.0)
            after_1 = after_row.get("p_if_ref1", 0.0)
            handle.write(
                f"{signal},{ref},{before_0:.15f},{after_0:.15f},{(after_0 - before_0):.15f},"
                f"{before_1:.15f},{after_1:.15f},{(after_1 - before_1):.15f}\n"
            )


def _write_leakage_comparison_csv(path, before_rows, after_rows):
    keys = sorted(set(before_rows.keys()) | set(after_rows.keys()))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("signal,ref,before_leakage,after_leakage,delta,before_pbv,after_pbv\n")
        for signal, ref in keys:
            before_row = before_rows.get((signal, ref), {})
            after_row = after_rows.get((signal, ref), {})
            before_leakage = before_row.get("Leakage_PBV", 0.0)
            after_leakage = after_row.get("Leakage_PBV", 0.0)
            before_pbv = before_row.get("PBV", 0.0)
            after_pbv = after_row.get("PBV", 0.0)
            handle.write(
                f"{signal},{ref},{before_leakage:.15f},{after_leakage:.15f},"
                f"{(after_leakage - before_leakage):.15f},{before_pbv:.15f},{after_pbv:.15f}\n"
            )


def _plot_before_after_bars(
    path,
    title,
    labels,
    before_values,
    after_values,
    ylabel,
):
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"Skipping graph generation for {path}: matplotlib unavailable ({exc})")
        return

    if not labels:
        print(f"Skipping graph generation for {path}: no data")
        return

    positions = list(range(len(labels)))
    width = 0.38

    fig_width = max(12, len(labels) * 0.7)
    fig, ax = plt.subplots(figsize=(fig_width, 6))
    ax.bar([p - width / 2 for p in positions], before_values, width=width, label="Without reconvergence")
    ax.bar([p + width / 2 for p in positions], after_values, width=width, label="With reconvergence")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def _generate_before_after_graphs(
    results_dir,
    before_signal_probs,
    after_signal_probs,
    before_conditional_probs,
    after_conditional_probs,
    before_leakage_rows,
    after_leakage_rows,
    top_n=20,
):
    _write_probability_comparison_csv(
        os.path.join(results_dir, "signal_probability_comparison.csv"),
        before_signal_probs,
        after_signal_probs,
    )
    _write_conditional_probability_csv(
        os.path.join(results_dir, "conditional_probability_comparison.csv"),
        before_conditional_probs,
        after_conditional_probs,
    )
    _write_leakage_comparison_csv(
        os.path.join(results_dir, "leakage_comparison.csv"),
        before_leakage_rows,
        after_leakage_rows,
    )

    prob_rows = []
    for signal in sorted(set(before_signal_probs.keys()) | set(after_signal_probs.keys())):
        before_val = before_signal_probs.get(signal, {}).get("probability", 0.0)
        after_val = after_signal_probs.get(signal, {}).get("probability", 0.0)
        prob_rows.append((signal, before_val, after_val, abs(after_val - before_val)))
    prob_rows.sort(key=lambda row: row[3], reverse=True)
    prob_rows = prob_rows[:top_n]
    _plot_before_after_bars(
        os.path.join(results_dir, "signal_probability_before_after.png"),
        "Output signal probability before vs after reconvergence",
        [row[0] for row in prob_rows],
        [row[1] for row in prob_rows],
        [row[2] for row in prob_rows],
        "P(signal=1)",
    )

    conditional_rows = []
    for key in sorted(set(before_conditional_probs.keys()) | set(after_conditional_probs.keys())):
        before_row = before_conditional_probs.get(key, {})
        after_row = after_conditional_probs.get(key, {})
        before_0 = before_row.get("p_if_ref0", 0.0)
        after_0 = after_row.get("p_if_ref0", 0.0)
        before_1 = before_row.get("p_if_ref1", 0.0)
        after_1 = after_row.get("p_if_ref1", 0.0)
        conditional_rows.append(
            (
                f"{key[0]} | {key[1]} | ref=0",
                before_0,
                after_0,
                abs(after_0 - before_0),
            )
        )
        conditional_rows.append(
            (
                f"{key[0]} | {key[1]} | ref=1",
                before_1,
                after_1,
                abs(after_1 - before_1),
            )
        )
    conditional_rows.sort(key=lambda row: row[3], reverse=True)
    conditional_rows = conditional_rows[:top_n]
    _plot_before_after_bars(
        os.path.join(results_dir, "conditional_probability_before_after.png"),
        "Conditional probabilities before vs after reconvergence",
        [row[0] for row in conditional_rows],
        [row[1] for row in conditional_rows],
        [row[2] for row in conditional_rows],
        "P(signal=1 | ref=value)",
    )

    leakage_rows = []
    for key in sorted(set(before_leakage_rows.keys()) | set(after_leakage_rows.keys())):
        before_val = before_leakage_rows.get(key, {}).get("Leakage_PBV", 0.0)
        after_val = after_leakage_rows.get(key, {}).get("Leakage_PBV", 0.0)
        leakage_rows.append(
            (f"{key[0]} | {key[1]}", before_val, after_val, abs(after_val - before_val))
        )
    leakage_rows.sort(key=lambda row: row[3], reverse=True)
    leakage_rows = leakage_rows[:top_n]
    _plot_before_after_bars(
        os.path.join(results_dir, "leakage_before_after.png"),
        "Leakage before vs after reconvergence",
        [row[0] for row in leakage_rows],
        [row[1] for row in leakage_rows],
        [row[2] for row in leakage_rows],
        "Leakage_PBV",
    )


def main(
    input_file_path,
    top_module_name,
    ref_module_name,
    ref_instance_name,
    ref_sig_name,
    ref_sig_width,
    design,
    leaks_file_path,
    time_file_path,
    reconvergence_aware=False,
    subgraph_path=None,
    reconvergence_algorithm="article",
    max_shared_ancestors=4,
):
    startTime = time.time()

    print("\n ******************************************************************")
    print("Design:", design, "\n")
    os.environ["PATH"] = r"C:\iverilog\bin;" + os.environ["PATH"]

    inputNames, inputWidths, signalNames, sigWidths, truthTableMap = (
        module_maps.subCircuitExtract(
            input_file_path,
            top_module_name,
            ref_module_name,
            ref_instance_name,
            [f"{ref_sig_name}[{j}:{j}]" for j in range(ref_sig_width)],
        )
    )
    truthTableMap, signalNames_unrolled = module_maps.build_time_unrolled_truth_table(
        truthTableMap, UNROLL_DEPTH
    )

    refSigBitNames = []
    for j in range(ref_sig_width):
        refSigBitNames.append(f"{ref_sig_name}[{j}:{j}]")
    signalNames = set(signalNames_unrolled) | set(refSigBitNames) | set(
        truthTableMap.keys()
    )

    inputSigBitNames = []
    for inp, wid in zip(inputNames, inputWidths):
        inputSigBitNames.extend([f"{inp}[{i}:{i}]" for i in range(wid)])

    graph_artifacts = build_recon_graph_artifacts(signalNames, truthTableMap)
    input_prior_cache = {}

    def _init_prob_tables_second_pass(s_hat, s_hat_0, s_hat_1):
        for sig in inputSigBitNames:
            prior = 0.5
            if "state" in sig:
                prior = _get_cached_uniform(input_prior_cache, sig)
            s_hat[sig] = prior
            s_hat_0[sig] = {ref: prior for ref in refSigBitNames}
            s_hat_1[sig] = {ref: prior for ref in refSigBitNames}
            if "rst" in sig.lower():
                s_hat[sig] = 0.0
                s_hat_0[sig] = {ref: 0.0 for ref in refSigBitNames}
                s_hat_1[sig] = {ref: 0.0 for ref in refSigBitNames}

        for sig in signalNames:
            if sig in refSigBitNames:
                s_hat[sig] = 0.5
                s_hat_0[sig] = {}
                s_hat_1[sig] = {}
                for ref in refSigBitNames:
                    conditional_prior = 0.5
                    s_hat_0[sig][ref] = conditional_prior
                    s_hat_1[sig][ref] = conditional_prior
                    if ref == sig:
                        s_hat_0[sig][ref] = 0.0
                        s_hat_1[sig][ref] = 1.0
        return s_hat, s_hat_0, s_hat_1

    def _init_prob_tables():
        s_hat = {}
        s_hat_0 = {}
        s_hat_1 = {}

        for sig in inputSigBitNames:
            prior = 0.5
            if "state" in sig:
                prior = _get_cached_uniform(input_prior_cache, sig)
            s_hat[sig] = prior
            s_hat_0[sig] = {ref: prior for ref in refSigBitNames}
            s_hat_1[sig] = {ref: prior for ref in refSigBitNames}
            if "rst" in sig.lower():
                s_hat[sig] = 0.0
                s_hat_0[sig] = {ref: 0.0 for ref in refSigBitNames}
                s_hat_1[sig] = {ref: 0.0 for ref in refSigBitNames}

        for sig in signalNames:
            if sig in refSigBitNames:
                s_hat[sig] = 0.5
                s_hat_0[sig] = {}
                s_hat_1[sig] = {}
                for ref in refSigBitNames:
                    conditional_prior = 0.5
                    s_hat_0[sig][ref] = conditional_prior
                    s_hat_1[sig][ref] = conditional_prior
                    if ref == sig:
                        s_hat_0[sig][ref] = 0.0
                        s_hat_1[sig][ref] = 1.0
        return s_hat, s_hat_0, s_hat_1

    s_hat, s_hat_0, s_hat_1 = _init_prob_tables()
    first_pass_results = None
    before_signal_probs = None
    before_conditional_probs = None

    outputSigBitNames = []
    if top_module_name in module_maps.moduleOutputPortListMap:
        outs = module_maps.moduleOutputPortListMap[top_module_name]
        outs_w = module_maps.moduleOutputPortWidthListMap[top_module_name]
        for oname, width in zip(outs, outs_w):
            for i in range(width):
                outputSigBitNames.append(f"{top_module_name}.{oname}[{i}:{i}]")
                for t in range(UNROLL_DEPTH + 1):
                    outputSigBitNames.append(f"{top_module_name}.{oname}[{i}:{i}]@{t}")

    def _print_top_150_leakage(per_output_results, title):
        aggregated = {}
        for (sig, ref), metrics in per_output_results.items():
            base_sig = sig.split("@")[0]
            base_ref = ref.split("@")[0]
            key = (base_sig, base_ref)
            if key not in aggregated or metrics["Leakage_PBV"] > aggregated[key]["Leakage_PBV"]:
                aggregated[key] = metrics

        top_150 = sorted(
            aggregated.items(),
            key=lambda x: x[1]["Leakage_PBV"],
            reverse=True,
        )[:150]
        print(f"\n{title}\n")
        for (sig, ref), metrics in top_150:
            print(
                f"Signal: {sig}, Ref: {ref}, "
                f"Leakage_PBV: {metrics['Leakage_PBV']:.25f}"
            )

    def _print_lmax_summary(max_per_secret_results, title):
        print(f"\n{title}\n")
        sorted_max_per_secret = sorted(
            (
                (ref, metrics)
                for ref, metrics in max_per_secret_results.items()
                if metrics["Leakage_PBV"] > 1.0
            ),
            key=lambda item: item[1]["Leakage_PBV"],
            reverse=True,
        )
        for ref, metrics in sorted_max_per_secret:
            print(
                f"Ref: {ref}, L_max: {metrics['Leakage_PBV']:.25f}, "
                f"argmax_output: {metrics['argmax_output']}"
            )

    if reconvergence_aware:
        print("Reconvergance aware calculation")
        recon_populate_fn = sig_prob_recon_article.populateSigProbs_recon_article
        if reconvergence_algorithm == "dp":
            recon_populate_fn = sig_prob_recon.populateSigProbs_recon_dp
        elif reconvergence_algorithm == "t":
            recon_populate_fn = sig_prob_recon_t.populateSigProbs_recon_t

        loaded_subgraph = None
        if subgraph_path:
            try:
                with open(subgraph_path, "r") as f:
                    loaded_subgraph = {line.strip() for line in f if line.strip()}
                print(f"Loaded Reconvergence subgraph: {len(loaded_subgraph)} nodes")
            except Exception as exc:
                print(f"Failed to read subgraph file {subgraph_path}: {exc}")
                loaded_subgraph = None

        if loaded_subgraph is None:
            print("Pass 1 - leakage before reconvergence-focused subgraph recomputation")
            recon_populate_fn(
                signalNames,
                s_hat,
                s_hat_0,
                s_hat_1,
                truthTableMap,
                refSigBitNames,
                inputSigBitNames,
                sigWidths,
                graph_artifacts=graph_artifacts,
                max_shared_ancestors=max_shared_ancestors,
            )
            first_pass_results = estimate_c_and_pbv_from_conditional_probs(
                s_hat_0,
                s_hat_1,
                s_hat,
                refSigBitNames,
                signalNames,
                target_signals=outputSigBitNames,
            )
            _print_top_150_leakage(
                first_pass_results["per_output"],
                "Top 150 signals with highest leakage before reconvergence refinement:",
            )
            _print_lmax_summary(
                first_pass_results["max_per_secret"],
                "L_max(H) across all selected outputs before reconvergence refinement:",
            )
            before_signal_probs = _snapshot_output_signal_probabilities(
                s_hat, outputSigBitNames
            )
            before_conditional_probs = _snapshot_output_conditional_probabilities(
                s_hat_0, s_hat_1, outputSigBitNames, refSigBitNames
            )
            pass1Time = time.time()
            print("Total time taken first pass: {:.4f}s".format(pass1Time - startTime))

            leaky_outputs = extract_leaky_outputs(
                first_pass_results["per_output"], leakage_threshold=1.0000088
            )
            recon_only_set = extract_sub_recon_graph(
                truth_table_map=truthTableMap,
                ref_sig_bit_names=refSigBitNames,
                signal_names=signalNames,
                results=first_pass_results["per_output"],
                leaky_outputs=leaky_outputs,
                leakage_threshold=1.0000088,
                unroll_depth=UNROLL_DEPTH,
            )
            print(f"First-pass leaky outputs: {len(leaky_outputs)}")
            print(f"Extracted Reconvergence subgraph: {len(recon_only_set)} nodes")

            results_dir = os.path.dirname(leaks_file_path)
            leaky_outputs_path = os.path.join(results_dir, "leaky_outputs_auto.txt")
            with open(leaky_outputs_path, "w") as f:
                for sig in sorted(leaky_outputs):
                    f.write(f"{sig}\n")
            print(f"Saved first-pass leaky outputs to: {leaky_outputs_path}")
        else:
            recon_only_set = loaded_subgraph

        results_dir = os.path.dirname(leaks_file_path)
        auto_subgraph_path = os.path.join(results_dir, "recon_subgraph_auto.txt")
        with open(auto_subgraph_path, "w") as f:
            for sig in sorted(recon_only_set):
                f.write(f"{sig}\n")
        print(f"Saved Reconvergence subgraph to: {auto_subgraph_path}")

        print("Pass 2 - leakage after reconvergence-focused subgraph recomputation")
        for sig in recon_only_set:
            s_hat.pop(sig, None)
            s_hat_0.pop(sig, None)
            s_hat_1.pop(sig, None)
        _init_prob_tables_second_pass(s_hat, s_hat_0, s_hat_1)
        recon_populate_fn(
            signalNames,
            s_hat,
            s_hat_0,
            s_hat_1,
            truthTableMap,
            refSigBitNames,
            inputSigBitNames,
            sigWidths,
            recon_only_set=recon_only_set,
            graph_artifacts=graph_artifacts,
            max_shared_ancestors=max_shared_ancestors,
        )
    else:
        for _sig in tqdm(signalNames, desc="Signal Probability Calculation"):
            if _sig not in s_hat:
                sig_prob_recon.populateSigProbs_recon_dp(
                    signalNames,
                    s_hat,
                    s_hat_0,
                    s_hat_1,
                    truthTableMap,
                    refSigBitNames,
                    inputSigBitNames,
                    sigWidths,
                    graph_artifacts=graph_artifacts,
                    max_shared_ancestors=max_shared_ancestors,
                )

    print("finished calc")

    results = estimate_c_and_pbv_from_conditional_probs(
        s_hat_0,
        s_hat_1,
        s_hat,
        refSigBitNames,
        signalNames,
        target_signals=outputSigBitNames,
    )
    per_output_results = results["per_output"]
    max_per_secret_results = results["max_per_secret"]

    _print_top_150_leakage(
        per_output_results,
        "Top 150 signals with highest leakage after reconvergence refinement:",
    )

    results_dir = os.path.dirname(leaks_file_path)
    if first_pass_results is not None:
        print("Saving probability and leakage comparison graphs")
        after_signal_probs = _snapshot_output_signal_probabilities(s_hat, outputSigBitNames)
        after_conditional_probs = _snapshot_output_conditional_probabilities(
            s_hat_0, s_hat_1, outputSigBitNames, refSigBitNames
        )
        before_leakage_rows = _aggregate_per_output_rows(first_pass_results["per_output"])
        after_leakage_rows = _aggregate_per_output_rows(per_output_results)
        _generate_before_after_graphs(
            results_dir,
            before_signal_probs,
            after_signal_probs,
            before_conditional_probs,
            after_conditional_probs,
            before_leakage_rows,
            after_leakage_rows,
        )
    else:
        print("Skipping probability/leakage comparison graphs: no first-pass baseline was computed")

    _print_lmax_summary(
        max_per_secret_results,
        "L_max(H) across all selected outputs after reconvergence refinement:",
    )

    endTime = time.time()
    print()
    print("Total time taken: {:.4f}s".format(endTime - startTime))
    print("\nCompleted!")
    print("******************************************************************\n")


if __name__ == "__main__":
    my_parser = argparse.ArgumentParser(
        description="Pre-silicon power side-channel analysis using FORTIFY (article reconvergence variant)"
    )

    my_parser.add_argument("InputFilePath", metavar="input_file_path", type=str)
    my_parser.add_argument("TopModuleName", metavar="top_module_name", type=str)
    my_parser.add_argument("RefModuleName", metavar="ref_module_name", type=str)
    my_parser.add_argument("RefInstanceName", metavar="ref_instance_name", type=str)
    my_parser.add_argument("RefSigName", metavar="ref_sig_name", type=str)
    my_parser.add_argument("RefSigWidth", metavar="ref_sig_width", type=int)
    my_parser.add_argument("Design", metavar="design", type=str)
    my_parser.add_argument(
        "--reconvergence-aware",
        action="store_true",
        help="enable reconvergence cone evaluation",
    )
    my_parser.add_argument(
        "--reconvergence-algorithm",
        type=str,
        default="article",
        choices=["article", "dp", "t"],
        help="reconvergence algorithm to use with --reconvergence-aware",
    )
    my_parser.add_argument(
        "--subgraph-path",
        type=str,
        action="store",
        help="path to subgraph nodes (one per line) to limit reconvergence",
    )
    my_parser.add_argument(
        "--max-shared-ancestors",
        type=int,
        default=4,
        help="maximum number of shared ancestors to condition on during reconvergence",
    )
    my_parser.add_argument(
        "-r",
        "--results-path",
        type=str,
        action="store",
        help="name of directory within results/ directory to store results",
    )

    args = my_parser.parse_args()

    start = time.time()
    if args.results_path:
        results_path = "results/" + args.results_path + "/" + args.Design + "/"
    else:
        results_path = (
            "results/"
            + datetime.today().strftime("%Y-%m-%d-%H:%M:%S")
            + "/"
            + args.Design
            + "/"
        )

    if not os.path.isdir(results_path):
        os.makedirs(results_path)

    leaks_file_path = "{}/leaks.txt".format(results_path)
    time_file_path = "{}/time.txt".format(results_path)
    main(
        args.InputFilePath,
        args.TopModuleName,
        args.RefModuleName,
        args.RefInstanceName,
        args.RefSigName,
        args.RefSigWidth,
        args.Design,
        leaks_file_path,
        time_file_path,
        reconvergence_aware=args.reconvergence_aware,
        subgraph_path=args.subgraph_path,
        reconvergence_algorithm=args.reconvergence_algorithm,
        max_shared_ancestors=args.max_shared_ancestors,
    )
    print("Runtime:", time.time() - start, "seconds")
