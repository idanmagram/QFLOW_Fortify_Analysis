# REACT: REconvergence-Aware Characterization for Hardware Trojan Detection in Verilog Designs

This repository contains Python tooling for analytical pre-silicon side-channel leakage analysis on Verilog RTL/netlists.

Main capabilities:
- Parse Verilog designs with PyVerilog.
- Build expression/truth-table maps for signals.
- Extract sub-circuits influenced by a reference (secret) signal.
- Estimate signal probabilities and conditional probabilities.
- Compute leakage-oriented metrics and write result artifacts.
- Support sequential/time-unrolled analysis and reconvergence-aware flows.

## Repository Layout

- `run_QFLOW_recon_aware1.py`: reconvergence-aware two-pass flow.
- `module_maps.py`: Verilog parsing, expression/truth-table construction, sub-circuit extraction, time unrolling.
- `generate_z3.py`: AST-to-expression conversion utilities.
- `sig_prob_recon.py`: probability propagation engines.
- `extract_sub_recon_graph.py`, `recon_graph_artifacts.py`: reconvergence subgraph extraction/artifacts.
- `std_cell_lib/`: standard gate/module definitions used by parser flow.
- `results/`: output folders from previous runs.
- `FORTIFY.md`: original project description and academic context.

## Requirements

- Python 3.10+ (3.11 also works).
- [Icarus Verilog](https://steveicarus.github.io/iverilog/) available in `PATH` (scripts also prepend `C:\iverilog\bin` on Windows).
- Python packages:
  - `pyverilog`
  - `z3-solver`
  - `tqdm`

Install packages:

```bash
pip install pyverilog z3-solver tqdm
```

## Quick Start

All flows write outputs under `results/<results-path>/<design>/`.

### 1) Baseline FORTIFY

```bash
python run_fortify.py <input_file> <top_module> <ref_module> <ref_instance> <ref_signal> <ref_width> <design> -r <results_path>
```

Example (AES100 sample in this repo):

```

### 3) Reconvergence-Aware QFLOW

```bash
python run_QFLOW_recon_aware1.py <input_file> <top_module> <ref_module> <ref_instance> <ref_signal> <ref_width> <design> --reconvergence-aware -r <results_path>
```

Optional:
- `--subgraph-path <path>` to reuse a previously extracted reconvergence subgraph.

Example:

```bash
python run_QFLOW_recon_aware1.py AES400.v top top top top.key 128 aes400_recon --reconvergence-aware -r demo
```

## Inputs and Naming

Arguments used by all primary scripts:
- `input_file`: Verilog file path.
- `top_module`: top-level module name.
- `ref_module`: module containing reference signal.
- `ref_instance`: hierarchical instance name containing reference signal (same as top when reference is in top).
- `ref_signal`: fully qualified reference signal base name (without bit index in CLI; script expands bit names).
- `ref_width`: reference signal width in bits.
- `design`: output design tag.

Use module names from each Verilog file. For bundled samples:
- `AES100.v`, `AES400.v`: top module is `top`.
- `RSA100.v`: top module is `top`.

## Output Artifacts


Additional artifacts in some flows:
- `truthTableMap.txt`
- `recon_subgraph_auto.txt`
- `leaky_outputs_auto.txt`

## Notes

- PyVerilog may print parser warnings (including shift/reduce conflicts); these are typically non-fatal.
- The codebase currently relies on module-level global maps in `module_maps.py`. If you add parallelism, prefer levelized/topological batching with merge points to avoid race conditions.

## Reference

- See [`FORTIFY.md`](FORTIFY.md) for detailed project description and publication context.
