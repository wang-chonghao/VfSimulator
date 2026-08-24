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

## 当前已落地状态

截至当前分支，下面几项已经进入 Python 和 C++ 主线实现，并有回归覆盖：

- 未支持 op/form 会在 `ParamDB` 中使用默认参数继续运行，并写入
  `model_warnings.json`。
- Python API 路径和 CLI 路径都会输出 `model_warnings.json`，不再依赖 vreg warning
  是否存在。
- load/store 执行完成时间已统一使用该指令自身 form 的 `latency`。
- 资源分类查询已统一传入 `ParamDB` 和指令 form；Python IDU 不再用全局 dtype
  判断 `VSSTB.b16` 等 form-specific 指令。
- 显式 `membar` 已支持 `VST_VLD` / `VLD_VST`，gate 位于 OoO/LSQ issue/start 阶段，
  不在 IDU dispatch 前制造 head-of-line blocking。
- 新增 `VPACK.b32` 和 `VSSTB.b16`：
  - `VPACK.b32`：compute，`latency = 11`，`dispatch_exu = EXU0_ONLY`。
  - `VSSTB.b16`：store，`latency = 9`。
  - 已补 `VCVT_F32_TO_F16 -> VPACK`、`VPACK -> VADD(fp16)`、
    `VPACK -> VSSTB` forwarding。
- 新增 `VEXPDIF.fp32` 和 `VMULSCVT.f32_to_f16`：
  - `VEXPDIF.fp32`：compute，`latency = 18`，`dispatch_exu = EXU01`。
  - `VMULSCVT.f32_to_f16`：compute，`latency = 8`，`dispatch_exu = EXU01`。
  - 旧测量表中的 startup/drain/load/store 分别记录到
    `pipeline_startup_cost`、`pipeline_drain_cost`、`data_load_cost`、
    `data_store_cost`，不重新接入主线 timing。
  - 已补 `VMULS -> VEXPDIF`、`VEXPDIF -> VMULSCVT`、
    `VMULSCVT -> VPACK` forwarding。
- 新增 `VCVT_F32_TO_BF16.f32_to_bf16` 和 `VSTS.bf16`：
  - `VCVT_F32_TO_BF16.f32_to_bf16`：compute，`latency = 7`，
    `dispatch_exu = EXU01`，`EXU = ALU`。
  - `VSTS.bf16`：store，`latency = 9`。
  - 已补 `VEXP -> VCVT_F32_TO_BF16`、`VCVT_F32_TO_BF16 -> VSTS.bf16`、
    `VCVT_F32_TO_BF16 -> VPACK`、`VEXPDIF -> VADD` forwarding。
- CCE adapter 已对 `vpack(...)` 和 `vsstb(...)` 做专门解析：
  - `vpack(dst_cast_reg, src_cast_reg, LOWER/UPPER)` 映射为 `VPACK.b32`。
  - `vsstb(src_reg, ub_ptr, config, pred, mode)` 映射为 `VSSTB.b16`。
  - C cast 表达式会提取最后一个真实变量名，例如
    `(vector_u16 &)vreg` 和 `((__ubuf__ half *&)ptr)`。
  - vector 声明提取只匹配完整 declaration statement，不会把 `vpack` cast
    误识别成 `vector_u16` 声明。
  - 逐语句解析时也会登记 `vector_*` 声明，避免 loop body 第一条
    `vector_f32 x0;` 因预扫描和 `for (...) {` 粘连而漏记，导致后续
    `vmuls(x0, x0, ...)` / `vexpdif(..., x0, ...)` 的源操作数被丢弃。
- 新增临时参数兼容层，独立维护 `b16 -> fp16`、`b32 -> fp32`：
  - Python: `core/param_compat.py`
  - C++: `native/ParamCompat.h` / `native/ParamCompat.cpp`
  - 该层只在模型参数缺失时提供兼容候选，不改变输入、日志和真实 form。

## Python 侧 SHQ 到 EXQ 分发策略实验

### 背景与目标

当前 Python 主线 queue-level4 的计算指令路径是：

```text
IDU -> OoO/SHQ -> EXQ -> EXU
```

其中 `core/isu.py` 的 `enqueue_shq_to_exq()` 会在每条 ready 指令进入 EXQ
时预测各个合法端口的最早 issue cycle，并选择 `(predicted_issue, occ, port)`
最小的端口。这等价于“哪个 EXU/EXQ 看起来能更快执行，就把指令送到哪里”。

这个策略对软件预测有利，但不一定符合硬件的固定分发行为。新的 Python 实验目标是：

- 按 ISA 参数中的 `EXU` 字段把 compute 指令分成 `ALU` 和 `SFU` 两组。
- `ALU` 组和 `SFU` 组分别维护 round-robin 分发指针。
- 同一组内部保持 FIFO：更晚的同组指令不能绕过更早的同组指令进入 EXQ。
- `ALU` 与 `SFU` 两组之间允许乱序：早的 ALU 被挡住时，后面的 SFU 仍可进入
  SFU 分发路径，反之亦然。
- `dispatch_exu = EXU0_ONLY` 的指令仍只能进入 EXU0/EXQ0，例如 `VPACK.b32`
  这类单端口指令不能被 round-robin 分到 EXU1。
- 本阶段只修改 Python 侧，不同步 C++ native 实现。

### 拟采用的具体语义

在每个 cycle 的 SHQ 扫描中：

1. 仍按 SHQ 程序顺序遍历指令。
2. 对每条 compute 指令读取 `fu_type = ALU/SFU`。
3. 如果该 `fu_type` 本周期已经被一个更早的 ready 同组指令阻塞，则跳过当前指令，保持组内
   FIFO。
4. 如果当前指令尚未 ready，则它不参与本周期 SHQ->EXQ 分发仲裁，也不阻塞后面已经
   ready 的同组指令。也就是说，这里的 FIFO 是 ready 候选分发 FIFO，不是按数据未
   ready 的指令做全局同组 head-of-line blocking。
5. 如果当前 ready 指令存在同周期源 hazard，则标记该 `fu_type` 本周期 blocked；
   后续 ready 同组指令不能绕过。
6. 如果当前 ready 指令没有合法可用端口，只在它的合法端口集合已经覆盖全部 EXQ
   端口时标记该 `fu_type` 本周期 blocked。若它是 `EXU0_ONLY` 这类窄端口指令，
   因 EXQ0 满而无法入队时，不阻塞后面可进入 EXQ1 的 ready ALU 指令。
7. `fu_round_robin_exu0_reserve` 策略下，若当前 ready 指令是 `EXU01` / `EXU012`
   这类 flexible 指令，则根据 SHQ 前瞻窗口中的 `EXU0_ONLY` compute 数量做
   平衡预留：
   - 前瞻窗口大小由 `configs/uarch.json` 的 `exu0_reserve_lookahead` 配置。
   - 当前实验值为 `8`，代码中不写死该值。
   - 触发阈值由 `configs/uarch.json` 的 `exu0_reserve_min_count` 配置。
   - 当前值为 `1`，即窗口内至少存在一条 `EXU0_ONLY` 时启用平衡预留。
   - 窗口从当前指令之后开始，最多看 8 条 SHQ compute 指令。
   - 窗口内指令不要求已经 ready；near-ready 的 `EXU0_ONLY` 也会形成 EXQ0 压力。
   - 若窗口内有 `n` 条 `EXU0_ONLY`，则 flexible 指令分发后尽量让
     `非 EXQ0 平均占用 - EXQ0 占用` 接近 `n`。
   - 端口选择只筛掉会让队列差值进一步偏离目标的候选；候选误差相同时仍由该
     FU 的 RR 指针决定，因此达到目标差值后 flexible 指令可以继续进入 EXQ0。
   - 该规则替代旧的“只要达到阈值就过滤 EXQ0”二值预留，避免 EXQ0 长时间
     空闲而 EXQ1 拥堵。
8. 对 ready 且可入队的指令，在它的合法端口集合内按该 `fu_type` 的 round-robin
   指针选择第一个可用端口。
9. 入队成功后只推进该 `fu_type` 的 round-robin 指针。

这个策略和当前贪心预测策略的主要差异是：端口选择不再看“哪个端口预测更早 issue”，
而是由 `ALU` / `SFU` 两套固定 RR 分发器决定。EXQ 内部仍保留现有行为：

- 每个 EXQ port 内仍分 `ALU` / `SFU` 两个 FIFO 子队列。
- EXQ 到 EXU issue 阶段仍允许 ALU/SFU 之间按 ready/recv/inst_id 仲裁。
- II、forwarding、EXQ depth、inflight cap、same-cycle source hazard 等约束继续生效。

### 影响文件

本阶段计划修改：

- `core/ooo_mainline.py`
  - 增加 Python 主线 OOO 的 `ALU` / `SFU` 独立 RR 指针配置和状态。
- `core/isu.py`
  - 修改 `enqueue_shq_to_exq()` 的端口选择策略。
  - 保留旧贪心策略的代码路径，便于本地对比和必要时回退。
- `configs/uarch.json`
  - 增加默认分发策略字段，使 Python 主线默认使用新的
    `fu_round_robin_exu0_reserve`。
  - 增加 `exu0_reserve_lookahead = 8`，作为 EXU0_ONLY 前瞻保护窗口大小。
  - 增加 `exu0_reserve_min_count = 1`，作为 EXU0_ONLY 压力触发阈值。
- `tests/test_instruction_fallback.py`
  - 增加最小调度测试，验证 `EXU0_ONLY` 不会被分到 EXU1，并验证 ALU/SFU 可跨组乱序。

不在本阶段修改：

- `native/*` C++ 实现。
- ISA 参数、forwarding 和 II 表。
- load/store LSQ 调度路径。
- mem_bar 控制语义。

### 验收与精度评估

开发完成后执行：

```text
python3 -m unittest discover tests
python3 tools/run_cost_model_regression.py --tier smoke --out-dir /tmp/vfsim_reg_after_shq_rr
```

并和修改前 `/tmp/vfsim_reg_before_shq_rr/current_metrics.json` 对比：

- 每个 case 的 `vf_end` 变化。
- 有 CCE ground truth 的 case 的 `error_to_cce_abs` / `error_to_cce_rel` 变化。
- 汇总平均绝对误差和平均相对误差，判断整体精度是提高、降低还是基本不变。

### 当前实验观察

softmax `macro_instr_ir_layout` 上，CA model 参考值为 u1=713、u4=594。

修复 loop body 首条 vector 声明漏记后，当前实验结果为：

| 策略 | u1 | u4 |
|---|---:|---:|
| `fu_round_robin_fifo`, cap=7 | 677 | 686 |
| `fu_round_robin_exu0_reserve`, `lookahead=8`, `min_count=1`, cap=7 | 706 | 674 |
| `fu_round_robin_exu0_reserve`, `lookahead=8`, `min_count=2`, cap=7 | 674 | 669 |
| greedy, cap=7 | 629 | 651 |
| `fu_round_robin_exu0_reserve`, `lookahead=8`, `min_count=2`, cap=8 | 624 | 590 |

结论：

- `EXU0_ONLY` reserve 能解释一部分 u4 误差，但不是全部。
- CCE adapter 漏记 loop body 首条 vector 声明会丢失 lane0 RAW 依赖，是独立的输入
  语义问题；修复后模型更保守。
- live range normalization 对 loop-carried accumulator 的出口别名必须传播到 loop 后
  的 sibling 指令；否则 u1 中 `vsts(sum, ...)` 会错误读取 `vdup` 初始值，而不是
  loop 内最后一次 `vadd` 的结果，导致 u1 明显偏乐观。
- loop 出口别名需要有 kill 语义：如果 loop 后某个普通 sibling 重新定义了同名逻辑
  vreg，例如 `VADD ... -> V5`，则后续 `VSTS V5` 应读取新定义的 `V5`，不能继续使用
  旧的 loop exit alias。
- 当时在 loop-carried 修复尚未完整的中间工作区记录了下表。该结果后来被误写成
  可复现基线；原始日志位于 `/tmp/vfsim_min1_cap8/min1_cap7`，但其依赖图仍然错误，
  因此只能作为历史实验记录，不能用于精度校准：

| 策略 | u1 | u4 |
|---|---:|---:|
| `fu_round_robin_fifo`, cap=7 | 661 | 686 |
| `fu_round_robin_exu0_reserve`, `lookahead=8`, `min_count=1`, cap=7 | 715 | 664 |
| `fu_round_robin_exu0_reserve`, `lookahead=8`, `min_count=2`, cap=7 | 685 | 651 |
| greedy, cap=7 | 626 | 645 |
| `fu_round_robin_exu0_reserve`, `lookahead=8`, `min_count=2`, cap=8 | 628 | 592 |

- 旧日志中，max reduction 的 `VMAX maxN, maxN, vrow -> maxN` 被错误归一化为
  `VMAX ..., vrow -> vrow`，四个独立 accumulator 被合并；u1 sum loop 的
  `VADD x, sum -> sum` 也被改为 `VADD V6, V5 -> V6`，随后下一轮 `VLDS -> V6`
  覆盖了前一轮 sum，loop-carried 依赖断开。因此旧 `min1/cap7` 的 `715/664` 不是
  合法程序依赖图上的预测。
- 完成“loop-carried 目的版本保留入口槽位，且该槽位不能被临时值复用”的修复后，
  当前提交与前端重构前 checkpoint `a04f8b1` 对同一 CCE 均稳定复现
  `u1=705、u2=665、u4=670`。当前日志中 max0..max3 分别保留在 `V0..V3`，u1 sum
  保留在 `V5` 并形成逐迭代回边。
- 因此基于旧错误依赖图得到的 cap 敏感性数值不能继续作为参数选择依据。是否调整
  `exq_issue_inflight_cap_per_port`，必须在当前正确依赖图上重新做独立实验。

## 当前问题

### 未支持指令识别分散

当前没有统一的未支持指令识别层：

- JSON adapter 直接接受 `op` 字段，不查 ISA 表。
- CCE adapter 会解析 `__VEC_SCOPE__` 内的向量调用，并做一部分 op 规范化：
  - `VLD` / `VLDS` 识别为加载风格指令。
  - `VST` / `VSTS` / `VSTUS` / `VSTAS` / `VSSTB` 识别为存储风格指令。
  - `VPACK` 会按 CCE intrinsic 形态专门解析为 `VPACK.b32`。
  - `VSSTB` 会按 store intrinsic 形态专门解析为 `VSSTB.b16`。
  - `VCVT` 会根据源/目的 dtype 推断 form，并在可识别时 specialize 成
    `VCVT_F32_TO_F16` 等 op。
  - 其它 `v*` callee 基本按大写 op 进入 `VFInst`。
- `isa_traits.py` 和 `ParamDB` 在元数据缺失时会按统一 fallback 规则分类和补默认参数。
- `ParamDB.get_inst_form()` 找不到 op/form 时默认不再直接抛错，而是记录 fallback
  warning；配置 schema 错误仍然抛错。

这会导致同一个未知 op：

- 前端可能正常通过。
- IDU 资源分类可能已经把它当 compute。
- OoO / ISU 查询 latency、EXU 或 dispatch port 时会拿到 fallback 参数，但结果置信度
  需要通过 warning 暴露。

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

Python 与 C++ 主线均已采用这一规则。旧的独立 load duration 配置和 producer
`data_store_cost` store duration 路径已经移除，`VLDS -> VSTS` 不再受生产者历史
字段影响。

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

输入层可以产生 `Membar` / `membar` 节点。历史实现曾依赖 `mem_bar_mode=strong`
和 `mem_inter_*` 形式建模跨 block memory ordering；该隐式 strong 路径已经删除。
当前 memory barrier 只通过显式 membar 建模：

- `membar` 不属于 `LOAD` / `STORE` / `COMPUTE`。
- `membar` 不进入 IDU window，不占 SHQ / LSQ / EXQ / EXU。
- IFU 仍然按动态指令流顺序吐出 `membar` 节点。
- runner 在取指阶段识别 `membar`，送入控制单元。
- 控制单元根据 `membar` 类型阻塞 OoO/LSQ 中后续受影响的 load/store 实际发射，
  不阻塞 IDU 将后续普通指令送入 OoO。
- 被 `membar` 阻塞的 load/store 仍保持 ready 状态，但 `sim_history.json` 需要记录
  `event = "blocked"` 和 `blocked_reason = "membar"`，用于区分数据未 ready 和
  barrier 限制发射。

短期只实现 `VST_VLD` 和 `VLD_VST`：

```text
VST_VLD: 前面所有 vector store 完成后，后续 vector load 才允许从 LSQ 发射。
VLD_VST: 前面所有 vector load 完成后，后续 vector store 才允许从 LSQ 发射。
```

判断前后关系应使用 IFU 生成的动态单调序号 `stream_seq`，静态 `pc` 只用于日志和调试。
字段语义采用以下约定：

- `pc`：Flattener 生成的静态程序位置，同一静态指令在 loop/unroll 展开后会重复出现。
- `stream_seq`：IFU 生成的动态指令流序号，`inst` 和 `membar` 共享同一个单调递增序列。
- `ControlUnit` 只使用 `stream_seq` 判断 barrier 前后关系。
- `barrier`：控制单元接收时做大小写归一化，并支持 `SMEM_BAR.VST_VLD` /
  `MEMBAR.VST_VLD` 这种带点形式，实际匹配最后一段。

显式 `membar` 的 gate 层级应位于 OoO/LSQ issue/start 阶段，而不是 IDU
dispatch 前，也不应把受阻塞的 load/store 提前标记为 not-ready。后续 compute
指令如果不依赖被 barrier 延后的 load，仍可进入 SHQ 并按正常依赖和资源规则乱序执行；
如果 compute 依赖该 load，则通过普通寄存器 ready 机制自然延后。

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
- 独立的 uarch load duration 配置已经删除。
- 删除或停用主线中的 producer `data_store_cost` store duration 路径。
- `data_load_cost` / `data_store_cost` 不再作为主线执行时长来源。
- `pipeline_startup_cost` / `pipeline_drain_cost` 不再作为主线 ready timing 来源。

需要覆盖的位置：

- `core/ooo_mainline.py` 的 load issue 路径。
- `core/ooo_mainline.py` 的 store issue 路径。
- `core/ooo.py` 中历史 `_data_store_cost()` 调用已停用；无引用 helper 已删除。
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
- IDU dispatch 不查询 `ControlUnit`；`membar` 不影响后续普通指令进入 OoO。
- OoO/LSQ 在 load/store 实际 issue/start 前查询 `ControlUnit`，被 active
  barrier 约束的 load/store 保持 ready 状态但暂不从 LSQ 发射。
- `ControlUnit` 每周期根据 IDU window、IDU-to-OOO pipe 和 OoO 中 `stream_seq`
  小于 barrier 的等待类 load/store 是否完成来释放 barrier。
- `ControlUnit` 释放 barrier 后应清理内部记录；仿真完成条件需要包含
  `ControlUnit.empty()`，避免末尾 barrier 在释放前提前结束。

完整支持其它 `SMEM_BAR` 类型放到后续阶段。旧的 `mem_bar_mode=strong` 已从
Python/C++ 活跃路径和默认配置中删除。

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

当前实现中，`ParamDB.get_ii()` 缺 pair 时使用以下默认回退策略：

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

这个规则已有回归覆盖，但仍需要后续硬件或 camodel 校准验证。实现时需要记录
`missing_ii_pair` 告警，标明：

- prev op/form
- cur op/form
- prev latency
- cur latency
- latency 是否来自默认回退
- 使用的 II 默认值

### 临时参数兼容 form 策略

由于当前 ISA / forwarding / II 参数表主要覆盖 `fp16` 和 `fp32`，而外部输入和部分
真实指令会出现 `b16` / `b32` form，短期引入专门的参数兼容层：

```text
b16 -> fp16
b32 -> fp32
```

这只是“参数缺失时借用”的临时策略，不是输入规范化规则。原则如下：

- API / CCE / JSON 输入仍保留真实 form，例如 `VADD.b16`、`VPACK.b32`。
- 日志和 warning 中也保留 requested form，不能把 `b16` 静默改写成 `fp16`。
- 兼容 fallback 只在同一个 opcode 内发生，不跨 opcode。
- 如果 `isa.json` 中存在显式 `OP.b16` 或 `OP.b32`，必须优先使用显式参数。
- 如果显式 form 完全缺失但兼容 form 存在，借用兼容 form 参数，并记录
  `compatible_isa_form_fallback`。
- 除上述共享映射外，不做普通 form 间的隐式借用。例如同 opcode 只有 `fp32`
  参数而请求 `fp16` 时，Python/C++ 都使用默认参数并记录
  `unsupported_isa_form`，不能静默继承 `fp32`。
- 如果显式 form 存在但参数字段覆盖不完整，则按以下顺序合并：

```text
global defaults
-> op defaults
-> compatible form params
-> requested form params
```

例如 `VADD.b16` 只写了 `latency`，而 `VADD.fp16` 写了 `throughput`、
`data_load_cost`、`dispatch_exu` 等字段，则 `VADD.b16` 使用自己的 `latency`，
其它缺失字段继承 `VADD.fp16`。
- forwarding / II 精确 pair 缺失时，按 producer/consumer form 独立尝试兼容 key。
  例如请求 `VADD.b16 -> VMUL.b16` 时，查询顺序为：

```text
VADD.b16  -> VMUL.b16
VADD.b16  -> VMUL.fp16
VADD.fp16 -> VMUL.b16
VADD.fp16 -> VMUL.fp16
```

命中兼容 pair 时分别记录：

- `compatible_forwarding_pair_fallback`
- `compatible_ii_pair_fallback`

实现位置保持独立，方便后续删除或扩展：

- Python: `core/param_compat.py`
- C++: `native/ParamCompat.h` / `native/ParamCompat.cpp`

`ParamDB` 只调用该 helper，不直接内嵌 `b16/b32` 特例。

## 告警设计

### 告警类型

至少记录以下告警：

- `unsupported_isa_op`：op 不在 `isa.json` 中，使用默认 ISA 参数。
- `unsupported_isa_form`：op 存在但 form 不存在，使用默认 form 参数。
- `missing_forwarding_pair`：forwarding pair 不在表中，使用默认 forwarding。
- `missing_ii_pair`：II pair 不在表中，使用默认 II。
- `compatible_isa_form_fallback`：requested form 缺 ISA 参数，但同 op 的兼容 form
  存在，借用兼容 form 参数。
- `compatible_forwarding_pair_fallback`：requested forwarding pair 缺失，但兼容 form
  pair 命中。
- `compatible_ii_pair_fallback`：requested II pair 缺失，但兼容 form pair 命中。
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

对兼容 form 参数借用：

```json
{
  "kind": "compatible_isa_form_fallback",
  "op": "VADD",
  "requested_form": "b16",
  "used_form": "fp16",
  "count": 1
}
```

对兼容 forwarding pair：

```json
{
  "kind": "compatible_forwarding_pair_fallback",
  "producer": "VADD.b16",
  "consumer": "VMUL.b16",
  "used_producer": "VADD.fp16",
  "used_consumer": "VMUL.fp16",
  "count": 1
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
- barrier 后如果先出现被阻塞的 load/store，再出现无关 compute，无关 compute
  不应因为 `membar` 产生 IDU head-of-line blocking。
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

`core/ooo.py` 的 `_inst_params()`、`_latency()`、`_get_fu_type()`、
`_eligible_exu_ports()` 都应从同一份 resolved params 读取。

在 `ParamDB` 内部维护结构化告警聚合：

- 按告警键去重。
- 记录 count。
- 记录少量 sample op/form/inst_id。

### 步骤五点五：新增具体指令覆盖和 CCE intrinsic 解析

已新增 `VPACK` / `VSSTB` 的最小覆盖：

- `VPACK.b32`
  - `op_class = COMPUTE`
  - `latency = 11`
  - `dispatch_exu = EXU0_ONLY`
  - 当前 dtype 记为 `bf16`，参数按 `b32` form 查询。
- `VSSTB.b16`
  - `op_class = STORE`
  - `latency = 9`
  - 当前 dtype 记为 `bf16`，参数按 `b16` form 查询。

已知 forwarding：

```text
VCVT_F32_TO_F16.f32_to_f16 -> VPACK.b32 = 4
VPACK.b32 -> VADD.fp16 = 8
VPACK.b32 -> VSSTB.b16 = 9
```

CCE adapter 要求：

- `_STORE_OPS` 包含 `VSSTB`。
- `vpack((vector_u16 &)dst, (vector_u32 &)src, LOWER/UPPER)` 解析为
  `VFInst("VPACK", form="b32", dst=[dst], src=[src])`。
- `vsstb(src_reg, ((__ubuf__ half *&)ub_ptr), config, pred, mode)` 解析为
  `VFInst("VSSTB", form="b16", src=[src_reg], dst=[ub_ptr])`。
- `_base_identifier()` 对 cast 表达式取最后一个真实变量名。
- vector 声明提取只匹配完整 declaration statement，不能从函数调用 cast 中抽取
  `vector_u16` / `vector_u32` 作为变量声明。

验收：

- 真实 CCE 写法中 `vpack` / `vsstb` 解析后能命中新配置。
- `vpack` cast 不会覆盖原始 `vector_f16` 声明 dtype。
- Python 和 C++ 都有 `VLDS -> VPACK -> VSSTB` 调度回归。

已新增 `VEXPDIF` / `VMULSCVT` 的最小覆盖：

- `VEXPDIF.fp32`
  - `op_class = COMPUTE`
  - `pipeline_startup_cost = 7`
  - `latency = 18`
  - `pipeline_drain_cost = 16`
  - `data_load_cost = 9`
  - `data_store_cost = 9`
  - `dispatch_exu = EXU01`
- `VMULSCVT.f32_to_f16`
  - `op_class = COMPUTE`
  - `pipeline_startup_cost = 6`
  - `latency = 8`
  - `pipeline_drain_cost = 6`
  - `data_load_cost = 9`
  - `data_store_cost = 9`
  - `dispatch_exu = EXU01`

已知 forwarding：

```text
VMULS.fp32 -> VEXPDIF.fp32 = 5
VEXPDIF.fp32 -> VMULSCVT.f32_to_f16 = 15
VMULSCVT.f32_to_f16 -> VPACK.b32 = 5
```

旧 microbenchmark 表里的 startup/drain/load/store 仍按当前 schema 记录在历史字段中，
其中 load/store 字段不参与主线 load/store 执行时长计算。

已新增 `VCVT_F32_TO_BF16` / `VSTS.bf16` 覆盖：

- `VCVT_F32_TO_BF16.f32_to_bf16`
  - `op_class = COMPUTE`
  - `pipeline_startup_cost = 6`
  - `latency = 7`
  - `pipeline_drain_cost = 5`
  - `dispatch_exu = EXU01`
  - `EXU = ALU`
- `VSTS.bf16`
  - `op_class = STORE`
  - `pipeline_startup_cost = 8`
  - `latency = 9`
  - `pipeline_drain_cost = 0`

已知 forwarding：

```text
VEXP.fp32 -> VCVT_F32_TO_BF16.f32_to_bf16 = 13
VCVT_F32_TO_BF16.f32_to_bf16 -> VSTS.bf16 = 5
VCVT_F32_TO_BF16.f32_to_bf16 -> VPACK.b32 = 4
VEXPDIF.fp32 -> VADD.fp32 = 15
```

已补 II 矩阵：

```text
prev/cur: VEXPDIF.fp32, VPACK.b32, VMULSCVT.f32_to_f16, VADD.fp32
```

### 步骤五点六：临时兼容 form helper

新增独立 helper 维护 `b16 -> fp16`、`b32 -> fp32`：

- Python: `core/param_compat.py`
- C++: `native/ParamCompat.h` / `native/ParamCompat.cpp`

`ParamDB` 通过 helper 获取：

- 单条 form 的兼容候选。
- producer/consumer pair 的兼容 key 候选。

验收：

- `VADD.b16` 在 `VADD.b16` 未覆盖时借用 `VADD.fp16` 参数。
- `VADD.b16` 如果只覆盖部分参数，缺失字段从 `VADD.fp16` 继承。
- `hasInst("VADD", "b16")` 为 true。
- `VADD.b16 -> VMUL.b16` forwarding 可借用 `VADD.fp16 -> VMUL.fp16`。
- `VADD.b16 -> VMUL.b16` II 可借用 `VADD.fp16 -> VMUL.fp16`。
- 兼容借用会记录 `compatible_*_fallback` warning。
- 显式存在的 `VPACK.b32` / `VSSTB.b16` 不应走兼容 form fallback。

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
- 被 barrier 阻塞的 load/store 不阻塞后续无关 compute 进入 SHQ 或执行。
- `VPACK.b32` 参数为 `latency = 11` 且 `dispatch_exu = EXU0_ONLY`。
- `VSSTB.b16` 参数为 store 且 `latency = 9`。
- `VCVT_F32_TO_F16 -> VPACK`、`VPACK -> VADD(fp16)`、`VPACK -> VSSTB`
  forwarding 命中显式表。
- CCE `vpack((vector_u16 &)dst, (vector_u32 &)src, LOWER)` 正确解析为
  `VPACK.b32`，且忽略第三个 selector 参数的数据依赖。
- CCE `vsstb(src, ((__ubuf__ half *&)ptr), ...)` 正确解析为 `VSSTB.b16` store。
- CCE cast 不会让 vector 声明提取把寄存器 dtype 从 `fp16` 覆盖成 `u16`。
- Python IDU 使用当前 instruction form 做资源分类，不会把 `VSSTB.b16` 查成
  `VSSTB.fp32`。
- `b16` / `b32` form 缺参数时可通过独立兼容 helper 借用 `fp16` / `fp32` 参数。
- 兼容 ISA / forwarding / II fallback 均有 warning。

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

当前主线规则应保持为：

- load/store 执行时长只取该指令自身 form 的 `latency`。
- producer/consumer ready timing 只通过 `forwarding.json` 查询，缺表时走
  `ParamDB` fallback 并告警。
- issue spacing 只通过 `InitiationInterval.json` 查询，缺表时走 `ParamDB`
  fallback 并告警。

### 清理步骤

#### 步骤一：标记历史字段为 ignored/deprecated

对仍留在 `isa.json` 中的历史字段增加配置校验或启动期扫描：

- 如果 `isa.json` form 中出现 `data_load_cost` / `data_store_cost` /
  `pipeline_startup_cost` / `pipeline_drain_cost`，记录
  `deprecated_isa_timing_field_ignored` 告警。
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

- `core/uop.py`：`Uop`。UB 顺序只由显式 Membar 或未来的显式 dependency edge 控制，不再迁移已删除的地址 key helper。
- `core/ooo_timing.py`：`_latency()`、`_get_ii()`、`_ready_time_for_src()`。
- `core/ooo_readiness.py`：compute/load/store ready-cycle 计算。
- `core/exu_scheduler.py`：EXU port 选择和 II 检查。

迁移原则：

- 每次只搬一个职责，搬完跑完整回归。
- 迁移期间保持函数行为和日志字段不变。
- 不同时修改 timing 数值和模块结构。

#### 步骤三：删除或缩小旧 `OoOCore`

当 `OoOCoreMainline` 不再继承 `core/ooo.py` 中的历史类后：

- `_data_store_cost()` 无引用 helper 已删除。
- 独立的 uarch load duration 读取已经删除。
- `mem_bar_mode=strong` 历史路径已经删除，后续清理只需要确认没有外部 trace 仍依赖
  `mem_inter_*` 的隐式 barrier 语义。
- 将 `core/ooo.py` 缩小为纯兼容 re-export，或直接删除。

这一步需要先用 `rg` 和回归确认没有外部 API 仍 import 旧符号。

#### 步骤四：物理清理配置字段

只有在步骤一到三完成后，才从配置中删除历史字段：

- 从 `configs/isa.json` 删除 `data_load_cost` / `data_store_cost` /
  `pipeline_startup_cost` / `pipeline_drain_cost`。
- 独立的 uarch load duration 字段已经从 Python、C++ 和配置中删除。
- 如需保留历史校准材料，移动到 `docs/` 或 `configs/legacy/`，并明确
  “不参与主线预测”。

### 清理回归要求

至少补以下测试：

- 修改 `data_store_cost` 不影响 store done cycle。
- 修改 `data_load_cost` 不影响 load done cycle。
- 修改 `pipeline_startup_cost` / `pipeline_drain_cost` 不影响 ready timing。
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
- API/adapter 层新增输入符号规范化层，集中维护外部 dtype/opcode/storage/membar
  写法到核心 canonical 字符串的映射。
- 日志中保留原始 value_id，必要时额外记录 canonical/debug name。

### 输入符号规范化层

新增 `api/input_symbols.py`，职责限定在外部输入规范化：

- `DType`：维护常见 dtype alias，例如 `float32 -> fp32`、`f32 -> fp32`、
  `s32 -> int32`。未知 dtype 保留原字符串，交给后端 fallback/warning。
- `OpCode`：维护常见外部 opcode alias，例如 `vld -> VLDS`、`vst -> VSTS`、
  `vadd -> VADD`、`vcvt -> VCVT`。它只是便利用法和常见 alias 集合，不是支持清单；
  未知 opcode 只做大写规范化。
- `StorageKind`：维护 `Register`、`UB`、`Scalar`。
- `MembarType`：维护 `VST_VLD`、`VLD_VST`。
- alias map 维护外部可能写法，例如 `float32 -> fp32`、`f32 -> fp32`、
  `vld -> VLDS`、`reg -> Register`、`SMEM_BAR.VST_VLD -> VST_VLD`。
- normalize 函数只在 API / adapter / lowering 层调用，核心 IFU / IDU / OoO /
  ParamDB 尽量只接收 canonical 字符串。
- `VCVT` 作为外部泛化 opcode 做 best-effort specialize：已知转换
  `f32_to_f16`、`f16_to_f32`、`f32_to_s32`、`s32_to_f32` 映射到具体
  `VCVT_*` opcode；未知转换保留为 `VCVT`，由 `ParamDB` fallback/warning 承接。
- `membar` 只做大小写和带点名称归一化。未知 membar 类型保留 canonical 字符串，
  由 `ControlUnit` 记录 `unsupported_membar_type` warning。

该层不替代 `isa.json`。`isa.json` 仍然决定某个 canonical opcode + form 是否有
模型参数；若没有参数，继续由 `ParamDB` 的 fallback / warning 逻辑处理。该层不维护
opcode 支持矩阵，不做 strict 支持性校验，避免和 `isa.json` 形成双源真相。

公开 API / JSON adapter / CCE adapter 和历史 lowered core payload 采用同一套策略：
输入符号尽量规范化，然后进入 core。未覆盖指令、form、forwarding、II 或 membar 统一
由 `ParamDB` / `ControlUnit` 使用默认参数并写入 `model_warnings.json`。
Python API 路径也必须写出同样的 `model_warnings.json`，不能只在 CLI `main.py`
路径可见。

### 迁移阶段

#### 阶段一：引入 API 输入符号规范化

- 新增 `api/input_symbols.py`。
- `ValueInfo` 构造阶段规范化 `storage` / `dtype`。
- `canonicalize_vf_info()` 规范化 `VFInst.name`、`form`、`Membar.type` 和
  `default_dtype`。
- `canonicalize_vf_info()` 对 `VCVT` 做 best-effort specialize。
- JSON / CCE adapter 继续返回 `VFInfo`，不直接把外部别名传入 core。
- 增加回归覆盖小写 opcode、dtype alias、storage alias、带点 membar 名称、VCVT
  specialize、未知符号保留并继续走 core fallback。

#### 阶段二：保留历史 dict，但消除剩余前缀依赖

这一步在短期默认回退中已经要先做一部分，尤其是资源分类和物理寄存器 credit 预约。
后续继续清理：

- 所有 core storage 判断统一走 `ValueStorageLookup(values)`。
- 修复 IDU 中仍直接使用 `d[:1].lower() == "v"` 的物理寄存器 credit 估算。
- unroll lane 后缀通过 values/base value metadata 识别 storage。

#### 阶段三：引入类型化 CoreIR

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

#### 阶段四：main.py 不再调用 VFInfoLowerer 历史 lowering（已完成）

新链路：

```text
旧 JSON / 旧 VFInfo / CCE -> CanonicalVfInfo -> CoreLoweringPass -> simulation
```

`VFInfoLowerer` 保留为兼容 API 名称，但内部同样执行 Canonical 版本化和 lowering；Python/C++ 公共预测入口均已退出旧 normalization 主线。

#### 阶段五：日志和回归更新

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
9. 增加 `VPACK.b32` / `VSSTB.b16` 配置和 CCE intrinsic 解析回归。
10. 增加独立参数兼容 helper，支持 `b16 -> fp16`、`b32 -> fp32` 临时借参。
11. 启动废弃 OoO 和冲突配置清理：先告警、再迁移 helper、最后物理删字段。
12. 后续单独启动其它 `SMEM_BAR` 类型建模。
13. 后续单独启动 VFInfo-core typed input 迁移。

## Python/C++ 指令参数热路径优化

本轮只优化参数解析和调度热路径，不调整 ready-cycle 算法，也不关闭或缩减日志。

### 统一 Instruction Profile

`core/instruction_profile.py` 定义不可变、带 `slots` 的 `InstructionProfile`。Profile
保存规范化 opcode、请求/实际 form、dtype、指令分类、FU、可分发 EXU 和 latency，
并由单个 `ParamDB` 实例分配稳定 `profile_id`。

实现约束如下：

1. `ParamDB` 加载时按 `ISA defaults -> opcode defaults -> compatible form -> 当前 form`
   预合并所有已声明 form；未知 opcode/form 仍按首次实际请求惰性生成，避免提前告警。
2. `resolve_inst(op, form, dtype)` 按三元组缓存不可变 Profile。公开
   `get_inst_form()` 继续返回 dict 副本，调用方不能修改内部缓存。
3. rename/accept 阶段将 Profile 绑定到 Uop；OoO/ISU 每周期直接读取分类、FU、端口和
   latency，不再重复解析 ISA dict。
4. Python forwarding 和 II 分别按 `(producer_profile_id, consumer_profile_id)` 与
   `(previous_profile_id, current_profile_id)` 缓存静态周期值，不缓存 ready cycle 或
   资源状态。Profile 必须由执行查询的同一个 `ParamDB` 创建，跨实例 Profile 会被
   明确拒绝，避免不同实例从相同 `profile_id` 起始值产生缓存碰撞。
5. Python 缓存属于 `ParamDB` 实例，Native 运行时参数对缓存属于 `OoOCore` 实例。
   fallback warning 按唯一缺失配置或参数对记录一次，`count` 不再反映调度热路径
   重复查询次数。
6. Native 的 form 参数原本已经在 `ParamDB` 加载时合并；Uop 在 rename 时复制小型
   profile 字段，不保存指向 fallback `unordered_map` 元素的指针。Native forwarding
   和 II 缓存属于每个 `OoOCore`，共享只读参数配置时不会由正常查询写入共享 cache。
7. `param_cache_stats` 仅在 `CoreVfCostModel(include_param_cache_stats=True)` 的 benchmark
   模式加入结果，普通公共预测结果保持不变。
8. Native `ParamDB` 可作为只读配置跨预测线程共享：`inst()` 按值返回 `InstConfig`，
   未覆盖指令不再写入共享 fallback map；warning 聚合使用互斥保护。运行期
   forwarding/II cache 仍位于各自 `OoOCore`，不会给共享配置增加热路径锁。

### 验收结果

`cce_code/softmax` 的 12 个 U1/U2/U4 case 与优化前 cycle 逐项一致。每个 case 只有
13～14 个唯一 Profile、13～14 个 forwarding pair 和 30～42 个 II pair。

代表 case `expdif_mulcvt/U4`（1161 条动态指令、完整日志）同机运行 7 次：

| 版本 | vf_end_cycle | 中位耗时 |
|---|---:|---:|
| 优化前提交代码 | 630 | 800.4 ms |
| Instruction Profile 优化后 | 630 | 314.7 ms |

当前主要耗时已经转移到完整 history JSON 写出。非调试日志模式属于后续独立优化，
不纳入本轮改动。

同一 lowered payload 的 Native Release 完整日志中位耗时由约 67.4 ms 降至
约 64.5 ms；Native 原本已完成加载期 merge，因此收益小于 Python。

## `VSTUS` / `VSTAS` 显式建模

本轮将两条指令从 timing fallback 升级为显式 STORE 模型：latency 均为 8，并通过
Catalog 声明 `vector_align` 状态参数。状态采用按动态指令序分组的 generation：
`VSTUS` 追加 producer，`VSTAS` 在进入 OoO 时封存 producer 快照并打开下一组。
`VSTAS` 不伪造 preg producer，而是在本组所有 producer start 后按配置的
`VSTUS -> VSTAS = 1` forwarding 就绪。

CCE Adapter、Canonical attributes、Python OoO 和 Native OoO 使用同一语义。
稳定 producer record 的生命周期覆盖 ROB/LSQ 副本和 producer 退休，禁止保存队列
元素裸指针。不同状态和不同 generation 分别维护，后续组不能加入已封存的前一组。
