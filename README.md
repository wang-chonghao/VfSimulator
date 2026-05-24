# VF Simulator

VF Simulator is a cycle-level performance model for Ascend-style vector function (VF) code. It predicts VF execution time from either a structured JSON trace or a CCE/DSL file containing `__VEC_SCOPE__` vector code.

The project has two major parts:

1. **VF modeling**: parse VF structure, lower it into simulator instructions, and estimate cycle-level timing.
2. **VF optimization**: explore split/unroll/rewrite strategies using the model as a fast cost oracle.

The current mainline model is focused on VF modeling. Optimization utilities are kept in the repository, but the default simulator path is the queue-level VF model described below.

## Current Default Model

The default simulation path is:

- `queue_level4`
- `consumer release = producer/consumer start + 4`
- `vreg live-range normalization = ON`
- `shq_depth = 58`
- `exq_depth = 26`
- `issue_ports = 2`
- `load_ports = 2`
- `store_ports = 1`
- per-EXU inflight cap from `configs/uarch.json`

The model path is:

```text
JSON/CCE input
  -> api input adapter
  -> flatten
  -> IFU
  -> IDU
  -> OOO rename + SHQ/LSQ
  -> ISU / EXQ
  -> EXU/VLD/VST timing
  -> VF end cycle
```

The default command does not require an explicit model flag. `main.py` always selects the current queue-level mainline unless a theoretical-limit or experimental option is explicitly provided.

## Quick Start

Run a JSON trace:

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/demo_gelu_poly
```

Run a CCE/DSL file:

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/demo_gelu_poly_cce
```

If a CCE file has multiple `__VEC_SCOPE__` kernels, select one explicitly:

```bash
python main.py --cce path/to/file.dsl --cce-kernel kernel_name --out_dir results/demo_kernel
```

Common outputs:

- `start_by_cycle.json`: instruction start events.
- `done_by_cycle.json`: instruction done events.
- `idu_to_ooo.json`: IDU to OOO admission trace.
- `vloop_trace.json`: top-level loop dispatch trace.
- `sim_history.json`: detailed simulation history.
- terminal line `VF end cycle (with drain) = ...`: main timing result.

## Theoretical-Limit Modes

Two theoretical-limit candidates are currently exposed:

```bash
python main.py --trace VFtest/GeLU_poly.json \
  --theoretical-limit-vloop-only \
  --out_dir results/theory_vloop_only
```

```bash
python main.py --trace VFtest/GeLU_poly.json \
  --theoretical-limit-vloop-only-legacy-forwarding-direct-issue \
  --out_dir results/theory_direct_issue
```

The first keeps the main queue-level path while relaxing top-level loop exposure constraints. The second is a more aggressive candidate that uses legacy forwarding and direct single-queue issue for comparison.

## Experimental Three-Port Mode

The repository also contains an experimental three-port VF model:

```bash
python main.py --trace VFtest/GeLU_poly.json --three-ports --out_dir results/demo_three_ports
```

In this mode, compute issue ports and VLD issue capacity are expanded to three, while VST remains single-issue.

## API Interface

The public API lives in `api/`.

Main pieces:

- `api/vf_costmodel.py`: data classes such as `VFInfo`, `InstInfo`, and `MemInfo`.
- `api/cce_adapter.py`: parses `__VEC_SCOPE__` kernels from CCE/DSL files.
- `api/vf_lowering.py`: lowers API-level VF information into simulator trace format.
- `api/input_api.py`: unified loader for JSON and CCE input.
- `api/simulator_costmodel.py`: programmatic cost model wrapper.

Typical programmatic use:

```python
from api.cce_adapter import parse_cce_vf_info
from api.simulator_costmodel import predict_vf_cycles

vf_info = parse_cce_vf_info("cce_code/GeLU_poly.dsl")
cycles = predict_vf_cycles(vf_info)
print(cycles)
```

Direct construction of `VFInfo` is also supported for tests and tools that do not start from CCE code.

## Configuration Files

Core configuration lives in `configs/`:

- `uarch.json`: queue depths, issue widths, delay knobs, inflight caps, and other microarchitecture parameters.
- `isa.json`: per-instruction latency, startup/drain cost, execution unit type, and dispatch restriction.
- `forwarding.json`: producer-consumer forwarding timing.
- `InitiationInterval.json`: instruction pair initiation interval table.

Important ISA dispatch markers:

- `EXU0_ONLY`: instruction can only execute on EXU0.
- `EXU01`: instruction can execute on EXU0 or EXU1.
- `EXU012`: used by experimental three-port mode.

## Directory Layout

Core directories intended for normal development and release branches:

```text
api/                 Public input/API adapter layer
ascend_runner/       CCE/camodel build, run, and calibration helpers
cce_code/            CCE/DSL examples and selected regression/optimization sources
configs/             uarch, ISA, forwarding, and II configuration
core/                Main simulator implementation
docs/                Architecture notes and modeling documentation
notes/               Curated working notes used by optimization/modeling flows
optimizer/           VF optimization and split/unroll search tools
regression_suite/    Regression package: cases, inputs, reports, docs
skills/              Codex skill docs/scripts for VF optimization workflow
tools/               Utility scripts for reports, plots, calibration, and experiments
VFtest/              JSON trace examples and selected regression inputs
```

Top-level documentation:

- `VF_modeling.md`: detailed VF modeling design.
- `README.md`: current project entry point.
- `api.md`: API design notes.

Generated outputs and temporary material should normally stay out of release commits:

- `results/`
- `__pycache__/`
- large `msprof`/camodel dump folders
- ad-hoc figures and scratch files unless intentionally curated

## Regression Suite

The regression package is now organized as:

```text
regression_suite/
  cases/
    cost_model_regression_cases.json
    baseline_consumer_done.json
    archive/
  inputs/
    json/
    cce/
  reports/
    precision_compare_3modes.md
  docs/
    unroll_precision_debug_guide.md
```

Run smoke regression:

```bash
python tools/run_cost_model_regression.py --tier smoke
```

Run full regression:

```bash
python tools/run_cost_model_regression.py --tier full
```

Update baseline intentionally:

```bash
python tools/run_cost_model_regression.py --tier smoke --update-baseline
```

The run output is written under `results/regression_suite/latest/` by default. Stable, curated reports belong under `regression_suite/reports/`.

## Ascend Runner

`ascend_runner/` is the CCE/camodel companion toolchain. It is used to:

- compile CCE/DSL cases through `ccec` and `ld.lld`;
- run native simulator executables with `runtime_camodel`;
- collect camodel logs such as instruction start/done and EXU traces;
- calibrate `isa.json`, `forwarding.json`, and `InitiationInterval.json`.

Current mainline scripts are in `ascend_runner/current/`. Historical debug and legacy scripts remain in `ascend_runner/debug/` and `ascend_runner/legacy/`.

## Development Notes

Recommended sanity checks after simulator changes:

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/sanity_gelu
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/sanity_gelu_cce
python tools/run_cost_model_regression.py --tier smoke
```

When preparing a clean release branch, prefer committing source, curated docs, selected regression inputs, and selected tools only. Avoid committing generated caches, temporary run logs, and large raw dumps.

