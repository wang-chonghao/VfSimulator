# VF Cost Model Usage Workflow

This workflow is for running the current VfSimulator model and collecting comparable outputs.

## 1. Run One JSON Trace

```bash
python main.py ^
  --trace VFtest/GeLU_poly.json ^
  --out_dir results/tmp_model/gelu_poly_json
```

Useful outputs:

- `start_by_cycle.json`
- `done_by_cycle.json`
- `idu_to_ooo.json`
- console line `VF end cycle (with drain) = ...`

The default run already uses the current mainline model:

- queue-level4 behavior.
- consumer release at `consumer start + 4`.
- vreg live-range normalization enabled.
- queue depths/delays and EXU limits from `configs/uarch.json`.

Do not pass old options such as `--ooo-model consumer-done`; the public CLI no longer uses them.

## 2. Run One CCE/DSL File

```bash
python main.py ^
  --cce cce_code/GeLU_poly.dsl ^
  --out_dir results/tmp_model/gelu_poly_cce
```

If the file has more than one `__VEC_SCOPE__` kernel:

```bash
python main.py ^
  --cce cce_code/example.dsl ^
  --cce-kernel my_vf_kernel ^
  --out_dir results/tmp_model/example_cce
```

The CCE path parses source into `VFInfo`, lowers it into the internal payload, and then runs the same core simulator as the JSON path.

## 3. Run Theoretical-Limit Candidates

Use these only when explicitly studying theoretical limits:

```bash
python main.py ^
  --trace VFtest/GeLU_poly.json ^
  --out_dir results/tmp_model/gelu_poly_theory_vloop ^
  --theoretical-limit-vloop-only
```

```bash
python main.py ^
  --trace VFtest/GeLU_poly.json ^
  --out_dir results/tmp_model/gelu_poly_theory_direct ^
  --theoretical-limit-vloop-only-legacy-forwarding-direct-issue
```

Do not use old generic `--theoretical-limit`; it is no longer the active interface.

## 4. Run Experimental Three-Port Mode

```bash
python main.py ^
  --trace VFtest/GeLU_poly.json ^
  --out_dir results/tmp_model/gelu_poly_3ports ^
  --three-ports
```

`--three-ports` changes runtime uarch settings:

- `issue_ports = 3`
- `load_ports = 3`
- `store_ports = 1`
- `EXU01` ops may use EXU0/EXU1/EXU2.
- `EXU0_ONLY` ops remain restricted to EXU0.

## 5. Use Skill Wrapper Scripts

One case:

```bash
python skills/vf-optimization/scripts/run_cost_model_once.py ^
  --trace VFtest/GeLU_poly.json ^
  --out-dir results/tmp_model_once/gelu_i64 ^
  --set-param I=64
```

Batch cases:

```bash
python skills/vf-optimization/scripts/run_cost_model_batch.py ^
  --manifest regression_suite/cases/cost_model_regression_cases.json ^
  --out-dir results/tmp_model_batch
```

The wrapper scripts are convenience helpers around `main.py`. Prefer direct `main.py` commands when debugging CLI behavior.

## 6. Compare Against CCE/Camodel

When CCE/camodel timing is already recorded in a case manifest, compare model and CCE timing with:

```bash
python skills/vf-optimization/scripts/compare_model_cce.py ^
  --summary results/tmp_model_batch/summary.json ^
  --cases regression_suite/cases/cost_model_regression_cases.json ^
  --out-csv results/tmp_model_batch/compare_model_cce.csv
```

Comparison uses:

- `model_vf_end`: model `VF end cycle (with drain)`.
- `cce_vf_end` or equivalent manifest field: CCE/camodel VF total time.
- absolute and relative error.

## 7. Low-Confidence Checks

Pay attention to `model_warnings.json` and console warnings. A common warning is an expanded vreg namespace exceeding the physical register count, especially for large unroll factors. Such cases may still run, but the prediction should be treated as lower confidence.
