# API 层

本目录包含 VF Simulator 的 typed input boundary。

当前迁移期包含两个明确边界：

- `api.frontend.CanonicalVfInfo`：版本化的目标输入契约。validator 不修改输入；Python 容器和 C++ 对象本身不承诺深度不可变。
- `api.vf_info.VFInfo`：旧入口和 Core 仍支持的迁移期逻辑接口；CCE/legacy JSON 同时提供显式 canonical 入口，不会自动切换契约。

指令语义目录的唯一手写数据源是 `configs/instruction_catalog.json`，Python loader/validator 位于 `api/frontend/instruction_catalog.py`。它集中声明 canonical opcode、alias、instruction class、semantic form、CCE operand signature 和 specialization；不包含 latency、forwarding、II 或 EXU 配置，也不是 timing 覆盖白名单。未知但语义明确的 opcode 仍可进入 ParamDB fallback。

CCE 中已登记的 load、store 和 compute 调用统一由 Catalog binder 按 argument index、方向和 kind 绑定。binder 同时检查参数数量、声明过的配置值和 semantic form；缺少必填 operand、携带额外参数，或把 UB、register、scalar、predicate 放入错误位置时会直接产生输入错误。未登记 opcode 暂时保留通用 vector-call 兼容路径，仍由 ParamDB 记录 timing fallback warning。

CCE binder 使用词法作用域符号表：vector、predicate、局部 scalar 和局部 `__ubuf__` 指针别名按源程序顺序生效，loop 内声明不会泄漏到外层。UB 指针别名始终保留原始函数参数对应的稳定 storage object，并把声明处和调用处的简单 affine offset 合并；带 C cast 的 alias 和 alias chain 使用同一解析规则。寄存器赋值 `b = a` 生成为零周期 `VFAlias`，由 `ValueVersioningPass` 在赋值位置把 `b` 绑定到 `a` 的当前 definition，因此后续重定义 `a` 不会改变 `b` 的依赖。局部 scalar 声明只记录名称、dtype 和 initializer；普通 scalar operand 不解析 initializer，只有实际作为 load/store offset 时才递归检查 affine 语义。因此与 VF 无关的 `get_block_idx()` 等初始化不会阻塞解析，也不需要函数特判。局部整数可以递归引用定义，例如 `stride = 64; off = stride * i`。load/store 的普通寻址与 `POST_UPDATE`、`VDUP` 的 4/5 参数形式由 Catalog `call_variants` 声明，不通过忽略尾部参数兼容。offset MVP 只接受常量、变量、加减和“至少一侧为常量表达式”的乘法；变量相乘不属于 affine expression，会在 offset 使用点报错。VF scope 内除显式登记的 `pset_*` 等 no-op 外，无法识别的语句会携带原始文本报错，不再静默忽略。

Catalog 的 form 描述指令语义，不代表 timing 已覆盖。已知 opcode 的 canonical 输入必须匹配 Catalog 中的 instruction class、form 和 operand signature；未知 opcode 只要显式携带完整语义仍可通过 validator。`VPACK.b32` 的 `b32` 表示输入 lane 视角，逻辑寄存器仍可使用 `fp16/bf16` dtype，validator 不把 form 与每个 value dtype 强制等同。

Timing form 兼容只由 `core/param_compat.py` 与 `native/ParamCompat.cpp` 维护，目前仅支持 `b16 -> fp16`、`b32 -> fp32`，并继承同 opcode 兼容 form 的完整参数后再由真实 form 局部覆盖。普通缺失 form（例如只有 `fp32` 参数却请求 `fp16`）不借用 `fp32`，统一使用默认参数并记录 `unsupported_isa_form`。

现有 frontend 入口包括：

- `InputAPI.load_json_trace(path)`：把旧 JSON trace 加载为 `VFInfo`。
- `InputAPI.load_legacy_json_canonical(path)`：明确按 legacy 规则读取旧 trace，再通过 `ValueVersioningPass` 生成 canonical definition；不会替代严格 canonical JSON 入口。
- `InputAPI.load_canonical_json(path)`：加载并校验 `schema_version=1` 的 canonical JSON；对象解码前直接用 `canonical_vf_info_v1.schema.json` 拒绝未知字段、缺失字段和非法类型，再执行 semantic validator。不执行 legacy 名称、dtype 或 storage 推断，失败时抛出携带结构化 diagnostics 的 `VfInfoValidationError`。
- `InputAPI.load_cce_file(path, kernel_name=None, loop_params=None)`：解析 CCE/DSL 文件，并从一个 `__VEC_SCOPE__` kernel 提取 `VFInfo`。
- `InputAPI.load_cce_canonical(path, kernel_name=None, loop_params=None)`：保留 Catalog operand、affine offset、induction 和 source location，并生成经过校验的 `CanonicalVfInfo`。
- `InputAPI.to_canonical(vf_info)`：将迁移期逻辑寄存器写入版本化为 instruction definition 和 loop entry/back-edge/exit。
- `JsonVfInfoAdapter.from_payload(payload)`：适配内存中的 JSON-shaped payload。
- `parse_cce_vf_info(path, kernel_name=None, loop_params=None)`：直接 CCE parser 入口。
- `VFInfoLowerer().lower(vf_info)`：把公共 `VFInfo` lower 成当前 core simulator payload。
- `CoreVfCostModel().predict_vf_cycles(vf_info)`：用当前 queue-level 主线模拟器运行 `VFInfo`。
- `CoreVfCostModel().predict_canonical_vf_cycles(vf_info)`：通过 `CoreLoweringPass` 运行 canonical 输入；当前纵向链路支持无显式 dependency、非默认 induction、loop-carried/zero iteration，以及可整除的 innermost `unroll>1`。
- C++ `runCanonicalVfInfo(vf_info, db)`：直接校验并展开 `api/native/CanonicalVfInfo.h`，跳过旧 `VfInfo` lowering、寄存器 normalization 和字符串 lane 改写；共享 fixture 会与 Python 比较 `cycles_executed/vf_end_cycle`。
- `InputAPI.validate_canonical_vf_info(vf_info)`：只校验 `CanonicalVfInfo`，不修复、不查询 timing 参数，也不产生文件系统副作用。
- `VfInfoBuilder` / `InputAPI.new_vf_info_builder()`：显式注册 storage object、value definition、instruction、loop 和 Membar，`build()` 校验后返回 `CanonicalVfInfo`；校验失败抛出携带结构化 diagnostics 的 `VfInfoValidationError`。

Python 调用方可直接使用 builder，不需要拼接内部 dict：

```python
builder = InputAPI.new_vf_info_builder(params={"N": 4})
builder.register_storage_object("ub.input", storage=StorageKind.UB)
builder.register_value(
    "input.0",
    logical_id="input",
    storage=StorageKind.UB,
    dtype="fp32",
    storage_object_id="ub.input",
)
with builder.loop("loop.0", induction=InductionVariable("i"), count="N"):
    ...
vf_info = builder.build()
```

builder 不推断 opcode、storage、dtype 或 producer，也不查询 timing。调用方必须提供 canonical 字段；重复 ID 会在注册时失败，跨节点和数据流错误由最终 validator 统一诊断。

canonical JSON 文件入口使用可选依赖 `jsonschema` 直接执行共享 schema；需要该入口时安装 `python3 -m pip install jsonschema`。依赖在首次调用 `load_canonical_json()` 时延迟加载，缺少它不会影响 `VfInfoBuilder`、CCE、legacy JSON、Tilesim adapter 或核心预测入口。

目标 canonical 数据模型定义在 `api/frontend/schema.py`：

- 必须携带 `schema_version=1`。
- 跨语言序列化事实来源是 `api/frontend/canonical_vf_info_v1.schema.json`，共享样例位于 `tests/fixtures/canonical_vf_info/`。
- value 使用 `definition_id` 表示一次定义、`logical_id` 关联同一逻辑值，并用 `producer_node_id` 建立唯一的数据依赖来源；instruction 使用稳定 `instruction_id`、显式 `instruction_class`、canonical `opcode/form` 和带角色的 `inputs/outputs`。
- `producer_node_id` 必须与 producer 的实际 outputs 对应：instruction 只能产生其唯一 output definition，loop 只能产生 `carried_values.exit_value_id`，Membar 不能产生 value。
- UB storage object 使用稳定 `object_id`。UB value definition 通过 `storage_object_id` 引用它，operand 携带 `MemoryAccess(base_object_id, affine offset, access_kind, span)`；内存 alias 基于 storage object 和访问范围，不基于 definition ID。
- loop 保留结构化 `count/unroll/body`、induction variable 和 entry/back-edge/exit 关系；validator 检查三类 definition 的作用域和类型一致性。Membar 仅支持 `VST_VLD` 和 `VLD_VST`。
- loop back-edge 可以引用当前 loop body 同级产生的值，或 loop 入口前已经可见的 invariant/carried definition；若值来自嵌套 loop，必须引用嵌套 loop node 产生的 exit definition，不能直接引用其内部 instruction。
- instruction class 与 memory access 使用严格矩阵：load 只读 UB、store 只写 UB、compute/control 不携带 memory access。未来如需 fused memory operation，必须增加显式类别。
- 显式 dependency 只表达额外 memory/control ordering；DATA dependency 自动从 input value 的 producer 推导，不能重复声明。未知 opcode 只要给出明确 `instruction_class` 和完整 operand 语义即可通过 validator，timing 缺失仍由 ParamDB fallback 并记录 warning。
- 所有整数和整数表达式必须可表示为 `int64_t`，scalar 浮点值必须有限，确保 Python 直接构造的对象能由 C++ 等价表示。
- validator 位于 `api/frontend/validator.py`，只检查语义完整性；未知但语义明确的 opcode 可以通过，timing 覆盖由 ParamDB 处理。
- `DEFAULT_INSTRUCTION_CATALOG.compare_timing_config()` 分别报告 semantic form 缺 timing、timing form 未声明语义、opcode 覆盖差异和 instruction class 冲突。前一类允许 ParamDB fallback，后三类属于需要修复的语义冲突。

C++ 对应契约位于 `api/native/CanonicalVfInfo.h`，字段类型与 v1 JSON 契约一致，不包含 CCE parser 或 legacy JSON 推断。必填枚举默认值均为 `Unknown` 并由 validator 拒绝；递归 loop payload 以 `shared_ptr<const CanonicalLoop>` 表示，可共享但不能通过副本修改原 loop。

C++ canonical 预测入口位于 `native/SimulatorRunner.h`。`CanonicalProgramLowering` 使用 definition ID、每层动态 iteration path 和 loop-carried binding 生成动态指令，贯穿 `staticInstructionId`、`streamSeq` 及 value last-use/keep 标记，并应用 canonical 中已知的 `UarchConfig` override。该入口不调用旧 `lowerVfInfoValueIds()`、`normalizeProgramVregLiveRanges()` 或 `canonicalizeSingleSuperIterationLoops()`；旧 `runVfInfo()` 只为主程序、legacy JSON 和对比回归保留。含 Membar 的 innermost unroll 会保守回退为 1，并记录 `membar_unroll_disabled`。

canonical `uarch` override 的字段类型由 `configs/uarch_override_schema.json` 统一定义。Python 直接读取该 schema，C++ 只读表由 `tools/generate_uarch_override_schema_cpp.py` 生成；已登记的 integer、boolean、string 字段不执行隐式类型转换，未知扩展字段仍需满足通用 JSON scalar 约束。

C++ opcode Catalog 位于 `api/native/InstructionCatalog.*`，只读数据由 `tools/generate_instruction_catalog_cpp.py` 从共享 JSON 生成到 `api/native/generated/InstructionCatalogData.inc`。测试会校验生成结果未过期；`api/native/VfInfo.cpp` 不再手写 opcode alias 或 `VCVT` specialization。

迁移期公共数据模型定义在 `vf_info.py`：

- `VFInfo`：顶层 program 容器，包含 `context`、`values`、`params`、`default_dtype` 和可选 `uarch` override。
- `VFLoop`：结构化 loop 节点，包含 `count`、`unroll`、`body` 和可选 `loop_id`。
- `VFInst`：向量指令节点，包含 `name`、`src`、`dst` 和可选 `form`。
- `VFAlias`：源语言寄存器值绑定，不产生模拟指令；版本化时快照 source definition。
- `ValueInfo`：typed value 描述，包含 `value_id`、`storage`、`dtype`、`shape`。
- `MemInfo`：`ValueInfo` 的兼容别名。
- `Membar`：显式 memory/order barrier 节点。

`ValueVersioningPass` 是迁移期逻辑 IR 到 canonical definition 的唯一版本化实现。它为每次寄存器写入创建 definition，并将循环中写入的寄存器统一描述为 entry/back-edge/exit，因此 accumulator、纯覆盖、嵌套 loop 和 zero iteration 不需要 opcode 特判。UB 只保留稳定 storage object 与显式 memory access，不从名称或地址自动建立数据依赖。

Catalog 的 CONFIG operand 默认只接受声明的符号值。只有显式设置 `allow_integer_expression` 的 operand 才可接受整数表达式；当前用于 load/store offset 和 `VSSTB` 编码配置，不能让任意整数绕过 mode/round/part 等 `allowed_values`。

`core/` 后端仍消费 JSON-like payload，但 canonical 对象只通过 `CoreLoweringPass` 接入，不会先降级成旧 `VFInfo`。旧 `VFInfoLowerer` 路径继续用于结果对比和兼容。

`CoreLoweringPass` 已建立 canonical 到 Python Core 的显式链路。它保留静态 instruction ID、definition ID、稳定 UB object、source location 和 affine memory metadata，并在 canonical 路径跳过旧 vreg normalization 与 single-super-iteration 字符串改写。每个动态 loop frame 独立维护 carried binding；`iteration_path` 记录动态 `iteration`、`induction_variable` 和按 `start + iteration * step` 计算的 `induction_value`。innermost `unroll>1` 先按真实迭代顺序建立结构化 operand identity，再按既有静态指令分组顺序送入流水线；OoO RAT 使用 `(definition_id, iteration_path)`，不再构造 `_laneN` 寄存器名。canonical 动态流统一计算每个值实例的最后使用，覆盖直线代码、`unroll=1`、`unroll>1`、循环前后和嵌套循环；IDU 和 OoO 均通过 value storage metadata 识别寄存器，不依赖 `V` 名称前缀。该身份继续贯穿 Uop、IDU dispatch 和执行日志。非 innermost unroll、不能整除的 batch 仍明确拒绝；含 Membar 的 innermost unroll 保守回退为 1 并记录 warning。

当前 canonical last-use 标注仍会在第一次取指时预展开动态流。`uarch.canonical_dynamic_instruction_limit` 默认限制为 20000 条，超过时在分配无界内存前明确报错；这是迁移期保护，不是最终的流式 lifetime 实现。可以通过运行时 `uarch` override 显式调整该限制。

`ValueVersioningPass` 对 Catalog 已知指令使用 Catalog operand signature 决定 role，value storage 只用于检查 register/scalar/UB 是否匹配；未知指令才使用通用 storage 推断。validator 失败统一抛出 `VfInfoValidationError`，保留 diagnostic code、path、context 和 source location。instruction、loop、membar 节点范围内产生的诊断默认继承当前节点的 `source_location`，因此 CCE canonical 错误可同时报告 canonical path 与原始文件、行、列。

Core 只自动建立寄存器 producer-consumer 依赖，不根据 UB 名称、迭代或地址表达式推导 load/store 依赖。UB 顺序当前统一由显式 `Membar(VST_VLD/VLD_VST)` 控制；canonical memory/control `DependencyRef` 在动态 Uop edge lowering 完成前会明确拒绝。
