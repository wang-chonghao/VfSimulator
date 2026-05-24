# VF Cost Model Helper Scripts

These scripts are convenience wrappers around the current `main.py` interface. For tricky debugging, prefer calling `main.py` directly first.

## 1. Run One Case

Script:

```bash
python skills/vf-optimization/scripts/run_cost_model_once.py ^
  --trace VFtest/GeLU_poly.json ^
  --out-dir results/tmp_model_once/gelu_i64 ^
  --set-param I=64
```

CCE input is also supported:

```bash
python skills/vf-optimization/scripts/run_cost_model_once.py ^
  --cce cce_code/GeLU_poly.dsl ^
  --out-dir results/tmp_model_once/gelu_poly_cce
```

If multiple CCE VF kernels exist:

```bash
python skills/vf-optimization/scripts/run_cost_model_once.py ^
  --cce cce_code/example.dsl ^
  --cce-kernel selected_kernel ^
  --out-dir results/tmp_model_once/example
```

Useful options:

- `--trace`: JSON trace input.
- `--cce`: CCE/DSL input.
- `--cce-kernel`: selects a CCE `__VEC_SCOPE__` kernel when needed.
- `--out-dir`: output directory.
- `--set-param K=V`: override JSON trace `params`; this applies to `--trace` input only.
- `--theoretical-limit-vloop-only`: run the vloop-only theoretical-limit candidate.
- `--theoretical-limit-vloop-only-legacy-forwarding-direct-issue`: run the direct-issue theoretical-limit candidate.
- `--three-ports`: run the experimental 3-port model.

Outputs:

- `result.json`
- `model_stdout.log`
- `model/` containing normal simulator logs.

## 2. Run Batch Cases

Script:

```bash
python skills/vf-optimization/scripts/run_cost_model_batch.py ^
  --manifest regression_suite/cases/cost_model_regression_cases.json ^
  --out-dir results/tmp_model_batch
```

Manifest format:

```json
{
  "defaults": {
    "theoretical_limit": "",
    "three_ports": false
  },
  "cases": [
    {
      "id": "gelu_poly_i16",
      "trace": "VFtest/GeLU_poly.json",
      "params": { "I": 16 }
    },
    {
      "id": "gelu_poly_cce",
      "cce": "cce_code/GeLU_poly.dsl",
      "cce_kernel": "optional_kernel_name"
    }
  ]
}
```

`theoretical_limit` may be:

- empty or omitted: normal mainline model.
- `vloop-only`
- `vloop-only-legacy-forwarding-direct-issue`

Outputs:

- `summary.json`
- `summary.csv`
- per-case subdirectories containing `trace_input.json` when JSON was used, `model_stdout.log`, and `model/` logs.

## 3. Compare Model With CCE/Camodel

Script:

```bash
python skills/vf-optimization/scripts/compare_model_cce.py ^
  --summary results/tmp_model_batch/summary.json ^
  --cases regression_suite/cases/cost_model_regression_cases.json ^
  --out-csv results/tmp_model_batch/compare_model_cce.csv
```

The comparison script expects the case metadata to contain CCE/camodel reference timing. It reports absolute and relative model error.

## 4. Calibration Scripts

The following scripts are for extracting or checking model parameters from CCE/camodel evidence:

- `calibrate_isa.py`
- `calibrate_forwarding.py`
- `calibrate_ii.py`
- `check_dispatch_exu.py`
- `summarize_config_coverage.py`

Use them when updating `configs/isa.json`, `configs/forwarding.json`, `configs/InitiationInterval.json`, or EXU dispatch constraints.
