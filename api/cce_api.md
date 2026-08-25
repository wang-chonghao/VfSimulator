# CCE VF 预测 API

CCE 前端与 Python/C++ Core 使用同一个正式合同：`CanonicalVfInfo v1`。

## 调用方式

命令行：

```bash
python3 main.py --cce kernel.cce --out_dir results/kernel
python3 main.py --cce kernels.cce --cce-kernel selected --out_dir results/selected
```

Python：

```python
from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel

vf_info = InputAPI.load_cce("kernel.cce", kernel_name="vector_kernel")
cycles = CoreVfCostModel().predict_vf_cycles(vf_info)
```

列举一个文件中的 VF kernel：

```python
from api.cce_adapter import list_cce_vf_kernels

print(list_cce_vf_kernels("kernels.cce"))
```

## 数据流

```text
CCE __VEC_SCOPE__
  -> Catalog binder
  -> private AdapterProgram
  -> ValueVersioningPass
  -> CanonicalVfInfo validation
  -> CoreLoweringPass
  -> IFU / IDU / SHQ / LSQ / EXQ / EXU
```

`AdapterProgram` 只是 CCE parser 内部结构，不是第二个外部输入合同。它保存源语言
中的逻辑寄存器、alias、loop、Membar 和 memory access，随后一次性生成 canonical
definition、producer、loop-carried 和 source location。

## 解析范围

- 只解析 `__VEC_SCOPE__` 内的 vector 指令、loop、Membar 和明确登记的无时序语句。
- 已知 opcode 由 `configs/instruction_catalog.json` 绑定 operands、form、class 和
  call variants。
- 未知 vector opcode 可携带明确 compute 语义进入 ParamDB fallback。
- vector、predicate、scalar 和 UB pointer 使用词法作用域。
- `b = a` 是零周期 value binding，不生成模拟指令。
- UB pointer alias 保留稳定 base object；普通 offset 与 `POST_UPDATE` 分开处理。
- affine offset 支持常量、loop variable、`+`、`-` 和常量乘变量。
- 无法识别的 VF scope 语句明确报错，不静默丢弃。

## Membar

CCE 显式 `mem_bar(VST_VLD)` 和 `mem_bar(VLD_VST)` 转换为 canonical Membar：

- Membar 不进入 IDU、SHQ、LSQ、EXQ 或 EXU。
- `VST_VLD` 阻塞 barrier 后的 load，直到 barrier 前 store 完成。
- `VLD_VST` 阻塞 barrier 后的 store，直到 barrier 前 load 完成。
- compute 不被 Membar 直接阻塞，只受普通寄存器依赖和资源约束。
- 未支持的 Membar 类型记录 `unsupported_membar_type` warning。

Core 不自动根据 UB 地址建立 load/store 依赖。当前 UB 顺序必须由显式 Membar
表达；更细粒度 memory dependency 以后应通过 canonical `DependencyRef` lowering
实现。

## JSON 与迁移

`main.py --trace` 只接受 canonical JSON：

```bash
python3 main.py --trace canonical.json --out_dir results/canonical
```

旧 JSON 必须先离线转换：

```bash
python3 tools/convert_legacy_vfinfo.py old.json canonical.json
```

预测 API 不提供旧 `VFInfo` 或旧 JSON 方法，也不会根据 payload 形状自动回退。
