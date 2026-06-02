# VF Simulator 建模说明

## 1. 项目概览

本项目当前可以分成两个大的部分：

1. **VF 建模（cost model / simulator）**
2. **VF 优化（optimization / search / split / unroll selection）**

这份文档**只整理 VF 建模部分**，不展开 VF 优化算法、启发式搜索、切分策略、自动调优流程等内容。本文的目标是把当前模型实现、模块边界、资源约束、寄存器规则、queue 规则、配置参数和输出日志说明清楚，作为后续继续对齐 CCE 日志和做精度迭代的基础文档。

从代码结构上看，VF 建模部分的职责是：

- 读取 VF trace JSON
- 将嵌套 loop 程序展开成线性 IR
- 在 IFU 中生成动态指令流
- 在 IDU 中按前端规则、VLOOP 可见性和资源信用发射指令
- 在 OoO 核心中进行重命名、依赖跟踪、ready 判定、queue 调度、执行、寄存器释放
- 输出模型日志和最终 VF end cycle

优化部分的职责则是生成或修改 trace，使模型预测更优，但这不是本文重点。

---

## 2. 当前目录结构

下面是和建模最相关的主目录结构说明。这里不追求把每个文件都列出来，而是突出当前真正参与 VF 建模的部分。

```text
VfSimulator/
├─ main.py                             # 建模主入口
├─ optimize_main.py                    # 优化入口（本文不展开）
├─ VF_modeling.md                      # 当前文档
├─ configs/
│  ├─ isa.json                         # 指令级参数：latency/startup/drain/EXU 类型等
│  ├─ uarch.json                       # 微架构参数：IDU/SHQ/EXQ/端口/寄存器数等
│  ├─ forwarding.json                  # producer->consumer forwarding 周期
│  └─ InitiationInterval.json          # 指令对之间的 II 约束
├─ core/
│  ├─ param_db.py                      # 配置数据库，统一读取 isa/uarch/forwarding/II
│  ├─ flatten.py                       # 静态 program -> 线性 IR
│  ├─ ifu.py                           # 线性 IR -> 动态指令流（支持 loop/unroll）
│  ├─ idu.py                           # IDU 窗口、dispatch、VLOOP 可见性、credit gate
│  ├─ dynamic_trace.py                 # 动态指令辅助标注（last-use 等）
│  ├─ vreg_live_range_normalization.py # 预处理 pass：规范化中间 vreg live range
│  ├─ ooo.py                           # 基础 OoO 核心（单队列/基础 ready-execute）
│  ├─ ooo_mainline.py                  # 当前主力 OoO 实现，包含 queue level4 主路径
│  ├─ ooo_factory.py                   # OoO model 选择与配置拼装
│  └─ ooo_npu_hybrid.py                # 混合模型（非本文重点）
├─ VFtest/                             # trace JSON 样例
├─ unroll_test/                        # unroll 测试 DSL/trace 与结果
├─ regression_suite/                   # 回归测试集合与基线
├─ results/                            # 模型运行输出目录
├─ tools/                              # 批量运行、统计、回归脚本
└─ optimizer/                          # 优化模块（本文不展开）
```

---

## 3. 建模主流程

当前建模入口是 [`main.py`](/D:/VfSimulator/main.py)。

整体执行链路如下：

```text
trace.json
  -> （可选）vreg live-range normalization
  -> Flattener：program -> linear IR
  -> IFUUnroll：linear IR -> dynamic instructions
  -> IDU：按 VLOOP/窗口/credit 发射到 OoO
  -> OoO Core：rename + wakeup + queue + execute + release
  -> 输出 start/done/history/dispatch/vloop 日志
```

### 3.1 输入 trace

trace JSON 主要包含：

- `dtype`
- `params`
- `program`
- 可选 `uarch`

其中：

- `params` 用来解析 loop trip count、unroll 因子等符号参数
- `program` 是嵌套 loop + inst 的静态程序表示
- `uarch` 如果在 trace 中提供，会覆盖全局 [`configs/uarch.json`](/D:/VfSimulator/configs/uarch.json) 的同名项

### 3.2 可选预处理：vreg live-range normalization

实现位置：[`core/vreg_live_range_normalization.py`](/D:/VfSimulator/core/vreg_live_range_normalization.py)

这是一个**建模前的程序规范化 pass**，不是硬件行为。

它的目的不是做 physical register rename，而是尽量消除“同一计算逻辑仅因 DSL 写法不同而导致 vreg 名字分配差异很大”的问题。直观上，它更接近一个轻量级编译器临时变量复用 pass。

核心规则是：

- 对 loop body 内的单层、平坦指令序列做分析
- 遍历每条指令的 `dst`
- 优先复用**之前已经出现过的 vreg slot**
- 但前提是该 slot 当前承载的值在后续**不会再被当作 src 使用**
- 如果没有安全可复用的旧 slot，才分配新的 vreg 名字

这个 pass 的意义是：

- 让模型对 DSL 表层 vreg 命名不那么敏感
- 更贴近“编译器会对中间值做一定复用整理”的现实
- 降低一些 case 中因为 vreg 写法松散带来的虚假寄存器压力

当前命令行开关是：

- `--enable-vreg-live-range-normalization`
- `--disable-vreg-live-range-normalization`

当前默认是开启的。

也就是说：

- 直接运行 `main.py` 时，会默认执行这个 pass
- 如果想关闭，需要显式传：
  - `--disable-vreg-live-range-normalization`

---

## 4. 静态程序展开：Flattener

实现位置：[`core/flatten.py`](/D:/VfSimulator/core/flatten.py)

Flattener 的任务是把 `program` 里的嵌套结构转换成线性 IR，但这里的“线性”仍然保留 loop 边界信息，而不是直接复制出所有动态迭代。

它会生成三类节点：

- `inst`
- `loop_begin`
- `loop_end`

每个静态 `inst` 都会带上：

- `pc`
- `depth`
- `loop_stack`
- `src`
- `dst`

每个 `loop_begin` 会带上：

- `loop_id`
- `iters`
- `unroll`
- `name`
- `is_innermost`

这里有一个很重要的约束：

- **只有最内层 loop 允许 `unroll > 1`**

如果外层 loop 也设置了 unroll，`flatten.py` 会直接报错。这保证了当前建模假设与测试集结构一致。

---

## 5. 动态指令生成：IFU

实现位置：[`core/ifu.py`](/D:/VfSimulator/core/ifu.py)

IFU 的职责是把线性 IR 变成**动态指令流**。它不是简单顺序吐指令，而是负责：

- 嵌套 loop 迭代展开
- innermost loop unroll
- 给每条动态指令附加 block / iteration 元信息

关键动态元信息包括：

- `inst_id`
- `iter_stack`
- `top_block_id`
- `is_last_in_top_block`
- `block_key_by_level`
- `block_end_levels`

这些信息后续会直接被 IDU 用于：

- top-level sibling loop 的 VLOOP 调度
- nested loop 的 body-open 判定
- block/iter 级发射节拍控制
- top block 结束后的后继 block 激活

### 5.1 top_block_id

当前代码把顶层并列 loop block 编号为：

- `0`
- `1`
- `2`
- ...

后续 IDU 会按这个编号维护每个 top block 的：

- VLOOP start
- body open 时间
- nested block 的动态触发

### 5.2 is_last_in_top_block

如果一条动态指令是某个 top-level block 的最后一条静态指令实例，它会被标成 `is_last_in_top_block=true`。

这个标记在 OoO 里主要用于：

- block 完成状态跟踪
- strong memory barrier 模式下 intermediate mem 的跨 block 约束

---

## 6. 参数数据库：ParamDB

实现位置：[`core/param_db.py`](/D:/VfSimulator/core/param_db.py)

ParamDB 是模型配置的统一入口。它会加载：

- [`configs/isa.json`](/D:/VfSimulator/configs/isa.json)
- [`configs/uarch.json`](/D:/VfSimulator/configs/uarch.json)
- [`configs/forwarding.json`](/D:/VfSimulator/configs/forwarding.json)
- [`configs/InitiationInterval.json`](/D:/VfSimulator/configs/InitiationInterval.json)

对外提供：

- `get_uarch()`
- `get_defaults()`
- `get_inst(op, dtype)`
- `get_forwarding_cycles(prod, cons, dtype)`
- `get_ii(prev, cur, dtype)`

### 6.1 ISA 参数含义

以 [`configs/isa.json`](/D:/VfSimulator/configs/isa.json) 为准，每条指令常见字段包括：

- `pipeline_startup_cost`
  - 对 load-producer 来说，这个值参与决定 consumer 何时可见
  - 对 compute pipeline 来说表示启动成本，不等于总 latency

- `latency`
  - 指令从开始执行到 `done_cycle` 的间隔

- `throughput`
  - 描述指令吞吐能力，但当前实际发射节拍主要由 II 表决定

- `pipeline_drain_cost`
  - 主要用于 store 类依赖建模，例如 VST 读取 producer 结果时的可用时间

- `data_load_cost`
  - 目前不是所有路径都直接使用

- `data_store_cost`
  - VST 执行持续时间通常取 producer op 的 `data_store_cost`

- `EXU`
  - 指令属于 `ALU` 还是 `SFU`

- `dispatch_exu`
  - 描述这条指令允许被派发到哪些执行端口
  - 当前模型已经把这个字段真正接进调度逻辑
  - 目前文档和代码里主要使用两种标记：
    - `EXU0_ONLY`
      - 只能进入 `EXQ0`
      - 只能在 `EXU0` 中执行
    - `EXU01`
      - 可以进入 `EXQ0` 或 `EXQ1`
      - 也可以在 `EXU0` 或 `EXU1` 中执行
  - 如果 `dispatch_exu` 缺失或是未知值，当前模型会回退成“默认允许所有可用端口”

### 6.2 forwarding 参数含义

[`configs/forwarding.json`](/D:/VfSimulator/configs/forwarding.json) 描述的是：

- producer 是哪种 op
- consumer 是哪种 op
- 两者之间的 forwarding 间隔是多少 cycle

例如：

- `VADDS -> VADDS = 3`
- `VMULS -> VADDS = 4`
- `VEXP -> 任意 = 13`

这个值不是 latency，而是“consumer 最早可开始依赖 producer 结果”的间隔基准。

### 6.3 II 参数含义

[`configs/InitiationInterval.json`](/D:/VfSimulator/configs/InitiationInterval.json) 描述的是：

- 某个端口/EXU 上，前一条指令是 `prev_op`
- 当前想发射的是 `cur_op`
- 二者之间至少要相隔多少 cycle

这体现的是**启动间隔**，而不是数据依赖。

换句话说：

- forwarding 解决“数据什么时候 ready”
- II 解决“功能单元什么时候允许再起一条”

这两个约束是并行存在的。

### 6.4 uarch 参数含义

当前 [`configs/uarch.json`](/D:/VfSimulator/configs/uarch.json) 中最重要的字段有：

- `issue_ports = 2`
  - 计算 EXU 数量，目前等价于两个执行端口

- `load_ports = 2`
  - 每拍最多启动两条 VLD

- `store_ports = 1`
  - 每拍最多启动一条 VST

- `IDU_window_width = 6`
  - IDU 前端窗口容量

- `IDU_issue_width = 5`
  - IDU 每拍最多向 OoO 发射 5 条指令

- `OoO_window_width`
  - 基础模型中的 IQ 容量；在 queue model 开启后，这个概念会被 SHQ/EXQ 细化

- `LDQ_width`
  - LSQ / load-store queue 容量

- `vreg_num = 68`
  - 物理向量寄存器个数

- `shq_depth = 58`
  - SHQ 容量

- `exq_depth = 26`
  - 单个 EXQ 容量

- `exq_recv_delay = 1`
  - 指令从 SHQ 发到 EXQ 后，EXQ 过 1 个 cycle 才收到

- `shq_to_exq_port_per_cycle = 1`
  - 每个 EXQ 每拍最多接收 1 条来自 SHQ 的计算指令

- `mem_bar_mode = strong`
  - 对 intermediate mem 的跨 block 内存可见性限制更严格

- `enforce_same_cycle_src_hazard = true`
  - 限制同拍内某些共享 src 的发射冲突

- `enable_cross_fu_ii = false`
  - 关闭后，II 主要按每个 FU 类型维度维护；在 queue 模式下 Stage-B 仍会额外看 EXU 级发射间隔

---

## 7. IDU：前端发射与 VLOOP 调度

实现位置：[`core/idu.py`](/D:/VfSimulator/core/idu.py)

IDU 负责做的事情不是重命名，而是：

- 维护一个前端窗口
- 控制 IFU -> IDU 接收
- 控制 IDU -> OoO 发射
- 维护 top block / nested block 的 VLOOP start
- 根据 body-open 和 iteration gate 决定某条指令能否被 dispatch
- 根据 credit proxy 判断当前拍还剩多少 preg / IQ / LSQ / SHQ 信用

### 7.1 VF 头开销

IDU 初始化时把：

- `top_block 0` 的 VLOOP start 固定在 cycle `19`

这和项目里一直沿用的 VF 开始语义一致。

随后：

- `vloop_to_dispatch_delay`
  - 决定 body 何时打开

- `idu_dispatch_start_advance`
  - 用于把实际 dispatch 起点从 `vf_startup_cost` 往前挪

当前默认组合下，常见地会看到：

- VLOOP 在 19 左右
- dispatch gate 在 23 左右开始打开

### 7.2 IDU dispatch 的硬约束

对每个 cycle，IDU 发射会同时受以下条件限制：

1. `IDU_issue_width`
2. preg credit
3. IQ free
4. LSQ free
5. SHQ free
6. top block body open
7. nested block body open
8. block-base + iter gate

其中：

- `VLD`
  - 消耗 `LSQ`
  - 不消耗 `SHQ`
  - 消耗目的寄存器数对应的 preg credit

- `VST`
  - 消耗 `LSQ`
  - 也消耗 `SHQ`
  - 一般不新分配 preg，除非指令本身有 dst vreg

- compute 指令
  - 消耗 `IQ`
  - 也消耗 `SHQ`
  - 消耗 dst 对应的 preg credit

### 7.3 block / iter gate

在非 theoretical-limit-vloop-only 情况下，IDU 还会施加一个 block 级节拍约束：

- 对某个 innermost block，第 0 次迭代建立 `block_base_cycle`
- 后续迭代要求：
  - `dispatch >= block_base_cycle + iter_id`

这相当于限制同一 inner block 的不同 iter 不会无限制地在同一拍全部暴露。

### 7.4 delayed credit visibility

在 queue level3/4 中，IDU 看到的并不是 OoO 内部的即时资源状态，而是**延迟可见后的信用**。

主循环里通过：

- `ooo.update_idu_visibility(cycle)`

把以下释放信息回传给 IDU：

- `preg_free`
- `shq_release`

也就是说：

- OoO 内部某拍已经释放，不等于 IDU 同拍就看见
- queue level3/4 会显式建模这个可见性差异

---

## 8. OoO / ISU mainline model

OoO core creation is handled by [`core/ooo_factory.py`](/D:/VfSimulator/core/ooo_factory.py). The public mainline is now a single default model:

- `queue_level4`
- consumer release rule: `start + 4`
- vreg live-range normalization: ON
- `shq_depth = 58`
- `exq_depth = 26`

Historical names such as `classical-cpu-type`, `consumer-done`, `queue_level1/2/3`, and `npu-hybrid` are no longer exposed as public CLI model selectors. They are useful for understanding the evolution of the model, but current `main.py` does not accept `--ooo-model`.

Current implementation split:

- [`core/ooo.py`](/D:/VfSimulator/core/ooo.py): shared OoO state, config, ready/forwarding/II lookup, and logging helpers.
- [`core/ooo_mainline.py`](/D:/VfSimulator/core/ooo_mainline.py): current mainline OoO core, including rename, preg lifecycle, SHQ/LSQ, LSU/compute issue paths, and the step loop.
- [`core/isu.py`](/D:/VfSimulator/core/isu.py): ISU/EXQ/EXU issue path for compute instructions.
- [`core/uarch_normalize.py`](/D:/VfSimulator/core/uarch_normalize.py): normalizes `configs/uarch.json` and theoretical-limit overrides.

### 8.1 Current queue path

Compute instruction path:

```text
IDU -> OoO rename -> SHQ -> EXQ0/EXQ1 -> EXU0/EXU1
```

Key points:

- SHQ is the OoO-side compute/VST credit and wait structure.
- EXQ is the ISU-side issue queue close to execution units.
- Each EXU has an inflight cap controlled by `exq_issue_inflight_cap_per_port`.
- ISA `dispatch_exu` controls legal execution units. For example, `EXU0_ONLY` can only execute on EXU0.

### 8.2 Register release rule

The default register release rule is consumer start + 4:

```text
last consumer starts at cycle S -> preg may be released at S + 4
```

The mainline also keeps the consumer-done sealing condition: after a vreg is overwritten, the new producer must enter rename before the model knows that the old preg will not be consumed by later instructions.

### 8.3 vreg live-range normalization

`main.py` runs this pass before simulation:

```python
normalize_program_vreg_live_ranges(program)
```

This pass reduces artificial logical-register reuse pressure and is part of the current `queue_level4+vregpass` accuracy configuration.

---

## 9. 物理寄存器重命名与生命周期

这部分是当前模型里最关键、也最容易和实验口径混淆的地方。

### 9.1 rename

OoO 在 `accept(inst)` 时做寄存器重命名。

对于每个 vreg dst：

- 从 freelist 分配一个新的 preg
- 读取 RAT 中该 vreg 的旧映射，记为 `preg_old`
- 更新 RAT：`vreg -> new_preg`

于是每条 uop 上会绑定三类寄存器信息：

- `preg_src`
- `preg_dst`
- `preg_old`

这意味着：

- 后续依赖都是按 **preg** 跟踪，而不是按表面的 vreg 名字跟踪
- 即使两条跨迭代指令表面上都写 `V0`，它们也可能绑定到不同 preg
- 所以“下一轮 VLD 写同名 vreg，会不会覆盖上一轮 VST 还没用完的数据”这种问题，不能只看 vreg，要看 preg 绑定是否不同

### 9.2 preg_pending

新分配出来的 `preg_dst` 会先放进 `preg_pending`。

含义是：

- 这个 preg 还没被真正生产完成
- consumer 看到它时不能认为已经 ready

当 producer 真正开始执行并建立生产者信息后，该 preg 才会：

- 写入 `preg_producer`
- 从 `preg_pending` 中移除

### 9.3 preg_consumer_count

在当前 queue_level4 主线模型中，模型会在 uop `accept` 时对每个 `preg_src` 做：

- `preg_consumer_count[preg] += 1`

这个计数表示：

- 已经绑定到该 preg 的真实 consumer 数量

后续当 consumer 到达它的“源寄存器释放点”时，才会对该计数做减法。

### 9.4 当前主释放规则

当前主力模型的默认释放参数是：

- `consumer_release_start_offset = 4`

也就是说，模型里实际采用的是：

- **consumer start + 4**

团队之前口头上经常把这条规则叫“start+5”，但以当前代码实现为准，默认参数是：

- `consumer_start + 4`

这条规则的具体含义是：

1. 某个 consumer 开始执行时
2. 在 `start_cycle + 4` 这个时刻触发一次 src release event
3. 对该 consumer 绑定的每个 `preg_src`：
   - `preg_consumer_count -= 1`
4. 当某个 preg 的 `preg_consumer_count` 降到 0 时，它获得释放资格
5. 若同时满足其它安全条件，就进入 freelist

### 9.5 为什么事件里要带 `(preg, generation)`

在 queue level2/3/4 中存在延迟事件：

- start+4 的 src release event
- SHQ release event
- IDU visible delay event

而同一个物理寄存器编号 `pX` 后续可能会被重复利用。

所以模型不能只记：

- “第 t 拍释放 p21”

否则旧事件晚到时，可能误伤新一代的 `p21`。

当前实现会为每个 preg 维护：

- `preg_generation[preg]`

并在 release event 中记录：

- `(preg, gen)`

触发事件时只有当：

- `event.gen == preg_generation[preg]`

才允许真的减计数或尝试释放。否则判定为旧事件，直接丢弃。

### 9.6 当前 `_try_free_preg` 的实际释放条件

一个 preg 真正回到 freelist 前，需要满足：

- 不是空 preg
- 不在 freelist 里
- 不是当前 RAT 里的现行映射
- `preg_consumer_count == 0`
- 不在 `preg_pending`
- 到达 `preg_release_eligible_cycle`

满足后才会：

- 从 producer 表中删除
- 从各种跟踪表中移除
- 放回 freelist

如果 queue level3 credit delay 开启，还会把这次释放放进“对 IDU 可见的延迟事件”里，而不是立刻让 IDU 看见。

### 9.7 overwrite 的作用

当某条指令写某个 vreg 时，这条 vreg 之前对应的 old preg 会记录在 `preg_old`。

在当前实现里，overwrite 的作用是：

- old preg 从此不再是该 vreg 的当前映射
- 后续不会再有新的 consumer 绑定到这个 old preg

但它不是唯一释放条件。真正能不能 free 还要看：

- 旧 consumer 是否都走完了 start+4 / dec-src
- old preg 是否已经不在 pending
- 是否已到 release eligible cycle

换句话说：

- overwrite 更像是“封口”
- consumer 释放事件更像是“把剩余引用数减到 0”

---

## 10. ready 判定

### 10.1 compute consumer 的 ready 时间

对于 compute 指令，`_compute_ready_cycle(u)` 会遍历所有 `preg_src`。

若 src 来自 `VLD`：

- 非 queue-legacy-forwarding 路径下：
  - ready 时间按 `producer_start + (pipeline_startup_cost - 1)` 计算

若 src 来自 compute producer：

- 非 legacy-forwarding 路径下：
  - ready 时间按 `producer_start + (forwarding - 1)` 计算

这正是 queue 系列模型里“wakeup 比旧 consumer-done 模型早 1 拍”的来源。

如果打开 theoretical-limit 的 legacy-forwarding 变种，则会回到更接近旧 consumer-done 的 ready 计算方式。

### 10.2 VLD ready

VLD 的 ready 主要看内存依赖：

- 如果读的 mem 地址有前序 store，必须等前序 store 完成
- 在 `mem_bar_mode = strong` 时，对某些 intermediate mem 还会额外等前一个 top block release

### 10.3 VST ready

VST 的 ready 需要看它依赖的 producer：

- 找到其 `preg_src` 对应的 compute producer
- VST ready 时间通常是：
  - `producer_start + producer.pipeline_drain_cost`

因此 VST 不是简单跟 src ready 同步，它更偏向“producer 的数据写出已足够稳定可 store”。

---

## 11. queue_level4 execution rules

Earlier documents described queue level1~4 as separate modes. The current code has converged on queue_level4 as the default mainline, so this section describes the effective rules.

### 11.1 SHQ credit

Default parameters:

- `shq_depth = 58`
- `enable_shq_credit_model = true`
- `shq_release_delay = 1`
- `enable_credit_visibility_delay = true`

Compute instructions occupy SHQ credit after entering OoO. After compute leaves SHQ for EXQ, SHQ credit is released with the configured delay. VST also participates in SHQ credit accounting. VLD is mainly controlled through the load path.

### 11.2 EXQ

The default model has two EXQs paired with two EXUs:

```text
SHQ -> EXQ0 -> EXU0
SHQ -> EXQ1 -> EXU1
```

Default parameters:

- `exq_depth = 26`
- `exq_recv_delay = 1`
- `shq_to_exq_port_per_cycle = 1`
- `exq_issue_inflight_cap_per_port = 7`
- `exq_capacity_counts_inflight = false`

SHQ to EXQ dispatch considers ready state, legal EXU set, EXQ occupancy, per-cycle receive width, and predicted issue timing.

### 11.3 EXQ -> EXU issue

Each EXU can start at most one compute instruction per cycle. A candidate must satisfy:

- `cycle >= exq_recv_cycle`
- sources are ready
- II constraints are satisfied
- per-EXU inflight cap is not full
- the EXU has not been used in the current cycle

### 11.4 dispatch_exu

ISA `dispatch_exu` values:

- `EXU0_ONLY`: only EXU0.
- `EXU01`: EXU0 or EXU1.
- `EXU012`: EXU0/1/2 in experimental three-port mode.

---

## 12. VLD / compute / VST 的区别化建模

### 12.1 VLD

- 从 IDU 发射后进入 LSQ
- ready 后直接启动，不经过 SHQ / EXQ
- 每拍最多启动 `load_ports` 条
- 启动后 `done_cycle = start + VLD_COST`

### 12.2 compute

- 从 IDU 发射后进入 IQ
- queue mode 下同时占用 SHQ 信用
- ready 后先从 SHQ 发到 EXQ，再从 EXQ 发到 EXU
- 每个端口每拍最多启动 1 条 compute
- 受 forwarding / ready / II / inflight cap / EXQ occupancy 共同限制

### 12.3 VST

- 从 IDU 发射后进入 LSQ
- 同时在 level2+ 中占用 SHQ 信用
- ready 时间通常取决于 producer 的 `pipeline_drain_cost`
- 每拍最多启动 `store_ports` 条
- 启动后执行时长取 producer 的 `data_store_cost`
- 开始执行后再触发 SHQ release 计时

---

## 13. theoretical-limit modes

Current public theoretical-limit candidates are implemented through:

- [`main.py`](/D:/VfSimulator/main.py)
- [`core/ooo_factory.py`](/D:/VfSimulator/core/ooo_factory.py)
- [`core/uarch_normalize.py`](/D:/VfSimulator/core/uarch_normalize.py)

These modes are upper-bound references, not real-hardware models.

### 13.1 `--theoretical-limit-vloop-only`

Keeps top-level VLOOP timing while relaxing selected cross-iteration exposure and capacity gates.

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only   --out_dir results/theory_vloop_only
```

### 13.2 `--theoretical-limit-vloop-only-legacy-forwarding-direct-issue`

A more aggressive candidate:

- keep VLOOP timing
- use legacy forwarding interpretation
- use direct issue, bypassing SHQ -> EXQ staging

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only-legacy-forwarding-direct-issue   --out_dir results/theory_direct_issue
```

The older generic `--theoretical-limit`, `--theoretical-limit-single-queue`, and `--theoretical-limit-vloop-only-legacy-forwarding` flags are no longer current public entry points.

---

## 14. Default behavior and recommended accuracy configuration

The current `main.py` default is already the recommended real-model configuration:

- fixed mainline model: `queue_level4`
- register release rule: consumer start + 4
- vreg live-range normalization: ON
- `shq_depth = 58`
- `exq_depth = 26`
- `exq_issue_inflight_cap_per_port = 7`

Therefore, a plain command such as:

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/gelu_poly
```

uses the same configuration as the `queue_level4+vregpass (shq=58 exq=26)` column in the current regression precision report.

The main knobs live in `configs/uarch.json`. For normal accuracy work, prefer changing configuration files deliberately rather than adding new public CLI flags.

---

## 15. Common invocation modes

### 15.1 Default JSON trace simulation

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/gelu_poly_default
```

Default behavior:

- `queue_level4`
- `start+4` release
- vreg live-range normalization ON
- `shq_depth=58`
- `exq_depth=26`

### 15.2 CCE/DSL input

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu_poly_cce
```

If the CCE file contains multiple `__VEC_SCOPE__` kernels:

```bash
python main.py --cce path/to/file.dsl --cce-kernel kernel_name --out_dir results/cce_kernel
```

### 15.3 theoretical-limit

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only   --out_dir results/theory_vloop_only
```

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only-legacy-forwarding-direct-issue   --out_dir results/theory_direct_issue
```

### 15.4 Experimental three-port mode

```bash
python main.py --trace VFtest/GeLU_poly.json --three-ports --out_dir results/three_ports
```

Three-port mode expands compute/VLD issue capacity to three ports. VST remains single-issue, and `EXU0_ONLY` instructions still execute only on EXU0.

---

## 16. Output logs and result files

Each `main.py` run writes a result directory specified by `--out_dir`.

Main files:

- `sim_history.json`: detailed cycle/event history.
- `start_by_cycle.json`: instruction start events.
- `done_by_cycle.json`: instruction completion events.
- `idu_to_ooo.json`: IDU admission trace and visible credit state.
- `vloop_trace.json`: top-level and nested VLOOP timing trace.
- `model_warnings.json`: optional low-confidence warnings, such as expanded vreg namespace pressure.

The canonical model timing is the terminal output:

```text
VF end cycle (with drain) = N
```

This value includes VF drain time and is the timing used by regression reports. When comparing against CCE/camodel, make sure the CCE number uses the same VF-end style metric, not raw total tick or unrelated dump timestamps.

---

## 17. Recommended code reading order

For future model work, the easiest reading order is:

1. [`main.py`](/D:/VfSimulator/main.py)
2. [`api/input_api.py`](/D:/VfSimulator/api/input_api.py) and [`api/cce_adapter.py`](/D:/VfSimulator/api/cce_adapter.py)
3. [`core/param_db.py`](/D:/VfSimulator/core/param_db.py)
4. [`core/flatten.py`](/D:/VfSimulator/core/flatten.py)
5. [`core/ifu.py`](/D:/VfSimulator/core/ifu.py)
6. [`core/idu.py`](/D:/VfSimulator/core/idu.py)
7. [`core/ooo_factory.py`](/D:/VfSimulator/core/ooo_factory.py)
8. [`core/ooo_mainline.py`](/D:/VfSimulator/core/ooo_mainline.py)
9. [`core/isu.py`](/D:/VfSimulator/core/isu.py)
10. [`core/vreg_live_range_normalization.py`](/D:/VfSimulator/core/vreg_live_range_normalization.py)

This order separates input parsing, static expansion, dynamic dispatch, queue scheduling, and register lifecycle.

---

## 18. Current modeling summary

The current VF modeling implementation can be summarized as follows:

1. The public mainline is no longer a family of selectable `--ooo-model` variants. It is the queue-level VF model: `queue_level4 + start+4 release + vreg live-range normalization`.

2. The main compute path is explicitly staged:
   - IDU
   - OoO rename
   - SHQ
   - EXQ
   - EXU

3. Register lifetime is tracked through physical registers, not surface vreg names. The release rule is based on the last consumer start time plus an offset, with overwrite/rename acting as the sealing condition.

4. The most important timing constraints are:
   - VLOOP exposure and IDU dispatch timing
   - IDU -> OoO delay
   - SHQ credit and delayed credit visibility
   - SHQ -> EXQ receive delay
   - EXQ depth and per-EXU inflight cap
   - forwarding, latency, and II tables
   - EXU legality from ISA `dispatch_exu`

5. Theoretical-limit modes are upper-bound references, not real hardware models. They should be used to estimate optimization headroom, not as the default accuracy model.

6. CCE/DSL input is now part of the normal interface through `api/cce_adapter.py`; JSON trace input remains available and is still the most explicit regression format.

Future VF optimization documentation should live separately from this modeling document, for example in a dedicated `VF_optimization.md` or the existing optimization notes.
