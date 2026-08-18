# VF Simulator 建模说明

## 1. 项目概览

本项目当前可以分成两个大的部分：

1. **VF 建模（代价模型/模拟器）**
2. **VF 优化（优化搜索、切分、展开和重写策略）**

这份文档**只整理 VF 建模部分**，不展开 VF 优化算法、启发式搜索、切分策略、自动调优流程等内容。本文的目标是把当前模型实现、模块边界、资源约束、寄存器规则、queue 规则、配置参数和输出日志说明清楚，作为后续继续对齐 CCE 日志和做精度迭代的基础文档。

从代码结构上看，VF 建模部分的职责是：

- 读取 VF trace JSON
- 将嵌套循环程序展开成线性 IR
- 在 IFU 中生成动态指令流
- 在 IDU 中按前端规则、VLOOP 可见性和资源信用发射指令
- 在 OoO 核心中进行重命名、依赖跟踪、就绪判定、队列调度、执行、寄存器释放
- 输出模型日志和最终 VF 结束周期

优化部分的职责则是生成或修改 trace，使模型预测更优，但这不是本文重点。

---

## 2. 当前目录结构

下面按当前仓库实际结构列出和建模最相关的目录。这里不把每个文件都展开，只标出当前主线建模、API、配置、回归和校准会直接接触的部分。

```text
VfSimulator/
├─ main.py                             # 建模主入口
├─ README.md                           # 项目入口说明
├─ api.md                              # 公共 API 总览
├─ VF_modeling.md                      # 当前文档
├─ api/                                # JSON/CCE 输入适配和公共 VFInfo API
│  ├─ vf_info.py                       # VFInfo、VFLoop、VFInst、ValueInfo、Membar
│  ├─ input_api.py                     # main.py 使用的统一输入加载入口
│  ├─ json_adapter.py                  # JSON trace 到 VFInfo 的适配
│  ├─ cce_adapter.py                   # CCE/DSL __VEC_SCOPE__ 解析
│  ├─ vf_lowering.py                   # VFInfo -> simulator trace payload
│  ├─ simulator_costmodel.py           # 程序化 cost model wrapper
│  ├─ vf_costmodel.py                  # 兼容 re-export 和抽象接口
│  └─ native/                          # API 侧 native 相关辅助目录
├─ configs/
│  ├─ isa.json                         # 指令级参数：延迟/启动/排空/EXU 类型等
│  ├─ uarch.json                       # 微架构参数：IDU/SHQ/EXQ/端口/寄存器数等
│  ├─ forwarding.json                  # 生产者到消费者的转发周期
│  └─ InitiationInterval.json          # 指令对之间的 II 约束
├─ core/
│  ├─ param_db.py                      # 配置数据库，统一读取 isa/uarch/forwarding/II
│  ├─ value_storage.py                 # Register/UB/Scalar 存储分类
│  ├─ isa_traits.py                    # LOAD/STORE/COMPUTE 资源分类 helper
│  ├─ program_analysis.py              # loop bound 和 top block 分析
│  ├─ program_canonicalization.py      # 单 super-iteration 循环规范化
│  ├─ flatten.py                       # 静态程序 -> 线性 IR
│  ├─ ifu.py                           # 线性 IR -> 动态指令流（支持 loop/unroll）
│  ├─ idu.py                           # IDU 窗口、发射、VLOOP 可见性、信用门控
│  ├─ simulator_runner.py              # IFU -> IDU -> OoO 主循环和日志写出
│  ├─ dynamic_trace.py                 # 动态指令辅助标注（last-use 等）
│  ├─ vreg_live_range_normalization.py # 预处理：规范化中间 vreg 活跃范围
│  ├─ ooo.py                           # 基础 OoO 核心（单队列/基础就绪执行）
│  ├─ ooo_mainline.py                  # 当前主力 OoO 实现，包含 queue level4 主路径
│  ├─ ooo_factory.py                   # OoO 模型选择与配置拼装
│  ├─ uarch_normalize.py               # 主线默认 uarch 和 theoretical-limit override
│  └─ isu.py                           # SHQ -> EXQ -> EXU 发射路径
├─ VFtest/                             # JSON trace 样例和部分回归输入
├─ cce_code/                           # CCE/DSL 样例和实验输入
├─ ascend_runner/                      # CCE/camodel 构建、运行、采集、校准辅助工具
│  ├─ current/                         # 当前使用的 runner 脚本
│  ├─ debug/                           # 调试脚本
│  ├─ legacy/                          # 历史脚本
│  ├─ forwarding_param_suite/          # forwarding 校准材料
│  ├─ ii_param_suite/                  # II 校准材料
│  └─ single_op_param_suite/           # 单指令参数校准材料
├─ regression_suite/                   # 回归测试集合与基线
│  ├─ cases/                           # 回归 case 和 baseline
│  ├─ inputs/                          # JSON/CCE 回归输入
│  ├─ reports/                         # 稳定报告
│  └─ docs/                            # 回归相关说明
├─ docs/                               # 架构、模型、优化和维护说明
├─ notes/                              # 建模/优化过程笔记
├─ optimizer/                          # 优化模块（本文不展开）
├─ skills/                             # VF 优化工作流 skill
├─ codex_optimization_skill/           # 旧版/独立 Codex 优化 skill 材料
├─ native/                             # native simulator 相关材料
├─ tests/                              # 单元测试
├─ tools/                              # 批量运行、统计、回归脚本
└─ pyrightconfig.json                  # Python 静态检查配置
```

注意：`results/` 是运行命令时常见的输出目录，但当前仓库顶层并不固定保留该目录；它通常由 `--out_dir` 指定路径后按需生成。

---

## 3. 建模主流程

当前建模入口是 [`main.py`](/D:/VfSimulator/main.py)。

整体执行链路如下：

```text
trace.json
  -> VFInfoLowerer：统一 JSON/CCE 输入
  -> vreg 活跃范围规范化
  -> 单 super-iteration 循环规范化
  -> Flattener：program -> linear IR
  -> IFUUnroll：linear IR -> 动态指令
  -> IDU：按 VLOOP/窗口/信用发射到 OoO
  -> OoO Core：重命名 + 唤醒 + 队列 + 执行 + 释放
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

### 3.2 固定预处理：vreg 活跃范围规范化

实现位置：[`core/vreg_live_range_normalization.py`](/D:/VfSimulator/core/vreg_live_range_normalization.py)

这是一个**建模前的程序规范化处理**，不是硬件行为。

它的目的不是做物理寄存器重命名，而是尽量消除“同一计算逻辑仅因 DSL 写法不同而导致 vreg 名字分配差异很大”的问题。直观上，它更接近一个轻量级编译器临时变量复用处理。

核心规则是：

- 对循环体内的单层、平坦指令序列做分析
- 遍历每条指令的 `dst`
- 优先复用**之前已经出现过的 vreg 槽位**
- 但前提是该槽位当前承载的值在后续**不会再被当作源操作数使用**
- 如果没有安全可复用的旧槽位，才分配新的 vreg 名字

这个处理的意义是：

- 让模型对 DSL 表层 vreg 命名不那么敏感
- 更贴近“编译器会对中间值做一定复用整理”的现实
- 降低一些 case 中因为 vreg 写法松散带来的虚假寄存器压力

当前 `main.py` 固定执行这个处理，并且不再暴露单独的启停命令行开关。

也就是说：

- 直接运行 `main.py` 时，会默认执行这个处理
- 如果需要做关闭该处理的实验，应通过代码或专门的实验分支显式修改，避免把非主线配置误用为默认精度口径

### 3.3 固定预处理：单 super-iteration 循环规范化

实现位置：[`core/program_canonicalization.py`](/D:/VfSimulator/core/program_canonicalization.py)

`main.py` 会在 vreg 活跃范围规范化之后、`flatten` 之前调用：

```python
canonicalize_single_super_iteration_loops(program, params, pdb=db, dtype=dtype)
```

这一步只处理满足以下条件的循环：

- 最内层循环。
- body 全部是 `inst`，没有嵌套 loop 或其它节点。
- `iters == 1`，或 `unroll > 1` 且 `iters == unroll`。

满足条件时，该循环会在静态 program 阶段被直接展开成指令序列；展开时会按 lane 给 `src`/`dst` 后缀加 `_laneN`。因此这类循环不会再作为 `loop_begin` / `loop_end` 进入 `Flattener` 和 IFU。这个行为和普通多 super-iteration 循环不同，文档和调试时要区分。

---

## 4. 静态程序展开：Flattener

实现位置：[`core/flatten.py`](/D:/VfSimulator/core/flatten.py)

Flattener 的任务是把预处理后的 `program` 嵌套结构转换成线性 IR，但这里的“线性”仍然保留 loop 边界信息，而不是直接复制出所有动态迭代。已经被单 super-iteration 规范化展开的循环不会再出现在这里。

它会生成三类节点：

- `inst`
- `membar`
- `loop_begin`
- `loop_end`

每个静态 `inst` 都会带上：

- `pc`
- `depth`
- `loop_stack`
- `src`
- `dst`

`membar` 也会作为独立节点保留，并带上 `pc`、`depth`、`loop_stack`。

每个 `loop_begin` 会带上：

- `loop_id`
- `iters`
- `unroll`
- `name`
- `is_innermost`

这里有一个很重要的约束：

- **只有最内层循环允许 `unroll > 1`**

如果外层循环也设置了 unroll，`flatten.py` 会直接报错。这保证了当前建模假设与测试集结构一致。

---

## 5. 动态指令生成：IFU

实现位置：[`core/ifu.py`](/D:/VfSimulator/core/ifu.py)

IFU 的职责是把线性 IR 变成**动态指令流**。它不是简单顺序吐指令，而是负责：

- 嵌套循环迭代展开
- 最内层循环展开
- 给每条动态指令附加 block / iteration 元信息

关键动态元信息包括：

- `inst_id`
- `iter_stack`
- `top_block_id`
- `is_last_in_top_block`
- `block_key_by_level`
- `block_end_levels`

这些信息后续会直接被 IDU 用于：

- 顶层并列循环的 VLOOP 调度
- 嵌套循环的 body-open 判定
- block/iter 级发射节拍控制
- 顶层 block 结束后的后继 block 激活

### 5.1 top_block_id

当前代码把顶层并列循环 block 编号为：

- `0`
- `1`
- `2`
- ...

后续 IDU 会按这个编号维护每个 top block 的：

- VLOOP 启动时间
- body 打开时间
- 嵌套 block 的动态触发

### 5.2 is_last_in_top_block

如果一条动态指令是某个顶层 block 的最后一条静态指令实例，它会被标成 `is_last_in_top_block=true`。

这个标记目前主要作为日志和后续 block-level 分析的元信息保留。历史版本曾用它配合
`mem_inter_*` 名字实现隐式 strong barrier；该时序路径已经删除，memory ordering
现在由显式 `Membar` 建模。

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
- `get_inst_form(op, form, dtype)`
- `get_forwarding_cycles(prod, cons, dtype)`
- `get_ii(prev, cur, dtype)`

### 6.1 ISA 参数含义

当前 [`configs/isa.json`](/D:/VfSimulator/configs/isa.json) 使用 `schema_version: 2`。记录格式是：

```text
instructions.<op>.op_class
instructions.<op>.forms.<form>.<param>
```

例如：

```text
VADDS.fp32
VADDS.fp16
VCVT_F32_TO_F16.f32_to_f16
VLDS.fp32
VSTS.fp16
```

`ParamDB.get_inst_form(op, form, dtype)` 会按 form 取配置；`get_inst(op, dtype)` 仍保留为兼容入口，并会映射到同名 dtype form。v2 查找顺序大致是：显式 `form`、`dtype`、少数 `VCVT_*` 的历史转换 form、`default`、`fp32`。

每条指令/形态常见字段包括：

- `op_class`
  - `COMPUTE`：走 SHQ / EXQ / EXU 计算路径
  - `LOAD`：走 LSQ load 路径
  - `STORE`：走 LSQ store 路径，并参与共享 SHQ 信用计数

- `pipeline_startup_cost`
  - 保留在 ISA form 元数据中，用于追溯/校准
  - 当前主线就绪判定主要通过 `forwarding.json` 的生产者/消费者 form 组合
  - 对计算流水线来说表示启动成本，不等于总延迟

- `latency`
  - 指令从开始执行到 `done_cycle` 的间隔

- `throughput`
  - 描述指令吞吐能力，但当前实际发射节拍主要由 II 表决定

- `pipeline_drain_cost`
  - 保留在 ISA form 元数据中，用于追溯/校准
  - 当前类 store 就绪判定同样主要通过 `forwarding.json`

- `data_load_cost`
  - 目前不是所有路径都直接使用

- `data_store_cost`
  - 类 store 指令执行持续时间当前仍取生产者指令/form 的 `data_store_cost`

- `EXU`
  - 指令属于 `ALU` 还是 `SFU`

- `dispatch_exu`
  - 描述这条指令允许被派发到哪些执行端口
  - 当前模型已经把这个字段真正接进调度逻辑
  - 目前文档和代码里主要使用三种标记：
    - `EXU0_ONLY`
      - 只能进入 `EXQ0`
      - 只能在 `EXU0` 中执行
    - `EXU01`
      - 可以进入 `EXQ0` 或 `EXQ1`
      - 也可以在 `EXU0` 或 `EXU1` 中执行
    - `EXU012`
      - 实验三端口模式下可进入 `EXQ0/1/2`
  - 如果 `dispatch_exu` 缺失或是未知值，当前模型会回退成“默认允许所有可用端口”

### 6.2 forwarding 参数含义

[`configs/forwarding.json`](/D:/VfSimulator/configs/forwarding.json) 描述的是：

- 生产者是哪种 `OP.form`
- 消费者是哪种 `OP.form`
- 两者之间的转发间隔是多少拍

例如：

- `VADDS.fp32 -> VADDS.fp32 = 3`
- `VMULS.fp32 -> VADDS.fp32 = 4`
- `VEXP.fp32 -> VADDS.fp32 = 13`
- `VCVT_F32_TO_F16.f32_to_f16 -> VSTS.fp16 = ...`

这个值不是总延迟，而是“消费者最早可开始依赖生产者结果”的间隔基准。v2 查表会优先使用显式 `OP.form` 组合；如果缺失，会回退到 `max(0, producer.latency - forwarding.defaults)`。`forwarding.defaults` 来自 `forwarding.json` 的 `defaults` 字段；该文件缺失时默认值是 3。

### 6.3 II 参数含义

[`configs/InitiationInterval.json`](/D:/VfSimulator/configs/InitiationInterval.json) 描述的是：

- 某个端口/EXU 上，前一条指令是 `prev OP.form`
- 当前想发射的是 `cur OP.form`
- 二者之间至少要相隔多少 cycle

这体现的是**启动间隔**，而不是数据依赖。

换句话说：

- forwarding 解决“数据什么时候就绪”
- II 解决“功能单元什么时候允许再起一条”

这两个约束是并行存在的。

`InitiationInterval.json` 缺失或缺少对应指令对时，II 默认回退到 `defaults` 字段；该文件缺失时默认值是 1。

### 6.4 uarch 参数含义

当前 [`configs/uarch.json`](/D:/VfSimulator/configs/uarch.json) 中最重要的字段有：

- `issue_ports = 2`
  - 计算 EXU 数量，目前等价于两个执行端口

- `load_ports = 2`
  - 每拍最多启动两条类 load 指令，当前常见指令是 `VLDS`

- `store_ports = 1`
  - 每拍最多启动一条类 store 指令，当前常见指令是 `VSTS`

- `IDU_window_width = 6`
  - IDU 前端窗口容量

- `IDU_issue_width = 5`
  - IDU 每拍最多向 OoO 发射 5 条指令

- `OoO_window_width`
  - 旧单队列模型中的窗口容量；当前主线队列模型主要由 SHQ/EXQ/LSQ 容量控制

- `LDQ_width`
  - LSQ / load-store 队列容量

- `vreg_num = 68`
  - 物理向量寄存器个数

- `shq_depth = 58`
  - SHQ 容量

- `exq_depth = 26`
  - 单个 EXQ 容量

- `exq_recv_delay = 1`
  - 指令从 SHQ 发到 EXQ 后，EXQ 过 1 个 cycle 才收到

- `ooo_to_shq_delay = 1`
  - 指令从 OoO rename 进入 SHQ 后，SHQ ready 判定过 1 拍可见

- `ooo_to_lsq_delay = 1`
  - load/store 指令从 OoO rename 进入 LSQ 后，LSQ ready 判定过 1 拍可见

- `shq_to_exq_port_per_cycle = 1`
  - 每个 EXQ 每拍最多接收 1 条来自 SHQ 的计算指令

- 显式 `Membar`
  - 对可见的 memory barrier 指令建模 load/store ordering；`mem_inter_*` 名字不再触发隐式 strong barrier

- `enforce_same_cycle_src_hazard = true`
  - 限制同拍内某些共享源操作数的发射冲突
  - 当前 `configs/uarch.json` 主线默认是 `false`

- `enable_cross_fu_ii = false`
  - 关闭后，II 主要按每个功能单元类型维度维护；在队列模式下 Stage-B 仍会额外看 EXU 级发射间隔

### 6.5 配置路径覆盖

`ParamDB` 默认从仓库的 `configs/` 目录加载上述文件，也支持显式路径或环境变量覆盖：

- `ISA_JSON_PATH`
- `UARCH_JSON_PATH`
- `FORWARDING_JSON_PATH`
- `II_JSON_PATH`

`forwarding.json` 和 `InitiationInterval.json` 是可选配置；缺失时会使用上述默认回退值。

---

## 7. IDU：前端发射与 VLOOP 调度

实现位置：[`core/idu.py`](/D:/VfSimulator/core/idu.py)

IDU 负责做的事情不是重命名，而是：

- 维护一个前端窗口
- 控制 IFU -> IDU 接收
- 控制 IDU -> OoO 发射
- 维护顶层 block / 嵌套 block 的 VLOOP 启动时间
- 根据 body-open 和迭代门控决定某条指令能否被发射
- 根据信用代理判断当前拍还剩多少 preg / SHQ 队列 / LSQ / 共享 SHQ 信用

### 7.1 VF 头开销

IDU 初始化时把：

- `top_block 0` 的 VLOOP 启动时间固定在第 `19` 拍

这和项目里一直沿用的 VF 开始语义一致。

随后：

- `vloop_to_dispatch_delay`
  - 决定 body 何时打开
  - 当前默认值是 2

- `idu_dispatch_start_advance`
  - 用于把实际发射起点从 `vf_startup_cost` 往前挪
  - 当前默认值是 2

当前默认组合下，常见地会看到：

- VLOOP 在 19 左右
- 顶层 body open 在 21 左右
- VF startup gate 通常在 `vf_startup_cost - idu_dispatch_start_advance` 打开

### 7.2 IDU 发射的硬约束

对每个 cycle，IDU 发射会同时受以下条件限制：

1. `IDU_issue_width`
2. preg 信用
3. SHQ 队列空闲项
4. LSQ 空闲项
5. SHQ 空闲项
6. 顶层 block body 已打开
7. 嵌套 block body 已打开
8. block 基准拍 + 迭代门控

其中：

- 类 load 指令
  - 消耗 `LSQ`
  - 不消耗 `SHQ`
  - 消耗目的寄存器数对应的 preg 信用
  - 当前常见指令是 `VLDS`

- 类 store 指令
  - 消耗 `LSQ`
  - 也消耗 `SHQ`
  - 一般不新分配 preg，除非指令本身有目的 vreg
  - 当前常见指令是 `VSTS`

- 计算指令
  - 消耗 SHQ 队列空闲项
  - 也消耗 `SHQ`
  - 消耗目的寄存器对应的 preg 信用

### 7.3 block / 迭代门控

在非理论上界 VLOOP-only 情况下，IDU 还会施加一个 block 级节拍约束：

- 对某个最内层 block，第 0 次迭代建立 `block_base_cycle`
- 后续迭代要求：
  - `dispatch >= block_base_cycle + iter_id`

这相当于限制同一内层 block 的不同迭代不会无限制地在同一拍全部暴露。

当前 theoretical-limit 模式会把 IDU 窗口、IDU 发射宽度、preg/LSQ/SHQ 信用等容量约束放大到近似无限，并跳过这个 innermost iter gate。`--theoretical-limit-vloop-only` 仍保留顶层 body-open gate，但跳过嵌套 body-open gate 和 iter gate；直接发射变体还会进一步绕过 SHQ -> EXQ 分阶段路径。

### 7.4 延迟信用可见性

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

## 8. OoO / ISU 主线模型

OoO 核心的创建由 [`core/ooo_factory.py`](/D:/VfSimulator/core/ooo_factory.py) 负责。当前公开主线只有一个默认模型：

- `queue_level4`
- 消费者释放规则：`start + 4`
- vreg 活跃范围规范化：开启
- `shq_depth = 58`
- `exq_depth = 26`

`classical-cpu-type`、`consumer-done`、`queue_level1/2/3`、`npu-hybrid` 等历史名称不再作为公开 CLI 模型选择器暴露。它们仍可用于理解模型演进，但当前 `main.py` 不接受 `--ooo-model`。

当前实现拆分：

- [`core/ooo.py`](/D:/VfSimulator/core/ooo.py)：共享 OoO 状态、配置、就绪/forwarding/II 查询和日志辅助函数。
- [`core/ooo_mainline.py`](/D:/VfSimulator/core/ooo_mainline.py)：当前主线 OoO 核心，包括重命名、preg 生命周期、SHQ/LSQ、LSU/计算发射路径和单拍推进循环。
- [`core/isu.py`](/D:/VfSimulator/core/isu.py)：计算指令的 ISU/EXQ/EXU 发射路径。
- [`core/uarch_normalize.py`](/D:/VfSimulator/core/uarch_normalize.py)：规范化 `configs/uarch.json` 和理论上界覆盖配置。

### 8.1 当前队列路径

计算指令路径：

```text
IDU -> OoO rename -> SHQ -> EXQ0/EXQ1 -> EXU0/EXU1
```

要点：

- SHQ 是 OoO 侧计算/类 store 信用和等待结构。
- EXQ 是 ISU 侧靠近执行单元的发射队列。
- 每个 EXU 都有在途数量上限，由 `exq_issue_inflight_cap_per_port` 控制。
- ISA `dispatch_exu` 控制合法执行单元。例如 `EXU0_ONLY` 只能在 EXU0 执行。

### 8.2 寄存器释放规则

默认寄存器释放规则是消费者开始 + 4：

```text
最后一个消费者在第 S 拍启动 -> preg 最早可在 S + 4 释放
```

主线同时保留 consumer-done 封口条件：一个 vreg 被覆盖后，新的生产者必须进入重命名阶段，模型才知道旧 preg 不会再被后续指令消费。

### 8.3 vreg 活跃范围规范化

`main.py` 会在模拟前运行这个处理：

```python
normalize_program_vreg_live_ranges(program)
```

这个处理会降低由表层逻辑寄存器命名造成的人为寄存器压力，是当前 `queue_level4+vregpass` 精度配置的一部分。

---

## 9. 物理寄存器重命名与生命周期

这部分是当前模型里最关键、也最容易和实验口径混淆的地方。

### 9.1 重命名

OoO 在 `accept(inst)` 时做寄存器重命名。

对于每个 vreg dst：

- 从空闲列表分配一个新的 preg
- 读取 RAT 中该 vreg 的旧映射，记为 `preg_old`
- 更新 RAT：`vreg -> new_preg`

于是每条 uop 上会绑定三类寄存器信息：

- `preg_src`
- `preg_dst`
- `preg_old`

这意味着：

- 后续依赖都是按 **preg** 跟踪，而不是按表面的 vreg 名字跟踪
- 即使两条跨迭代指令表面上都写 `V0`，它们也可能绑定到不同 preg
- 所以“下一轮 load 写同名 vreg，会不会覆盖上一轮 store 还没用完的数据”这种问题，不能只看 vreg，要看 preg 绑定是否不同

### 9.2 preg_pending

新分配出来的 `preg_dst` 会先放进 `preg_pending`。

含义是：

- 这个 preg 还没被真正生产完成
- 消费者看到它时不能认为已经就绪

当生产者真正开始执行并建立生产者信息后，该 preg 才会：

- 写入 `preg_producer`
- 从 `preg_pending` 中移除

### 9.3 preg_consumer_count

在当前 queue_level4 主线模型中，模型会在 uop `accept` 时对每个 `preg_src` 做：

- `preg_consumer_count[preg] += 1`

这个计数表示：

- 已经绑定到该 preg 的真实消费者数量

后续当消费者到达它的“源寄存器释放点”时，才会对该计数做减法。

### 9.4 当前主释放规则

当前主力模型的默认释放参数是：

- `consumer_release_start_offset = 4`

也就是说，模型里实际采用的是：

- **消费者开始 + 4**

团队之前口头上经常把这条规则叫“start+5”，但以当前代码实现为准，默认参数是：

- `consumer_start + 4`

这条规则的具体含义是：

1. 某个消费者开始执行时
2. 在 `start_cycle + 4` 这个时刻触发一次源寄存器释放事件
3. 对该消费者绑定的每个 `preg_src`：
   - `preg_consumer_count -= 1`
4. 当某个 preg 的 `preg_consumer_count` 降到 0 时，它获得释放资格
5. 若同时满足其它安全条件，就进入 freelist

### 9.5 为什么事件里要带 `(preg, generation)`

在 queue level2/3/4 中存在延迟事件：

- start+4 的源寄存器释放事件
- SHQ 释放事件
- IDU 可见性延迟事件

而同一个物理寄存器编号 `pX` 后续可能会被重复利用。

所以模型不能只记：

- “第 t 拍释放 p21”

否则旧事件晚到时，可能误伤新一代的 `p21`。

当前实现会为每个 preg 维护：

- `preg_generation[preg]`

并在释放事件中记录：

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

- 从生产者表中删除
- 从各种跟踪表中移除
- 放回空闲列表

如果 queue level3 信用延迟开启，还会把这次释放放进“对 IDU 可见的延迟事件”里，而不是立刻让 IDU 看见。

### 9.7 覆盖的作用

当某条指令写某个 vreg 时，这条 vreg 之前对应的旧 preg 会记录在 `preg_old`。

在当前实现里，覆盖的作用是：

- 旧 preg 从此不再是该 vreg 的当前映射
- 后续不会再有新的消费者绑定到这个旧 preg

但它不是唯一释放条件。真正能不能释放还要看：

- 旧消费者是否都走完了 start+4 / 源引用递减
- 旧 preg 是否已经不在 pending
- 是否已到释放资格周期

换句话说：

- 覆盖更像是“封口”
- 消费者释放事件更像是“把剩余引用数减到 0”

---

## 10. 就绪判定

### 10.1 计算消费者的就绪时间

对于计算指令，`_compute_ready_cycle(u)` 会遍历所有 `preg_src`。

对每个源操作数，如果能找到生产者，当前主线通过 `ParamDB.get_forwarding_cycles(...)` 查询生产者/消费者 form 组合：

- 非 queue-legacy-forwarding 路径下：
  - 就绪时间按 `producer_start + (forwarding - 1)` 计算

这正是队列系列模型里“唤醒比旧 consumer-done 模型早 1 拍”的来源。

如果打开理论上界的旧版转发变种，则会回到更接近旧 consumer-done 的就绪计算方式。

### 10.2 类 load 指令就绪

类 load 指令不根据 UB 名称、offset 或地址范围自动推导前序 store 依赖。UB 访问顺序必须由输入中的显式 `Membar` 表达：

- `Membar(VST_VLD)` 等待 barrier 前的 store 完成，并阻塞 barrier 后的 load 发射
- 没有 Membar 时，即使 load/store 使用同一个 UB 对象，也不会自动建立 store 到 load 的依赖
- canonical `DependencyRef` 的 memory/control edge 尚未接入 Core，当前会明确拒绝，不会静默忽略

### 10.3 类 store 指令就绪

类 store 指令的就绪需要看它依赖的生产者：

- 找到其 `preg_src` 对应的计算/load 生产者
- 通过 `forwarding.json` 查询生产者/消费者 form 组合
- 就绪时间通常是：
  - 队列路径：`producer_start + max(0, forwarding - 1)`
  - legacy-forwarding 路径：`producer_start + forwarding`

因此类 store 就绪不是简单跟源操作数完成同步，而是由生产者/消费者依赖时序控制。

---

## 11. queue_level4 执行规则

早期文档把 queue level1~4 描述成多个独立模式。当前代码已经收敛到以 queue_level4 作为默认主线，因此本节只描述实际生效规则。

### 11.1 SHQ 信用

默认参数：

- `shq_depth = 58`
- `enable_shq_credit_model = true`
- `shq_release_delay = 1`
- `enable_credit_visibility_delay = true`

计算指令进入 OoO 后占用 SHQ 信用。计算指令离开 SHQ 进入 EXQ 后，SHQ 信用按配置延迟释放。类 store 指令也参与 SHQ 信用计数。类 load 指令主要由 load 路径控制。

### 11.2 EXQ

默认模型有两个 EXQ，分别对应两个 EXU：

```text
SHQ -> EXQ0 -> EXU0
SHQ -> EXQ1 -> EXU1
```

默认参数：

- `exq_depth = 26`
- `exq_recv_delay = 1`
- `shq_to_exq_port_per_cycle = 1`
- `exq_issue_inflight_cap_per_port = 7`
- `exq_capacity_counts_inflight = false`

SHQ 到 EXQ 的发射会同时考虑就绪状态、合法 EXU 集合、EXQ 占用、每拍接收宽度和预测发射时间。

### 11.3 EXQ -> EXU 发射

每个 EXU 每拍最多启动一条计算指令。候选指令必须满足：

- `cycle >= exq_recv_cycle`
- 源操作数已就绪
- II 约束满足
- 每个 EXU 的在途数量上限未满
- 该 EXU 在当前拍还没被使用

### 11.4 dispatch_exu

ISA `dispatch_exu` 取值：

- `EXU0_ONLY`：仅 EXU0。
- `EXU01`：EXU0 或 EXU1。
- `EXU012`：实验三端口模式下可用 EXU0/1/2。

---

## 12. load / 计算 / store 的区别化建模

### 12.1 类 load 指令

- 从 IDU 发射后进入 LSQ
- 就绪后直接启动，不经过 SHQ / EXQ
- 每拍最多启动 `load_ports` 条
- 当前常见指令是 `VLDS`
- 启动后 `done_cycle = start + isa_latency(load_op, form)`
- load 完成时间只使用该 load 指令自身在 `isa.json` 中的 `latency`

### 12.2 计算指令

- 从 IDU 发射后经过 `idu_to_ooo_delay` 管线进入 OoO rename
- rename 后进入 SHQ，当前主线不再有单独的 IQ 活跃路径
- 队列模式下同时占用 SHQ 信用
- 就绪后先从 SHQ 发到 EXQ，再从 EXQ 发到 EXU
- 每个端口每拍最多启动 1 条计算指令
- 受 forwarding、就绪、II、在途数量上限、EXQ 占用共同限制

### 12.3 类 store 指令

- 从 IDU 发射后进入 LSQ
- 同时占用共享 SHQ 信用
- 当前常见指令是 `VSTS`
- 就绪时间通过生产者/消费者 forwarding 表计算
- 每拍最多启动 `store_ports` 条
- 启动后执行时长取生产者的 `data_store_cost`
- 开始执行后再触发 SHQ release 计时

---

## 13. 理论上界模式

当前公开理论上界候选模式由以下文件实现：

- [`main.py`](/D:/VfSimulator/main.py)
- [`core/ooo_factory.py`](/D:/VfSimulator/core/ooo_factory.py)
- [`core/uarch_normalize.py`](/D:/VfSimulator/core/uarch_normalize.py)

这些模式是上界参考，不是真实硬件模型。它们会在 `resolve_model_uarch()` 规范化主线配置之后，再通过 `apply_theoretical_limit_overrides()` 覆盖部分微架构参数。

### 13.1 `--theoretical-limit-vloop-only`

保留顶层 VLOOP 启动和顶层 body-open 时序，同时放宽前端窗口、发射宽度、OoO/LSQ/SHQ/EXQ 容量、IDU 到 OoO 延迟、OoO 到 SHQ/LSQ 延迟、EXQ 接收延迟、SHQ 释放延迟和在途数量上限。当前实现中，该模式还会跳过嵌套 body-open gate 和 innermost iter gate。

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only   --out_dir results/theory_vloop_only
```

### 13.2 `--theoretical-limit-vloop-only-legacy-forwarding-direct-issue`

更激进的候选模式：

- 保留 VLOOP 时序
- 使用旧版 forwarding 解释方式
- 使用直接发射，绕过 SHQ -> EXQ 分阶段路径
- 关闭 ISU 队列模型、SHQ 信用模型和延迟信用可见性

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only-legacy-forwarding-direct-issue   --out_dir results/theory_direct_issue
```

旧的通用 `--theoretical-limit`、`--theoretical-limit-single-queue`、`--theoretical-limit-vloop-only-legacy-forwarding` 参数已不再是当前公开入口。

---

## 14. 默认行为和推荐精度配置

当前 `main.py` 默认值已经是推荐真实模型配置：

- 固定主线模型：`queue_level4`
- 寄存器释放规则：消费者开始 + 4
- vreg 活跃范围规范化：开启
- `shq_depth = 58`
- `exq_depth = 26`
- `exq_issue_inflight_cap_per_port = 7`

因此，普通命令：

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/gelu_poly
```

使用的配置与当前回归精度报告中的 `queue_level4+vregpass (shq=58 exq=26)` 列一致。

主要调节项位于 `configs/uarch.json`。正常精度工作中，优先有意识地修改配置文件，不建议随意增加新的公开 CLI 参数。

---

## 15. 常见调用模式

### 15.1 默认 JSON trace 模拟

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/gelu_poly_default
```

默认行为：

- `queue_level4`
- `start+4` release
- vreg 活跃范围规范化开启
- `shq_depth=58`
- `exq_depth=26`

### 15.2 CCE/DSL 输入

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu_poly_cce
```

如果 CCE 文件包含多个 `__VEC_SCOPE__` kernel：

```bash
python main.py --cce path/to/file.dsl --cce-kernel kernel_name --out_dir results/cce_kernel
```

### 15.3 理论上界

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only   --out_dir results/theory_vloop_only
```

```bash
python main.py --trace VFtest/GeLU_poly.json   --theoretical-limit-vloop-only-legacy-forwarding-direct-issue   --out_dir results/theory_direct_issue
```

### 15.4 实验三端口模式

```bash
python main.py --trace VFtest/GeLU_poly.json --three-ports --out_dir results/three_ports
```

三端口模式把计算/load 发射容量扩展到 3 个端口。store 发射仍然单发射，`EXU0_ONLY` 指令仍只在 EXU0 执行。

---

## 16. 输出日志和结果文件

每次运行 `main.py` 都会写入 `--out_dir` 指定的结果目录。

主要文件：

- `sim_history.json`：详细周期/事件历史。
- `start_by_cycle.json`：指令 start 事件。
- `done_by_cycle.json`：指令完成事件。
- `idu_to_ooo.json`：IDU 接收 trace 和可见信用状态。
- `vloop_trace.json`：顶层和嵌套 VLOOP 时序 trace。
- `model_warnings.json`：可选低置信度告警，例如 vreg 命名空间膨胀导致的压力。

规范模型时序是终端输出：

```text
VF end cycle (with drain) = N
```

该值包含 VF 排空时间，也是回归报告使用的时序指标。和 CCE/camodel 对比时，需要确认 CCE 数字使用同一种 VF 结束口径，而不是原始总 tick 或无关 dump 时间戳。

---

## 17. 推荐代码阅读顺序

后续做模型工作时，建议按以下顺序阅读：

1. [`main.py`](/D:/VfSimulator/main.py)
2. [`api/input_api.py`](/D:/VfSimulator/api/input_api.py) 和 [`api/cce_adapter.py`](/D:/VfSimulator/api/cce_adapter.py)
3. [`core/param_db.py`](/D:/VfSimulator/core/param_db.py)
4. [`core/flatten.py`](/D:/VfSimulator/core/flatten.py)
5. [`core/ifu.py`](/D:/VfSimulator/core/ifu.py)
6. [`core/idu.py`](/D:/VfSimulator/core/idu.py)
7. [`core/ooo_factory.py`](/D:/VfSimulator/core/ooo_factory.py)
8. [`core/ooo_mainline.py`](/D:/VfSimulator/core/ooo_mainline.py)
9. [`core/isu.py`](/D:/VfSimulator/core/isu.py)
10. [`core/vreg_live_range_normalization.py`](/D:/VfSimulator/core/vreg_live_range_normalization.py)

这个顺序把输入解析、静态展开、动态发射、队列调度和寄存器生命周期分开。

---

## 18. 当前建模总结

当前 VF 建模实现可以总结为：

1. 公开主线不再是一组可选 `--ooo-model` 变体，而是队列级 VF 模型：`queue_level4 + start+4 release + vreg 活跃范围规范化`。

2. 主要计算路径显式分阶段：
   - IDU
   - OoO 重命名
   - SHQ
   - EXQ
   - EXU

3. 寄存器生命周期通过物理寄存器跟踪，而不是通过表层 vreg 名称跟踪。释放规则基于最后一个消费者开始时间加偏移量，覆盖/重命名作为封口条件。

4. 最重要的时序约束包括：
   - VLOOP 暴露和 IDU 发射时序
   - IDU -> OoO 延迟
   - SHQ 信用和延迟信用可见性
   - SHQ -> EXQ 接收延迟
   - EXQ 深度和每个 EXU 的在途数量上限
   - forwarding、latency、II 表
   - 来自 ISA `dispatch_exu` 的 EXU 合法性

5. 理论上界模式是上界参考，不是真实硬件模型。它们应用于估计优化空间，不应作为默认精度模型。

6. CCE/DSL 输入已经通过 `api/cce_adapter.py` 成为常规接口的一部分；JSON trace 输入仍然可用，并且仍是最显式的回归格式。

后续 VF 优化文档应与本建模文档分开，例如放在单独的 `VF_optimization.md` 或现有优化笔记中。
