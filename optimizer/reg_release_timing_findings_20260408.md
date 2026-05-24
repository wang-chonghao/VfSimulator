# 寄存器释放时机实验结果（第一轮）

时间：2026-04-08  
目标：判断 producer 对应物理寄存器释放更接近 `consumer start` 还是 `consumer done`。

## 1. 本轮 case

- `reg_release_e1_single_fast`
- `reg_release_e2_dual_slowvdiv`
- `reg_release_e3_gap0`
- `reg_release_e3_gap8`
- `reg_release_e4_store_only`
- `reg_release_e4_compute_then_store`

运行目录（WSL）统一在：
- `/home/lenovo/msprof_run/<case>_native_simexec`

## 2. 关键统计

脚本输出（`tools/release_rule_summary.py`）：

| case | consumer start | consumer done | first jump after start | first jump after done | Δ(start) | Δ(done) |
|---|---:|---:|---:|---:|---:|---:|
| e1_single_fast | 2390 | 2406 | 2392 | 2407 | +2 | +1 |
| e2_dual_slowvdiv | 2398 | 2415 | 2399 | 2417 | +1 | +2 |
| e3_gap0 | 3220 | 3237 | 3221 | 3239 | +1 | +2 |
| e3_gap8 | 3220 | 3237 | 3221 | 3239 | +1 | +2 |
| e4_store_only | 2391 | 2401 | 2392 | 2402 | +1 | +1 |
| e4_compute_then_store | 2389 | 2397 | 2392 | 2398 | +3 | +1 |

注：
- `first jump` 指 IDU 派发日志中第一次出现 `vreg` 回升事件（`prev_vreg -> curr_vreg`）。
- 该信号是“发生了某些寄存器释放”的窗口信号，不是逐 producer 精确一对一映射。

## 3. 结论（第一轮）

1. 释放事件整体更接近 `consumer done` 一侧（通常 `done + 1~2`）。
2. `e4_compute_then_store` 显示 `start` 侧偏差可到 +3，而 `done` 侧仅 +1，进一步支持“不是在 consumer 发射即释放”。
3. `e3_gap0` 与 `e3_gap8` 结果相同，说明当前写法下编译后动态调度把“人为 gap”弱化了，未形成有效可分离信号。

## 3.1 第二轮补充（E6：高 live 压力，快慢 consumer 对照）

新增对照：
- `e6_highlive_fast`：consumer 使用 `VMULS`
- `e6_highlive_slow`：consumer 使用 `VEXP`

关键统计（来自 `tools/release_rule_summary.py`）：

| case | consumer start | consumer done | first jump after start | first jump after done | Δ(start) | Δ(done) |
|---|---:|---:|---:|---:|---:|---:|
| e6_highlive_fast | 3190 | 3198 | 3201 | 3201 | +11 | +3 |
| e6_highlive_slow | 3194 | 3210 | 3200 | 3212 | +6 | +2 |

观察：
- 两组都更接近 `done` 一侧（`+2~+3`）而不是 `start` 一侧。
- slow consumer（`VEXP`）相对 fast consumer（`VMULS`）的释放点整体后移，符合“consumer 完成更晚 -> 释放更晚”的趋势。

## 4. 当前局限

- 该方法观测的是全局 `vreg` 变化，多个 producer/consumer 同时在飞，会有混叠。
- 还不能直接证明“严格 last-consumer-done 才释放”，只能证明“明显不是 consumer-start 即刻释放”。
- 第二轮低并发 case（`e5_low_single/e5_low_dual`）由于寄存器压力不足，没有触发 `vreg` 回升事件，无法直接用于释放窗口判定。

## 5. 下一轮建议

1. 构造“低并发、可唯一归因”的短 VF：
   - 强制每迭代只产生 1 个关键 producer。
   - 通过独立长延迟链控制最后 consumer 的完成位置。
   - 同时人为提高 live 值数量，让 `OOO no avail phy vreg` 可稳定出现（否则看不到回升窗口）。
2. 关闭会打乱序列的编译重排选项（若可行），提高可解释性。
3. 在分析脚本里加入 `iteration_info` 维度，对同一迭代内 producer/consumer 做配对，再统计 `release - last_consumer_done` 的分布。
