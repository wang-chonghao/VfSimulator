# VF Simulator

VF Simulator 是面向 Ascend 风格 VF（vector function）代码的周期级性能模型。它可以从结构化 JSON trace 或包含 `__VEC_SCOPE__` 的 CCE/DSL 文件预测 VF 执行时间。

项目主要分为两部分：

1. **VF 建模**：解析 VF 结构，lower 成模拟器指令，并估算周期级时序。
2. **VF 优化**：用模型作为快速 cost oracle，搜索 split、unroll、rewrite 等策略。

当前主线重点是 VF 建模。优化工具仍保留在仓库里，但默认模拟路径是下面描述的 queue-level VF 模型。

## 当前默认模型

默认模拟路径是：

- `queue_level4`
- `consumer release = producer/consumer start + 4`
- `vreg` 活跃范围规范化：开启
- `shq_depth = 58`
- `exq_depth = 26`
- `issue_ports = 2`
- `load_ports = 2`
- `store_ports = 1`
- per-EXU inflight cap 来自 `configs/uarch.json`

模型链路是：

```text
JSON/CCE 输入
  -> API 输入适配层
  -> flatten
  -> IFU
  -> IDU
  -> OOO rename + SHQ/LSQ
  -> ISU / EXQ
  -> EXU/load/store timing
  -> VF end cycle
```

默认命令不需要显式指定模型。除非使用 theoretical-limit 或实验选项，`main.py` 总是选择当前 queue-level 主线模型。

## 快速开始

运行 JSON trace：

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/demo_gelu_poly
```

运行 CCE/DSL 文件：

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/demo_gelu_poly_cce
```

如果一个 CCE 文件里有多个 `__VEC_SCOPE__` kernel，需要显式选择：

```bash
python main.py --cce path/to/file.dsl --cce-kernel kernel_name --out_dir results/demo_kernel
```

常见输出：

- `start_by_cycle.json`：指令 start 事件。
- `done_by_cycle.json`：指令 done 事件。
- `idu_to_ooo.json`：IDU 到 OOO 的接收 trace。
- `vloop_trace.json`：顶层 loop dispatch trace。
- `sim_history.json`：详细模拟历史。
- 终端输出 `VF end cycle (with drain) = ...`：主要时序结果。

## Theoretical-Limit 模式

当前暴露两个 theoretical-limit 候选模式：

```bash
python main.py --trace VFtest/GeLU_poly.json \
  --theoretical-limit-vloop-only \
  --out_dir results/theory_vloop_only
```

```bash
python main.py --trace VFtest/GeLU_poly.json \
  --theoretical-limit-vloop-only-legacy-forwarding-direct-issue \
  --out_dir results/theory_direct_issue
```

第一个保留主队列级路径，但放宽顶层循环暴露限制。第二个更激进，使用旧版转发解释和直接单队列发射作为对比。

## 实验三端口模式

仓库还包含实验性的三端口 VF 模型：

```bash
python main.py --trace VFtest/GeLU_poly.json --three-ports --out_dir results/demo_three_ports
```

该模式把 compute issue port 和 load issue capacity 扩展到 3，store issue 仍然单发射。

## API 接口

公共 API 位于 `api/`。

主要文件：

- `api/vf_info.py`：公共数据类，包括 `VFInfo`、`VFLoop`、`VFInst`、`ValueInfo`、`MemInfo`、`Membar`。
- `api/vf_costmodel.py`：兼容 re-export，并定义抽象接口 `VfCostModel`。
- `api/cce_adapter.py`：从 CCE/DSL 文件解析 `__VEC_SCOPE__` kernel。
- `api/vf_lowering.py`：把 API 层 `VFInfo` lower 到模拟器 trace 格式。
- `api/input_api.py`：JSON 和 CCE 输入的统一 loader。
- `api/simulator_costmodel.py`：程序化 cost model wrapper。

典型程序化用法：

```python
from api.cce_adapter import parse_cce_vf_info
from api.simulator_costmodel import CoreVfCostModel

vf_info = parse_cce_vf_info("cce_code/GeLU_poly.dsl")
cycles = CoreVfCostModel().predict_vf_cycles(vf_info)
print(cycles)
```

不从 CCE 开始的测试和工具也可以直接构造 `VFInfo`。

## 配置文件

核心配置位于 `configs/`：

- `uarch.json`：queue depth、issue width、delay knob、inflight cap 等微架构参数。
- `isa.json`：schema v2 指令元数据。记录方式是 `instructions.<op>.forms.<form>`，op 级别记录 `op_class`，form 级别记录 latency、startup、drain、dtype 等字段。
- `forwarding.json`：schema v2 生产者到消费者的依赖时序表，按 `OP.form` 建 key，例如 `VADDS.fp32 -> VEXP.fp32`。
- `InitiationInterval.json`：schema v2 指令对 initiation interval 表，同样按 `OP.form` 建 key。

重要 ISA 字段：

- `op_class`：`COMPUTE`、`LOAD` 或 `STORE`，用于决定走 SHQ/EXQ/EXU 还是 LSQ 资源路径。
- `forms`：每种 form 的参数，例如 `fp32`、`fp16`，或 `f32_to_f16` 这样的转换 form。
- `dispatch_exu`：compute 指令可使用的执行端口。

重要 `dispatch_exu` 标记：

- `EXU0_ONLY`：只能在 EXU0 执行。
- `EXU01`：可以在 EXU0 或 EXU1 执行。
- `EXU012`：实验三端口模式使用。

## 目录结构

正常开发和 release 分支主要关注这些目录：

```text
api/                 公共输入/API 适配层
ascend_runner/       CCE/camodel 构建、运行、校准辅助工具
cce_code/            CCE/DSL 示例以及部分回归/优化源
configs/             uarch、ISA、forwarding、II 配置
core/                主模拟器实现
docs/                架构说明和建模文档
notes/               优化/建模流程使用的整理笔记
optimizer/           VF 优化和 split/unroll 搜索工具
regression_suite/    回归包：case、输入、报告、文档
skills/              VF 优化工作流相关 Codex skill 文档/脚本
tools/               报告、绘图、校准、实验工具脚本
VFtest/              JSON trace 示例和部分回归输入
```

顶层文档：

- `VF_modeling.md`：详细 VF 建模设计。
- `README.md`：当前项目入口。
- `api.md`：API 设计说明。

生成输出和临时材料通常不要放进 release commit：

- `results/`
- `__pycache__/`
- 大型 `msprof`/camodel dump 目录
- 临时图片、日志和 scratch 文件，除非已经整理成稳定资料

## 回归测试

回归包结构：

```text
regression_suite/
  cases/
    cost_model_regression_cases.json
    baseline_balanced_exu0_reserve.json
    baseline_queue_level4_ooo_transfer_delay.json
    baseline_consumer_done.json
    archive/
  inputs/
    json/
    cce/
  reports/
    precision_compare_3modes.md
  docs/
    unroll_precision_debug_guide.md
```

运行 smoke 回归：

```bash
python tools/run_cost_model_regression.py --tier smoke
```

运行 full 回归：

```bash
python tools/run_cost_model_regression.py --tier full
```

有意刷新 baseline：

```bash
python tools/run_cost_model_regression.py --tier full --update-baseline
```

默认输出写到 `results/regression_suite/latest/`。稳定、整理过的报告应放在 `regression_suite/reports/`。
默认 baseline 为 `baseline_balanced_exu0_reserve.json`，对应平衡 EXU0 预留策略：
`lookahead=8`、`min_count=1`、每端口执行中上限 `cap=7`。旧 baseline 仅用于历史对比。

## Ascend Runner

`ascend_runner/` 是 CCE/camodel 伴随工具链，用于：

- 通过 `ccec` 和 `ld.lld` 编译 CCE/DSL case；
- 使用 `runtime_camodel` 运行 native 模拟器可执行文件；
- 收集 camodel 日志，例如指令 start/done 和 EXU trace；
- 校准 `isa.json`、`forwarding.json`、`InitiationInterval.json`。

当前主线脚本位于 `ascend_runner/current/`。历史 debug 和 legacy 脚本保留在 `ascend_runner/debug/` 和 `ascend_runner/legacy/`。

## 开发备注

修改模拟器后建议运行：

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/sanity_gelu
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/sanity_gelu_cce
python tools/run_cost_model_regression.py --tier smoke
```

准备干净 release 分支时，优先提交源码、整理过的文档、选定的回归输入和必要工具。不要提交生成缓存、临时运行日志和大型原始 dump。
