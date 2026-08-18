# Softmax CCE 预测结果汇总

本文档汇总 `cce_code/softmax` 下四组指令组合的 CAModel 实测周期和
VfSim 预测周期。

- 运行日期：`2026-08-18`
- 循环参数：`kRows=128`、`kCols=64`
- 周期口径：VF 从开始到完成并包含 drain 的周期数
- 精度公式：`(1 - abs(VfSim - CAModel) / CAModel) * 100%`

## 原二值预留策略

- VfSim 提交：`1230d32`
- SHQ 到 EXQ 策略：`fu_round_robin_exu0_reserve`
- EXU0 预留参数：`lookahead=8`、`min_count=1`
- 每个 EXU 的执行中指令上限：`cap=7`

| 指令组合 | Unroll | CAModel | VfSim | 误差 | 精度 |
|---|---:|---:|---:|---:|---:|
| `vexpdif + vmulscvt` | U1 | 713 | 705 | -8 | 98.88% |
| `vexpdif + vmulscvt` | U2 | 609 | 665 | +56 | 90.80% |
| `vexpdif + vmulscvt` | U4 | 594 | 670 | +76 | 87.21% |
| `vexpdif + vcvt` | U1 | 615 | 675 | +60 | 90.24% |
| `vexpdif + vcvt` | U2 | 622 | 665 | +43 | 93.09% |
| `vexpdif + vcvt` | U4 | 618 | 655 | +37 | 94.01% |
| `vsub + vexp + vmulscvt` | U1 | 701 | 795 | +94 | 86.59% |
| `vsub + vexp + vmulscvt` | U2 | 668 | 717 | +49 | 92.66% |
| `vsub + vexp + vmulscvt` | U4 | 660 | 700 | +40 | 93.94% |
| `vsub + vexp + vcvt` | U1 | 686 | 740 | +54 | 92.13% |
| `vsub + vexp + vcvt` | U2 | 681 | 715 | +34 | 95.01% |
| `vsub + vexp + vcvt` | U4 | 682 | 672 | -10 | 98.53% |

原二值预留策略 12 个 case 的平均预测精度为 **92.76%**。

## 当前平衡预留策略

- 基础提交：`1230d32`
- VfSim 实现提交：`0fe292e`
- 策略标识：`fu_round_robin_exu0_reserve`（平衡预留语义）
- 向后观察窗口：8 条 SHQ 指令，统计其中全部 ready 和未 ready 的
  `EXU0_ONLY` 指令数 `n`
- 分发目标：每次放置 flexible 指令后，尽量使
  `EXQ1 occupancy - EXQ0 occupancy` 接近 `n`
- 其他参数保持不变，包括每个 EXU 的 `cap=7`

| 指令组合 | Unroll | CAModel | VfSim | 误差 | 精度 |
|---|---:|---:|---:|---:|---:|
| `vexpdif + vmulscvt` | U1 | 713 | 660 | -53 | 92.57% |
| `vexpdif + vmulscvt` | U2 | 609 | 628 | +19 | 96.88% |
| `vexpdif + vmulscvt` | U4 | 594 | 630 | +36 | 93.94% |
| `vexpdif + vcvt` | U1 | 615 | 644 | +29 | 95.28% |
| `vexpdif + vcvt` | U2 | 622 | 623 | +1 | 99.84% |
| `vexpdif + vcvt` | U4 | 618 | 620 | +2 | 99.68% |
| `vsub + vexp + vmulscvt` | U1 | 701 | 690 | -11 | 98.43% |
| `vsub + vexp + vmulscvt` | U2 | 668 | 678 | +10 | 98.50% |
| `vsub + vexp + vmulscvt` | U4 | 660 | 681 | +21 | 96.82% |
| `vsub + vexp + vcvt` | U1 | 686 | 679 | -7 | 98.98% |
| `vsub + vexp + vcvt` | U2 | 681 | 669 | -12 | 98.24% |
| `vsub + vexp + vcvt` | U4 | 682 | 674 | -8 | 98.83% |

当前平衡预留策略 12 个 case 的平均预测精度为 **97.33%**，相比原二值预留
策略提高 **4.57 个百分点**。

## 数据集目录映射

| 目录 | 指令组合 |
|---|---|
| `expdif_mulcvt` | `vexpdif + vmulscvt` |
| `expdif_vcvt` | `vexpdif + vcvt` |
| `sub_exp_mulcvt` | `vsub + vexp + vmulscvt` |
| `sub_exp_vcvt` | `vsub + vexp + vcvt` |
