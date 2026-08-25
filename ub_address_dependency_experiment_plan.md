# UB 地址依赖识别实验开发计划

## 1. 实验目标

当前 VfSim 只自动建立寄存器 producer-consumer 依赖。UB 中 load/store 的顺序由
显式 `Membar(VST_VLD)` 和 `Membar(VLD_VST)` 保证：

- `VST_VLD` 等待 barrier 前所有 store 完成，阻塞 barrier 后所有 load。
- `VLD_VST` 等待 barrier 前所有 load 完成，阻塞 barrier 后所有 store。

该模型符合当前硬件，但 barrier 是方向性的全局阻塞，可能等待地址完全无关的 LSU
指令。本分支用于评估一种假设微架构：硬件能够识别 UB 动态地址范围，只阻塞真正
存在地址重叠的 load/store，因此编译器可以不插入相应 Membar。

本实验不直接改变主线硬件模型。需要保留两种可对比模式：

1. **显式 Membar 基线模式**：保持当前行为，作为周期和正确性基准。
2. **UB 地址依赖实验模式**：测试输入移除 Membar，由 LSQ/OoO 根据动态 UB 地址范围
   建立局部依赖。

性能比较必须保持动态计算指令、load/store 指令、loop 和 unroll 完全一致，只改变
内存顺序机制。

## 2. 分支与阶段范围

- 初始计划基线：`master@5972f25`。
- 实际开发基线：合入最新 RR/Canonical 前端后的 `ded85be`。
- 当前复测基线：`cdb7109`，已合入主线共享 `ub_slots=2`和
  `lsu_store_priority_preg_threshold=1` 仲裁。
- 实验分支：`ub-address-dependency-experiment`。
- 本次开发只实现 Python 前端与 Python Core，用于实验性验证语义和性能收益。
- 第一阶段可以增加默认关闭的实验字段，但不改变默认行为和已有回归结果。
- UB 地址依赖实验能力仍只在 Python 侧实现。当前分支中的 C++
  变更只是同步主线共享 UB 槽和阈值仲裁，不实现
  `range_overlap` 地址依赖。
- 现有 `mem-bar-local-dep-mvp` 分支不作为实现基础；可以参考其实验结论，但不直接
  合入，避免带回旧 Core 和旧前端假设。

### 2.1 Canonical 路径约束

本实验只允许使用当前 Canonical 路径：

```text
CCE / Canonical builder
  -> CanonicalVfInfo
  -> validate_canonical_vf_info
  -> CoreLoweringPass
  -> canonical IFU dynamic identity
  -> IDU / OoO / LSQ
```

不允许使用下列旧路径实现或验证地址依赖：

- legacy JSON trace 直接进入 Core。
- `VFInfoLowerer` 产生旧 `src/dst` 字符串 payload 后再猜测地址。
- `normalize_program_vreg_live_ranges()` 或
  `canonicalize_single_super_iteration_loops()` 修补语义。
- 根据 `mem0`、`V0`、变量名前缀或 `iter_stack` 拼接 memory key。
- 在 OoO 内重新解析 CCE offset、cast、POST_UPDATE 或 pointer alias 文本。

CCE adapter 可以继续先生成逻辑 `VFInfo/VFMemoryAccess` 作为前端中间表示，但在进入
Core 前必须经 `ValueVersioningPass` 转成 Canonical memory object、pointer state 和
affine expression。Core 实验入口只接收 Canonical lowering 的结果。

A/B 两侧必须来自同一个 `CanonicalVfInfo`：基线副本保留 Membar，实验副本通过明确
transform 移除 Membar。不能让基线走 legacy、实验走 Canonical，否则 cycle 差异会
混入前端 lowering 和 loop 展开差异。

实验 runner 若收到 legacy payload，应明确报错并提示先转换为 Canonical，不提供
静默兼容 fallback。

### 2.2 仅 Python 实验范围

本次开发允许修改：

- `api/*.py` 和 `api/frontend/*.py` 中的 Python CCE/Canonical 路径。
- `core/*.py` 中的 Python IFU、OoO、LSQ 和日志实现。
- `tests/*.py`、Python 实验 runner、配置与实验文档。

本次开发禁止修改：

- `native/` 下任何 C++ 源码、头文件、CMake 或 Native 测试。
- `api/native/` 和 C++ generated schema/catalog。
- Native runner 的行为、周期基准或编译器 C++ 接口。

Canonical v1 是 Python/C++ 共享契约，因此本实验不直接把 Python-only 字段加入稳定
wire schema，避免出现同一 schema version 两端含义不同。pointer state、
POST_UPDATE delta 和动态地址生成所需的新增信息先放在独立的 Python 实验元数据
结构中，并使用 Canonical `instruction_id`、`base_object_id` 和 loop identity 作为
关联键：

```text
CanonicalVfInfo
+ PythonUbAddressExperimentMetadata
  -> ExperimentalCanonicalCoreLowering
  -> Python IFU / OoO
```

该元数据只能补充地址生成信息，不能替代 Canonical 中的 instruction、loop、value、
storage object 或 Membar 语义。实验 runner 必须先正常完成 Canonical validation，
再校验实验元数据引用的所有 node/object ID。

若实验结果证明该能力值得产品化，后续独立任务再把这些字段正式加入下一版共享
Canonical schema，并同步 Python serialization、C++ validator/lowering 和编译器
接口；这不属于本次开发范围。

## 3. 已有能力与缺口

当前前端已经能够保留下列信息：

- `base_object_id`：稳定 UB storage object。
- affine offset：常量、loop induction variable、参数及其线性组合。
- `access_kind`：`read` 或 `write`。
- `span`：已知访问跨度；未知时为 `None`。
- `stream_seq`、`static_instruction_id`、`iteration_path`：动态指令身份。

当前缺口：

1. IFU 没有把 affine offset 按当前 `iteration_path` 和参数求成动态具体地址。
2. Uop 没有稳定携带动态 `memory_range`。
3. OoO/LSQ 不维护前序动态 load/store 的地址依赖关系。
4. `NORM/NORM_B16/NORM_B32` 已按完整 256-byte 向量访问实现；B16 对应 128 个
   元素，B32 对应 64 个元素。`PK_*` 等其他模式的 span 仍需补充可审计定义。
5. CCE adapter 当前没有区分普通 offset 与 `POST_UPDATE` delta，也没有保留独立的
   UB pointer state。
6. 当前 Membar ControlUnit 仍是全局方向性 gate，这是基线模式需要保留的行为。

这些新增语义必须进入经过校验的 Python Canonical 实验元数据和 Core lowering，
不能只存在于 CCE adapter 的临时 Python 对象中，也不能退回 legacy payload。

## 4. 地址语义

### 4.1 静态访问描述

静态 Core instruction 保留前端提供的：

```text
base_object_id
pointer_state_id
pointer_initial_offset
access_offset
post_update_delta
access_kind
span
access_mode
```

`base_object_id` 用于区分独立 UB 对象，不能使用 value definition ID 或寄存器名替代。
`pointer_state_id` 用于区分指向同一 UB 对象但独立更新的指针变量。

### 4.2 动态访问范围

IFU 展开每条动态 LSU 指令时，使用当前 loop induction value 和顶层整数参数计算：

```text
memory_range = (base_object_id, byte_start, byte_end)
```

范围采用左闭右开区间 `[byte_start, byte_end)`。统一使用字节单位，避免 b16/b32、
fp16/fp32 混合访问时按元素下标比较产生错误。

CCE offset 当前表达的是相对 UB 指针的元素偏移。adapter/lowering 需要保留基址元素
类型，动态求值时再换算为 byte offset。若元素宽度无法确定，该访问不能伪造具体
范围。

### 4.3 span 来源

span 的优先级：

1. 输入显式给出的访问跨度。
2. Catalog/独立配置中经 ISA 语义确认的 `access_mode -> span`。
3. 无法确定时标记为 unresolved。

不能把 `BRC_B32` 建成 64 个元素的读取；它读取一个 scalar 后广播，UB span 为一个
B32 元素。`ONEPT_*` 同样按单点访问处理。已确认 `NORM/NORM_B16/NORM_B32` 每次
访问完整 256 bytes，因此 B16 搬运 128 个元素，B32 搬运 64 个元素；当前不根据
mask 缩小范围。`PK_*` 和其它模式在没有明确 ISA 依据前不得猜测。

临时访问模式映射应放在独立配置或独立兼容模块中，不散落为 opcode 特判。

### 4.4 普通 offset 与 POST_UPDATE

该区分是 UB 地址依赖实验的前置正确性要求。当前 CCE adapter 对 load/store 的第
3 个参数统一按本次访问 offset 处理，并与 UB alias 初始 offset 相加；带
`POST_UPDATE` 时该参数实际是访问后的指针增量，因此当前静态 memory metadata 会
给出错误的首次访问地址。

普通寻址：

```cpp
vlds(dst, base, 64 * i, NORM);
```

语义为：

```text
effective_address = pointer_current(base) + access_offset
access_offset = 64 * i
post_update_delta = 0
pointer_current 不变
```

POST_UPDATE：

```cpp
vlds(dst, ptr, 64, NORM, POST_UPDATE);
```

语义为：

```text
effective_address = pointer_current(ptr)
access_offset = 0
post_update_delta = 64
pointer_current(ptr) += 64
```

例如：

```cpp
__ubuf__ float *p0 = scores + 4 * kCols;
vlds(vrow, p0, 4 * kCols, NORM, POST_UPDATE);
```

第一次访问必须是 `scores + 4*kCols`，访问地址生成后才把 `p0` 更新为
`scores + 8*kCols`。不能把第一次访问直接解析成 `scores + 8*kCols`。

前端内存访问描述需要明确区分：

```text
base_object_id
pointer_state_id
pointer_initial_offset
access_offset
post_update_delta
span_bytes
```

约束如下：

- `base_object_id` 是稳定 UB storage object。
- 每个可更新的 CCE 指针变量拥有稳定且独立的 `pointer_state_id`。
- `p0`、`p1` 即使指向同一 storage object，也维护两个 pointer current value。
- 普通 `base + offset` 只设置 `access_offset`，不改变 pointer state。
- `POST_UPDATE` 当前访问的 `access_offset=0`，第 3 个参数进入
  `post_update_delta`。
- `NO_UPDATE` 不产生 pointer update。
- pointer alias 声明需要在声明点快照源指针的当前值并创建新 state，不能永久引用
  同一个可变 state。第一阶段若无法表达 loop 内动态 pointer 声明，应明确拒绝，
  不能退化成静态字符串 alias。
- offset、initial offset 和 update delta 必须使用同一元素单位，并在生成动态 range
  前统一换算为 byte。

IFU 按动态指令流顺序维护：

```text
pointer_current[pointer_state_id] = evaluated(pointer_initial_offset)
byte_start = pointer_current[pointer_state_id] + evaluated(access_offset)
byte_end = byte_start + span_bytes
pointer_current[pointer_state_id] += evaluated(post_update_delta)
```

pointer update 属于地址生成顺序，不等待 LSU 执行完成。它应在 IFU 产生动态访问范围
时按 `stream_seq` 更新；OoO 只接收求值后的 `DynamicMemoryRange`，不能识别
`POST_UPDATE`、CCE 参数位置或 pointer alias 语法。

loop/unroll 下 pointer state 必须按实际动态指令流推进。不能按静态 pc 合并多次
update，也不能让不同 unroll lane 共享错误的初始快照。

## 5. 局部依赖规则

第一阶段只替代当前两类 Membar 方向，不扩展到未知硬件语义：

| 前序访问 | 后序访问 | 实验依赖 |
|---|---|---|
| STORE | LOAD | range overlap 时等待前序 STORE done |
| LOAD | STORE | range overlap 时等待前序 LOAD done |
| LOAD | LOAD | 不增加依赖 |
| STORE | STORE | 第一阶段不增加新依赖，后续单独确认硬件顺序语义 |

两个范围仅在 `base_object_id` 相同且区间相交时有依赖：

```text
max(prior_start, current_start) < min(prior_end, current_end)
```

判断前后关系必须使用动态 `stream_seq`，不能使用可能在 loop/unroll 中重复的静态
`pc` 或 `static_instruction_id`。

### 5.1 UB 对象身份与 alias 边界

第一阶段只分析来自同一个原始 UB buffer 的地址依赖：

- 每个原始 UB 函数参数、局部 UB buffer 或其它明确声明的 storage object 分配唯一
  `base_object_id`。
- 从同一原始 buffer 派生出的 pointer、C cast 和 alias chain 必须继承相同的
  `base_object_id`。
- `p0`、`p1` 可以拥有不同 `pointer_state_id`，但只要来自同一个原始 buffer，仍然
  使用同一个 `base_object_id` 做 overlap 判断。
- `pointer_state_id` 只用于计算各自的动态 current pointer，不能作为内存对象 alias
  key，也不能因为 pointer state 不同就跳过依赖。
- `base_object_id` 不同，第一阶段直接视为独立 UB buffer，不建立依赖。
- 第一阶段不分析不同原始 UB 参数在运行时指向同一物理区域的可能性，不引入
  `alias_group`、may-alias 集合或跨 buffer 保守依赖。

上述最后一项是实验输入契约，而不是一般 C/C++ 指针 alias 结论。用于性能评估的
case 必须确认不同 `base_object_id` 对应不重叠的真实 UB buffer；报告中需要明确记录
该假设。若以后接入的编译器不能保证这一点，应由编译器提供 alias group，不能继续
无条件认为不同 base 独立。

cast 不改变 `base_object_id` 或指针当前 byte address，但 cast 后的 pointer
arithmetic 必须按 cast 后元素宽度换算为 byte offset。例如同一个数值 offset 作用于
`half*` 和 `float*` 时，产生的 byte delta 不同。

### 5.2 核心 overlap 判断

在两个访问都已经得到具体动态 byte range 后，地址冲突的唯一判断为：

```python
conflict = (
    prior.base_object_id == current.base_object_id
    and max(prior.byte_start, current.byte_start)
        < min(prior.byte_end, current.byte_end)
)
```

byte range 使用左闭右开区间 `[byte_start, byte_end)`，并要求
`byte_end > byte_start`。相邻但不相交的范围不冲突。只有 `conflict=True` 后才根据
访问方向决定是否建立 `STORE -> LOAD` 或 `LOAD -> STORE` 依赖。

该纯函数不处理 affine expression、POST_UPDATE、pointer alias 或 CCE 语法：这些
信息必须先由 IFU 地址生成阶段解析成 `DynamicMemoryRange`。POST_UPDATE 更新后的每
次动态地址也必须先完成求值，再进入同一 overlap 判断。

若任一访问无法得到具体 byte range，不调用上述精确判断，而是进入第 7 节定义的
same-base/global 保守 fallback。

## 6. 流水线落点

地址依赖 gate 放在 LSQ issue/start 阶段：

1. LSU 指令仍正常通过 IFU、IDU 和 OoO rename。
2. 数据、端口和队列条件满足后，该指令可以处于 ready 状态。
3. start 前查询尚未完成的前序异类 LSU 是否存在 range overlap。
4. 存在依赖时不允许 start，但不阻塞无关 compute，也不阻塞后续地址无关的 LSU。

这样日志能够区分：

- 指令尚未 ready。
- 指令已经 ready，但被显式 Membar 阻塞。
- 指令已经 ready，但被 `ub_address_dependency` 阻塞。

Uop/history 至少增加：

```text
memory_range
blocked_reason = "ub_address_dependency"
blocked_by_inst_ids
```

MVP 可以按 `base_object_id` 维护 pending load/store 列表。若扫描成为性能瓶颈，再改为
区间索引；第一阶段优先保证语义可验证。

### 6.1 依赖解除的 cycle 边界

A/B 实验必须固定依赖完成到 consumer 可发射之间的可见性延迟。当前 simulator 每
周期先执行 `control_unit.update()`，随后才在 `ooo.step()` 中把 `done_cycle` 到达的
Uop 标记为 done。因此一个 producer 在 cycle 20 完成时，显式 Membar 最早在 cycle
21 解除：

```text
producer done:   cycle 20
consumer start:  cycle 21 或更晚
```

如果新的 UB range gate 在 `ooo.step()` 更新 done state 后立即查询 pending producer，
consumer 可能在 cycle 20 同周期发射。这会让实验收益同时包含：

1. 全局 Membar 改成局部地址依赖的收益。
2. completion wakeup 提前一个 cycle 的收益。

第一阶段禁止混合这两个变量。UB 地址依赖统一采用当前 Membar 的下一周期可见语义：

```text
release_cycle = max(overlapping_producer.done_cycle) + 1
consumer_start_cycle >= release_cycle
```

等价发射条件为：

```text
current_cycle > producer.done_cycle
```

实现不能只根据 `producer.state == "done"` 判断可发射，因为 state 在当前 cycle 开始
时刚更新为 done。pending range 记录需要保留 `done_cycle`，直到其 release cycle
已经对 consumer 可见。

多个 overlap producer 存在时，取所有相关 producer 的最大 release cycle。地址无法
解析而触发 same-base/global fallback 时，也必须使用同一可见性规则。

当前 Membar 测试只断言 `consumer_start >= producer_done`，无法区分同周期和下一
周期。需要在无其它端口/资源冲突的定向 case 中增加精确断言：

```python
self.assertEqual(consumer_start, producer_done + 1)
```

同时为局部地址 gate 增加相同边界测试，确保 A/B 两侧 wakeup 口径一致。若后续要
评估硬件 completion 当周期唤醒，应新增独立配置和第三组实验，不得修改本实验的
固定口径。

## 7. 无法解析地址时的保守策略

实验模式不能因为地址信息缺失而放松正确性：

- base 和 range 都可解析：使用局部 overlap 依赖。
- base 可解析但 span/offset 不可解析：对同一 base 回退方向性全局阻塞。
- base 也不可解析：对所有 UB 对象回退方向性全局阻塞。

每类回退记录聚合 warning：

```json
{
  "kind": "ub_address_dependency_fallback",
  "op": "VLDS",
  "static_instruction_id": "load.3",
  "stream_seq": 42,
  "base_object_id": "tmp",
  "reason": "unknown_span",
  "fallback_scope": "same_base",
  "count": 1
}
```

报告需要统计精确解析比例。若一个 case 大量回退全局阻塞，则不能把“没有收益”解释
为地址依赖硬件没有价值。

## 8. A/B 输入与运行方式

默认 simulator 行为保持不变。新增实验配置建议为：

```json
{
  "ub_dependency_mode": "disabled"
}
```

可选值：

- `disabled`：主线默认，只使用显式 Membar。
- `range_overlap`：启用动态 UB 地址依赖。

实验 runner 对同一个 Canonical VFInfo 生成两份输入：

1. `membar_global`：保留 Membar，`ub_dependency_mode=disabled`。
2. `ub_local`：只移除 `VST_VLD/VLD_VST` Membar，启用
   `ub_dependency_mode=range_overlap`。

移除 Membar 必须由独立实验 transform 显式完成并记录数量，Core 不能在
`range_overlap` 模式下静默忽略输入中的 Membar。这样可以避免用户以为 barrier 已
生效但模型实际丢弃它。

transform 前后都运行 Canonical validator。除 Membar 节点数量和实验 uarch override
外，storage objects、values、instructions、loops、forms 和 source locations 必须
完全一致。

## 9. 验收用例

### 9.1 定向微基准

1. **STORE -> LOAD，同地址并列循环**
   `VSTS tmp[i] -> Membar(VST_VLD) -> VLDS tmp[i]`。验证 `load[i]` 只等待
   `store[i]`，第一个 load 可以早于最后一个 store done 开始。
2. **LOAD -> STORE，同地址并列循环**
   `VLDS tmp[i] -> Membar(VLD_VST) -> VSTS tmp[i]`。验证逐地址放行。
3. **不同 UB 对象**
   `VSTS a[i] -> VLDS b[i]` 不互相阻塞。
4. **同对象不重叠范围**
   `VSTS tmp[i] -> VLDS tmp[i + N]` 不互相阻塞。
5. **部分范围重叠**
   验证 overlap，而不是 exact offset，相交一部分也必须阻塞。
6. **未知 offset/span**
   验证保守回退和 `ub_address_dependency_fallback` warning。
7. **loop、嵌套 loop 和 unroll**
   验证依赖使用动态 induction value、`iteration_path` 和 `stream_seq`。
8. **无关 compute 绕行**
   地址依赖阻塞 LSU 时，无关 ready compute 仍可进入 EXQ/EXU。
9. **POST_UPDATE 首次访问**
   验证 alias 初始 offset 是首次地址，第 3 个参数只在下一次访问生效。
10. **独立 pointer state**
    `p0`、`p1` 指向同一个 UB object 并分别 POST_UPDATE，验证两条地址序列互不
    污染。
11. **普通 offset 与 POST_UPDATE 对照**
    验证普通 `base + 64*i` 不更新 pointer，而 POST_UPDATE 连续访问形成递增地址。
12. **loop/unroll pointer progression**
    验证每个动态 iteration/lane 的 update 次数和 emitted `stream_seq` 一致。
13. **依赖解除 cycle 边界**
    在无额外资源冲突时，显式 Membar 和 UB range gate 都必须满足
    `consumer_start == producer_done + 1`。
14. **多个 overlap producer**
    consumer 使用所有相关 producer 中最大的 `done_cycle + 1`，不能因较早完成的
    producer 提前发射。
15. **pointer/cast/alias chain 的 base 统一**
    多级 alias 和 cast 后的访问必须保留同一 `base_object_id`，同时按各自元素宽度
    产生正确 byte offset。
16. **不同原始 UB buffer**
    即使 offset/range 数值相同，只要 `base_object_id` 不同就不建立依赖。

### 9.2 算子穿刺

优先选择现有含 Membar 且地址表达式清楚的 case：

- RMSNorm：`mem_reduce_tmp[64 * row + fold]` 等访问。
- GeLU split case。
- Softmax 中含局部 UB 临时数组和显式 offset 的版本。

对访问粒度不明确的 case，先报告 fallback coverage，不通过猜测 span 制造收益。

## 10. 性能报告

每个 case 输出：

| 指标 | 含义 |
|---|---|
| global_membar_cycle | 当前全局 Membar 周期 |
| local_dependency_cycle | 移除 Membar 并启用局部地址依赖后的周期 |
| speedup | `global / local` |
| cycles_saved | `global - local` |
| precise_access_ratio | 动态 LSU 中得到具体 byte range 的比例 |
| local_dependency_edges | 实际建立的局部依赖边数量 |
| global_fallback_count | 因地址信息不足回退全局阻塞的次数 |
| membar_blocked_cycles | 基线因 Membar 不能 issue 的累计周期 |
| ub_dependency_blocked_cycles | 实验模式因真实地址依赖不能 issue 的累计周期 |

报告还需要确认两种模式的动态非 Membar 指令数、op/form 分布和 loop iteration 完全
一致，并明确两侧都使用 completion 下一周期可见语义，防止把解析差异或 wakeup
提前误认为硬件收益。

## 11. 影响文件

第一阶段预计修改：

- `configs/uarch.json`：增加默认关闭的实验开关。
- `configs/uarch_override_schema.json`：登记实验字段。
- `core/ifu.py`：计算动态 affine offset 和 memory range。
- `core/ooo.py`、`core/ooo_mainline.py`：Uop 元数据、pending LSU range 和 issue gate。
- `core/simulator_runner.py`：模式初始化、统计和 warning 输出。
- `core/dynamic_trace.py` 或独立 `core/memory_dependency.py`：纯函数形式的地址求值、
  range overlap 和保守回退规则。
- `api/frontend/core_lowering.py`：确认静态 memory metadata 无损进入 Core payload。
- `api/vf_info.py`：仅作为 CCE 到 Canonical 的逻辑中间表示，补齐 pointer/update
  信息但不作为 Core 实验输入。
- 新增 Python 实验元数据模块：以 Canonical node/object ID 关联 pointer identity、
  普通 access offset 和 post-update delta，并提供独立 validator。
- `api/frontend/schema.py` 和共享 JSON schema 保持 Canonical v1 兼容；本实验不加入
  Python-only wire 字段。
- `api/cce_adapter.py`：按 Catalog call variant 识别 `POST_UPDATE/NO_UPDATE`，保留
  pointer state，不再把 update delta 合入当前访问 offset。
- Catalog/独立访问模式配置：仅补确有 ISA 依据的 span 和元素宽度。
- `tests/`：微基准、loop/unroll、warning 和默认行为不变测试。
- `tools/`：Membar 保留/移除 A/B runner 与汇总报告。

不修改旧 `api/vf_lowering.py`、legacy JSON adapter 或旧 memory key 路径来实现实验
功能。如果 Canonical lowering 缺少所需字段，应补 Canonical 契约，不在 legacy 层
旁路解决。

本次开发不修改 Native 执行代码，也不以 Python/Native cycle 一致性作为本实验验收
项。共享 Catalog 增加 VSTUS/VSTAS 语义时允许机械刷新
`api/native/generated/InstructionCatalogData.inc`，但 C++ align-state 和地址依赖执行
逻辑仍留在独立开发任务中。

## 12. 开发步骤

1. 固化当前 Membar 微基准周期和日志，确保实验前基线可复现。
2. 增加 Canonical-only 实验入口和 legacy payload 拒绝测试。
3. 修正 Python 前端并建立 Canonical 实验元数据，区分普通 offset、pointer state
   和 POST_UPDATE delta，不修改共享 Canonical v1 wire schema。
4. 增加 pointer state 动态推进测试，先保证生成的地址序列正确。
5. 定义动态 byte range 数据结构与纯函数求值/overlap 测试。
6. 将 Canonical memory metadata 从 IFU 贯穿到 Uop/history，不启用 gate，确认周期
   完全不变。
7. 固化 Canonical 显式 Membar 的 `producer_done + 1` 精确边界测试。
8. 实现 `range_overlap` LSQ issue gate、下一周期 release、blocked reason 和保守
   fallback warning。
9. 实现 Canonical transform runner，显式移除 Membar 并生成 A/B 报告。
10. 跑定向微基准，验证逐地址放行、未知地址保守性和 release cycle 一致性。
11. 通过 `InputAPI.load_cce_canonical()` 跑 RMSNorm、GeLU、Softmax 穿刺，统计收益
    与解析覆盖率。
12. 跑现有 Python 单测和完整回归；默认 `disabled` 模式必须与
   `baseline_balanced_exu0_reserve.json` 逐项一致。
13. 根据结果评审是否另立任务开发 C++ Native Canonical 入口和编译器接口；本分支
    到此结束。

## 13. 退出条件

满足以下条件后，Python MVP 才算完成：

- 默认模式现有回归 0 cycle 漂移。
- A/B 输入都通过 Canonical validator 和 CoreLoweringPass；实验入口明确拒绝 legacy
  payload。
- Git diff 不包含 Native 执行代码；共享 Catalog 对应的 C++ generated 数据表可以
  机械刷新，但本分支不实现 C++ 调度语义。
- 同地址和 range overlap 不会过早发射。
- 不同地址的 LSU 可以越过全局 Membar 原本造成的等待。
- 未知地址必定保守回退并有可见 warning。
- 普通 offset 和 POST_UPDATE 产生正确且可重复的动态地址序列。
- 同一 UB object 上的不同 pointer state 不互相污染。
- 同源 pointer/cast/alias chain 统一到相同 base，不同原始 UB buffer 不做跨对象
  alias 推断。
- 局部依赖和显式 Membar 均采用 producer 完成后下一周期可发射的相同口径。
- loop/unroll 使用动态地址和动态顺序，不依赖静态 pc。
- A/B 报告能够把真实局部依赖收益与地址解析不足区分开。
- 文档明确该模式是假设硬件实验，不代表当前 NPU 已具备此能力。

## 14. 当前实现状态与验证结果

### 14.1 已完成

截至本轮开发，Python MVP 已完成以下链路：

```text
CanonicalVfInfo
+ PythonUbAddressExperimentMetadata
  -> ExperimentalCanonicalCoreLowering
  -> IFU 动态 byte range
  -> Uop.memory_ranges
  -> LSQ start gate
```

具体实现包括：

- CCE 参数和局部 pointer 保留元素宽度、稳定 `base_object_id`、独立
  `pointer_state_id`、初始 byte offset、普通访问 offset 和 POST_UPDATE delta。
- POST_UPDATE 按“本次访问使用当前 pointer，之后再更新”的顺序执行。
- `VSTUS(align, count, src, pointer, POST_UPDATE)` 将 `count` 解释为本次追加的
  scalar 数量，生成 `count * element_size` 的精确写区间，并按该字节数推进 pointer。
- 每个 `vector_align` 独立维护 generation。VSTUS 把动态写区间追加到当前
  generation；VSTAS 在动态展开时封存该 generation 的区间快照并开启下一代。
  因而后续 VLDS 会等待与 VSTAS generation 区间重叠的写完成，不会等待其它
  generation 或同 base 上不重叠的标量。
- VSTUS/VSTAS 的 align state 只参与 generation ready，不进入 RAT 或物理寄存器
  分配；VSTAS 仍属于 STORE，并采用 latency 8。
- loop 内动态 pointer 声明，以及对已经动态更新过的 pointer 再做静态 alias
  snapshot，当前会明确拒绝，不静默产生错误地址。
- `BRC_*`、`ONEPT_*` 的 UB span 按单元素处理；其它未经确认的 mode 保持
  unresolved。
- IFU 根据 `iteration_path`、induction value 和参数求值 affine byte range；普通
  loop、结构化 unroll 和 POST_UPDATE 均按动态 `stream_seq` 推进。
- LSQ 只增加 `STORE -> LOAD` 和 `LOAD -> STORE` 两个方向的依赖。具体 range
  overlap 时建立精确边；range 不完整时按 same-base/global 保守 fallback。
- producer 在 cycle `N` 完成时，consumer 最早在 `N+1` start，与当前显式 Membar
  的可见性边界一致。
- history 和简化日志输出 `memory_ranges`、`blocked_reason` 和
  `blocked_by_inst_ids`；`model_warnings.json` 输出
  `ub_address_dependency_fallback`。
- A/B runner 从同一个 Canonical 输入生成两侧，只移除方向性 Membar，并校验动态
  op/form 分布完全一致。
- 实验 runner 输出精确访问比例、精确/回退依赖边、fallback 数量以及两种阻塞周期。

实验开关没有写入共享 Canonical v1 schema 或 C++ 生成配置。它只由
`ExperimentalCanonicalCoreLowering` 注入 Python Core payload；普通 Canonical 或
legacy payload 不能直接启用 `range_overlap`。这保证本轮 diff 不包含 `native/` 或
`api/native/`。

### 14.2 定向微基准

`VSTS tmp[i] -> Membar(VST_VLD) -> VLDS tmp[i]`，`i=0..3`：

| 指标 | 结果 |
|---|---:|
| 全局 Membar | 64 cycle |
| 局部地址依赖 | 63 cycle |
| 节省 | 1 cycle |
| 加速比 | 1.0159x |
| 精确访问比例 | 12/12，100% |
| 精确依赖边 | 4 |
| fallback 依赖边 | 0 |
| Membar blocked 记录 | 50 |
| UB dependency blocked 记录 | 44 |

日志逐项验证 `load[i].start == store[i].done + 1`，且第一个 post-barrier load
不需要等待最后一个 store 完成。

定向测试还覆盖：

- `LOAD -> STORE` 逐地址放行。
- 同 base 不重叠、不同 base 不建依赖。
- 部分 range overlap、多个 overlap producer 取最大 `done+1`。
- unknown span 回退 same-base，缺 metadata 回退 global，并写 warning。
- 普通 offset、独立 POST_UPDATE pointer、pointer cast/alias chain。
- loop/unroll 动态 induction offset 和每 lane 一次 pointer update。
- VSTUS 两次追加形成相邻 scalar range，VSTAS 封存聚合 range；使用独立 read/write
  pointer alias 的后续 VLDS 在 VSTAS done 后下一拍启动，且不产生 fallback。
- 实验入口拒绝 legacy 输入和缺少实验 metadata 的手工开关。

### 14.3 GeLU 穿刺

输入：`GeLU_poly_split_only_I96_penalty1.dsl`。

| 指标 | 结果 |
|---|---:|
| 全局 Membar | 1869 cycle |
| 局部地址依赖 | 1809 cycle |
| 节省 | 60 cycle |
| 加速比 | 1.0332x |
| 动态非 Membar 指令 | 4224，两侧一致 |
| 精确访问比例 | 1536/1536，100% |
| 精确依赖边 | 0 |
| fallback 依赖边 | 0 |
| fallback warning 实例 | 0 |
| Membar blocked records | 1212 |
| UB dependency blocked records | 0 |

该 case 的 `NORM/NORM_B32` 已展开为精确 256-byte 区间。运行时阻塞边为 0，表示
后续 load 到达 LSU 时，与其区间重叠的前序 store 已经完成；收益来自不再等待同一
全局 Membar 覆盖的其他地址或其他 UB object 上尚未完成的 store。

### 14.4 64 轮双循环 CCE 复测

输入：`/tmp/vfsim_ub_two_loops_i64.cce`。前一段每轮执行
`2 VLDS + VADD + VSTS`，通过 `Membar(VST_VLD)` 后，后一段每轮执行
`VLDS + VMULS + VSTS`。

| 指标 | 合入共享 UB 槽前 | span 未恢复 | 当前复测 |
|---|---:|---:|---:|
| 全局 Membar | 203 cycle | 262 cycle | 262 cycle |
| 局部地址依赖 | 184 cycle | 262 cycle | 229 cycle |
| 节省 | 19 cycle | 0 cycle | 33 cycle |
| 加速比 | 1.1033x | 1.0000x | 1.1441x |
| CAModel | 246 cycle | 246 cycle | 246 cycle |
| 动态非 Membar 指令 | 448 | 448 | 448 |
| 精确访问比例 | - | 0/320 | 320/320，100% |
| fallback 依赖边 | - | 998 | 0 |
| Membar blocked 记录 | - | 1005 | 1005 |
| UB dependency blocked 记录 | - | 1005 | 0 |

共享 `ub_slots=2` 禁止了原模型同拍启动 `2 VLD + 1 VST`，因此两种
VfSim 模式相对旧调度均发生变化。恢复 `NORM/NORM_B32` 的完整 256-byte 范围后，
局部模式不再回退成同 base 全局阻塞，最终为 229 cycle，比全局 Membar 节省
33 cycle；相对 CAModel 246 cycle 偏小 17 cycle，约 6.9%。

已刷新计算指令完成 IPC（窗口 10 cycle）：

- `results/ub_address_dependency_experiment/vadd_vmuls_i64/ipc/compute_ipc_vfsim_window10.csv`
- `results/ub_address_dependency_experiment/vadd_vmuls_i64/ipc/compute_ipc_vfsim_window10.png`
- `results/ub_address_dependency_experiment/vadd_vmuls_i64/ipc/compute_ipc_camodel_compare_window10.csv`
- `results/ub_address_dependency_experiment/vadd_vmuls_i64/ipc/compute_ipc_camodel_compare_window10.png`

实验结果从本轮起按 case 归档。每个 case 必须独立保存 CCE/DSL 源码、CAModel
`instr_log`/`instr_popped_log`/`rvec.EXU.dump`、VfSim 全局与局部模式完整日志，以及
IPC CSV/图片。目录模板见 `results/ub_address_dependency_experiment/README.md`。

### 14.5 RMSNorm 穿刺状态

`RMSNorm_T4_ref_no_newton.dsl` 当前在既有 Catalog 校验阶段被合法但尚未登记的
`UNPK_B16`（后续还有 `PART_EVEN` 变体）阻塞，尚未进入本实验 lowering。为了遵守
“Catalog 是统一语义来源”和“本轮不修改 C++ generated Catalog”的范围，本分支未在
CCE parser 中增加实验专用 opcode/config 特判。需要先由独立 Catalog 任务补齐这些
合法调用变体，再运行 RMSNorm A/B 数据。

### 14.6 默认回归

- `python3 -m unittest discover tests`：`196/196` 通过。
- 完整 cost-model regression：通过。
- 默认 `disabled` 结果与开发前基线提交 `ded85be` 逐项一致。
- 相对仓库基准文件，`gelu_poly_i96_u6/u8` 仍分别为 `+15/+11` cycle；在
  `ded85be` 上复跑结果相同，不是本实验引入。
- 未修改任何 C++ Native 文件。

### 14.7 A/B 可比性和地址宽度收尾

- 实验侧移除 loop body 内的方向 Membar 时，保留基线因 Membar 产生的有效 `unroll=1`，避免收益混入动态指令重排。
- A/B 除了比较 op/form 直方图，还必须按 `stream_seq` 比较 `(static_instruction_id, iteration_path, op, form)` 完整动态顺序。
- `bfloat16_t` 明确是 2-byte CCE 元素类型；实验模式下未知 UB 元素宽度直接报错，不再静默当作 1 byte。
- 已知 `base_object_id` 不同时不建依赖且不记 fallback warning。
- LSU 历史仅保留尚未完成或本周期刚完成的记录，保持 `consumer_start >= producer_done + 1` 边界同时避免历史无界增长。
