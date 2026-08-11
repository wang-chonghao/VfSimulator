# CCE VF Cost Model API

本文说明从 CCE 代码使用 VFSim 作为 VF cost model 时的当前 API 边界。

API 支持两条等价输入路径：

1. 把一个 `__VEC_SCOPE__` CCE kernel 解析成 `VFInfo` 对象。
2. 在 Python 中直接构造 `VFInfo` 对象。

两条路径最终调用同一个 cost model 入口：

```python
cycles = model.predict_vf_cycles(vf_info)
```

`core/` 下的后端模拟器不直接解析 CCE 源码。API 层负责面向源代码的输入，并把它 lower 到统一的 `VFInfo` 表示。

## 架构

```text
CCE __VEC_SCOPE__ kernel
        |
        v
CCE parser / adapter
        |
        v
VFInfo
        |
        v
VfCostModel.predict_vf_cycles(VFInfo)
        |
        v
VFInfo -> simulator program adapter
        |
        v
core/ simulator backend
        |
        v
predicted cycles
```

直接 Python 用法会绕过 CCE parser：

```text
Python 代码构造 VFInfo
        |
        v
VfCostModel.predict_vf_cycles(VFInfo)
        |
        v
core/ simulator backend
        |
        v
predicted cycles
```

## 公共数据模型

公共 API 数据模型定义在 `api/vf_info.py`。`api/vf_costmodel.py` 为兼容性 re-export 这些类型，并定义抽象接口 `VfCostModel`。

### `VFInfo`

顶层 VF program 容器。

字段：

- `context`：按程序顺序排列的 `VFLoop`、`VFInst`、`Membar` 节点列表。
- `values`：可选的 value ID 到 `ValueInfo` 的映射。
- `params`：符号 loop/count 参数。
- `default_dtype`：当无法从 operand 推断 instruction form 时使用的默认 dtype。
- `uarch`：可选的 per-input uarch override。

含义：

- 表示从 vector scope 提取出来的一个 VF region/kernel body。
- 节点顺序就是 program order。

### `VFLoop`

结构化 loop 节点。

字段：

- `count`：loop trip count。
- `unroll`：loop unroll factor。
- `body`：按顺序排列的嵌套 `VFLoop`、`VFInst`、`Membar` 节点列表。

含义：

- 保留 loop 结构，不要求 API 用户手动 flatten program。
- 后端 adapter 会把它 lower 到模拟器已有的 loop 表示。

### `VFInst`

向量指令节点。

字段：

- `name`：指令名，例如 `VADD`、`VMUL`、`VEXP`、`VLDS`、`VSTS`。
- `src`：source operand。
- `dst`：destination operand。
- `form`：可选 instruction form，例如 `fp32`、`fp16`、`f32_to_f16`。

含义：

- 按 program order 描述一条 VF 指令。
- API 层会把 `name` 映射到模拟器内部 instruction `op`。

### `ValueInfo` / `MemInfo`

operand 描述。

字段：

- `value_id`：operand 标识符。
- `storage`：`"Register"`、`"UB"` 或 `"Scalar"`。
- `dtype`：可选 value dtype，例如 `fp32`、`fp16`、`int32`、`uint32`。
- `shape`：可选逻辑 shape。

含义：

- `"Register"` 表示 vector register value。
- `"UB"` 表示 UB/memory-side value。
- `"Scalar"` 表示 scalar value 或符号 operand。
- `value_id` 是公共 API symbol，不要求以 `v` 或 `mem` 开头。API adapter 会把它映射到当前模拟器内部 operand 命名。
- `MemInfo` 当前是 `ValueInfo` 的兼容别名；构造函数仍接受 `name=` 和 `location=` alias。

当前 storage type：

```python
Literal["Register", "UB", "Scalar"]
```

### `Membar`

memory/order barrier 节点。

字段：

- `type`：barrier 类型。初始默认值是 `"VST_VLD"`。

含义：

- 表示无法用普通 VF 指令表达的 ordering edge。
- 当前后端已显式支持 `VST_VLD` 和 `VLD_VST`。
- `VST_VLD`：barrier 之前的 vector store 全部完成后，barrier 之后的 vector load 才允许发射。
- `VLD_VST`：barrier 之前的 vector load 全部完成后，barrier 之后的 vector store 才允许发射。
- 其它 `SMEM_BAR` 类型暂不建模，会记录 `unsupported_membar_type` warning。

## CCE 输入约定

CCE adapter 会从 `__VEC_SCOPE__` kernel 中提取一个 VF region，并生成 `VFInfo`。

当前约定：

- 只建模 vector-scope 代码。
- scalar host code 和非 VF 控制逻辑会被忽略，除非需要用它推断 loop bound。
- loop 结构保留为 `VFLoop`。
- VF operation 转换成 `VFInst`。
- 显式或推断出的 ordering constraint 转换成 `Membar`。

实现可以保守；遇到不支持的 CCE pattern 时，清晰报错比静默生成错误 `VFInfo` 更好。

## 映射规则

### Loop

CCE loop：

```cpp
for (int i = 0; i < I; ++i) {
    ...
}
```

映射为：

```python
VFLoop(count=I, unroll=1, body=[...])
```

如果 pragma 或已知 CCE annotation 提供 unroll 信息，则映射到 `VFLoop.unroll`。

### VF 指令

CCE vector operation：

```cpp
VADD(dst, src0, src1);
```

映射为：

```python
VFInst(
    name="VADD",
    src=[...],
    dst=[...],
)
```

### Register 和 UB value

vector register operand。公共名称可以任意：

```python
MemInfo(name="acc.tmp", location="Register")
```

UB 或 memory-side operand。公共名称也可以任意：

```python
MemInfo(name="input_tensor", location="UB")
```

当前 adapter 会把这些名字 lower 成内部名称：

```text
Register symbols -> V0, V1, ...
UB symbols       -> mem0, mem1, ...
```

这样公共 API 不需要暴露后端命名限制，同时保持和当前 core model 兼容。

### Memory barrier

store-before-later-load 这样的 ordering constraint 会映射为：

```python
Membar(type="VST_VLD")
```

load-before-later-store 这样的 ordering constraint 会映射为：

```python
Membar(type="VLD_VST")
```

## Cost Model 入口

公共 cost-model 接口是：

```python
class VfCostModel(ABC):
    @abstractmethod
    def predict_vf_cycles(self, vf_info: VFInfo) -> int:
        pass
```

实现行为：

1. 校验 `VFInfo`。
2. 把 `VFInfo` lower 成 `core/` 当前使用的 simulator program 格式。
3. 运行主线 simulator backend。
4. 返回整数形式的预测 VF cycles。

调用方不需要了解这些内部细节：

- `core.flatten.Flattener`
- `core.ifu.IFUUnroll`
- `core.idu.IDU`
- `core.ooo_mainline.OoOCoreMainline`
- JSON trace 内部格式

这些细节都留在 API 边界之后。

## Legacy JSON Fallback

已有 JSON trace 路径仍可用于兼容和调试。

当前 adapter：

```python
InputAPI.load_json_trace(path)
```

当前形态：

- JSON trace 输入和 CCE 输入都会生成 `VFInfo`。
- frontend adapter 返回后，core simulator payload 由 `VFInfoLowerer` 生成。

## 示例：直接 Python API

```python
from api.vf_costmodel import ValueInfo, VFInfo, VFInst, VFLoop
from api.simulator_costmodel import CoreVfCostModel

lhs_source = ValueInfo("lhs_input", "UB", "fp32", [16, 64])
rhs_source = ValueInfo("rhs_input", "UB", "fp32", [16, 64])
lhs = ValueInfo("lhs", "Register", "fp32", [64])
rhs = ValueInfo("rhs", "Register", "fp32", [64])
total = ValueInfo("total", "Register", "fp32", [64])
output = ValueInfo("output", "UB", "fp32", [16, 64])

vf_info = VFInfo(
    context=[
        VFLoop(
            count=16,
            body=[
                VFInst("VLDS", [lhs_source], [lhs]),
                VFInst("VLDS", [rhs_source], [rhs]),
                VFInst("VADD", [lhs, rhs], [total]),
                VFInst("VSTS", [total], [output]),
            ],
        )
    ]
)

cycles = CoreVfCostModel().predict_vf_cycles(vf_info)
```

## 示例：CCE API

```python
from api.cce_adapter import list_cce_vf_kernels, parse_cce_vf_info
from api.simulator_costmodel import CoreVfCostModel

print(list_cce_vf_kernels("cce_code/GeLU_poly.dsl"))

vf_info = parse_cce_vf_info("cce_code/GeLU_poly.dsl")
model = CoreVfCostModel()
cycles = model.predict_vf_cycles(vf_info)
```

如果一个文件包含多个 `__VEC_SCOPE__` kernel，需要显式选择：

```python
vf_info = parse_cce_vf_info(
    "cce_code/multi_kernel.dsl",
    kernel_name="gelu_simd_ub",
)
```

当前 adapter 实现支持当前示例中使用的常见 DSL 子集：

- `__VEC_SCOPE__ { ... }`
- `#pragma unroll(N)`
- 带大括号的 `for` loop，loop bound 可以是常量或可推断值
- `vector_f32 vec_0;` 这类向量寄存器声明
- `vlds` / `vsts` load-store operation
- 常规 VF call，例如 `vadd`、`vadds`、`vmul`、`vmuls`、`vexp`、`vdiv`

不支持的 CCE construct 应明确失败，不能静默生成错误 `VFInfo`。

## 已实现入口

- `list_cce_vf_kernels(path)`：列出所有包含 `__VEC_SCOPE__` block 的函数。
- `parse_cce_vf_info(path, kernel_name=None, loop_params=None)`：解析一个 CCE kernel 为 `VFInfo`。
- `InputAPI.load_cce_file(path, kernel_name=None, loop_params=None)`：`main.py` 使用的仓库输入边界。
- `CoreVfCostModel().predict_vf_cycles(vf_info)`：运行解析出的 `VFInfo`。
- `predict_cce_file_cycles(path, kernel_name=None, loop_params=None, out_dir="results/api_costmodel")`：针对单个 CCE 文件的便利 wrapper。

## 当前兼容说明

- 公共 API 支持显式 `Membar` 节点。
- `VFInfoLowerer` 会把 `Membar` 保留为 lowered program 中的 `"membar"` 节点。
- 当前后端通过控制单元建模显式 `VST_VLD` / `VLD_VST`。
- `membar` 不进入 IDU window，不占 SHQ / LSQ / EXQ / EXU。
- 旧的 `mem_bar_mode=strong` 仍作为 legacy memory-order 行为保留。

## 开放问题

- `VFInst.name` 是否应改名为 `op`，以匹配模拟器内部命名？
- `MemInfo` 是否应改名为 `OperandInfo`，因为它既可以表示 register，也可以表示 UB operand？
- 除 `VST_VLD` / `VLD_VST` 外，哪些 CCE 内存/屏障结构应先映射到 `Membar`？
- 不支持的 CCE construct 应该报错还是警告？
