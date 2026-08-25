---
name: vf-optimization
description: Workflows for VF cost-model simulation, CCE/VFInfo ingestion, model-vs-CCE comparison, and VF optimization experiments in VfSimulator.
---

# VF Optimization Skill

This skill is the entry point for VF cost-model and optimization work in `VfSimulator`.

The project has two related tracks:

- VF modeling: parse JSON traces or CCE `__VEC_SCOPE__` kernels, run the simulator, inspect timing/resource logs, and compare with CCE/camodel results.
- VF optimization: use the model and CCE logs to reason about unroll, split, fusion, scheduling, and register-pressure choices.

The current mainline simulator defaults to:

- `queue_level4` behavior.
- start-based physical-register release with `consumer start + 4`.
- vreg live-range normalization enabled by default.
- SHQ/EXQ depth, queue delay, EXU inflight limit, issue ports, load/store ports, and related queue parameters configured through `configs/uarch.json`.
- OOO/rename/SHQ logic in `core/ooo.py` and `core/ooo_mainline.py`; EXQ/EXU issue logic in `core/isu.py`.

## Common Commands

Run model from a JSON trace:

```bash
python main.py --trace VFtest/canonical/GeLU_poly.json --out_dir results/tmp_model/gelu_poly
```

Run model from a CCE/DSL file:

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/tmp_model/gelu_poly_cce
```

Select a specific CCE VF kernel when one file has multiple `__VEC_SCOPE__` kernels:

```bash
python main.py --cce cce_code/example.dsl --cce-kernel my_vf_kernel --out_dir results/tmp_model/example
```

Run the two currently kept theoretical-limit candidates:

```bash
python main.py --trace VFtest/canonical/GeLU_poly.json --out_dir results/tmp_model/theory_vloop --theoretical-limit-vloop-only
python main.py --trace VFtest/canonical/GeLU_poly.json --out_dir results/tmp_model/theory_direct --theoretical-limit-vloop-only-legacy-forwarding-direct-issue
```

Run the experimental 3-port mode:

```bash
python main.py --trace VFtest/canonical/GeLU_poly.json --out_dir results/tmp_model/gelu_poly_3ports --three-ports
```

## API Path

The preferred programmatic path is:

1. CCE file or direct Python construction.
2. `InputAPI.load_cce(...)` 或用 `InputAPI.new_builder()` 构造 canonical 输入。
3. `api.simulator_costmodel.CoreVfCostModel.predict_vf_cycles(...)`.
4. Core simulator logs under the selected output directory.

Relevant API files:

- `api/frontend/schema.py`：`CanonicalVfInfo v1` 数据模型。
- `api/cce_adapter.py`: CCE `__VEC_SCOPE__` parser.
- `api/frontend/core_lowering.py`：canonical 到内部 Core payload 的 lowering。
- `api/simulator_costmodel.py`: API wrapper around the current core simulator.

## References

Use these when working on cost-model tasks:

- `references/cost_model_architecture.md`
- `references/cost_model_usage_workflow.md`
- `references/cost_model_cce_e2e_workflow.md`
- `references/cost_model_debug_playbook.md`
- `references/log_field_cheatsheet.md`

Use these when working on configuration or instruction calibration:

- `references/config_acquisition_workflow.md`
- `references/instruction_onboarding.md`
- `references/dispatch_exu_test_plan.md`

Useful helper scripts live under `scripts/`; see `scripts/model_scripts_usage.md`.
