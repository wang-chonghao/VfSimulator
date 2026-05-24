# 物理寄存器释放时机实验设计（CCE）

目标：明确 `producer` 对应物理寄存器的释放时机，判断更接近以下哪一类：

1. 在第一个 consumer 发射时释放
2. 在最后一个 consumer 发射时释放
3. 在最后一个 consumer 完成时释放
4. 还有固定滞后（例如 `last_consumer_done + k`）

## 1. 观测信号
- `core0.veccore0.rvec.IDU.dump`
  - `instr send to OOO ... vreg:<n>`：派发时可用寄存器
  - `IDU_BLOCK ... REASON:OOO no avail phy vreg`：寄存器短缺
- `core0.veccore0.instr_popped_log.dump`：开始执行周期
- `core0.veccore0.instr_log.dump`：完成周期

判定核心：
- 如果 IDU 连续两次派发记录里 `vreg` 从小到大跳升（如 `1 -> 4`），说明这两次派发之间发生了释放。
- 将该释放窗口与 consumer 的 `start/done` 对齐，判断规则。

## 2. 最小实验矩阵
### E1 单消费者（baseline）
- 结构：`VLD -> VADDS(producer) -> VMULS(consumer)`
- 目标：判断释放更接近 `consumer.start` 还是 `consumer.done`。

### E2 双消费者（快 + 慢）
- 结构：`producer=VADDS`，`consumer_fast=VMULS`，`consumer_slow=VDIV`
- 目标：区分“最后 consumer 发射释放”还是“最后 consumer 完成释放”。

### E3 延后第二消费者发射（gap）
- 结构：`producer -> fast_consumer -> (N条无关指令) -> slow_consumer`
- N 取 `0/4/8`
- 目标：看释放点是否随 N 系统性后移。

### E4 consumer 类型对照（VST 特例）
- A：`producer -> VST`
- B：`producer -> VMULS -> VST`
- 目标：判断 `store-consumer` 是否存在特殊释放延迟。

## 3. 运行建议
- `repeat_times=96`（稳态）
- 单核运行
- 保持相同编译选项，避免额外变量

## 4. 自动分析
工具：`tools/analyze_release_timing.py`

输入：
- `--dump-dir <cce_dump/case_dir>`

输出：
- 派发表（cycle/id/op/vreg）
- `vreg` 跳升事件
- 按 `id` 对齐的 `start/done` 周期

## 5. 结论模板
- 释放规则（示例）：
  - `single-consumer: release ~= consumer.done + k`
  - `two-consumer: release ~= last_consumer.done + k`
- `k` 统计：P50 / P90
- 是否存在 `VST` 特例
