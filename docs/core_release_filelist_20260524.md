# Core Release File List 2026-05-24

This document records the intended file scope for the clean core/documentation branch.

## Include

Core simulator and public API:

- `main.py`
- `api/`
- `core/`
- `configs/`

Modeling and user documentation:

- `README.md`
- `VF_modeling.md`
- `api.md`
- `docs/`
- `notes/isa.md`
- `notes/optimization_guide.md`
- `notes/optimization_rules.md`
- `notes/program.md`

Calibration and CCE/camodel companion tools:

- `ascend_runner/`

Optimization and workflow helpers:

- `optimizer/`
- selected `tools/` scripts used by modeling, regression, plotting, calibration, and operator workflow.
- `skills/`

Regression package:

- `regression_suite/`

Selected model inputs:

- selected `VFtest/` JSON traces used by examples and regression.
- selected `cce_code/` CCE/DSL files used by examples, calibration, and regression.

Repository metadata:

- `.gitignore`
- `pyrightconfig.json`

## Exclude

Generated files and local scratch material should not be included in the clean release branch:

- `results/`
- `__pycache__/`
- `*.pyc`
- `.venv/`
- `.idea/`
- `.vscode/`
- `cce_dump/`
- `trash/`
- `allfiles.txt`
- ad-hoc figures and CSVs under `notes/` unless explicitly curated.
- local comparison folders such as `gelu_poly_i16_u1_compare/` and `ipc_compare/`.
- large raw camodel/msprof dumps.

## Notes

- `regression_suite/reports/precision_compare_3modes.md` is a curated report and should be included.
- `regression_suite/inputs/` intentionally contains stable JSON/CCE copies for regression reproducibility.
- If the branch should contain only these files at repository root, create it as a clean/orphan branch rather than a normal branch from existing history.

