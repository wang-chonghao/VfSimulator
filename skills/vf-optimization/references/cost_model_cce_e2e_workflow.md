# Cost Model 与 CCE 端到端工作流

本文说明从 CCE 源码到 VfSim 预测，以及 VfSim 与 CCE/Camodel 结果对比的当前工作流。

## 1. 从 CCE 源码运行模型

主线模型可以直接读取 CCE/DSL：

```bash
python main.py ^
  --cce cce_code/GeLU_poly.dsl ^
  --out_dir results/tmp_model/gelu_poly_cce
```

内部链路为：

```text
CCE/DSL 源码
  -> 提取 __VEC_SCOPE__ kernel
  -> api.cce_adapter.parse_cce_canonical_vf_info
  -> CanonicalVfInfo
  -> api.frontend.CoreLoweringPass
  -> Core payload
  -> core simulator
```

文件包含多个 VF kernel 时需要显式选择：

```bash
python main.py ^
  --cce cce_code/example.dsl ^
  --cce-kernel selected_kernel ^
  --out_dir results/tmp_model/selected_kernel
```

对应 Python API：

```python
from api.cce_adapter import list_cce_vf_kernels, parse_cce_canonical_vf_info
from api.simulator_costmodel import CoreVfCostModel

kernels = list_cce_vf_kernels("cce_code/example.dsl")
vf_info = parse_cce_canonical_vf_info(
    "cce_code/example.dsl",
    kernel_name=kernels[0],
)
cycles = CoreVfCostModel(
    out_dir="results/api_costmodel/example"
).predict_vf_cycles(vf_info)
```

也可以直接调用便利函数：

```python
from api.simulator_costmodel import predict_cce_file_cycles

cycles = predict_cce_file_cycles(
    "cce_code/example.dsl",
    kernel_name="selected_kernel",
)
```

## 2. 直接构造输入

新代码应使用 `VfInfoBuilder` 构造 `CanonicalVfInfo`，避免依赖迁移期 `VFInfo` 的隐式补全：

```python
from api.frontend import (
    CanonicalOperand,
    InstructionClass,
    OperandRole,
    StorageKind,
)
from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel

builder = InputAPI.new_builder()
for value_id in ("lhs.entry", "rhs.entry"):
    builder.register_value(
        value_id,
        logical_id=value_id.split(".")[0],
        storage=StorageKind.REGISTER,
        dtype="fp32",
    )
builder.register_value(
    "sum.def0",
    logical_id="sum",
    storage=StorageKind.REGISTER,
    dtype="fp32",
    producer_node_id="inst.add",
)
builder.add_instruction(
    "inst.add",
    opcode="VADD",
    instruction_class=InstructionClass.COMPUTE,
    form="fp32",
    inputs=(
        CanonicalOperand("lhs.entry", OperandRole.SOURCE, "fp32"),
        CanonicalOperand("rhs.entry", OperandRole.SOURCE, "fp32"),
    ),
    outputs=(
        CanonicalOperand("sum.def0", OperandRole.DESTINATION, "fp32"),
    ),
)
canonical = builder.build()
cycles = CoreVfCostModel().predict_vf_cycles(canonical)
```

旧 JSON 必须先离线转换，不能直接进入预测 API：

```bash
python3 tools/convert_legacy_vfinfo.py old.json canonical.json
```

## 3. 运行 CCE/Camodel

CCE native 仿真依赖独立环境，常用辅助命令为：

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_cce_round.ps1 `
  -DslPath cce_code\GeLU_poly.dsl `
  -RoundTag gelu_poly_probe `
  -TotalElems 6144
```

如果 WSL distribution 名称或用户上下文导致启动失败，可以显式指定：

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_cce_round.ps1 `
  -DslPath cce_code\GeLU_poly.dsl `
  -RoundTag gelu_poly_probe `
  -TotalElems 6144 `
  -WslDistro "-Ubuntu"
```

自动化进程的 Windows/WSL 用户上下文可能与手工终端不同。此时可以手工运行 CCE，再让模型侧工具读取生成的 dump。

常见 CCE/Camodel dump：

- `core0.veccore0.instr_popped_log.dump`
- `core0.veccore0.instr_log.dump`
- `core0.veccore0.rvec.EXU.dump`
- `core0.veccore0.rvec.IDU.dump`

CCE 时序通常按下式统计：

```text
CCE VF total = VF_end - VF_start
```

模型侧使用：

```text
VF end cycle (with drain)
```

对比前必须确认两侧使用相同的 VF 起止和排空口径。

## 4. IPC 对比

使用 CCE EXU dump 和模型 start log 绘制 IPC：

```bash
python tools/plot_cce_model_ipc_compare.py ^
  --cce-exu-dump <core0.veccore0.rvec.EXU.dump> ^
  --model-start-log <model_out_dir>/start_by_cycle.json ^
  --window 25 ^
  --align-start ^
  --out-png results/ipc_compare/cce_vs_model.png ^
  --out-csv results/ipc_compare/cce_vs_model.csv
```

分析计算发射行为时，通常只统计 compute 指令，排除 VLD/VST、SEND 和 PSET 类指令。若分析 LSU 或前端拥塞，则应单独定义统计集合，不能混用计算 IPC 口径。

## 5. 旧 JSON 回归输入

旧 JSON 仍用于回归测试和手写微用例：

```json
{
  "dtype": "fp32",
  "params": {"I": 16, "U": 1},
  "program": [
    {
      "type": "loop",
      "iters": "I",
      "unroll": "U",
      "body": [
        {"type": "inst", "op": "VLD", "dst": ["V0"], "src": ["memA"]},
        {"type": "inst", "op": "VADDS", "dst": ["V1"], "src": ["V0"]},
        {"type": "inst", "op": "VST", "dst": ["memB"], "src": ["V1"]}
      ]
    }
  ]
}
```

运行命令：

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/tmp_model/gelu_poly_json
```

`main.py` 会通过 `LegacyCanonicalJsonAdapter` 将旧 JSON 转换为 `CanonicalVfInfo`，随后复用同一 `CoreLoweringPass` 和 Core 执行路径。旧 JSON 不拥有独立模拟语义。

## 6. 精度对比流程

1. 确认每个 case 在 manifest 或结果表中具有 CCE/Camodel 参考时间。
2. 确认两侧使用相同的 VF 指令、loop count、unroll、dtype 和 Membar 语义。
3. 比较 `model_vf_end` 与 CCE/Camodel VF total。
4. 对异常 case 检查 `start_by_cycle.json`、`idu_to_ooo.json`、`sim_history.json` 和 CCE IDU/EXU dump。
5. 记录模型 commit、配置 commit、输入文件和统计口径，避免不同版本结果混用。

CCE 编译结果可能拆分、合并或变换源码中的 loop。即使源码看起来只有一个 loop，也必须检查实际 `vloop_pc` 分段或等价 dump 证据，再判断 CCE 与模型是否具有一一对应的动态结构。
