# VfSim 前端架构与代码规范改造计划

> 状态：阶段一契约与校验完成，阶段二 Catalog 主体完成、adapter 迁移继续
> 适用代码：Python VfSim 前端、Python Core 入口、C++ VfInfo 入口  
> 不包含：timing 参数标定、OoO 调度策略调优和具体算子精度对齐

## 1. 文档目的

本文档用于规范 VfSim 前端代码的职责、数据边界和后续改造方式。

这里的“前端”不是图形界面，而是指把 CCE、JSON、Tilesim 或编译器输入转换成 VfSim 核心可执行语义的代码。改造目标不是让 Python 和 C++ 使用完全相同的输入解析代码，而是让二者在进入模拟核心前遵守同一份、可验证的 `CanonicalVfInfo` 语义契约。

本次改造重点解决以下问题：

1. 输入解析、符号别名、指令签名、类型推断和 Core lowering 混在同一层。
2. 为修复某个 CCE case，不断增加 opcode 特判或字符串判断。
3. 同一份 VF 程序在 Python 和 C++ 中可能生成不同的依赖关系。
4. 输入语义不明确时静默猜测或丢弃语句，使结果看似可运行但实际错误。
5. 同一对象被多次 canonicalize、normalize 或 lower，阶段边界不明确。
6. 测试过度关注内部字符串和临时寄存器编号，缺少语义等价验证。

本文档是开发规范和迁移计划，不直接改变现有 timing 参数、OoO 调度策略或预测公式。

## 2. 适用范围

### 2.1 Python 版本

Python 版本需要支持两种使用方式：

1. 作为 Tilesim 中的 Python cost model 被调用。
2. 作为独立工具，接收 CCE、JSON 或 Python API 构造的输入。

因此 Python 侧允许存在多种输入 adapter，并负责完成必要的语法解析和语义解析。

### 2.2 C++ 版本

C++ 版本主要对接编译器。编译器应直接构造满足契约的 VfInfo，C++ VfSim 不需要复制 CCE parser、Tilesim adapter 或旧 JSON 兼容逻辑。

C++ 侧的前端职责应限制为：

1. 接收编译器构造的 VfInfo。
2. 验证 VfInfo 是否满足规范。
3. 将已验证的 VfInfo 交给模拟核心。

### 2.3 共享范围

Python 和 C++ 必须共享的是：

1. `CanonicalVfInfo` 数据契约及版本号。
2. opcode、dtype、指令 form、操作数角色和 Membar 类型的规范定义。
3. ISA、forwarding、II 和 uarch 配置的查询语义。
4. 同一份 canonical 输入的依赖图和 cycle 一致性测试。

Python 和 C++ 不要求共享的是：

1. CCE 源码解析器。
2. Tilesim 对接代码。
3. 旧 JSON 格式兼容代码。
4. Python 高层输入所需的语法糖。

## 3. 总体设计原则

### 3.1 先确定语义，再进入 Core

Core 不应通过名称前缀猜测存储类型，不应根据调用来源猜测 dtype，也不应解析 CCE 表达式。进入 Core 的每个字段必须已经具有明确语义。

### 3.2 Adapter 只处理输入格式差异

Adapter 可以识别输入语言中的别名和语法，但不能直接实现 timing 查询、依赖调度或物理寄存器分配。

### 3.3 一种规则只保留一个实现位置

以下规则都必须具有唯一事实来源：

1. opcode 别名和 canonical opcode。
2. 指令操作数数量、方向和角色。
3. 指令支持的 dtype/form 组合。
4. load、store、compute、control 分类。
5. Membar 支持类型。
6. timing 参数覆盖和默认回退规则。

不允许在 CCE adapter、`VFInfo`、lowerer 和 Core 中分别维护同一套分支。

### 3.4 缺少性能参数可以回退，缺少语义不能猜测

必须区分两类问题：

1. 已知是什么指令，但 ISA、forwarding 或 II 参数没有覆盖：使用规定的默认值，并输出结构化 warning。
2. 不知道操作数角色、内存基址、转换方向或语句含义：报诊断或错误，不得静默忽略，也不得按名称猜测。

本项目不再引入 strict/lenient 两套行为模型。相同输入在相同配置下只能有一种语义。

### 3.5 Pass 必须满足幂等性或单次执行约束

每个转换 pass 都必须明确以下属性：

1. 输入类型和输出类型。
2. 是否允许重复执行。
3. 是否改变程序语义。
4. 是否改变静态标识或动态依赖。
5. 失败时的诊断形式。

若 pass 不是幂等的，pipeline 必须通过不同的数据类型或状态标记保证它只执行一次。

### 3.6 不为单个 case 增加语义特判

不得使用以下方式修复输入转换问题：

1. 按 kernel 名称、文件名或测试名分支。
2. 为某个 opcode 在 parser 中硬编码一套独立参数排列，而其他 opcode 继续走通用路径。
3. 通过 `startswith("v")`、`startswith("mem")` 等名称约定决定核心语义。
4. 通过插入 `_lane0`、`_iter1` 等字符串后缀隐式表达动态值版本。
5. 为通过 cycle 回归而跳过语义错误或吞掉不支持的语句。

确实存在 ISA 特例时，应将特例声明在指令描述表中，并通过通用解析和校验流程消费。

## 4. 目标架构

### 4.1 Python 数据流

```text
CCE source --------> CCEAdapter -----------+
Tilesim objects ---> TilesimAdapter --------+--> RawVfInfo
legacy JSON -------> LegacyJsonAdapter -----+       |
Python API --------> VfInfoBuilder ---------+       v
                                                SemanticResolver
                                                     |
                                                     v
                                              CanonicalVfInfo
                                                     |
                                                     v
                                               Core Validator
                                                     |
                                                     v
                                                Python Core
```

### 4.2 C++ 数据流

```text
Compiler IR / analysis
        |
        v
Compiler-side VfInfoBuilder
        |
        v
CanonicalVfInfo
        |
        v
C++ Core Validator
        |
        v
C++ Core
```

### 4.3 禁止的反向依赖

Core 不得依赖以下模块：

1. CCE parser。
2. Tilesim 类型。
3. legacy JSON 字段。
4. 用户输入别名表。
5. 文件路径、命令行参数或默认配置文件位置。

前端可以依赖共享 schema 和指令目录，Core 只能依赖 canonical IR、参数数据库接口和模拟所需的数据结构。

## 5. 分层职责

### 5.1 Source Adapter

Source Adapter 负责把一种外部格式转换为 `RawVfInfo`，包括：

1. 保留源码位置，例如文件、行、列或 JSON path。
2. 解析输入格式中的语法结构。
3. 将外部名称映射为项目内部候选符号。
4. 收集符号声明、loop、instruction、Membar 和参数绑定。
5. 对无法识别的语句产生诊断。

Source Adapter 不负责：

1. timing 参数查询。
2. SHQ、EXQ、EXU 调度。
3. 物理寄存器重命名。
4. 为未知 operand 猜测 Register、UB 或 Scalar。
5. 展开 loop 或 unroll。

### 5.2 Semantic Resolver

Semantic Resolver 负责：

1. 根据 `InstructionCatalog` 解析 canonical opcode。
2. 校验操作数个数、方向和角色。
3. 解析 dtype/form 与转换方向。
4. 将内存表达式解析为结构化地址。
5. 建立明确的值定义、使用和 loop-carried 关系。
6. 生成 `CanonicalVfInfo`。

Semantic Resolver 失败时必须返回带源码位置的诊断，不能返回部分可执行程序。

### 5.3 Canonical Validator

Validator 只校验，不修复、不猜测。至少检查：

1. schema 版本受支持。
2. 所有引用都存在。
3. opcode、form 和操作数角色在语义上完整；validator 不以 timing 配置是否覆盖作为合法性条件。
4. operand 数量、角色、storage 和 dtype 一致。
5. loop 参数可解析且 count、unroll 合法。
6. Membar 类型受支持。
7. 内存访问具有明确的 base 和访问方向。
8. instruction、loop 和动态展开标识不冲突。

Python 和 C++ 必须使用语义相同的 validator 测试向量。

### 5.4 Core Lowering

Core lowering 只做模拟器数据结构转换，例如：

1. 将 canonical node 转成 Core 内部 node。
2. 建立模拟器需要的索引。
3. 保留来源位置和静态 instruction ID。

Core lowering 不得再次 normalize opcode、推断 dtype 或改变值依赖。

## 6. CanonicalVfInfo 契约

### 6.1 契约版本

Canonical VfInfo 必须带 `schema_version`。任何不兼容的字段或语义变化都必须提升版本，禁止在不改版本的情况下改变已有字段含义。

建议首个版本使用：

```text
schema_version = 1
```

### 6.2 程序级字段

程序级至少包含：

1. `schema_version`：契约版本。
2. `context`：结构化节点列表。
3. `values`：显式声明的逻辑值或存储对象。
4. `params`：已绑定的整型参数。
5. `uarch`：仅包含本次运行的微架构覆盖，不承载输入语义。
6. `source`：可选来源信息。

`default_dtype` 只能作为 legacy adapter 的输入便利项。进入 canonical IR 后，每条指令的 form 和必要 operand dtype 必须明确，不允许 Core 继续依赖全局默认 dtype。

### 6.3 指令节点

每条 canonical 指令至少包含：

1. 稳定的静态 `instruction_id`。
2. canonical `opcode`。
3. 显式 `instruction_class`，包括 load、store、compute、control；未知 opcode 也必须提供该字段。
4. canonical `form`。
5. 明确区分的 `inputs`、`outputs` 和 scalar `attributes`。
6. 编译器可选提供额外 memory、control 显式 dependency。DATA dependency 只能由 input definition 的 `producer_node_id` 推导，禁止在 instruction dependencies 中重复表达。
7. 可选 `source_location`。

操作数顺序和角色由 `InstructionCatalog` 定义，不应由各 adapter 自行解释。

通用 validator 在 Catalog 之前也必须执行基础 class/access 矩阵：load 只能读取 memory operand，store 只能写入 memory operand，compute/control 不得携带 memory access。fused load-compute、compute-store 等语义不能伪装成现有类别，需新增明确 instruction class 或在后续 schema 版本中定义组合语义。

### 6.4 值与版本

必须区分以下概念：

1. 源码变量或逻辑寄存器名称。
2. 一次具体定义产生的值版本。
3. Core 运行时分配的物理寄存器。

禁止继续用修改字符串名称的方式混合这三者。

推荐表示方式：

1. `logical_id` 表示源码级逻辑对象。
2. `definition_id` 表示一次静态定义，`producer_node_id` 表示产生该 definition 的 instruction 或 loop。
3. 动态展开时使用结构化的 `iteration_path` 和 `stream_seq` 区分实例。
4. 物理寄存器编号仅存在于 Core rename/allocate 阶段。

`loop_id`、普通 `instruction_id` 和 Membar `instruction_id` 共用一个全局 node ID 命名空间；`definition_id` 使用独立的 value definition 命名空间。每个输出必须引用由当前 instruction 产生的新 definition，输入不得引用当前 instruction 自己产生的 definition。

loop accumulator 必须通过通用的数据流规则表达 loop entry、back-edge 和 loop exit。entry 必须在 loop 前可见；back-edge definition 必须在循环尾可见，可以来自当前 loop body 同层节点、嵌套 loop 暴露的 exit，或 loop entry 处已经可见的 invariant/carried entry；exit 必须由 loop node 产生，且三者 storage、dtype、shape 和 storage object 必须一致。不得在 normalizer 中按某种指令排列增加特殊 alias。

producer 关系必须双向一致：instruction producer 必须且只能在自己的 outputs 中产生一次 definition；loop producer 必须通过对应 `carried_values.exit_value_id` 产生；Membar 不得作为 value producer。嵌套 loop 内部 instruction 不能直接向外层 loop 泄漏 definition，必须经由嵌套 loop exit；循环后产生的 definition 也不能作为该循环的 back-edge。

canonical `uarch` override 只允许仍有活跃语义的配置。已经物理删除的字段，例如
`load_done_latency`，由 Python/C++ validator 统一返回
`deprecated_uarch_field`，不得静默忽略或让调用方误以为 override 已生效。
活跃 override 的 integer、boolean、string 类型由
`configs/uarch_override_schema.json` 统一维护，Python 直接加载，C++ 从该文件生成
只读表；已登记字段不得通过 `int()` / `bool()` 等隐式转换接受错误类型。

### 6.5 存储类型

storage 必须显式声明，支持项由 schema 统一维护，例如：

1. `Register`
2. `UB`
3. `Scalar`

不得在 canonicalization 中使用以下推断：

```text
mem* -> UB
v*   -> Register
其他 -> Scalar
```

如需兼容旧 JSON，该规则只能存在于 `LegacyJsonAdapter`，且每次推断都应产生可汇总的兼容性 warning。

### 6.6 dtype 与 form

dtype 和 form 必须由指令及其操作数共同确定，不能由全局 `values[value_id].dtype` 的首次出现决定后续所有使用。

必须支持同一逻辑存储位置在不同定义上具有不同 dtype，例如转换或复用寄存器。具体 dtype 应绑定到值版本或指令 operand，而不是只绑定到可变的逻辑名称。

`b16 -> fp16`、`b32 -> fp32` 这类 timing form 参数回退属于 ParamDB 查询逻辑，不应改变 canonical 输入的真实 dtype。兼容映射只允许来自 Python/C++ 共享规则；普通缺失 form 不允许隐式借用 `fp32`，而是使用默认参数并产生 warning。

### 6.7 内存访问

内存操作必须使用结构化表示，至少包含：

1. `base_object_id`：稳定内存对象或 UB 基址，不是一次 value definition ID。
2. `offset`：结构化 affine expression，由常量项和 `(variable_id, coefficient)` 项组成。
3. `access_kind`：read 或 write。
4. `span` 或可推导的访问范围。
5. 可选 alias group。

例如：

```c
vlds(v0, a + off, 0, NORM);
```

UB 的 memory state/value definition 通过 `storage_object_id` 引用稳定对象；alias 和 range overlap 使用 `base_object_id + offset/span`。不能只保留最后一个 identifier 并把 `off` 当作 UB，也不能用 store 产生的 definition ID 代替稳定基址。offset 中的变量只能引用所在结构化 loop 的 induction variable 或顶层整型参数；表达式不受支持时必须明确报错。

### 6.8 Loop 和 unroll

loop 必须保持结构化表示：

1. `count` 为已解析整数，或明确引用 `params` 中的参数。
2. `induction` 显式给出 variable ID、start 和 step，内层 memory access 通过该 ID 引用当前动态迭代。
3. `carried_values` 显式给出同一 logical value 的 entry、back-edge 和 exit definition。
4. `unroll` 为合法正整数。
5. 静态 instruction ID 不因动态展开而改变。
6. 动态实例通过 `iteration_path`、`unroll_lane` 和 `stream_seq` 标识。

不允许通过给所有 src/dst 增加 `_laneN` 后缀表达 unroll。该方式会破坏循环前后共享值、内存别名和单次循环的语义。

### 6.9 Membar

Membar 是控制节点，不是 load、store 或 compute 指令。Canonical 表示至少包含：

1. 支持的同步类型，目前为 `VST_VLD`、`VLD_VST`。
2. 稳定静态 ID 和源码位置。
3. 动态展开后由结构化顺序产生的 `stream_seq`。

Membar 不进入 IDU、SHQ、EXQ 或 EXU。其对 SHQ 乱序发射的限制由 Core 控制逻辑实现，adapter 不得提前把受影响指令重排或删除。

## 7. InstructionCatalog 规范

### 7.1 目的

当前 opcode、dtype、form specialization 和操作数规则分散在 `api/input_symbols.py`、`api/cce_adapter.py`、`api/vf_info.py`、C++ `VfInfo.cpp` 和 config 文件中。应新增独立的 `InstructionCatalog`，作为指令语义的唯一事实来源。

InstructionCatalog 与 timing config 必须分开：

1. Catalog 描述“这条指令是什么、操作数是什么、支持什么 form”。
2. `isa.json`、`forwarding.json`、`initiation_interval.json` 描述“已覆盖的性能参数是什么”。

某条指令在 Catalog 中有明确签名但 timing 未覆盖时，可以使用默认参数。Catalog 未收录的指令也不必一律拒绝：如果调用方已经显式给出类别、form 和操作数角色，或者该输入格式有已定义且无歧义的通用调用签名，则可以生成 canonical 指令并使用默认 timing；如果连这些语义都无法确定，则不能仅靠 timing 默认值继续模拟。

### 7.2 每条指令的描述字段

建议至少包含：

1. canonical opcode。
2. 外部别名列表。
3. 指令类别：load、store、compute、control。
4. 操作数签名，包括位置、名称、方向、storage 和是否参与数据依赖。
5. 已知的 dtype/form 组合和必要的语义约束。
6. form 解析规则。
7. specialization 规则，例如 `VCVT + f32_to_bf16`。
8. 可选属性及其类型。

FU 类型、`dispatch_exu`、latency、II 和 forwarding 属于微架构或性能参数，不放入 Catalog，继续由 ISA/uarch/ParamDB 侧统一维护，避免产生第二份资源配置。

Catalog 中“已知的 dtype/form”不等于 timing 覆盖白名单。语义明确的新 form 可以进入 canonical IR，再由 ParamDB 使用默认参数并告警。只有转换方向、操作数角色等基础语义不成立时，才拒绝输入。

### 7.3 实现形式

Catalog 应使用可校验的结构化数据或声明式 Python 数据，不建议继续扩充大型 Enum 和多份手写 map。

Python 直接加载 Catalog。C++ 可采用以下任一方式，但生成结果必须可测试：

1. 构建时从 Catalog 生成只读 C++ 表和 enum。
2. 加载同一份稳定格式的数据文件。

不允许长期手工同步 Python 和 C++ 两份 opcode 列表。

### 7.4 Parser 使用方式

CCE parser 解析调用后，应执行统一流程：

```text
callee name
  -> catalog alias lookup
  -> signature lookup
  -> argument binding
  -> operand semantic parsing
  -> dtype/form resolution
  -> canonical instruction
```

`VPACK`、`VSSTB`、`VCVT`、`VMULSCVT` 等差异应由 signature 或 form resolver 表达，而不是在 `_parse_statement()` 中持续增加 opcode `if` 分支。

## 8. Python 前端代码规范

### 8.1 CCE Adapter

CCE adapter 应拆为以下职责：

1. tokenizer/结构扫描：识别 scope、loop、declaration、call、pragma 和 Membar。
2. symbol table：记录参数、vector、scalar 和 UB 声明。
3. expression parser：解析整数表达式和内存地址表达式。
4. instruction binder：基于 Catalog 绑定调用参数。
5. diagnostic collector：收集错误、warning 和源码位置。

不得继续静默返回 `None` 忽略无法识别的普通语句或调用。允许忽略的内容必须在白名单中声明，例如已知无模拟语义的 `pset_*`，并在代码中说明忽略依据。

### 8.2 Tilesim Adapter

Tilesim adapter 应直接从 Tilesim 已有对象构造 `RawVfInfo` 或 `CanonicalVfInfo`，不要先伪造 CCE/JSON 再走文本解析。

Tilesim 对接层可以负责：

1. Tilesim dtype/opcode 到 Catalog 符号的映射。
2. Tilesim value、buffer 和 loop 对象到 VfInfo 字段的映射。
3. 把诊断转换为 Tilesim 可识别的日志或异常。

Tilesim adapter 不得把 Tilesim 类型泄漏到 Core。

### 8.3 Legacy JSON Adapter

所有基于名称前缀、缺省 dtype 和旧字段名的兼容逻辑应集中到 Legacy JSON adapter。该 adapter 的输出必须经过同一个 Semantic Resolver 和 Validator。

兼容逻辑必须：

1. 有明确 deprecation warning。
2. 记录具体字段、推断结果和输入位置。
3. 可通过版本或迁移工具逐步删除。

### 8.4 Python API

Python API 应提供显式 builder，避免调用方直接拼接内部 dict：

```python
builder = VfInfoBuilder()
builder.register_value(...)
builder.add_instruction(...)
builder.add_loop(...)
builder.add_membar(...)
vf_info = builder.build()
```

`build()` 必须完成校验并返回 canonical 对象。公开 API 不应要求调用方手动执行多次 `canonicalize_vf_info()` 或 `lower()`。

### 8.5 独立 cost model API

独立 Python cost model 应提供无文件系统副作用的库接口：

```python
result = simulate_vf_info(vf_info, param_db, options)
```

读取默认 config、解析命令行和写结果文件属于 CLI 层，不应在 import 或核心 API 调用时自动发生。

## 9. C++ 接口规范

### 9.1 C++ 不复制 Python 输入前端

C++ 侧不新增 CCE parser、Tilesim adapter 或名称前缀兼容逻辑。编译器必须通过 `VfInfoBuilder` 显式提供 canonical 字段。

### 9.2 建议公开接口

```cpp
ValidationResult validateCanonicalVfInfo(const VfInfo& vfInfo);
SimulationResult simulate(const VfInfo& vfInfo,
                          const ParamDB& paramDb,
                          const SimulationOptions& options);
```

编译器侧 builder 可以是独立组件：

```cpp
VfInfoBuilder builder;
builder.addValue(...);
builder.addInstruction(...);
builder.addLoop(...);
builder.addMembar(...);
VfInfo vfInfo = builder.build();
```

### 9.3 C++ 必须删除或隔离的行为

进入 canonical API 后，C++ 不应再执行：

1. opcode alias 猜测。
2. storage 名称推断。
3. dtype 首次出现推断。
4. 与 Python 不一致的旧 vreg live-range normalization。
5. 为 legacy JSON 设计的字段修补。

如果暂时必须保留旧接口，应命名为 `LegacyJsonVfInfoAdapter`，与 canonical API 物理隔离并标记废弃计划。

## 10. Normalization 与数据流 Pass 规范

### 10.1 当前问题

现有 `core/vreg_live_range_normalization.py` 同时承担了：

1. loop trip count 解析。
2. 寄存器 live range 分析。
3. loop-carried alias 传播。
4. sibling redefinition kill。
5. 临时槽位复用。
6. loop exit 值传播。

这些职责耦合后，修复普通 sibling、零次 loop、loop back-edge 或 loop 后读取时容易出现相互覆盖的特殊分支。

### 10.2 目标拆分

建议拆成以下 pass：

1. `ParameterBindingPass`：只解析 loop count 和 unroll 参数。
2. `ValueVersioningPass`：建立定义、使用、loop entry、back-edge 和 exit 关系。
3. `CanonicalValidationPass`：验证数据流完整性。
4. `RegisterAllocationApproximationPass`：仅在确实需要模拟高层虚拟寄存器容量时运行。
5. `CoreLoweringPass`：转换为 Core 内部结构。

值版本化和寄存器槽位复用不能放在同一个递归函数中完成。

### 10.3 Python 与 C++ 的差异处理

Python CCE/Tilesim 高层输入若没有提供完整数据流，可由 `ValueVersioningPass` 补全。

C++ 编译器若已经提供明确的 value version 或依赖关系，应跳过近似的寄存器 normalization，只做 validator 和 Core lowering。不能让 C++ 再次重写编译器已经确定的依赖。

### 10.4 unroll 处理

unroll 应在 IFU 动态展开阶段统一处理。前端仅保留结构化 loop 与 unroll 信息，不应存在“单 super iteration 特殊 canonicalization”。

即使 `iters=1`，任何 pass 也不得无条件改写所有 operand 名称。循环内外的同一逻辑值和同一内存对象必须保持可追踪关系。

## 11. 诊断与告警规范

### 11.1 诊断级别

建议统一为：

1. `error`：无法确定输入语义，禁止模拟。
2. `warning`：语义明确，但使用 timing 默认值或 legacy 兼容推断。
3. `note`：补充来源和候选修复信息。

### 11.2 必须报 error 的情况

1. 未知 opcode 且调用方未显式提供语义，Catalog 和通用签名也都无法确定其操作数角色。
2. 操作数数量或角色不匹配。
3. 无法确定 memory base。
4. 转换指令无法确定源/目标 dtype。
5. 未知 storage。
6. 不支持的 Membar 类型。
7. 无法解析的 loop bound。
8. parser 遇到可能影响 VF 语义但不支持的语句。

### 11.3 允许 warning 后继续的情况

1. 指令 latency 未覆盖，使用统一默认 latency。
2. forwarding pair 未覆盖，使用规定公式。
3. II pair 未覆盖，使用规定公式。
4. b16/b32 timing form 缺失，按已确定的 fp16/fp32 回退链查询。
5. Legacy JSON 根据旧名称规则补全 storage 或 dtype。

### 11.4 结构化字段

诊断至少包含：

1. code。
2. severity。
3. message。
4. source location 或 JSON path。
5. opcode/form/operand 等相关上下文。
6. fallback 值及来源。

同类 warning 应支持去重和最终汇总，但不能只输出一句无法定位来源的通用文本。

## 12. 测试规范

### 12.1 Adapter 单元测试

每个 adapter 必须覆盖：

1. 正常输入到 canonical 输出的 golden test。
2. 非法输入的诊断内容和源码位置。
3. alias、dtype/form、memory expression、loop 和 Membar。
4. 未知语句不能静默丢失。

### 12.2 语义测试

测试应优先断言：

1. 定义-使用边。
2. 内存 base/offset 和 alias 关系。
3. loop-carried、loop exit 和 zero-iteration 语义。
4. 动态 `iteration_path`、`stream_seq` 和静态 ID 的关系。
5. Membar 实际阻塞对象。

不要把临时 `_lane0` 名称、某个内部槽位编号或 normalizer 的中间形态作为主要正确性依据。

### 12.3 Python/C++ 一致性测试

维护一组语言无关的 canonical VfInfo fixtures。Python 和 C++ 对同一 fixture 至少比较：

1. validator 结果。
2. 动态指令数量和顺序标识。
3. dependency graph。
4. Membar gate 关系。
5. timing 参数查询结果和 warning。
6. 最终 cycle。

任何只在 Python 或只在 C++ 中存在的 normalization，都必须说明为何不会改变 canonical 语义。

### 12.4 Cycle 回归

Cycle 回归用于验证性能模型没有意外变化，但不能替代语义测试。开发顺序应为：

1. 先验证 canonical IR。
2. 再验证依赖图和动态 trace。
3. 最后验证 cycle。

若 cycle 改善但依赖边错误，测试必须判失败。

### 12.5 Property Test

建议为通用 pass 增加以下性质测试：

1. canonical validator 不修改输入。
2. 幂等 pass 连续执行两次结果一致。
3. `unroll=1` 不改变跨 loop 值身份。
4. alpha-renaming 不改变 dependency graph。
5. 添加无依赖 compute 不改变既有 load/store 依赖。
6. 序列化再反序列化保持 canonical 语义。

## 13. 当前代码问题与目标归属

| 当前模块 | 当前主要问题 | 目标归属 |
| --- | --- | --- |
| `api/cce_adapter.py` | parser、符号表、opcode 特判、form 推断混合；不支持语句可静默忽略；地址表达式可能取错 identifier | CCE tokenizer、symbol table、expression parser、Catalog binder、diagnostics |
| `api/input_symbols.py` | opcode enum 和 alias 表不完整；承担过多输入兼容规则；Python/C++ 重复维护 | `InstructionCatalog` 与 adapter-local alias |
| `api/vf_info.py` | canonicalization 中按名称推断 storage；全局 dtype 合并；同时 normalize 和 specialize | schema 数据类、Semantic Resolver、Validator |
| `api/json_adapter.py` | 旧格式兼容与 canonical 输入边界不明确 | `LegacyJsonAdapter` 与 canonical serializer |
| `api/vf_lowering.py` | 可能重复 canonicalize；阶段输入输出类型不清晰 | 单一 `CoreLoweringPass` |
| `core/program_canonicalization.py` | 通过字符串 lane 后缀表达 unroll，可能破坏循环外身份 | IFU 结构化动态展开 |
| `core/vreg_live_range_normalization.py` | 数据流、alias 和槽位分配耦合，容易产生 case 分支 | `ValueVersioningPass` 与独立 allocation pass |
| `api/native/VfInfo.cpp` | 与 Python 重复维护别名、dtype 和 specialization | 生成的 Catalog 表或仅 canonical validator |
| C++ 旧 normalization | 与 Python loop/back-edge/exit 语义不一致 | compiler-provided value semantics 或共享 conformance contract |

## 14. 迁移计划

### 阶段零：建立基线

目标：冻结当前行为，避免重构过程中无法判断变化来源。

工作项：

1. 保存 Python 和 C++ 当前测试结果。
2. 为已确认的错误补充 `expected failure` 或问题复现测试。
3. 保存代表性 CCE、JSON 和 canonical VfInfo fixture。
4. 记录每个 fixture 的 dependency graph、warning 和 cycle。

退出条件：重要输入均有可重复基线，已知语义错误不会被误标成正确 golden result。

### 阶段一：定义 schema 和 validator

目标：建立 `RawVfInfo` 与 `CanonicalVfInfo` 的明确边界，不立即改变模拟行为。

工作项：

1. 定义 schema version 1。
2. 定义 instruction、value、memory access、loop、Membar 和 source location。
3. 实现 Python validator。
4. 实现 C++ 等价 validator。
5. 增加跨语言合法/非法 fixture。

当前完成：schema v1、Python/C++ 等价数据类型与 validator、共享合法/非法 fixture，以及 producer、loop scope、class/access 和跨语言数值边界校验。

迁移说明：现有 Core 入口仍保留 `VFInfo` 兼容路径，待 `CoreLoweringPass` 完成后再收紧为仅接收 canonical 对象，避免在 schema 阶段隐式丢失已有字段。

退出条件：同一 fixture 在 Python/C++ 中得到一致校验结果；Core 入口只接受 canonical 对象。

### 阶段二：建立 InstructionCatalog

目标：消除 opcode、dtype/form 和 operand signature 的分散定义。

工作项：

1. 从现有 ISA 和 parser 汇总完整 opcode。
2. 为每条指令声明签名和支持 form。
3. 迁移 VCVT、VMULSCVT、VPACK、VSSTB 等规则。
4. Python 接入 Catalog。
5. 生成或加载 C++ Catalog 表。
6. 增加 Catalog 与 `isa.json` 覆盖差异检查。

当前完成：

1. `configs/instruction_catalog.json` 作为跨语言唯一手写语义来源。
2. Python Catalog loader、自校验、动态完整 `OpCode` 便利枚举和 alias/form specialization。
3. unary、binary、scalar、reduction、conversion、load/store 等 CCE 签名族，以及已登记指令统一 call binder。
4. semantic form 与 timing form 的双向差异、opcode 覆盖差异和 instruction class 冲突检查；semantic form 缺 timing 允许进入 ParamDB fallback。
5. C++ 只读 Catalog 生成表，`VfInfo.cpp` 已删除手写 opcode alias 和 `VCVT` specialization。

当前新增：Catalog binder 的结果会保留到迁移期逻辑 IR，`ValueVersioningPass` 据此构建 canonical operand；CCE 和 legacy JSON 已有显式 canonical 入口。Tilesim adapter 和未登记 opcode 的 CCE 通用猜测路径清理仍待完成。

退出条件：新增一条普通指令只需修改 Catalog 和 timing config，不需要修改 parser 控制流。

### 阶段三：重构 Python adapters

目标：让 CCE、Tilesim 和 legacy JSON 经独立 adapter 进入统一 resolver。

工作项：

1. 修复 CCE `base + offset` 表达式解析。
2. 为不支持语句增加结构化诊断。
3. 将 opcode 参数绑定改为 Catalog 驱动。
4. 新增 Tilesim 专用 adapter。
5. 将名称前缀推断移动到 `LegacyJsonAdapter`。
6. 提供稳定 `VfInfoBuilder`。

退出条件：adapter 不查询 timing、不依赖 Core；所有输入都能追踪到来源位置。

当前进展：CCE canonical 入口已保留 memory base、affine offset、access kind、induction 和 source location；legacy JSON canonical 入口与严格 canonical JSON 入口隔离。Tilesim 本轮按计划暂不接入。

### 阶段四：统一动态展开与值身份

目标：删除基于字符串 lane 名称的依赖表达。

工作项：

1. 引入结构化静态 instruction ID 和动态 iteration path。
2. IFU 统一处理 loop/unroll 动态实例。
3. 删除“单 super iteration”特殊 canonicalization。
4. 增加跨 loop register/UB 身份和 `unroll=1` 回归。

退出条件：unroll 只改变动态实例数量，不改变静态值和内存对象语义。

当前进展：Python canonical 使用 `(definition_id, iteration_path)` 结构化 operand identity。IFU 按真实 lane 顺序建立依赖后，保持原静态指令分组发射顺序；canonical 路径不再生成 `_laneN` 名称。动态展开完成后统一统计值实例的最后使用并标注 RAT keep/release，覆盖直线代码、`unroll=1`、`unroll>1`、循环前后和嵌套循环，不在 unroll builder 中维护独立生命周期规则。IDU 物理寄存器 credit 与 OoO rename 共用 value storage 语义，不再通过名称前缀分类。非 innermost 和不可整除 unroll 明确拒绝，含 Membar 的 loop 保守回退为 1。

当前统一 last-use 仍以完整 canonical 动态流预展开为过渡实现。为避免直接 Canonical 输入中的超大 int64 loop count 在 cycle 0 前导致无界内存分配，`canonical_dynamic_instruction_limit` 默认设为 20000，并允许通过 `uarch` override 显式调整。旧 JSON/旧 `VFInfo` 由 legacy adapter 显式写入 `canonical_dynamic_instruction_limit=0` 以保持历史行为；Core 不读取 `source.adapter`，同内容、同配置的 Canonical 输入具有相同语义。后续应将 carried binding 和 last-use 计数下沉为在线状态机，在保持相同 `(definition_id, iteration_path)` 语义的前提下恢复 IFU 流式展开；达到该目标后删除预展开上限。

Canonical validator 使用节点级诊断位置作用域：校验 instruction、loop、membar 时，内部产生且未显式指定位置的诊断自动继承该节点的 `source_location`；延迟到全局收尾阶段检查的 producer/dependency 诊断保存对应 producer 或 consumer 的位置。前端异常必须保留该结构化位置，不得重新拼接成普通字符串。

### 阶段五：替换 vreg normalization

目标：用通用数据流分析替代 alias 特判堆叠。

工作项：

1. 实现 `ValueVersioningPass`。
2. 显式处理 loop entry、back-edge、exit 和 zero iteration。
3. 将临时槽位复用拆为独立 pass。
4. 对编译器已提供版本的 C++ 输入跳过近似 normalization。
5. 删除 Python/C++ 不一致的旧实现。

退出条件：loop 语义由数据流定义，新增 accumulator 形态不需要增加 opcode 或 case 判断。

当前进展：Python `ValueVersioningPass` 已覆盖直线重定义、纯覆盖、accumulator、串行/嵌套 loop、entry/back-edge/exit 和 zero iteration；Python/C++ canonical 路径均跳过旧 normalization。旧 `VFInfo` 对比路径及其近似 normalization 暂时保留。

### 阶段六：收紧 C++ 入口

目标：C++ 只接受 compiler-built canonical VfInfo。

工作项：

1. 提供 C++ `VfInfoBuilder`、validator 和 `simulate()`。
2. 隔离或废弃 `JsonVfInfoAdapter`。
3. 删除 C++ 名称推断和重复 normalization。
4. 扩充 C++ 测试，不再只依赖少量 smoke case。

退出条件：编译器接入无需 Python 前端逻辑；同一 canonical fixture 与 Python 结果一致。

### 阶段七：清理旧代码和文档

目标：删除迁移期间的双路径，避免新旧语义长期并存。

工作项：

1. 删除废弃 adapter、helper 和兼容入口。
2. 删除重复 canonicalize 调用。
3. 更新 `api.md`、`api/README.md`、`VF_modeling.md` 和开发计划。
4. 增加架构依赖检查和 reviewer checklist。

退出条件：每条输入路径只有一个受支持 pipeline；文档与代码入口一致。

## 15. 建议提交顺序

为便于审查和定位 cycle 变化，建议按以下顺序拆分提交：

1. 仅新增 schema、诊断类型和 validator，不改 cycle。
2. 新增 Catalog，并保持现有 adapter 输出等价。
3. 修复 CCE memory expression 和静默忽略问题。
4. 新增 Tilesim adapter 和 builder。
5. 将 legacy 推断隔离到 Legacy JSON adapter。
6. 引入结构化动态 identity，删除 lane 字符串改写。
7. 实现 ValueVersioningPass。
8. 拆分或删除旧 vreg normalization。
9. 收紧 C++ canonical 入口。
10. 增加 Python/C++ conformance suite，并删除旧路径。

一个提交不应同时包含大规模前端重构、timing 参数更新和调度策略修改。

## 16. 验收标准

### 16.1 架构验收

1. Python 多输入 adapter 和 C++ compiler VfInfo 入口边界清晰。
2. Core 不依赖输入名称、CCE/Tilesim 类型或 legacy JSON 字段。
3. opcode、signature 和支持 form 只有一个事实来源。
4. Canonicalization 和 lowering 不会在调用链中重复执行。

### 16.2 正确性验收

1. `base + offset` 内存表达式保留正确 base 和 offset。
2. 不支持的 CCE 语句不会静默消失。
3. 同一逻辑位置不同定义可具有不同 dtype。
4. 普通 sibling、loop-carried、loop exit 和 zero-iteration 依赖正确。
5. `unroll=1` 不改变循环前后值身份。
6. Membar 的控制语义与动态 `stream_seq` 正确。
7. Python/C++ 对 canonical fixture 的依赖图和 cycle 一致。

### 16.3 可维护性验收

1. 新增普通 opcode 不修改 parser 主控制流。
2. 新增 dtype/form 组合不复制 Python/C++ 映射代码。
3. 修复 loop 数据流问题不需要按 opcode 或 kernel 增加分支。
4. 每个 pass 均有独立单元测试和明确输入输出类型。
5. 所有 fallback warning 可定位到具体指令、参数和来源。

## 17. 代码评审检查表

提交前和评审时至少检查以下内容：

1. 这条规则是否已经在其他模块实现过？
2. 新增分支依据的是通用语义，还是某个 case 的表面形态？
3. 输入语义不明时是否被静默猜测或忽略？
4. 是否把 timing fallback 和输入合法性混为一谈？
5. 是否使用字符串前缀或后缀表达 storage、dtype、iteration 或 value version？
6. pass 是否可能被重复调用？重复调用后结果是否一致？
7. Python 和 C++ 是否会对同一 canonical 输入产生不同依赖？
8. 测试断言的是程序语义，还是当前实现的临时编号？
9. 是否保留了 source location 和足够的诊断上下文？
10. 是否同时修改了前端语义、timing 参数和调度策略，导致结果不可归因？

## 18. 当前优先级

### P0：先消除会生成错误依赖的行为

1. 定义 CanonicalVfInfo schema 和 validator。
2. 修复 CCE 内存 `base + offset` 解析。
3. 禁止 parser 静默忽略未知 VF 语句。
4. 删除或停用会破坏跨 loop 身份的 lane 字符串改写。
5. 在 C++ canonical 路径停用与 Python 语义不一致的旧 normalization。

### P1：降低新增指令和输入接入成本

1. 建立 InstructionCatalog。
2. 增加 Tilesim 专用 adapter。
3. 建立 Python/C++ conformance fixtures。
4. 隔离 Legacy JSON 兼容逻辑。

### P2：完成结构性清理

1. 用 ValueVersioningPass 替换旧 vreg normalization。
2. 简化 C++ VfInfo 入口和重复映射。
3. 删除迁移期双路径和废弃 helper。
4. 更新所有公开 API 与建模文档。

在 P0 完成前，不建议继续为新的 CCE case 在 adapter 或 normalizer 中增加局部特判。

## 19. 当前实施进度

### 19.1 已完成

1. 新增 Python `api/frontend` 包，定义 `schema_version = 1` 的 `CanonicalVfInfo`、instruction、operand、value、memory access、loop、Membar 和 source location。
2. 新增 Python 无副作用 validator 和结构化 diagnostic；validator 不读取 timing config，不拒绝语义明确但 timing 未覆盖的 opcode。`frozen dataclass` 只限制字段重新赋值，其中的 mapping 不承诺深度不可变。
3. 新增 C++ `api/native/CanonicalVfInfo.h` 和等价 validator；C++ canonical 节点使用 variant 表示 payload，非法输入返回 diagnostic，不因整数解析失败抛异常。
4. `InputAPI.validate_canonical_vf_info()` 提供显式校验入口。
5. 定义语言无关的 `api/frontend/canonical_vf_info_v1.schema.json`，并建立 Python/C++ 共用的 conformance fixture。测试侧 C++ decoder 会完整转换 fixture，再与 Python validator 对齐合法结果和非法 diagnostic code。
6. Canonical value 明确表示一次 definition，同时保留 logical ID 和 producer node；DATA dependency 只从 input definition 推导，显式 dependency 仅允许 memory/control。
7. UB storage object 与 memory value definition 已拆分；alias 身份使用稳定 `storage_object_id`，definition ID 只描述一次 memory state。
8. loop 显式描述 induction variable 和 entry/back-edge/exit，并校验 definition scope 与类型；Python 直接输入同时检查 int64 边界和有限 scalar。
9. 新增 Python/C++ 合法与非法契约测试，覆盖 value 引用、operand role/dtype、memory object、affine variable scope、loop-carried scope、非法 payload 和 Membar。
10. 在前端重构前同步 Python/C++ 后端：native 已采用 ALU/SFU 独立 RR、EXU0 reserve 配置，并对齐 loop back-edge、loop exit alias、重定义 kill、零次 loop 和 `three_ports_mode` 语义。native SHQ 到 EXQ 只生成一次候选端口，再按 policy 选择 greedy 或 RR。
11. 建立共享 `InstructionCatalog`：`configs/instruction_catalog.json` 是唯一手写语义目录，Python 直接加载，C++ 由生成表消费；已覆盖 alias、instruction class、semantic form、specialization、operand signature、可选参数和 CCE 配置值。
12. CCE 已知指令统一通过 Catalog binder，严格检查参数数量、operand kind、predicate、配置值与 semantic form；Canonical Python/C++ validator 对已知 opcode 检查 Catalog class/form/signature，未知 opcode 保留显式语义输入和 ParamDB fallback。
13. CCE block 使用按声明顺序生效的词法作用域符号表，覆盖 vector、predicate 和局部 scalar；scalar initializer 延迟到实际作为 offset 使用时再递归校验，普通 scalar operand 和无关声明不受 affine 规则影响。离开 block 后局部定义失效。Catalog `call_variants` 描述 POST_UPDATE 与关联参数 overload；offset MVP 拒绝变量乘变量，只接受 affine 整数表达式。VF scope 中未登记语句必须携带原始文本报错，不能静默忽略。
14. 新增 Python `VfInfoBuilder`：显式注册 storage/value/node，支持嵌套 loop context、直接 loop body 和 Membar；重复 ID 早报错，`build()` 统一调用 canonical validator 并通过 `VfInfoValidationError` 暴露结构化 diagnostics。`InputAPI.new_vf_info_builder()` 是公开创建入口。
15. 新增 canonical JSON 正式入口：`CanonicalJsonVfInfoAdapter` / `InputAPI.load_canonical_json()` 在对象解码前直接消费共享 JSON Schema，严格拒绝任意层级的未知字段、缺失字段和非法类型，再执行 semantic validator；不执行 legacy 推断。`jsonschema` 是仅在首次使用该入口时加载的可选依赖，不能扩散到 builder、CCE、legacy JSON、Tilesim 或 Core 的 import 路径。JSON 语法、schema、payload 解码和语义校验失败均通过结构化 diagnostics 暴露。旧 `JsonVfInfoAdapter` 继续只负责迁移期 trace，两个入口不自动互相回退。
16. 建立 canonical 到 Python Core 的显式纵向链路：`CoreLoweringPass` 保留 static instruction ID、definition ID、稳定 UB object、source location 和 affine memory metadata；canonical cost-model 入口跳过旧 vreg normalization 与 single-super-iteration 改写。IFU 支持 frame-local loop-carried、zero iteration、非默认 induction 和 innermost `unroll>1`；动态 operand identity 使用 definition ID 与 iteration path，继续贯穿 IDU、Uop 和日志。显式 dependency 在动态 edge lowering 完成前仍明确拒绝。
17. 统一 Python/C++ Core 的依赖契约：只自动推导寄存器 producer-consumer 依赖，删除基于 `(UB 名称, iter_stack)` 的隐式 store-to-load edge。UB 顺序只由显式 Membar 控制；canonical memory/control `DependencyRef` 在动态 Uop edge lowering 实现前明确拒绝。canonical cost-model 固定执行 `validate -> compatibility -> lower -> Core`。
18. 建立 C++ canonical Core 入口：`runCanonicalVfInfo()` 直接消费 `CanonicalVfInfo`，由 `CanonicalProgramLowering` 展开 definition、loop-carried binding、动态 iteration identity 和 value lifetime，不经过旧 `VfInfo` lowering/normalization/canonicalization。共享普通 loop 与 loop-carried fixture 的 cycle 已与 Python 对齐。
19. CCE 寄存器赋值使用零周期 `VFAlias`，由 `ValueVersioningPass` 快照赋值点 definition，不再维护永久字符串 alias；带 cast 的 UB pointer alias 和 alias chain 统一保留稳定 storage object。Catalog CONFIG 只有显式 `allow_integer_expression` 时接受整数编码。C++ canonical 与 Python 一致拒绝 non-innermost `unroll>1`。
20. canonical `uarch` override 使用共享字段类型与目标范围 schema；`canonical_dynamic_instruction_limit` 明确为 Python-only。C++ validator 拒绝 Python-only 和未知字段，resolver 对未消费字段再次报错，并由 native 测试双向校验 schema 的 C++ 字段集合与 resolver 字段集合，避免新增配置只校验但不生效。
21. Python 与 C++ 公共预测入口已统一到 Canonical：Python 以 `predict_canonical_vf_cycles()` / `run_canonical_vf_info()` 为正式接口，`main.py` 和 CCE 文件预测直接生成 `CanonicalVfInfo`；旧输入通过显式 `LegacyVfInfoAdapter`、`predict_legacy_vf_cycles()` 或 `run_legacy_vf_info()` 接入。C++ 以 `runCanonicalVfInfo()` 为正式入口，`runLegacyVfInfo()` 为适配入口，`runVfInfo()` 仅保留弃用包装。旧 value-ID lowering、vreg normalization 和 single-super-iteration 改写不再位于公共执行路径。

### 19.2 当前迁移边界

现有 CCE 和 legacy JSON 的部分兼容 loader 仍可返回 `api.vf_info.VFInfo`，供旧调用方读取和检查；一旦进入预测，必须经 `ValueVersioningPass` 生成 definition，再由 `CoreLoweringPass` 接入。显式 canonical loader 直接返回同一目标契约。

阶段二 Catalog、Python builder、通用 ValueVersioningPass、CCE/legacy canonical 入口以及 Python/C++ canonical Core 入口已建立。当前主要迁移边界是：Tilesim 暂未接入，显式 memory/control dependency 尚未 lower 为动态 Uop edge。旧 `VFInfo` 类型与旧 JSON 格式只保留为 adapter 输入；旧 Core 执行分支已经退出主程序和回归入口，相关 normalization 模块仅供历史专项测试和后续物理清理。
