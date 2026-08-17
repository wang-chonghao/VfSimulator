# API 层

本目录包含 VF Simulator 的 typed input boundary。

当前迁移期包含两个明确边界：

- `api.frontend.CanonicalVfInfo`：版本化的目标输入契约。validator 不修改输入；Python 容器和 C++ 对象本身不承诺深度不可变。
- `api.vf_info.VFInfo`：现有 CCE/legacy JSON adapter 和 Core 仍在使用的迁移期接口。

指令语义目录的唯一手写数据源是 `configs/instruction_catalog.json`，Python loader/validator 位于 `api/frontend/instruction_catalog.py`。它集中声明 canonical opcode、alias、instruction class、semantic form、CCE operand signature 和 specialization；不包含 latency、forwarding、II 或 EXU 配置，也不是 timing 覆盖白名单。未知但语义明确的 opcode 仍可进入 ParamDB fallback。

CCE 中已登记的 load、store 和 compute 调用统一由 Catalog binder 按 argument index、方向和 kind 绑定。缺少必填 operand，或把 UB、register、scalar 放入错误位置时会直接产生输入错误，不再回退到遍历参数猜测。未登记 opcode 暂时保留通用 vector-call 兼容路径，仍由 ParamDB 记录 timing fallback warning。

现有 frontend 入口包括：

- `InputAPI.load_json_trace(path)`：把旧 JSON trace 加载为 `VFInfo`。
- `InputAPI.load_cce_file(path, kernel_name=None, loop_params=None)`：解析 CCE/DSL 文件，并从一个 `__VEC_SCOPE__` kernel 提取 `VFInfo`。
- `JsonVfInfoAdapter.from_payload(payload)`：适配内存中的 JSON-shaped payload。
- `parse_cce_vf_info(path, kernel_name=None, loop_params=None)`：直接 CCE parser 入口。
- `VFInfoLowerer().lower(vf_info)`：把公共 `VFInfo` lower 成当前 core simulator payload。
- `CoreVfCostModel().predict_vf_cycles(vf_info)`：用当前 queue-level 主线模拟器运行 `VFInfo`。
- `InputAPI.validate_canonical_vf_info(vf_info)`：只校验 `CanonicalVfInfo`，不修复、不查询 timing 参数，也不产生文件系统副作用。

目标 canonical 数据模型定义在 `api/frontend/schema.py`：

- 必须携带 `schema_version=1`。
- 跨语言序列化事实来源是 `api/frontend/canonical_vf_info_v1.schema.json`，共享样例位于 `tests/fixtures/canonical_vf_info/`。
- value 使用 `definition_id` 表示一次定义、`logical_id` 关联同一逻辑值，并用 `producer_node_id` 建立唯一的数据依赖来源；instruction 使用稳定 `instruction_id`、显式 `instruction_class`、canonical `opcode/form` 和带角色的 `inputs/outputs`。
- `producer_node_id` 必须与 producer 的实际 outputs 对应：instruction 只能产生其唯一 output definition，loop 只能产生 `carried_values.exit_value_id`，Membar 不能产生 value。
- UB storage object 使用稳定 `object_id`。UB value definition 通过 `storage_object_id` 引用它，operand 携带 `MemoryAccess(base_object_id, affine offset, access_kind, span)`；内存 alias 基于 storage object 和访问范围，不基于 definition ID。
- loop 保留结构化 `count/unroll/body`、induction variable 和 entry/back-edge/exit 关系；validator 检查三类 definition 的作用域和类型一致性。Membar 仅支持 `VST_VLD` 和 `VLD_VST`。
- 外层 loop back-edge 只能由外层 loop body 同级节点产生；若值来自嵌套 loop，必须引用嵌套 loop node 产生的 exit definition，不能直接引用其内部 instruction。
- instruction class 与 memory access 使用严格矩阵：load 只读 UB、store 只写 UB、compute/control 不携带 memory access。未来如需 fused memory operation，必须增加显式类别。
- 显式 dependency 只表达额外 memory/control ordering；DATA dependency 自动从 input value 的 producer 推导，不能重复声明。未知 opcode 只要给出明确 `instruction_class` 和完整 operand 语义即可通过 validator，timing 缺失仍由 ParamDB fallback 并记录 warning。
- 所有整数和整数表达式必须可表示为 `int64_t`，scalar 浮点值必须有限，确保 Python 直接构造的对象能由 C++ 等价表示。
- validator 位于 `api/frontend/validator.py`，只检查语义完整性；未知但语义明确的 opcode 可以通过，timing 覆盖由 ParamDB 处理。
- `DEFAULT_INSTRUCTION_CATALOG.compare_timing_config()` 分别报告 semantic form 缺 timing、timing form 未声明语义、opcode 覆盖差异和 instruction class 冲突。前一类允许 ParamDB fallback，后三类属于需要修复的语义冲突。

C++ 对应契约位于 `api/native/CanonicalVfInfo.h`，字段类型与 v1 JSON 契约一致，不包含 CCE parser 或 legacy JSON 推断。必填枚举默认值均为 `Unknown` 并由 validator 拒绝；递归 loop payload 以 `shared_ptr<const CanonicalLoop>` 表示，可共享但不能通过副本修改原 loop。

C++ opcode Catalog 位于 `api/native/InstructionCatalog.*`，只读数据由 `tools/generate_instruction_catalog_cpp.py` 从共享 JSON 生成到 `api/native/generated/InstructionCatalogData.inc`。测试会校验生成结果未过期；`api/native/VfInfo.cpp` 不再手写 opcode alias 或 `VCVT` specialization。

迁移期公共数据模型定义在 `vf_info.py`：

- `VFInfo`：顶层 program 容器，包含 `context`、`values`、`params`、`default_dtype` 和可选 `uarch` override。
- `VFLoop`：结构化 loop 节点，包含 `count`、`unroll`、`body` 和可选 `loop_id`。
- `VFInst`：向量指令节点，包含 `name`、`src`、`dst` 和可选 `form`。
- `ValueInfo`：typed value 描述，包含 `value_id`、`storage`、`dtype`、`shape`。
- `MemInfo`：`ValueInfo` 的兼容别名。
- `Membar`：显式 memory/order barrier 节点。

`core/` 后端仍消费历史 JSON-like payload。当前不会把新的 canonical 对象隐式转换成旧 `VFInfo`，因为那会丢失结构化 memory access 和 source location。后续 adapter 迁移完成后，再由单一 `CoreLoweringPass` 接入 Core。
