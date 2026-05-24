# Stage C 优化策略设计（unroll=1）

本文档定义当前开发阶段的 Stage C 优化策略。  
范围严格限定为 `unroll=1` 的 loop 优化，不覆盖 Stage B 的 unroll 搜索。

---

## 1. 目标与范围

### 1.1 目标

- 在不改变语义的前提下，提升 VF 执行效率（`vf_end`）。
- 提供可通用、可解释、可扩展的优化框架。
- 支持三种可通过 CLI 选择的优化模式：
  - `loop_uncut`
  - `loop_cut_strict`
  - `loop_cut_loose`

### 1.2 非目标

- 不在 Stage C 内做 unroll 搜索（由外部脚本负责）。
- 不将 CCE 结果直接并入模型评分（CCE 用于后验验证）。

---

## 2. 总体思路

Stage C 的核心分为两层：

1. **结构层（Structure）**：不切分或切分（partition）  
2. **调度层（Schedule）**：每个 loop/分块内做依赖合法的指令重排

统一流程：

1. 读取 trace（强制 `U=1`）
2. 构建依赖 DAG
3. 根据模式生成候选结构（不切分/切分）
4. 对每个候选进行双发感知重排
5. 生成 trace 并用模型仿真得到 `vf_end`
6. 应用模式约束（strict/loose）
7. 打分并选择最优候选

---

## 3. 三种优化模式定义

## 3.1 `loop_uncut`

- 不切分 loop。
- 仅做 loop 内重排。
- 必须严格保持 DAG 拓扑约束，保证语义不变。

适用：

- 依赖链较规整、切分收益不确定、希望先低风险提速时。

## 3.2 `loop_cut_strict`

- 允许 loop 切分 + 每段内重排。
- UB 策略为“严格复用”：
  - 只允许复用已经死亡（未来不再读）的输入/中间结果空间；
  - 不允许新增净中间槽占用。

适用：

- 对落地约束要求高、需要保守策略时。

## 3.3 `loop_cut_loose`

- 允许 loop 切分 + 每段内重排。
- 允许新增中间槽，但必须满足：

```text
UB_peak <= 248KB
```

适用：

- 希望在 UB 可控范围内换取更高性能时。

---

## 4. 目标函数与约束

## 4.1 统一目标函数（仅比较可行解）

```text
score = vf_end
      + λ_cut * cut_penalty
      + λ_small_chain * small_chain_penalty
```

说明：

- **不再加入** `λ_ub * ub_pressure_penalty`。  
  UB 由模式定义为硬约束（可行/不可行），不作为软惩罚项参与打分。

## 4.2 约束处理原则

- `loop_uncut`：无切分相关 UB 约束。
- `loop_cut_strict`：不满足严格复用即判不可行。
- `loop_cut_loose`：超过 `248KB` 即判不可行。

不可行候选不参与最优比较。

---

## 5. 指令重排策略（关键）

当前重排目标不是“任意合法拓扑序”，而是“尽量提高双发利用率并降低流水空泡”。

采用 **Dual-Issue-Aware List Scheduling**：

1. 维护 ready 集（拓扑入度为 0 且到达最早可发时间）。
2. 每个 cycle 最多选择两条指令发射（受 issue/load/store 端口限制）。
3. 选择时检查：
  - `dispatch_exu` 约束（EXU0_ONLY / EXU01）
  - 同 EXU 的 `II(prev, cur)` 约束
  - 依赖边延迟近似（forwarding/startup/drain）
4. 用优先级函数排序候选：
  - 长关键路径优先（critical tail 大）
  - 双发配对更优先（co-issue 友好）
  - 高延迟链（如 `VDIV/VEXP`）适当前置

### 5.1 为什么这样做

- 单纯 BFS/拓扑重排通常能“合法”，但难以稳定利用双发端口。
- 引入端口/EXU/II 约束后，重排更贴近硬件发射行为。
- 这种调度器对不同算子更通用，不依赖 GeLU 特定规则。

---

## 6. 切分搜索策略

针对 `loop_cut_strict/loop_cut_loose`：

1. 使用通用 seed（宽度变化、深度间隔、并串结构）。
2. 局部搜索（加 cut / 删 cut / 邻近移动 cut）。
3. 每个候选都执行“段内重排 + 仿真评估”。
4. 引入短尾约束与惩罚：
  - `min_chain_len`
  - 过短尾块可做“合并回退再评估”（后续增强）

---

## 7. UB 策略实现建议

## 7.1 strict（目标形态）

- 将中间值视作生命周期区间，做区间复用分配。
- 仅允许复用死亡槽；不允许创建新增净槽。
- 若分配失败，候选不可行。
- 当前实现口径：按“分区边界”做精确可行性判定。  
  在每个边界比较：
  - 额外跨分区中间值的活跃槽需求（extra active slots）
  - 基线值生命周期可腾挪出来的空槽（free reusable slots）  
  若任一边界 `extra > free`，则 strict 候选不可行。

## 7.2 loose

- 同样使用生命周期分配器；
- 允许创建新槽；
- 峰值容量校验 `<=248KB`。

---

## 8. CLI 设计（Stage C）

建议入口：

```bash
python optimizer/stage_c_optimizer.py \
  <trace.json> \
  --trip-count <I> \
  --loop-opt-mode loop_uncut|loop_cut_strict|loop_cut_loose|all \
  --ooo-model consumer-done \
  --output-dir <dir>
```

可调参数：

- `--lambda-cut`
- `--lambda-small-chain`
- `--min-chain-len`

---

## 9. 输出规范

每次运行至少输出：

- `mode_results`（每个模式的 `vf_end/score/cuts/ub_bytes`）
- `best_mode`
- `best_vf_end`
- `best_cuts`
- `best_trace.json`
- 关键配置（`trip_count`, `ooo_model`, `U=1`）

---

## 10. 与当前计划的关系

与 `optimizer/optimize_plan_v1.md` 一致：

- Stage C 单独落地；
- 不处理 Stage B 的 unroll 搜索；
- 三模式通过 CLI 手动选择；
- 先以模型结果为主，CCE 做后验验证。

---

## 11. 分阶段落地建议

1. 先稳定 `loop_uncut` 重排质量（验证双发收益）。
2. 再补齐 `loop_cut_loose` 搜索质量（短尾修正 + 候选稳定性）。
3. 最后将 `loop_cut_strict` 升级为“精确生命周期复用器”。
4. 输出“近差候选”标记（例如前两名差距 <3%）以触发 CCE 验证。

## 11.1 已记录的后续优化点（暂缓）

1. strict 复用器升级为“真实槽 ID 分配日志”：
   - 输出每个边界值分配到哪个槽、在哪些分区复用。
   - 便于定位 strict 不可行的根因。
2. 输出 dual-issue 利用率指标：
   - 例如 `dual_issue_util = dual_issue_cycles / active_cycles`。
   - 用于直接评估重排器是否提升双发利用。
3. 增加 tiny-tail 自动合并回退：
   - 对过短末块执行与前块合并后重评估。
4. 扩展 cut 搜索：
   - 在 beam search 之外引入多起点随机扰动，提高跨算子稳定性。

---

## 12. 验收建议

开发期至少看三组指标：

1. 性能：`best_vf_end` 相对 baseline 的提升。
2. 稳定性：多 case 下模式排名是否稳定。
3. 可行性：strict/loose 约束是否正确生效（不出现越界方案）。

---

## 13. Stage C2 联合优化补充（当前主线）

为避免“固定切分再调 unroll”的局部最优偏差，Stage C2 采用联合策略：

1. 外层搜索切分方案 `cuts`。
2. 内层在该 `cuts` 下优化每段最优 `U_k`（默认候选 `{1,2,3,4}`，并按可整除过滤）。
3. 以该方案的最优 `(cuts, U*)` 时间代表该切分方案。
4. 在所有候选切分中选择全局最优。

核心原则：**每个切分方案必须在其自身最优 unroll 下再参与比较**。

实现入口：

- 内部主实现：`optimizer/stage_c2_segment_unroll_optimizer.py`
- 对外统一 CLI：`optimizer/run_vf_stage_c_optimization.py`（默认 `--algo stage_c2`）

脚本组织（暂定）：

1. `stage_c_optimizer.py`：基础能力层（切分/重排/模式约束）。
2. `stage_c2_segment_unroll_optimizer.py`：联合优化主实现（cuts + per-segment unroll）。
3. `run_vf_stage_c_optimization.py`：对外统一入口（减少使用侧心智负担）。
