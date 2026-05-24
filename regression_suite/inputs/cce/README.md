# CCE/DSL Inputs

这里用于存放生成 regression ground truth 的 CCE/DSL 源文件。

当前主回归 manifest 主要通过 `cce_vf_end` 和 `cce_vf_end_source` 记录 CCE/camodel 的参考时间。这里已经归档了一批可对应主回归 case 的 DSL 源文件，例如 `GeLU_poly.dsl`、`GeLU.dsl`、`online_update.dsl`、`SiLU/SwiGLU` 以及 `consumer_done/regress_*`。

后续新增或整理 case 时，建议把对应 CCE/DSL 放到本目录，并在 `regression_suite/cases/cost_model_regression_cases.json` 中补充来源说明，方便追溯 ground truth。
