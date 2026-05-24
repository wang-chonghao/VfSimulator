# 优化策略迭代计划 v1（开发版）

本文档定义当前阶段（开发期）统一执行的 VF 优化流程、策略选项和输出规范。

## 1. 目标

- 先用低成本步骤判断优化空间，减少无效搜索。
- 将 `unroll` 作为优先优化手段，而不是直接做 loop 切分。
- 在 `unroll` 无法逼近上限时，再进入 loop 内重排/切分优化。
- 为后续 CCE 验证提供清晰的候选集合和可追溯产物。

## 2. 核心流程（v1）

## Stage A: 理论极限预估

1. 运行 `--theoretical-limit`。
2. 得到 `theoretical_vf_end`（性能上限参考）。

## Stage B: Unroll 优先搜索

### 2.1 搜索范围

- 仅搜索 `unroll < 6` 的情况（当前模型在大 unroll 下误差偏大）。
- `unroll` 候选不限定为 `1/2/4`，而是取 loop bound 的因子并与 `<6` 取交集。
- 示例：
  - loop bound = 48 -> 扫 `1,2,3,4`
  - loop bound = 30 -> 扫 `1,2,3,5`（若工程上暂时不支持 `2/3`，需在产物中注明）

### 2.2 评估与判定

1. 对每个候选 `U`，仅改写 trace 中 `U` 参数，运行 baseline（不切分、不重排）。
2. 记录 `vf_end`，得到 `best_unroll_vf_end` 和 `U_best`。
3. 按下式计算逼近度：

```text
unroll_ratio = theoretical_vf_end / best_unroll_vf_end
```

等价判定可写为：

```text
best_unroll_vf_end <= theoretical_vf_end / 0.9
```

4. 若达到理论极限性能 90%+，按正式策略可早停。

## 开发阶段特例（当前启用）

- **即使达到 90%+，仍继续执行后续 Stage C（用于持续收集对比数据和验证优化链路）。**
- 最终报告中必须显式标注：
  - `dev_mode_continue_after_90 = true`
  - `unroll_already_good_enough = true/false`

## Stage C: `unroll=1` 下的 loop 优化搜索

当进入 Stage C 时，固定 `unroll=1`，比较三种策略（支持命令行选择）：

- `loop_uncut`
- `loop_cut_strict`
- `loop_cut_loose`

### 3.1 `loop_uncut`

- 不做 loop 切分。
- 仅做 loop 内指令重排。
- 必须保持拓扑关系不变（语义不变）。

### 3.2 `loop_cut_strict`

- 允许 loop 切分 + 每段内重排。
- 严格执行中间结果复用，不允许新增额外 UB 地址占用。
- 复用规则：
  - 可占用“已读入且未来不会再读入”的输入空间；
  - 可占用“已读入且未来不会再读入”的中间结果空间。

### 3.3 `loop_cut_loose`

- 允许 loop 切分 + 每段内重排。
- 允许使用更多 UB 地址，但总占用必须满足：

```text
UB_peak <= 248KB
```

### 3.4 当前评分策略

- 主目标仍以模型结果（`vf_end`）为准。
- 对过小切分加入惩罚（tiny-partition / tiny-tail penalty）。

## Stage D: 候选输出与对比

最终至少输出两类候选：

1. `candidate_unroll_best`
   - 来自 Stage B（`U_best` 对应结果）
2. `candidate_loop_opt_best`
   - 来自 Stage C（三策略中最优）

并给出推荐结论与差值。

## Stage E: CCE 验证（按需触发）

若进入硬件侧验证，至少验证以下 3 个 case：

1. baseline（初始配置）
2. unroll 最优（Stage B 最优）
3. loop 优化最优（Stage C 最优）

每个 case 需要：

- CCE `vf` 总时间采集
- golden 精度验证（数值正确性）

## 3. 命令行接口约定（v1）

loop 优化策略参数：

- `--loop-opt-mode loop_uncut`
- `--loop-opt-mode loop_cut_strict`
- `--loop-opt-mode loop_cut_loose`

开发阶段控制参数：

- `--dev-continue-after-unroll-90 on|off`（默认 `on`）

## 4. 输出规范（每次优化必带）

- `theoretical_vf_end`
- `unroll_sweep`（每个 `U` 对应 `vf_end`）
- `U_best`、`best_unroll_vf_end`
- `unroll_already_good_enough`
- `dev_mode_continue_after_90`
- `loop_opt_mode_results`（三策略结果）
- `candidate_unroll_best`
- `candidate_loop_opt_best`
- 最终推荐候选及提升比例
- UB 峰值与约束模式（若涉及切分）

## 5. 当前执行结论

v1 先落实两件事：

1. 按上述流程打通端到端执行与统一产物输出。
2. 在开发阶段保留“90%+ 继续往下走”的分支，优先积累样本与验证数据。

