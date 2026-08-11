# API 层

本目录包含 VF Simulator 的 typed input boundary。

当前两个 frontend 都返回同一个公共 `VFInfo` 结构：

- `InputAPI.load_json_trace(path)`：把旧 JSON trace 加载为 `VFInfo`。
- `InputAPI.load_cce_file(path, kernel_name=None, loop_params=None)`：解析 CCE/DSL 文件，并从一个 `__VEC_SCOPE__` kernel 提取 `VFInfo`。
- `JsonVfInfoAdapter.from_payload(payload)`：适配内存中的 JSON-shaped payload。
- `parse_cce_vf_info(path, kernel_name=None, loop_params=None)`：直接 CCE parser 入口。
- `VFInfoLowerer().lower(vf_info)`：把公共 `VFInfo` lower 成当前 core simulator payload。
- `CoreVfCostModel().predict_vf_cycles(vf_info)`：用当前 queue-level 主线模拟器运行 `VFInfo`。

公共数据模型定义在 `vf_info.py`：

- `VFInfo`：顶层 program 容器，包含 `context`、`values`、`params`、`default_dtype` 和可选 `uarch` override。
- `VFLoop`：结构化 loop 节点，包含 `count`、`unroll`、`body` 和可选 `loop_id`。
- `VFInst`：向量指令节点，包含 `name`、`src`、`dst` 和可选 `form`。
- `ValueInfo`：typed value 描述，包含 `value_id`、`storage`、`dtype`、`shape`。
- `MemInfo`：`ValueInfo` 的兼容别名。
- `Membar`：显式 memory/order barrier 节点。

`core/` 后端仍消费历史 JSON-like payload。该格式被有意放在 `VFInfoLowerer` 后面，调用方不需要依赖模拟器内部 operand 命名或 loop flattening 细节。
