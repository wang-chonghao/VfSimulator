# 优化策略迭代计划（optimize_plan）

本文档用于统一当前 cost model 优化器的下一阶段改进方向、术语定义和落地顺序。

## 1. 目标

- 提升“是否值得优化”的判断质量，减少无效搜索。
- 将 `unroll` 纳入优化主流程，避免只靠切分。
- 解决不合理切分（如极短尾块）。
- 引入可配置的 UB 压力策略（strict / unrestricted / permissive）。
- 探索“不切 loop 的指令重排”并与切分策略联合。

## 2. 关键术语定义

### 2.1 precheck 中的 baseline（重点）

precheck 的 `baseline` 定义为：

- 不做任何优化（不切分、不重排）
- 使用同一份 trace、同一组参数（`I`、`U`、`ooo-model`）
- 直接运行 `main.py` 得到的 `VF end cycle`

注意：这不是 `regression_suite/baseline_consumer_done.json` 的“历史版本基线”。  
precheck baseline 是“当前算子当前配置的原始时间”。

### 2.2 theoretical-limit

- 使用 `--theoretical-limit` 模式得到的上界估计时间（当前口径继续叫“理论极限”）。

### 2.3 headroom

用于判断优化空间：

```text
headroom = (baseline_vf_end - theoretical_vf_end) / baseline_vf_end
```

建议默认阈值：

- `headroom < 0.10`：判定优化空间有限，可跳过复杂优化。

## 3. 总体流程（新框架）

## Stage A: precheck

输入：trace + 参数（I/U/ooo-model）  
输出：`baseline_vf_end`、`theoretical_vf_end`、`headroom`

策略：

1. 跑 baseline
2. 跑 theoretical-limit
3. 计算 headroom
4. 低于阈值则早停（可配置）

## Stage B: unroll-first 搜索

候选集合：`U in {1,2,4,8}`（可扩展）  
每个 U：

1. 改写 trace 参数 `U`
2. 跑 baseline 时间
3. 记录 `vf_end`

选择：

- 选最优 `U_best`，并保留前 1~2 个候选进入后续阶段。
- 若 `U_best` 已接近 theoretical-limit（例如达到 90%+），可直接结束，不做 split。

## Stage C: reorder-only（新增）

目标：不切 loop，仅在 VF scope 内做依赖合法的局部重排。

方法：

- 基于 DAG 依赖 + EXU/II/forwarding 约束做 list scheduling
- 不改变语义，不改变块边界

产物：

- `reorder_only_trace`
- `reorder_only_vf_end`

## Stage D: split-only（当前主线，增强）

目标：保持当前 split 框架，重点修复“短尾块”和 UB 策略。

新增约束（默认打开）：

1. 末块最小规模（min tail size）
2. 每块最小深度跨度（min depth span）
3. 每块最小占比（min block ratio）
4. 若末块过短，启用“自动与前块合并再评估”

## Stage E: split + reorder（联合）

目标：切分后每个子块内再做重排，取联合最优。

建议先在“前面阶段收益不足但 headroom 仍明显”时触发，避免全量开销。

## 4. UB 压力策略（3 档）

## 4.1 strict

- 不允许新增额外中间 UB 槽。
- 仅允许复用输入空间和已有中间结果空间。
- 目标：对硬件/工程约束最保守，保证可落地性。

## 4.2 unrestricted

- 允许新增中间槽。
- 仅约束总峰值 `<= 248KB`。
- 在满足约束前提下，尽量复用已有槽（减少峰值和碎片）。

## 4.3 permissive

- 预算：`min(248KB, alpha * input_bytes)`，默认 `alpha=2`。
- 比 unrestricted 更灵活，便于探索。

实现建议：

- 统一“slot 生命周期分配器（含覆写复用）”
- 三种模式仅通过预算和禁用/启用规则切换

## 5. 目标函数建议（统一评分）

主目标：

- 最小化 `vf_end_cycle`

正则项（可配置）：

- 切分惩罚（已有）
- 短尾块惩罚（新增）
- UB 峰值惩罚（在 permissive/unrestricted 下可作为次目标）

可写成：

```text
score = vf_end
      + lambda_cut * cut_penalty
      + lambda_tail * tiny_tail_penalty
      + lambda_ub * ub_pressure_penalty
```

## 6. 实施优先级（建议）

## P0（先做）

1. precheck + headroom 早停
2. unroll-first 搜索并并入主流程

原因：收益大、改动风险小、可快速减少无效优化。

## P1

1. 短尾块约束 + 合并回退机制
2. UB 三档策略与 slot 复用分配器

## P2

1. reorder-only
2. split + reorder 联合

## 7. 结果输出规范（每次优化都要有）

- baseline_vf_end
- theoretical_vf_end
- headroom
- unroll sweep 表（每个 U 的 vf_end）
- 最终策略类型（unroll-only / reorder-only / split-only / split+reorder）
- 最终 vf_end 与提升比例
- UB 峰值与策略档位
- 切分结果（cuts、块大小分布、是否触发短尾修正）

## 8. 回归与验收

新增优化逻辑必须通过：

1. `regression_suite` 冒烟回归（模型稳定性）
2. CCE 对齐误差守门（已支持 `cce_vf_end`）
3. 关键算子（GeLU_poly / GeLU / online_update）至少不劣化

---

当前建议从 P0 开始逐条落地，先把“值不值得优化”和“unroll 是否已足够”这两个决策做正确，再进入更重的切分/联合搜索。
