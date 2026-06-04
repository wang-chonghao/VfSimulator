# TileSim 接入开发日志

## 1. 分支与工作区

本开发日志记录 VfSimulator 为 TileSim 接入所做的专项适配。

当前开发分支：

```text
VfSim-tilesim
```

当前 worktree：

```text
/mnt/e/VfSimulator_tilesim
```

该 worktree 从已提交的 LSU ISA 重构基线创建：

```text
8567e1f Refactor LSU ISA modeling path
```

主工作区 `/mnt/e/VfSimulator_structure` 保持在 `master`，后续 TileSim 适配
开发不在主工作区直接进行。

## 2. 接入目标

TileSim 的工程模型在 tile 粒度调度算子，包含 vector、cube、MTE、同步和
跨核 pipeline。

VfSimulator 只负责 VF 结构体进入 vector 核后的核内微架构耗时预测。

目标接口边界：

```text
TileSim VFInfo
  -> TileSim adapter 转成 VfSimulator 输入 IR
  -> VfSimulator predict_from_program()
  -> cycles
  -> TileSim TileOpLatency
```

TileSim 仍然负责：

- `VFEntity` 识别
- `VFInfo` 构建
- tile-level `COPY/MOVE` 搬运
- MTE/cube/vector segment 外层调度
- `TileOpLatency` 汇总进 `OperatorResult`

VfSimulator 负责：

- VF 内部 ISA 指令流展开
- `VLDS/VSTS` 这类 VF 内部 LSU 指令建模
- IDU/OoO/LSQ/SHQ/EXQ/EXU
- forwarding / II
- vloopv2 / hardware loop dispatch
- VF end cycle 预测

## 3. 接口形式

### 3.1 不直接依赖 TileSim `VFInfo`

TileSim 的 `VFInfo` 与 VfSimulator 需要的输入在概念上非常接近，但它携带
TileSim 类型：

- `TileOpContext`
- `TensorEntity`
- `DType`
- `MemLoc`
- `ArcConfig`
- `CoreUnit`

VfSimulator 不应该反向 import TileSim。

因此约定中间使用 VfSimulator 自己定义的轻量输入 IR，暂称
`VfSimProgram`。

### 3.2 VfSimulator 侧输入 IR

建议在 VfSimulator 中新增：

```python
@dataclass
class VfSimInst:
    op: str
    src: list[str]
    dst: list[str]
    config: dict | None = None


@dataclass
class VfSimLoop:
    count: int
    body: list["VfSimInst | VfSimLoop"]
    name: str | None = None
    unroll: int = 1


@dataclass
class VfSimProgram:
    dtype: str
    body: list[VfSimInst | VfSimLoop]
    params: dict | None = None
    config: dict | None = None
```

`VfSimProgram` 是现有 JSON trace 的内存对象版本。它不是 TileSim 类型，
只表达 VfSimulator 真正需要的信息：

- dtype
- op name
- src/dst virtual register name
- loop count / loop body
- 可选 config

### 3.3 VfSimulator 侧 API

建议新增稳定 API：

```python
predict_from_program(
    program: VfSimProgram,
    *,
    config_root: str | None = None,
    model: str = "mainline",
    dump_trace_path: str | None = None,
) -> dict
```

返回建议：

```python
{
    "cycles": 1682,
    "model": "mainline",
    "trace_path": "...",   # 可选，仅 dump 时返回
    "breakdown": {...},
}
```

后续 TileSim 侧将结果转换为：

```python
TileOpLatency(
    latency=cycles / vf_info.context.arc_config.clock_freq,
    cycles=cycles,
    unit_latency={CoreUnit.VEC: latency},
)
```

### 3.4 JSON trace 的兼容关系

现有 CLI 入口继续保留：

```text
python3 main.py --trace xxx.json
```

内部建议逐步统一为：

```text
JSON trace
  -> VfSimProgram
  -> IFU/IDU/OoO
```

TileSim 则走：

```text
VFInfo
  -> VfSimProgram
  -> IFU/IDU/OoO
```

这样现有回归测试和命令行入口不受影响。

### 3.5 模型选择

TileSim 是否调用 VfSimulator 不依赖 accelerator YAML 或 accelerator 名字。
调用边界由 DSL 中显式的 `vf_begin/vf_end` 决定：

```text
DSL 显式写 vf_begin/vf_end
  -> TileSim 构建 VFEntity
  -> eval 层识别 VFEntity
  -> build_vf_info_from_entity()
  -> predict_vf_latency(vf_info)
  -> 调用 VfSimulator
```

因此第一阶段不需要在 VfSimulator 中解析 `davidV100` 或其他 accelerator
名字来判断是否启用 VF 模型。

`davidV100` 暂作为 A5 官方 accelerator YAML。VfSimulator 不读取该 YAML 的
微架构参数；TileSim 只在最终把 cycles 换算成时间时使用其中的主频字段。

工程可达模型和理论极限模型需要保持对应关系：

```text
TileSim 工程可达模型
  -> VfSimulator mainline / level4 模型

TileSim 理论极限模型
  -> VfSimulator 理论极限模型
```

因此 `predict_from_program()` 需要预留 `model` 参数，TileSim adapter 根据
当前 TileSim costmodel 类型传入：

```text
mainline
theory_vloop_only
theory_direct_issue
```

TileSim 侧如果只接一个理论极限，默认接更激进的：

```text
theory_direct_issue
```

同时保留 `theory_vloop_only` 显式接口，方便后续需要更接近主线 queue staging
结构的理论上界时切换。

两套理论极限的含义：

```text
theory_vloop_only
  -> 对应 CLI: --theoretical-limit-vloop-only
  -> 保留 VLOOP/top-level loop timing
  -> 放宽 IDU/OOO window、preg、SHQ、EXQ、inflight 等容量瓶颈
  -> 去掉 IDU->OOO、EXQ recv、SHQ release、IDU credit visibility 等队列传输延迟
  -> 仍保留主线 queue staging，即 SHQ -> EXQ -> EXU 路径
  -> 保留 queue-level forwarding 解释

theory_direct_issue
  -> 对应 CLI: --theoretical-limit-vloop-only-legacy-forwarding-direct-issue
  -> 包含 theory_vloop_only 的放宽
  -> 额外启用 legacy forwarding 解释
  -> 额外启用 direct issue，绕过 SHQ -> EXQ staging
  -> 更接近旧 single-queue theoretical-limit candidate，属于更激进上界
```

旧的 generic `--theoretical-limit`、`--theoretical-limit-single-queue`、
`--theoretical-limit-vloop-only-legacy-forwarding` 不再作为公开模型入口。

## 4. 双边目录改动规划

### 4.1 VfSimulator 侧

预计修改目录：

```text
api/
  program_api.py                  # 新增 predict_from_program API
  simulator_costmodel.py          # 如需复用现有 run_simulation 包装，可少量调整

core/
  program_ir.py                   # 新增 VfSimProgram/VfSimInst/VfSimLoop
  ifu.py                          # 支持从 Program IR 展开，或复用 trace dict 转换
  simulator_runner.py             # 如需接收 Program IR，可增加轻量入口

tests/
  ...                             # 增加 program API 和 tilesim-style program 单测
```

第一阶段尽量不改 IDU/OoO/ISU 主逻辑。目标是让新的 program API 复用当前
`flatten -> IFU -> IDU -> OoO` 的主路径。

### 4.2 TileSim 侧

TileSim 已经完成的 VF 内部 LSU 显式表达改动：

```text
core/common/tile_op_enum.py
  - 新增 TileOpCode.VLDS / TileOpCode.VSTS

core/frontend/dsl/grammar/tile_op.py
  - 新增 T.TileOp.vlds()
  - 新增 T.TileOp.vsts()

core/backend/vf_costmodel/vf_costmodel.py
  - map_tileop_to_vf_insts() 支持 VLDS/VSTS

tests/ut/core/frontend/dsl/parse/test_vf_parse.py
tests/ut/core/backend/backend_entity/test_vf_entity_build.py
tests/ut/core/backend/vf_costmodel/test_vf_inst_mapping.py
  - 覆盖 vlds/add/vsts 到 VFInfo 的链路

docs/VFSIM_A5_INTEGRATION_DESIGN.md
  - 记录 A5 VfSim 接入设计
```

TileSim 后续预计新增：

```text
core/backend/vf_costmodel/
  vfsim_adapter.py                # VFInfo -> VfSimProgram，调用 VfSimulator
  vfsim_config.py                 # 解析 config_root/backend 选择
```

后续 `predict_vf_latency(vf_info)` 直接调用 VfSimulator，不再保留当前简化
VF predictor 作为长期路径。

## 5. 当前 TileSim 调用链

TileSim 当前在 eval 层已经决定何时调用 VF costmodel。

### 5.1 Rule-based pipeline

```text
EvalRulePipeImpl.eval()
  -> _calc_subtask_pipe(sub_task)
  -> op_list = sub_task.expand(ctx)
  -> for op in op_list:
       if isinstance(op, VFEntity):
           vf_info = build_vf_info_from_entity(op, TileOpContext(...))
           latency = predict_vf_latency(vf_info)
       else:
           tile_op = _to_tile_op(...)
           latency = tile_op_dispatch(...)
```

### 5.2 Predecessor-based pipeline

```text
EvalByPredecessorImpl.eval()
  -> build_predecessors(sub_task_list, ...)
  -> _schedule_and_evaluate(all_sops)
  -> for sop in all_sops:
       op = sop.op
       if isinstance(op, VFEntity):
           vf_info = build_vf_info_from_entity(op, TileOpContext(...))
           latency = predict_vf_latency(vf_info)
       else:
           tile_op = _to_tile_op(...)
           latency = tile_op_dispatch(...)
```

因此 VfSimulator 接入不需要修改 eval 层判断逻辑。接入点保持为：

```text
core/backend/vf_costmodel/vf_costmodel.py::predict_vf_latency(vf_info)
```

接入后，`predict_vf_latency(vf_info)` 内部逻辑应调整为：

```text
predict_vf_latency(vf_info)
  -> vfsim_adapter.convert_vf_info_to_program(vf_info)
  -> select_vfsim_model(tile_costmodel_mode)
  -> VfSimulator.predict_from_program(program, model=...)
  -> cycles
  -> TileOpLatency
```

当前 TileSim 中的简化 VF predictor 后续直接删除，不作为 fallback 长期保留。

## 6. VF 内部搬运语义

需要严格区分两类搬运：

### 6.1 核外 tile-level 搬运

```text
TileSim COPY/MOVE
```

语义：

- GM/UB/L1/L0/SHM 等 tile-level memory movement
- 由 TileSim MTE/bandwidth/pipeline 模型负责
- 不进入 VfSimulator VF 指令流

### 6.2 VF 结构体内部搬运

```text
VLDS / VSTS
```

语义：

- vector core 内部 LSU ISA
- 由 DSL 显式写在 `vf_begin/vf_end` 内
- 进入 `VFInfo`
- 进入 `VfSimProgram`
- 由 VfSimulator 的 LSU/LSQ/forwarding/II/OoO 模型负责

示例 TileSim DSL：

```python
T.TileOp.vf_begin(config={"vf_id": "seg_lsu_add"})
T.TileOp.vlds(tmp, src)
T.TileOp.add(tmp, tmp, tmp)
T.TileOp.vsts(dst, tmp)
T.TileOp.vf_end()
```

当前 TileSim 已验证该 DSL 会得到：

```text
VFInfo.loops = [
  VFInst("VLDS", src=["src"], dst=["tmp.1.1"]),
  VFInst("VADD", src=["tmp.1.1", "tmp.1.1"], dst=["tmp.2.1"]),
  VFInst("VSTS", src=["tmp.2.1"], dst=["dst.1.1"]),
]
```

### 6.3 mem_loc 与 VfSimulator operand 表达

TileSim DSL/Tensor 会标记 `REG/UB` 等 `mem_loc`。

当前 VfSimulator trace 通过 operand name 前缀表达位置：

```text
v*     -> vector register，参与寄存器重命名
mem*   -> memory/UB-like operand，不参与寄存器重命名
```

接入时有两个可选方案：

1. TileSim adapter 把 `MemLoc.REG` 映射成 `v*` 名字，把 `MemLoc.UB` 映射成
   `mem*` 名字，VfSimulator 内部继续沿用现有命名语义。
2. VfSimulator 的 `VfSimInst` operand 显式携带 `REG/UB` 标签，IFU/rename
   阶段按标签判断是否参与寄存器重命名。

第一阶段建议采用方案 1，原因是改动面小，能完全复用现有寄存器重命名逻辑。
但 `VfSimProgram` 中应保留扩展空间，后续可以升级为显式 operand metadata。

建议转换规则：

```text
TileSim MemLoc.REG tensor
  -> v:<stable_tensor_name>
  -> 参与 VfSimulator 寄存器重命名

TileSim MemLoc.UB tensor
  -> mem:<stable_tensor_name>
  -> 不参与 VfSimulator 寄存器重命名
```

具体前缀是否继续用历史的 `v`/`mem`，需要在实现时对齐当前 parser/IFU 对
operand 字符串的判断逻辑，避免引入新格式导致旧 trace 行为变化。

## 7. VfSimulator 侧开发步骤

### 7.1 总体计划表

| 步骤 | 位置 | 目标 | 主要产出 | 验收方式 |
| --- | --- | --- | --- | --- |
| 1 | VfSimulator | 定义稳定输入 IR | 新增 `core/program_ir.py`，包含 `VfSimProgram / VfSimLoop / VfSimInst` | 单测校验 op/src/dst/loop 基本结构 |
| 2 | VfSimulator | 提供 Python API | 新增 `api/program_api.py::predict_from_program()` | 用等价 `VADD` program 跑出和 JSON trace 一致的 cycle |
| 3 | VfSimulator | 统一 Program IR 和 JSON trace 展开入口 | `coerce_trace_program()` + `Flattener` 支持 `VfSimProgram`，IFU 继续吃 linear inst list | `VLDS -> VADD -> VSTS` 与等价 JSON trace 对齐 |
| 4 | VfSimulator | 接入模型选择 | `model="mainline/theory_vloop_only/theory_direct_issue"` 映射到主线和两套理论极限 | API 与 CLI 理论极限输出对齐 |
| 5 | VfSimulator | 支持 debug dump | `dump_trace_path` 写出可复现的中间 trace/program | 检查 dump 文件可读、可复现 |
| 6 | TileSim | 增加 adapter | 新增 `core/backend/vf_costmodel/vfsim_adapter.py`，负责 `VFInfo -> VfSimProgram` | 用 `vf_begin/vlds/add/vsts/vf_end` 生成正确 program |
| 7 | TileSim | 接入真实 VfSimulator | 修改 `predict_vf_latency(vf_info)`，替换当前简化 VF predictor | TileSim 侧 VF 单测通过，返回 `TileOpLatency` |
| 8 | TileSim | 处理 REG/UB 映射 | adapter 中把 `MemLoc.REG -> v*`，`MemLoc.UB -> mem*` | 单测覆盖寄存器 operand 和 memory operand |
| 9 | TileSim | 接工程/理论模型选择 | 根据 TileSim 当前 costmodel 模式传 `model` 参数 | 工程模型走 mainline，理论模型走理论入口 |
| 10 | 双边 | 回归与案例验证 | 跑小规模 VF case 和 TileSim ADD case | `VADD`、`VLDS+VADD+VSTS`、loop case 不跑偏 |

执行顺序：

```text
第一阶段：VfSimulator Program IR/API
第二阶段：TileSim VFInfo -> VfSimProgram adapter
第三阶段：真实调用、trace dump、理论极限入口和简化 predictor 删除
```

### Step 1：定义 Program IR

新增 `core/program_ir.py`：

- `VfSimInst`
- `VfSimLoop`
- `VfSimProgram`
- dtype/op/src/dst 基本校验

### Step 2：增加 program API

新增或扩展 API：

```text
api/program_api.py
```

提供：

```python
predict_from_program(
    program: VfSimProgram,
    config_root: str | None = None,
    model: str = "mainline",
    dump_trace_path: str | None = None,
)
```

第一版可以内部把 `VfSimProgram` 转为现有 trace dict，然后复用当前
`run_simulation()` 路径，降低改动风险。

### Step 3：Flattener 支持 Program IR

当前确认不直接修改 IFU。`IFUUnroll` 的输入是 Flattener 生成的 linear inst
list，不是原始 JSON trace 或 `VfSimProgram`。

因此 Step 3 的边界是：

```text
JSON trace program
  -> coerce_trace_program()
  -> ProgramAnalyzer / vreg normalization / Flattener
  -> IFUUnroll

VfSimProgram
  -> coerce_trace_program()
  -> ProgramAnalyzer / vreg normalization / Flattener
  -> IFUUnroll
```

`Flattener` 本身也应支持直接接受 `VfSimProgram` / `VfSimLoop` /
`VfSimInst`，方便单测和后续逐步去掉 trace dict 中转。

### Step 4：模型选择

已新增模型分发层：

```text
core/model_config.py
```

公开 model 名称：

| model | 对应 CLI | 含义 |
| --- | --- | --- |
| `mainline` | 默认路径 | 当前 queue_level4 + vreg live-range normalization 主线 |
| `theory_vloop_only` | `--theoretical-limit-vloop-only` | 保留 VLOOP timing，放宽容量和队列传输延迟，仍走 SHQ -> EXQ -> EXU |
| `theory_direct_issue` | `--theoretical-limit-vloop-only-legacy-forwarding-direct-issue` | 在 `theory_vloop_only` 基础上启用 legacy forwarding 和 direct issue，绕过 SHQ -> EXQ staging |

API 入口：

```python
predict_from_program(program, model="mainline")
predict_from_program(program, model="theory")                 # alias -> theory_direct_issue
predict_from_program(program, model="theory_vloop_only")
predict_from_program(program, model="theory_direct_issue")
```

旧 alias 如 `queue_level4`、`level4` 会归一到 `mainline`；CLI flag 风格
的理论极限名字也会归一到对应 API model 名称。泛化的 `theory` /
`theoretical_limit` alias 会归一到 `theory_direct_issue`。

### Step 5：增加回归测试

新增轻量单测：

```text
tests/test_program_api.py
```

覆盖：

- 单条 `VADD`
- `VLDS -> VADD -> VSTS`
- 一层 loop
- nested loop
- 与等价 JSON trace 输出 cycles 一致
- `model="theory"` 默认映射到 `theory_direct_issue`
- `theory_vloop_only` 显式接口保留
- invalid model fail-fast

### Step 6：支持 trace dump

TileSim 调用 VfSimulator 时允许 dump 中间 trace/program，便于 debug。

建议第一阶段支持：

```text
dump_trace_path=None     -> 不 dump
dump_trace_path=...      -> 写出 VfSimulator 可复现输入
```

dump 内容至少包括：

- model name
- dtype
- flattened 或结构化 program
- op/src/dst
- loop count / nested loop structure

## 8. 已确认设计决策

1. 是否调用 VfSimulator 由 DSL 显式 `vf_begin/vf_end` 决定，不依赖
   accelerator 名字或 YAML 字段。
2. A5 第一批 accelerator YAML 暂用 `davidV100`，VfSimulator 不读取其中的
   微架构参数，只由 TileSim 使用主频做 cycles 到时间的换算。
3. 连续 VEC op 不自动分段成 VF，必须由 DSL 显式写 `vf_begin/vf_end`。
4. VF 内部搬运必须显式写 `vlds/vsts`，进入 `VFInfo` 和 VfSimulator 指令流。
5. Tile-level `COPY/MOVE` 属于核外搬运，继续由 TileSim MTE/带宽模型负责。
6. TileSim 工程可达模型调用 VfSimulator mainline/level4。
7. TileSim 理论极限模型默认调用 VfSimulator `theory_direct_issue`；保留
   `theory_vloop_only` 显式接口。
8. 当前 TileSim 简化 VF predictor 后续删除，不保留为长期 fallback。
9. 支持 dump VfSimulator 中间 trace/program，便于 debug。

## 9. 当前验证状态

新 worktree 编译检查已通过：

```text
python3 -m py_compile main.py api/*.py core/*.py
```

smoke case 已在 `/mnt/e/VfSimulator_tilesim` 验证：

| case | VF end cycle |
| --- | ---: |
| `VFtest/VADD_oneloop.json` | 118 |
| `regression_suite/inputs/json/GeLU_poly.json` | 1682 |
| `regression_suite/inputs/json/online_update.json` | 430 |

Step 3 验证：

| case | 结果 |
| --- | --- |
| `Flattener(VfSimProgram)` | 输出 `VLOOPv2, VLDS, VADDS, VSTS, VLOOPv2` |
| `VfSimProgram` vs `VFtest/VADD_oneloop.json` | linear 输出逐字段一致 |
| Program API mainline | `118 cycles` |

Step 4 验证：

| case | model | VF end cycle |
| --- | --- | ---: |
| `VFtest/VADD_oneloop.json` | `mainline` | 118 |
| `VFtest/VADD_oneloop.json` | `theory_vloop_only` | 117 |
| `VFtest/VADD_oneloop.json` | `theory_direct_issue` | 118 |
| `regression_suite/inputs/json/GeLU_poly.json` | `mainline` | 1682 |
| `regression_suite/inputs/json/GeLU_poly.json` | `theory_vloop_only` | 1675 |
| `regression_suite/inputs/json/GeLU_poly.json` | `theory_direct_issue` | 1499 |

`VFtest/VADD_oneloop.json` 上 API 与 CLI 三种模型输出一致。

Step 5 验证：

```text
python3 tests/test_program_api.py
```

完整回归本轮没有继续跑。用户已确认当前只需要准备开发日志后开始开发。

## 10. 剩余实现细节

1. `VfSimProgram` 第一版建议作为正式 Python API 暴露，但保持字段最小化。
2. `config_root` 第一版通过参数传入；如 TileSim 后续需要，可再加环境变量兜底。
3. `model` 参数已对齐 VfSimulator 当前两个理论极限模型：`theory_vloop_only` 和 `theory_direct_issue`。
4. `REG/UB` 到 `v*/mem*` 的转换规则需要实现时对齐当前 parser/IFU 判断逻辑。
5. TileSim adapter 需要确认从哪里读取当前处于工程可达还是理论极限 costmodel。

## 11. Package 化决策

TileSim 和 VfSimulator 当前都有顶层 `core/`、`api/` 目录。TileSim 进程内
直接 `import core.*` / `import api.*` 会命中 TileSim 自己的包，不能安全
调用 VfSimulator。

长期方案是把 VfSimulator 作为独立 Python package 暴露：

```text
vfsimulator/
  api/
  core/
  configs/
```

TileSim 后续只 import namespaced API：

```python
from vfsimulator import VfSimProgram, VfSimInst, VfSimLoop, predict_from_program
```

当前已新增 `pyproject.toml`，目标 wheel 名称：

```text
vfsimulator-0.1.0-py3-none-any.whl
```

为降低风险，现阶段保留仓库原有 `api/`、`core/`、`main.py` 兼容路径，
同时新增 namespaced package。后续稳定后再决定是否删除旧顶层 package。

Package 验证：

```text
python3 -m pip wheel --no-deps --no-build-isolation -w /tmp/vfsim_wheel .
/tmp/vfsim_pkg_venv/bin/python -m pip install --no-index /tmp/vfsim_wheel/vfsimulator-0.1.0-py3-none-any.whl
```

安装后从 `/tmp` 目录直接 import namespaced package：

```python
from vfsimulator import VfSimProgram, VfSimLoop, VfSimInst, predict_from_program
```

最小 `VLDS -> VADDS -> VSTS` case 输出：

```text
mainline 118
```
