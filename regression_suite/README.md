# Cost Model 回归测试集

这个目录是 VfSimulator 的回归测试包，用来维护稳定输入、基线和精度报告。

主要用途：

1. cost model 精度回归：同一 trace 在模型里的 `VF end cycle` 是否漂移。
2. CCE/camodel 精度守门：模型相对 CCE/camodel 的误差是否变差。
3. 稳定报告归档：保存人工整理过的精度对比表，避免散落在 `results/` 中。

## 目录结构

```text
regression_suite/
  README.md
  cases/
    cost_model_regression_cases.json
    baseline_queue_level4_ooo_transfer_delay.json
    baseline_consumer_done.json
    archive/
      ...
  inputs/
    json/
      ...
    cce/
      README.md
  reports/
    precision_compare_3modes.md
  docs/
    unroll_precision_debug_guide.md
```

## 文件说明

- `cases/cost_model_regression_cases.json`：主测试集清单，包含 case id、trace、参数、CCE/camodel 参考时间和容忍阈值。
- `cases/baseline_queue_level4_ooo_transfer_delay.json`：主线默认基线，对应 `precision_compare_3modes.md` 的 `queue_level4+ooo-transfer-delay` 列。
- `cases/baseline_consumer_done.json`：历史 consumer-done 基线，仅用于结果追溯。
- `cases/archive/`：历史 queue-level 对比实验的 case/baseline，保留供追溯，不作为主线默认入口。
- `inputs/json/`：主回归 manifest 使用的 JSON trace 副本，使回归包能自包含地复现输入。
- `inputs/cce/`：用于存放生成 CCE/camodel ground truth 的 CCE/DSL 文件；当前部分 ground truth 仅记录为 `cce_vf_end_source`。
- `reports/precision_compare_3modes.md`：稳定精度汇总报告。
- `docs/unroll_precision_debug_guide.md`：unroll 精度排查指南。

## 一键命令

基线来源于 `precision_compare_3modes.md` 的
`queue_level4+ooo-transfer-delay` 列。确认需要整体刷新时运行：

```bash
python tools/run_cost_model_regression.py --tier full --update-baseline
```

之后每次改模型后做回归对比：

```bash
python tools/run_cost_model_regression.py --tier smoke
```

跑全量：

```bash
python tools/run_cost_model_regression.py --tier full
```

如果需要显式指定路径：

```bash
python tools/run_cost_model_regression.py \
  --suite regression_suite/cases/cost_model_regression_cases.json \
  --baseline regression_suite/cases/baseline_queue_level4_ooo_transfer_delay.json \
  --tier smoke
```

## 输出位置

自动运行产生的临时结果仍放在 `results/`，不放进 `regression_suite/`：

- 运行目录：`results/regression_suite/latest/`
- 当前结果：`results/regression_suite/latest/current_metrics.json`
- 对比结果：`results/regression_suite/latest/compare_summary.json`

## 通过/失败规则

每个 case 用 `primary_metric` 对比基线，若满足以下任一条件即判定通过：

- 绝对误差 `abs_diff <= abs_tol`
- 相对误差 `rel_diff <= rel_tol`

默认阈值在 `cases/cost_model_regression_cases.json` 的 `defaults` 里，个别 case 可单独覆盖。

## CCE 基准精度守门

测试项可选填写 `cce_vf_end`，表示 CCE/camodel 的 VF 总时间。脚本会自动计算：

- `error_to_cce_abs`
- `error_to_cce_rel`

并在回归时检查：当前版本相对 CCE/camodel 的误差，不能比 `queue_level4+ooo-transfer-delay` 基线变差太多。

默认阈值在 `cases/cost_model_regression_cases.json` 的 `defaults`：

- `cce_error_abs_worse_tol`
- `cce_error_rel_worse_tol`
