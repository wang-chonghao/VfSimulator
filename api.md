# VF Simulator API

本文说明 VF Simulator 当前公开输入接口。

模拟器支持两条输入路径：

1. 通过 `main.py --trace` 读取 JSON trace。
2. 通过 `main.py --cce` 读取 CCE/DSL，并从 `__VEC_SCOPE__` 中解析 VF 代码。

两条路径都会降到同一种内部 trace 格式，并运行当前主线模型：

```text
queue_level4 + vreg 活跃范围规范化 + start+4 释放
```

## 命令行

运行 JSON trace：

```bash
python main.py --trace VFtest/canonical/GeLU_poly.json --out_dir results/demo_json
```

运行 CCE/DSL 文件：

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/demo_cce
```

如果一个 CCE 文件里有多个 `__VEC_SCOPE__` block，需要选择指定 VF kernel：

```bash
python main.py --cce path/to/file.dsl --cce-kernel kernel_name --out_dir results/demo_kernel
```

旧的 `--ooo-model` 选择器已不再是公开主线命令行接口。默认模型在 `main.py` 内部选择。

## JSON Trace 格式

最小示例：

```json
{
  "dtype": "fp32",
  "params": {
    "I": 96,
    "U": 1
  },
  "program": [
    {
      "type": "loop",
      "iters": "I",
      "unroll": "U",
      "body": [
        { "type": "inst", "op": "VLDS", "dst": ["V0"], "src": ["memA"] },
        { "type": "inst", "op": "VADDS", "dst": ["V1"], "src": ["V0"] },
        { "type": "inst", "op": "VSTS", "dst": ["memB"], "src": ["V1"] }
      ]
    }
  ]
}
```

字段：

- `dtype`：指令数据类型，常见值是 `fp32` 或 `fp16`。
- `params`：`iters` 和 `unroll` 引用的符号参数。
- `program`：顶层 VF 程序列表。
- `type=loop`：loop block，包含 `iters`、`unroll`、`body`。
- `type=inst`：指令，包含 `op`、`dst`、`src`。

支持嵌套循环。分析器会从程序树推导顶层循环边界和发射结构。

## CCE/DSL 输入

CCE 适配器会解析 `__VEC_SCOPE__` 内的向量代码，生成并校验
`CanonicalVfInfo`，再由 `CoreLoweringPass` 接入模拟器。

示例：

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu_poly_cce
```

程序化用法：

```python
from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel

vf_info = InputAPI.load_cce("cce_code/GeLU_poly.dsl")
cycles = CoreVfCostModel().predict_vf_cycles(vf_info)
print(cycles)
```

相关 API 文件：

- `api/frontend/schema.py`：`CanonicalVfInfo v1` 数据模型。
- `api/vf_costmodel.py`：canonical cost model 抽象接口。
- `api/cce_adapter.py`：CCE/DSL 解析器。
- `api/frontend/core_lowering.py`：canonical 到 Core payload 的唯一 lowering。
- `api/input_api.py`：命令行输入的共享加载器。
- `api/simulator_costmodel.py`：程序化模拟器封装。

## 内存和寄存器操作数

Canonical value 通过 `storage` 显式表示操作数存储位置。当前取值包括：

- `Register`
- `UB`
- `Scalar`

当前 lowering 路径仍兼容历史 JSON trace 使用的名字，例如 `V0`、`V1`、`memA`、`mem_inter_*`。

JSON trace 推荐命名：

- 向量寄存器：`V0`、`V1`、`V2` 等。
- 输入/输出 UB 内存：`memA`、`memB`、`memOut` 等。
- 中间 UB 内存：`mem_inter_*`。该名字只作为普通 UB operand 兼容，不再触发特殊屏障语义。

## 显式内存屏障

CCE 解析可以在源代码可见时表示显式内存屏障语义。当前后端只通过显式 `Membar`
节点建模 memory ordering；`mem_inter_*` 等名字不再触发隐式跨 block strong barrier。
旧 JSON trace 若需要表达 store/load 或 load/store ordering，应显式插入 `Membar`。

## 理论上界选项

当前公开理论上界参数：

```bash
--theoretical-limit-vloop-only
--theoretical-limit-vloop-only-legacy-forwarding-direct-issue
```

旧的通用 `--theoretical-limit` 参数不是当前活跃接口。

## 输出

规范模型时序会打印为：

```text
VF end cycle (with drain) = N
```

常见输出文件：

- `start_by_cycle.json`
- `done_by_cycle.json`
- `idu_to_ooo.json`
- `vloop_trace.json`
- `sim_history.json`
