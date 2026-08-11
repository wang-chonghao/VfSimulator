# 未支持指令默认参数与 VFInfo 后端改造计划

## 背景

当前 VFSim 的指令时序主要来自三张表：

- `configs/isa.json`
- `configs/forwarding.json`
- `configs/InitiationInterval.json`

当输入 VF 中出现暂未收录的向量指令时，模型通常会在
`ParamDB.get_inst_form()` 或 OoO 查询 `latency` / `EXU` / `dispatch_exu`
等字段时失败。短期目标是：对已经识别出的未支持计算指令使用默认参数继续模拟，
同时输出结构化告警，明确标记本次预测包含低置信度默认回退。

这个工作需要先修几个现有一致性问题，否则默认回退会扩大已有风险。尤其是
load/store 指令时序口径必须先收敛：load/store 的执行完成延迟统一使用
`isa.json` 中该 load/store 指令自身 form 的 `latency`，不再使用生产者或消费者
指令上的 `data_load_cost` / `data_store_cost`。

中长期目标仍然是：core 后端直接消费 `VFInfo` 或类型化 `CoreIR` 中的显式信息，
不再依赖 `V0`、`mem0` 这类历史命名约定判断 operand storage。

## 当前问题

### 未支持指令识别分散

当前没有统一的未支持指令识别层：

- JSON adapter 直接接受 `op` 字段，不查 ISA 表。
- CCE adapter 会解析 `__VEC_SCOPE__` 内的向量调用，并做一部分 op 规范化：
  - `VLD` / `VLDS` 识别为加载风格指令。
  - `VST` / `VSTS` / `VSTUS` / `VSTAS` 识别为存储风格指令。
  - `VCVT` 会根据源/目的 dtype 推断 form，并在可识别时 specialize 成
    `VCVT_F32_TO_F16` 等 op。
  - 其它 `v*` callee 基本按大写 op 进入 `VFInst`。
- `isa_traits.py` 在元数据缺失时会把多数未知 op 回退为计算类。
- `ParamDB.get_inst_form()` 找不到 op/form 时抛 `KeyError`，通常在 core 时序查询阶段暴露。

这会导致同一个未知 op：

- 前端可能正常通过。
- IDU 资源分类可能已经把它当 compute。
- OoO / ISU 查询 latency、EXU 或 dispatch port 时才失败。

### 资源分类调用不一致

短期默认回退前必须先处理资源分类一致性。

当前 `core/simulator_runner.py` 的 `_inst_reservation()` 在 IDU-to-OOO
在途指令信用预约里调用：

```python
uses_lsq(op)
uses_shq_queue(op)
uses_shared_shq_credit(op)
```

这里没有传 `ParamDB`，也没有传实际 `form`。但 IDU / OoO 主路径会传 `db`
和 dtype/form 参与分类。因此未来如果靠 `isa.json` 新增 LSU op，
在途预约可能和主路径分类不一致，导致 IDU 看到的 LSQ / SHQ / 物理寄存器 credit 不准确。

这个问题属于短期默认回退的前置修复，不应放到 VFInfo-core 中长期迁移。

### load/store 执行时长规则存在冲突

当前配置和代码里存在重复定义：

- `VLDS.<form>.latency`
- `VSTS.<form>.latency`
- `data_load_cost`
- `data_store_cost`
- `pipeline_startup_cost`
- `pipeline_drain_cost`

现在规定主线模型只保留一个活跃口径：

```text
LOAD done_cycle  = load_start  + LOAD 指令自身 isa.json latency
STORE done_cycle = store_start + STORE 指令自身 isa.json latency
```

也就是说：

- load 类指令，例如 `VLDS`，执行完成时间看 `VLDS.<form>.latency`。
- store 类指令，例如 `VSTS`，执行完成时间看 `VSTS.<form>.latency`。
- 不再读取 producer 的 `data_store_cost` 来决定 store 执行时长。
- 不再读取 consumer 或其它指令的 `data_load_cost` 来决定 load 执行时长。

当前代码还不符合这个规则：

- load 当前使用 `uarch.load_done_latency`。
- store 当前使用 producer op/form 的 `data_store_cost`。

这会导致配置含义冲突，也会让 `VLDS -> VSTS` 这类路径因为
`VLDS.data_store_cost = 0` 出现不合理 store duration。这个问题必须在默认指令
fallback 前修掉。

### startup/drain 字段和 forwarding 表语义重复

`pipeline_startup_cost` 和 `pipeline_drain_cost` 原本表达的是：

- load 或 producer 开始后多久 consumer 可以开始。
- producer 完成或数据稳定后多久 store 可以开始。

这些语义已经可以由 `forwarding.json` 统一表达。当前主线应把
`forwarding.json` 作为唯一活跃依赖时序来源：

```text
consumer_ready = producer_start + forwarding[producer_OP.form][consumer_OP.form]
```

在 queue-level compute wakeup 路径中，仍可保留当前实现已有的 `forwarding - 1`
对齐规则，但依赖间隔本身仍来自 `forwarding.json`。

因此：

- `pipeline_startup_cost` 只作为历史/校准参考字段保留。
- `pipeline_drain_cost` 只作为历史/校准参考字段保留。
- 主线 ready timing 不应再从这两个字段推导。
- 后续补表和 fallback 都应围绕 `forwarding.json` 的 producer/consumer pair。

### 显式 Membar 需要控制类建模

输入层可以产生 `Membar` / `membar` 节点，但当前主线主要依赖 `mem_bar_mode=strong`
和 `mem_inter_*` 形式建模跨 block memory ordering。现在需要把显式 membar
升级成独立控制类指令：

- `membar` 不属于 `LOAD` / `STORE` / `COMPUTE`。
- `membar` 不进入 IDU window，不占 SHQ / LSQ / EXQ / EXU。
- IFU 仍然按动态指令流顺序吐出 `membar` 节点。
- runner 在取指阶段识别 `membar`，送入控制单元。
- 控制单元根据 `membar` 类型阻塞后续受影响的 load/store 发射到 OoO。

短期只实现 `VST_VLD` 和 `VLD_VST`：

```text
VST_VLD: 前面所有 vector store 完成后，后续 vector load 才允许发射。
VLD_VST: 前面所有 vector load 完成后，后续 vector store 才允许发射。
```

判断前后关系应使用 IFU 生成的动态单调序号 `stream_seq`，静态 `pc` 只用于日志和调试。
字段语义采用以下约定：

- `pc`：Flattener 生成的静态程序位置，同一静态指令在 loop/unroll 展开后会重复出现。
- `stream_seq`：IFU 生成的动态指令流序号，`inst` 和 `membar` 共享同一个单调递增序列。
- `ControlUnit` 只使用 `stream_seq` 判断 barrier 前后关系。

含显式 `membar` 的 innermost loop 暂不执行 lane unroll 展开。若该 loop 请求
`unroll > 1`，IFU 在进入 loop 时回退为 `unroll = 1`，并记录
`membar_unroll_disabled` 告警。这样模型仍可运行，且 barrier 的动态顺序语义不会被
当前 lane batch 展开破坏。后续若要支持这种场景，需要单独实现按 lane 保序或按
barrier 切分 unroll batch 的语义。

### Core 输入仍是历史 dict program

当前公开输入已经统一到 `VFInfo`，但 `main.py` 仍通过 `VFInfoLowerer` 把它转换成
历史 simulator payload：

```text
VFInfo
  -> {"program": [...], "values": {...}, "dtype": ..., "params": ...}
  -> vreg_live_range_normalization
  -> canonicalize_single_super_iteration_loops
  -> flatten / IFU / IDU / OoO
```

这保留了历史 core 的输入合同，但也让后端仍然存在按字符串前缀判断的逻辑。
例如 IDU 中对目的寄存器数量的估算仍有直接检查 `V*` 前缀的路径。

## 短期目标

短期目标分成两个阶段：

1. 先修已有一致性问题，避免默认回退扩大错误。
2. 再对未支持计算指令引入默认参数和告警。

## 前置修复

### 修复零：统一 load/store 执行 latency 口径

这是默认回退前的第一步。

实现要求：

- `OoO` load 启动后，`done_cycle = start_cycle + isa_latency(load_op, form)`。
- `OoO` store 启动后，`done_cycle = start_cycle + isa_latency(store_op, form)`。
- 删除或停用主线中的 `uarch.load_done_latency` 活跃路径。
- 删除或停用主线中的 producer `data_store_cost` store duration 路径。
- `data_load_cost` / `data_store_cost` 不再作为主线执行时长来源。
- `pipeline_startup_cost` / `pipeline_drain_cost` 不再作为主线 ready timing 来源。

需要覆盖的位置：

- `core/ooo_mainline.py` 的 load issue 路径。
- `core/ooo_mainline.py` 的 store issue 路径。
- `core/ooo.py` 中 `_data_store_cost()` 相关调用。
- `configs/isa.json` 字段说明和后续清理计划。

需要新增或更新测试：

- `VLDS -> VSTS` 最小 case 可以跑通。
- load done latency 等于 `VLDS.<form>.latency`。
- store done latency 等于 `VSTS.<form>.latency`。
- 修改 producer 的 `data_store_cost` 不应改变 store done cycle。

### 修复一：资源分类必须统一传入 ParamDB 和 form

所有资源分类调用都应使用同一套入口，并传入足够上下文：

```text
op + form + dtype + ParamDB -> op_class
```

需要覆盖的位置：

- `core/idu.py`
- `core/ooo.py`
- `core/ooo_mainline.py`
- `core/isu.py`
- `core/simulator_runner.py` 的 `_inst_reservation()`

尤其是 `_inst_reservation()` 应从当前：

```python
uses_lsq(op)
uses_shq_queue(op)
uses_shared_shq_credit(op)
```

改成等价的上下文感知形式：

```python
uses_lsq(op, pdb, form_or_dtype)
uses_shq_queue(op, pdb, form_or_dtype)
uses_shared_shq_credit(op, pdb, form_or_dtype)
```

如果现有 helper 只接受 dtype，应扩展为能接受 form，或者新增统一 resolver，
避免 form-specific ISA 条目在资源分类时丢失。

### 修复二：显式 Membar 控制单元

在默认回退扩大 CCE 覆盖前，实现 `VST_VLD` / `VLD_VST` 的显式控制语义。

实现要求：

- `Flattener` 保留 `membar` 节点。
- `IFU` 为 `inst` 和 `membar` 都生成单调递增的 `stream_seq`。
- `IFU` 动态吐出 `membar`，但不为 `membar` 分配普通 `inst_id`。
- `runner` 取到 `membar` 后送入 `ControlUnit`，不送入 IDU。
- `ControlUnit` 记录 barrier 类型、`pc`、`stream_seq`、等待类别和阻塞类别。
- IDU dispatch 前查询 `ControlUnit`，被 active barrier 约束的 load/store 暂不发射到 OoO。
- `ControlUnit` 每周期根据 IDU window、IDU-to-OOO pipe 和 OoO 中 `stream_seq`
  小于 barrier 的 load/store 是否完成来释放 barrier。

完整支持其它 `SMEM_BAR` 类型放到后续阶段。旧的 `mem_bar_mode=strong` 可先保留为
legacy 行为，但显式 `membar` 应优先生效。

## 默认参数策略

### 未支持计算指令的默认 ISA 参数

对识别出的暂未支持计算指令，拟引入以下默认行为：

- `op_class = "COMPUTE"`
- `EXU = "ALU"`
- `dispatch_exu = "EXU01"`
- `latency = 9`
- `pipeline_startup_cost = 0`
- `pipeline_drain_cost = 0`
- `throughput = 1`

暂不在这里承诺未知计算指令的 `data_store_cost`。该字段必须服从前置修复中的
load/store latency 规则。主线 store duration 不再从未知计算指令的
`data_store_cost` 读取，因此默认 ISA 参数不需要为未知计算指令定义活跃
`data_store_cost`。

说明：

- 默认认为未知向量计算指令可以在 EXU0 和 EXU1 上执行。
- 默认 latency 先按 `9`，后续根据真实硬件或 camodel 数据逐步补表。
- 对明显的加载/存储指令不要无条件按计算指令默认回退。`VLD*` / `VST*`
  会影响 LSQ、shared SHQ credit、UB memory dependency 和存储执行时长，
  需要单独策略。

### 缺失 forwarding pair 的默认策略

对缺失的 producer/consumer forwarding pair，拟继续使用当前思路：

```text
forwarding = max(0, producer_latency - 3)
```

其中未知 producer 的 `producer_latency` 使用默认 `9`。

load/store 相关依赖先采用一个明确的临时默认值：

```text
forwarding(LOAD, STORE) = 6
```

这里的 `LOAD` / `STORE` 指通过 ISA `op_class` 或统一 resolver 分类出的 load/store
类指令，例如 `VLDS -> VSTS`。后续有 camodel 或硬件校准数据后，再把具体
`OP.form -> OP.form` pair 写入 `forwarding.json`。

需要记录 `missing_forwarding_pair` 告警，标明：

- 生产者 op/form
- 消费者 op/form
- 生产者 latency 是否来自默认回退
- 使用的 forwarding 默认值
- 是否命中了 load/store 临时默认值 `6`

### 缺失 Initiation Interval pair 的新默认策略

当前实现中，`ParamDB.get_ii()` 缺 pair 时直接回退
`InitiationInterval.json` 的 `defaults`，文件缺失时默认 `1`。

下面规则是拟引入的新默认回退策略，不是当前行为：

```text
if prev_latency - cur_latency == 1:
    II = 2
else:
    II = 1
```

理由：

- 当同一个 EXU 中前一条指令的 latency 比后一条指令的 latency 大 1 时，
  如果默认 II 为 1，可能出现同一个 cycle 完成并写回寄存器，存在写冲突风险。
- 其它未知 pair 先保持 `II = 1`，避免过度悲观。

这个规则必须通过回归和校准验证后再成为主线默认。实现时需要记录
`missing_ii_pair` 告警，标明：

- prev op/form
- cur op/form
- prev latency
- cur latency
- latency 是否来自默认回退
- 使用的 II 默认值

## 告警设计

### 告警类型

至少记录以下告警：

- `unsupported_isa_op`：op 不在 `isa.json` 中，使用默认 ISA 参数。
- `unsupported_isa_form`：op 存在但 form 不存在，使用默认 form 参数。
- `missing_forwarding_pair`：forwarding pair 不在表中，使用默认 forwarding。
- `missing_ii_pair`：II pair 不在表中，使用默认 II。
- `unknown_lsu_op`：op 看起来像加载/存储，但没有明确 ISA 配置或分类。
- `unsupported_membar_type`：显式 membar 类型暂未支持。
- `membar_unroll_disabled`：innermost loop 请求 `unroll > 1`，但 loop body 内包含
  显式 `membar`，因此 IFU 回退为 `unroll = 1`。

### 告警字段

建议结构：

```json
{
  "kind": "unsupported_isa_op",
  "op": "VFOO",
  "form": "fp32",
  "used_defaults": {
    "op_class": "COMPUTE",
    "latency": 9,
    "dispatch_exu": "EXU01"
  },
  "count": 12,
  "sample_inst_ids": [3, 19, 35]
}
```

对 II：

```json
{
  "kind": "missing_ii_pair",
  "prev": "VFOO.fp32",
  "cur": "VBAR.fp32",
  "prev_latency": 9,
  "cur_latency": 8,
  "used_default": 2,
  "rule": "prev_latency_minus_cur_latency_eq_1",
  "count": 7
}
```

对含 `membar` 的 innermost unroll 回退：

```json
{
  "kind": "membar_unroll_disabled",
  "pc": 12,
  "barrier": "VST_VLD",
  "loop_id": 1,
  "requested_unroll": 2,
  "used_unroll": 1,
  "reason": "membar_in_unrolled_innermost_loop",
  "count": 1
}
```

### 告警输出

默认回退告警必须独立触发落盘。

当前 `main.py` 的 `model_warnings.json` 只在 vreg capacity warning 非空时写。
引入默认回退告警后，写文件逻辑应改成：

```text
if vreg_capacity_warnings or instruction_fallback_warnings:
    write model_warnings.json
```

终端打印简短摘要：

```text
[WARN] unsupported instruction fallback used: 3 unique ops, 18 instances
[WARN] missing timing pairs: forwarding=12, ii=9
```

详细内容写入：

```text
<out_dir>/model_warnings.json
```

建议文件结构：

```json
{
  "has_warning": true,
  "vreg_capacity_warnings": [],
  "instruction_fallback_warnings": []
}
```

## 实现计划

### 步骤零：清理 load/store timing 配置冲突

先完成前置修复零：

- load/store 执行完成时间统一使用自身 ISA `latency`。
- `data_load_cost` / `data_store_cost` 不再作为主线时序来源。
- `pipeline_startup_cost` / `pipeline_drain_cost` 不再作为主线 ready 来源。
- `forwarding.json` 成为 producer/consumer ready timing 的唯一活跃配置表。

这一步完成后再做未知指令 fallback。

### 步骤一：修复资源分类一致性

目标：

- 所有资源分类入口都传入 `ParamDB` 和 form/dtype。
- `simulator_runner._inst_reservation()` 与 IDU/OoO 主路径分类一致。
- 新增测试覆盖未来新增 LSU op 时的 in-flight reservation 行为。

验收：

- 已支持 case cycle 不变。
- 未知计算指令的预约分类与后端分类一致。
- 显式配置的 LSU op 在 IDU、runner、OoO 中均走同一资源路径。

### 步骤二：补充 load/store latency 回归并记录 cycle 变化

目标：

- 覆盖步骤零中的 load/store latency 规则。
- 修复 `VLDS -> VSTS` 这类路径可能触发的源释放 mismatch。
- 明确记录因 load/store latency 口径切换导致的 cycle 变化。

验收：

- 新增 `VLDS -> VSTS` 最小回归。
- load done latency 等于 load 指令自身 ISA `latency`。
- store done latency 等于 store 指令自身 ISA `latency`。
- 修改 producer `data_store_cost` 不影响 store done cycle。
- `python3 -m unittest discover tests` 通过。
- 现有 smoke case cycle 变化需要明确记录原因；如果不应变化，则保持不变。

### 步骤三：实现显式 Membar 控制单元

目标：

- `membar` 作为控制类节点建模，不进入普通执行队列。
- 支持 `VST_VLD` 和 `VLD_VST`。
- 使用动态 `stream_seq` 区分 barrier 前后的动态 load/store。

验收：

- `VST_VLD` 会阻塞 barrier 后的 load，直到 barrier 前的 store 全部 done。
- `VLD_VST` 会阻塞 barrier 后的 store，直到 barrier 前的 load 全部 done。
- 普通已支持 case 无 membar 时 cycle 不变。

### 步骤四：在 ParamDB 中集中指令默认回退

新增或扩展 `ParamDB` API：

- `get_inst_form(..., allow_fallback=True)`
- 或新增 `resolve_inst_form(op, form, dtype) -> (params, resolution)`

`resolution` 至少包含：

- 是否使用 fallback。
- 默认回退类型。
- canonical op/form。
- 使用的默认参数。

原则：

- 指令缺失可以默认回退。
- 配置存在但 schema 错误仍应报错，避免坏配置被静默吞掉。
- 默认回退逻辑集中在 `ParamDB`，不要散落在 OoO/ISU 的 `try/except` 中。

### 步骤五：统一 ISA traits、时序查询和告警收集器

让 `isa_traits.py` 使用 ParamDB 的同一套 resolution 结果：

- 未知计算指令 -> `COMPUTE` 告警。
- 明显 LSU op 缺配置 -> `unknown_lsu_op` 告警或保守报错，具体取决于策略开关。

`core/ooo.py` 的 `_inst_params()`、`_latency()`、`_data_store_cost()`、`_get_fu_type()`、
`_eligible_exu_ports()` 都应从同一份 resolved params 读取。

在 `ParamDB` 内部维护结构化告警聚合：

- 按告警键去重。
- 记录 count。
- 记录少量 sample op/form/inst_id。

### 步骤六：在最终 program 上做预扫描

预扫描位置应放在 program 预处理之后，而不是刚 `VFInfoLowerer` 后。

当前实际链路应调整为：

```text
VFInfoLowerer
  -> vreg_live_range_normalization
  -> canonicalize_single_super_iteration_loops
  -> instruction fallback pre-scan on final program
  -> ProgramAnalyzer / Flattener / IFU / IDU / OoO
```

这样可以覆盖 normalization / canonicalization 后最终进入 core 的 program。
如果后续 lane 后缀由 IFU 生成，预扫描仍应以 op/form 为主；storage 判断必须依赖
`values` 和 `ValueStorageLookup`，不能依赖 lane 后的字符串前缀。

后续当 core 直接消费 `VFInfo` 后，预扫描应改为扫描类型化 `CoreIR`。

### 步骤七：测试

新增测试覆盖：

- load done latency 使用 load 指令自身 `latency`。
- store done latency 使用 store 指令自身 `latency`。
- producer `data_store_cost` 变化不影响 store done cycle。
- `VLDS -> VSTS` 最小 case 不触发 start-release mismatch。
- 未知计算指令可以跑通，并输出 `unsupported_isa_op`。
- 未知 op 默认 `latency = 9`。
- 缺 forwarding pair 时使用 `latency - 3`。
- 缺 load/store forwarding pair 时先使用临时默认值 `6`。
- 缺 II pair 且 `prev_latency - cur_latency == 1` 时使用新策略 `II = 2`。
- 缺 II pair 且 `prev_latency - cur_latency != 1` 时使用新策略 `II = 1`。
- 文档和测试明确该 II 规则是新默认回退，不是旧行为。
- 已支持指令不产生默认回退告警。
- schema 错误仍然抛错。
- 默认回退告警非空但 vreg warning 为空时，`model_warnings.json` 仍会写出。
- `VST_VLD` 阻塞后续 load，直到前序 store done。
- `VLD_VST` 阻塞后续 store，直到前序 load done。

建议增加一个最小 trace：

```json
{
  "dtype": "fp32",
  "params": {"I": 1},
  "program": [
    {
      "type": "loop",
      "iters": "I",
      "body": [
        {"type": "inst", "op": "VLDS", "dst": ["V0"], "src": ["memA"]},
        {"type": "inst", "op": "VUNKNOWN", "dst": ["V1"], "src": ["V0"]},
        {"type": "inst", "op": "VSTS", "dst": ["memB"], "src": ["V1"]}
      ]
    }
  ]
}
```

## 中长期目标：core 直接消费 VFInfo

## 废弃 OoO 和冲突配置清理计划

### 当前状态

当前实际预测链路只创建 `OoOCoreMainline`：

```text
main.py / CoreVfCostModel
  -> create_ooo_core()
  -> OoOCoreMainline
```

`core/ooo.py` 里的 `OoOCore` 已经不是可独立运行的 backend，`accept()` 和
`step()` 都是抽象入口。但 `OoOCoreMainline` 仍继承它，并复用其中一部分 helper：

- `_latency()`
- `_get_ii()`
- `_ready_time_for_src()`
- `_compute_ready_cycle()`
- `_load_ready_cycle()`
- `_store_ready_cycle()`
- `_pick_exu_port()`
- 日志和基础 credit 查询接口

因此不能直接删除整个 `core/ooo.py`。它目前是“共享 helper + 历史残留”的混合文件。

### 需要清理的历史语义

以下字段不应再作为主线时序来源：

- `configs/isa.json` 中的 `data_load_cost`
- `configs/isa.json` 中的 `data_store_cost`
- `configs/isa.json` 中的 `pipeline_startup_cost`
- `configs/isa.json` 中的 `pipeline_drain_cost`
- `configs/uarch.json` 中的 `load_done_latency`

当前主线规则应保持为：

- load/store 执行时长只取该指令自身 form 的 `latency`。
- producer/consumer ready timing 只通过 `forwarding.json` 查询，缺表时走
  `ParamDB` fallback 并告警。
- issue spacing 只通过 `InitiationInterval.json` 查询，缺表时走 `ParamDB`
  fallback 并告警。

### 清理步骤

#### 步骤一：标记历史字段为 ignored/deprecated

先不物理删除配置字段。增加配置校验或启动期扫描：

- 如果 `isa.json` form 中出现 `data_load_cost` / `data_store_cost` /
  `pipeline_startup_cost` / `pipeline_drain_cost`，记录
  `deprecated_isa_timing_field_ignored` 告警。
- 如果 `uarch.json` 中出现 `load_done_latency`，记录
  `deprecated_uarch_timing_field_ignored` 告警。
- 告警写入 `model_warnings.json`，但不改变 cycle。

告警 payload 建议包含：

```json
{
  "kind": "deprecated_isa_timing_field_ignored",
  "op": "VLDS",
  "form": "fp32",
  "field": "data_store_cost",
  "active_source": "isa.forms.fp32.latency",
  "reason": "mainline_uses_instruction_latency_for_lsu_duration",
  "count": 1
}
```

#### 步骤二：迁移 `core/ooo.py` 中仍被 Mainline 使用的 helper

将 `OoOCoreMainline` 仍依赖的通用逻辑迁出 `core/ooo.py`，拆成更明确的模块：

- `core/uop.py`：`Uop`、`make_mem_key()`。
- `core/ooo_timing.py`：`_latency()`、`_get_ii()`、`_ready_time_for_src()`。
- `core/ooo_readiness.py`：compute/load/store ready-cycle 计算。
- `core/exu_scheduler.py`：EXU port 选择和 II 检查。

迁移原则：

- 每次只搬一个职责，搬完跑完整回归。
- 迁移期间保持函数行为和日志字段不变。
- 不同时修改 timing 数值和模块结构。

#### 步骤三：删除或缩小旧 `OoOCore`

当 `OoOCoreMainline` 不再继承 `core/ooo.py` 中的历史类后：

- 删除 `_data_store_cost()`。
- 删除 `load_done_latency` 读取。
- 删除未被调用的 `mem_bar_mode=strong` 历史路径，或明确移动到
  `legacy_memory_barrier.py` 作为参考实现。
- 将 `core/ooo.py` 缩小为纯兼容 re-export，或直接删除。

这一步需要先用 `rg` 和回归确认没有外部 API 仍 import 旧符号。

#### 步骤四：物理清理配置字段

只有在步骤一到三完成后，才从配置中删除历史字段：

- 从 `configs/isa.json` 删除 `data_load_cost` / `data_store_cost` /
  `pipeline_startup_cost` / `pipeline_drain_cost`。
- 从 `configs/uarch.json` 删除 `load_done_latency`。
- 如需保留历史校准材料，移动到 `docs/` 或 `configs/legacy/`，并明确
  “不参与主线预测”。

### 清理回归要求

至少补以下测试：

- 修改 `data_store_cost` 不影响 store done cycle。
- 修改 `data_load_cost` 不影响 load done cycle。
- 修改 `pipeline_startup_cost` / `pipeline_drain_cost` 不影响 ready timing。
- 修改 `load_done_latency` 不影响 load done cycle。
- deprecated 字段存在时会写 `model_warnings.json`。
- 删除历史字段后，现有 VF case cycle 不变。

### 不在本阶段做的事

- 不重命名 `forwarding.json`。虽然它语义更接近 dependency delay，但改名会扩大
  API 和文档影响。
- 不同时做 VFInfo-core typed input 迁移。
- 不重新校准 latency / forwarding / II 数值。

### 目标形态

将后端输入从历史 dict program 迁移为类型化表示：

```text
VFInfo
  -> optional typed CoreIR
  -> flatten / IFU / IDU / OoO
```

核心原则：

- operand storage 来自 `ValueInfo.storage`，不再从 `V*` / `mem*` 前缀推断。
- op/form/canonicalization 在统一 instruction resolution 层完成。
- 日志中保留原始 value_id，必要时额外记录 canonical/debug name。

### 迁移阶段

#### 阶段一：保留历史 dict，但消除剩余前缀依赖

这一步在短期默认回退中已经要先做一部分，尤其是资源分类和物理寄存器 credit 预约。
后续继续清理：

- 所有 core storage 判断统一走 `ValueStorageLookup(values)`。
- 修复 IDU 中仍直接使用 `d[:1].lower() == "v"` 的物理寄存器 credit 估算。
- unroll lane 后缀通过 values/base value metadata 识别 storage。

#### 阶段二：引入类型化 CoreIR

定义 core 内部节点，例如：

- `CoreInst`
- `CoreLoop`
- `CoreMembar`
- `CoreValueRef`

字段包含：

- `op`
- `form`
- `src`
- `dst`
- `storage`
- `dtype`
- `shape`
- loop metadata

`Flattener` 和 `IFU` 先支持 CoreIR，同时保留历史 dict 兼容入口。

#### 阶段三：main.py 不再调用 VFInfoLowerer 历史 lowering

新链路：

```text
InputAPI -> VFInfo -> canonicalize/resolve -> CoreIR -> simulation
```

`VFInfoLowerer` 保留为兼容老工具的 adapter，退出主线。

#### 阶段四：日志和回归更新

- 日志输出保留 `value_id`。
- 若仍需要旧字段，提供兼容字段或转换工具。
- 更新回归 baseline 时明确记录输入 IR 迁移，但保持 cycle 行为尽量不变。

## 风险和约束

- 默认回退会让更多 case 跑通，但结果置信度下降，告警必须显眼。
- 未知 LSU 指令不能简单按 compute 处理，否则资源路径和 UB dependency 会错。
- 默认 II 新规则依赖 latency；如果 latency 也来自默认回退，告警中需要同时标出。
- load/store latency 口径切换会改变部分回归 cycle，需要单独记录 baseline 更新原因。
- `data_load_cost`、`data_store_cost`、`pipeline_startup_cost`、`pipeline_drain_cost`
  在物理删除前应先告警并完成 `core/ooo.py` helper 迁移，避免误删 Mainline
  仍复用的基础逻辑。
- VFInfo-core 迁移应与默认回退分开提交，避免同时改变行为和输入结构。

## 建议提交顺序

1. load/store latency 口径清理：自身 ISA `latency` 为准，停用主线重复字段。
2. 资源分类一致性修复，尤其是 `simulator_runner._inst_reservation()`。
3. 显式 membar 控制单元：支持 `VST_VLD` / `VLD_VST`。
4. `ParamDB` fallback resolution + 告警收集器。
5. OoO/ISU/isa_traits 改为统一 resolution。
6. 在最终 program 上做未支持指令预扫描。
7. 输出 `model_warnings.json` 中的 instruction fallback warnings，且不依赖 vreg warning 是否存在。
8. 增加 load/store latency、membar、未支持指令、缺失 forwarding、load/store forwarding 默认值、缺失 II 测试。
9. 启动废弃 OoO 和冲突配置清理：先告警、再迁移 helper、最后物理删字段。
10. 后续单独启动其它 `SMEM_BAR` 类型建模。
11. 后续单独启动 VFInfo-core typed input 迁移。
