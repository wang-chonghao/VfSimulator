# Queue Level4 Effective Defaults Before Cleanup

本文档记录的是：在 `dev_0429` 这一轮主线清理之前，历史 `queue_level4+vregpass` 口径下，`queue_level4` **实际生效**的默认参数。

目的不是描述“理想设计”，而是固定一份“当时程序真实跑出来的默认值基线”，方便后续继续清理代码、显式化默认值时做对照。

## 1. 结论先行

历史 `queue_level4` 的有效默认值，并不等于 `configs/uarch.json` 顶层字段直接给出的值。

原因是旧代码会沿着下面这条继承链重写参数：

- `queue_level1`
- `queue_level2`
- `queue_level3`
- `queue_level4`

因此：

- `uarch.json` 顶层 `shq_depth = 58`
- `uarch.json` 顶层 `exq_depth = 26`

这两个值在历史 `queue_level4` 默认路径下**并没有真正生效**。

历史主线里真正生效的是：

- `shq_depth = 10**9`
- `exq_depth = 10**9`

## 2. 历史主线口径

这份基线对应的是当时的主线使用方式：

- OOO 模型：`queue_level4`
- 寄存器释放规则：`consumer start + 4`
- `vreg live-range normalization`：开启
- `three-ports`：关闭

## 3. 顶层 `uarch.json` 默认值

`configs/uarch.json` 顶层与本主题直接相关的字段是：

- `issue_ports = 2`
- `load_ports = 2`
- `store_ports = 1`
- `IDU_window_width = 6`
- `IDU_issue_width = 5`
- `OoO_window_width = 2600000`
- `LDQ_width = 2400000`
- `vreg_num = 68`
- `enable_isu_queue_model = false`
- `shq_depth = 58`
- `exq_depth = 26`
- `exq_recv_delay = 1`
- `shq_to_exq_port_per_cycle = 1`
- `exq_capacity_counts_inflight = false`
- `enforce_same_cycle_src_hazard = true`

注意：这些顶层值中，只有一部分会在历史 `queue_level4` 路径下继续保留；另一部分会在旧的 level1/2/3/4 继承链中被覆盖。

## 4. 历史 `queue_level4` 实际生效默认值

### 4.1 Issue / Port

- `issue_ports = 2`
- `load_ports = 2`
- `store_ports = 1`

### 4.2 Frontend / Window

- `IDU_window_width = 6`
- `IDU_issue_width = 5`
- `OoO_window_width = 2600000`
- `LDQ_width = 2400000`
- `vreg_num = 68`

### 4.3 Queue 架构开关

- `enable_isu_queue_model = True`
- `enable_queue_level2_shq_model = True`
- `enable_queue_level3_credit_delay = True`

这意味着历史 `queue_level4` 走的是：

- `SHQ -> EXQ -> EXU`

而不是单队列直发。

### 4.4 SHQ / EXQ 容量

- `shq_depth = 10**9`
- `exq_depth = 10**9`

这是历史默认路径下最容易误解的地方。

虽然：

- `uarch.json` 顶层写的是 `shq_depth = 58`
- `uarch.json` 顶层写的是 `exq_depth = 26`

但旧代码会先在 `queue_level1` 语义下把这两个值覆盖成：

- `queue_level1_shq_depth = 10**9`
- `queue_level1_exq_depth = 10**9`

随后：

- `queue_level2` 对 `shq_depth` 的写法是：
  - `queue_level2_shq_depth if provided else current shq_depth`
- `queue_level4` 对 `exq_depth` 的写法是：
  - `queue_level4_exq_depth if provided else current exq_depth`

而历史默认情况下：

- 没有显式提供 `queue_level2_shq_depth`
- 没有显式提供 `queue_level4_exq_depth`

所以最终保留下来的仍然是：

- `shq_depth = 10**9`
- `exq_depth = 10**9`

### 4.5 SHQ / Credit / Visible Delay

- `queue_level2_shq_release_delay = 1`
- `queue_level3_idu_visible_delay = 2`
- `queue_level3_preg_visible_delay = 0`
- `queue_level3_shq_visible_delay = 0`

解释：

- SHQ credit 释放在 level2 语义下有 `1 cycle` 延迟
- 对 IDU 的可见性再经过 level3 侧的可见延迟建模
- `preg` 和 `shq` 在 level3 层没有额外单独增加延迟
- 主要是通过 `queue_level3_idu_visible_delay = 2` 体现回传可见性

### 4.6 传输 / Dispatch 相关

- `idu_to_ooo_delay = 1`
- `vloop_to_dispatch_delay = 2`
- `idu_dispatch_start_advance = 2`
- `exq_recv_delay = 1`
- `shq_to_exq_port_per_cycle = 1`

### 4.7 EXQ / Inflight

- `compute_inflight_cap = 0`
- `exq_issue_inflight_cap_per_port = 7`
- `exq_capacity_counts_inflight = false`

这里的语义是：

- `compute_inflight_cap = 0`
  - 不启用全局 compute inflight cap
- `exq_issue_inflight_cap_per_port = 7`
  - 每个 EXQ / EXU 端口的 inflight issue 上限为 7
- `exq_capacity_counts_inflight = false`
  - EXQ 容量默认不把已经 issue 到 EXU 的 inflight 指令计入 EXQ 占用

### 4.8 其他遗留控制位

- `enforce_same_cycle_src_hazard = false`
- `queue_level1_admit_blocked_to_exq = false`
- `queue_level3_global_shq_preg_gate = false`
- `queue_level3_use_explicit_idu_credit_bank = false`

## 5. 为什么这份文档重要

后续如果我们要把 `queue_level4` 真正收成一个“干净主线”模型，就需要把这些历史继承链带来的隐式默认值显式化。

最容易踩坑的点有两个：

- 不要把 `uarch.json` 顶层 `shq_depth = 58` 误认为历史主线默认值
- 不要把 `uarch.json` 顶层 `exq_depth = 26` 误认为历史主线默认值

历史主线实际默认是：

- `shq_depth = 10**9`
- `exq_depth = 10**9`

## 6. 当前建议

如果后续要继续清理 `queue_level1/2/3` 概念，建议遵循这个顺序：

1. 先固定这份“历史有效默认值基线”
2. 再把 `queue_level4` 所有依赖的默认值显式化
3. 最后再决定哪些默认值要保留为历史兼容，哪些要改成新的主线定义

这样做可以避免“代码变干净了，但结果悄悄变了”的情况。
