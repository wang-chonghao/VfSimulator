# Single-Op Minimal Param Suite

This suite is a minimal test set for `configs/isa.json` single-op params.

Goal:
- Build one case per ISA op.
- Each case follows `VLD -> OP -> VST`.
- Measure and compare:
  - `pipeline_startup_cost`
  - `latency`
  - `pipeline_drain_cost`
  - `data_load_cost`
  - `data_store_cost`

## Directory Layout

- `generate_cases.py`
  Generates all minimal DSL files into `cases/`.
- `cases/singleop_*.dsl`
  One-op kernels (`repeat_times=1`) for clean timing extraction.
- `run_suite.sh`
  Batch build + run all cases through current native simexec flow.
- `extract_single_op_params.py`
  Parses dumps under `/home/lenovo/msprof_run/*_native_simexec` and compares against `configs/isa.json`.

## Covered ISA Ops

- `VADDS`
- `VEXP`
- `VADD`
- `VMULS`
- `VDIV`
- `VABS`
- `VSUB`
- `VMUL`
- `VMAXS`
- `VMINS`
- `VMAX`
- `VMIN`

## Usage

1. Generate cases (optional, `run_suite.sh` auto-generates if missing):

```bash
python3 ascend_runner/single_op_param_suite/generate_cases.py
```

2. Run all cases:

```bash
bash ascend_runner/single_op_param_suite/run_suite.sh
```

3. Extract and compare params:

```bash
python3 ascend_runner/single_op_param_suite/extract_single_op_params.py \
  --msprof-root /home/lenovo/msprof_run \
  --isa-json configs/isa.json \
  --csv-out results/single_op_param_suite_compare.csv
```

The parser prints per-case metric comparison and optional CSV.
