# LSU / ISA 重构计划

## 目的

本文记录当前模拟器对 load/store 向量指令的实现方式，以及让 LSU 指令进一步 table-driven 所剩的重构工作。

硬件语义上的区分必须保留：

- load/store 风格向量指令使用 LSU 资源。
- compute 向量指令使用 EXU 资源。

当前主线已经减少了实现层面的硬编码区分：

- `VLDS` 和 `VSTS` 已在 `configs/isa.json` 中用 `op_class = "LOAD"` / `"STORE"` 表示。
- IDU、模拟器运行器的预约估算、OoO load/store 放置、内存依赖检查都使用 `core/isa_traits.py` 中的统一辅助函数。
- 生产者到消费者的就绪时间使用 `configs/forwarding.json`，并按 schema v2 的 `OP.form` key 查询。

剩余工作：

- 在时序数据准备好后，为其他 LSU op spelling 增加校准后的 ISA 配置，例如 `VLD`、`VST`、`VSTUS`、`VSTAS`。
- 决定 store 执行时长是否继续使用 producer 的 `data_store_cost`，还是迁移到 store op 自身 latency 或单独的 duration table。

目标是在不改变硬件语义的前提下，让 LSU op 支持更容易扩展。

## 当前主线流程

当前主线流程：

```text
JSON / CCE
  -> API adapter
  -> vreg 活跃范围规范化
  -> flatten
  -> IFU 动态指令生成
  -> IDU 发射和信用门控
  -> OoO rename / SHQ / LSQ
  -> 计算指令的 ISU / EXQ / EXU 路径
  -> LSU 直接 load/store 发射
  -> VF end cycle
```

相关文件：

- `main.py`：CLI 入口和模型选择。
- `api/cce_adapter.py`：解析 CCE `__VEC_SCOPE__`。
- `api/frontend/core_lowering.py`：把公共 canonical VF API lower 成 simulator program payload。
- `core/flatten.py`：静态 program 到线性 IR。
- `core/ifu.py`：动态 loop 和 unroll 展开。
- `core/idu.py`：IDU window、VLOOP gate、credit gate。
- `core/ooo.py`：基础 OoO 工具和 dependency timing helper。
- `core/ooo_mainline.py`：主线 rename、preg lifecycle、SHQ/LSQ/ROB、load/store 路径。
- `core/isu.py`：compute 的 SHQ -> EXQ -> EXU 路径。
- `core/param_db.py`：ISA、uarch、forwarding、II 的配置数据库。

## 当前配置含义

### `configs/isa.json`

当前指令字段包括：

- `schema_version: 2`
- `instructions.<op>.op_class`：`COMPUTE`、`LOAD` 或 `STORE`
- `instructions.<op>.forms.<form>`：每种 form 的参数，例如 `fp32`、`fp16`，或 `f32_to_f16` 这类转换 form
- `latency`：指令 start 到 done 的 latency
- `pipeline_startup_cost`：作为 form metadata 保留；当前 ready 主要由 `forwarding.json` 驱动
- `pipeline_drain_cost`：作为 form metadata 保留；当前 ready 主要由 `forwarding.json` 驱动
- `data_store_cost`：字段保留为历史/校准参考，主线 store 路径不直接使用
- `data_load_cost`：字段保留为历史/校准参考，主线 load 路径不直接使用
- `EXU`：计算功能单元类别，通常是 `ALU` 或 `SFU`
- `dispatch_exu`：合法 EXU port，例如 `EXU0_ONLY`、`EXU01`、`EXU012`
- `throughput`：字段保留，但主线 issue spacing 主要由 `InitiationInterval.json` 控制

全局默认值：

- `vf_startup_cost`
- `vf_drain_cost`

### `configs/forwarding.json`

当前含义：

```text
consumer_ready_cycle = producer_start_cycle + forwarding[producer_OP.form][consumer_OP.form]
```

表按 `OP.form` 建 key，例如 `VADDS.fp32`、`VCVT_F32_TO_F16.f32_to_f16`、`VLDS.fp32`、`VSTS.fp16`。
如果 pair 缺失，fallback 是：

```text
max(0, producer.latency - forwarding.defaults)
```

queue-level 模式下，compute wakeup 使用：

```text
producer_start + max(0, forwarding - 1)
```

### `configs/InitiationInterval.json`

当前含义：

```text
cycle(cur_op) >= last_issue_cycle(prev_op_on_same_port) + II(prev_op, cur_op)
```

这是同端口 structural issue-spacing 约束，不是数据依赖规则。

### `configs/uarch.json`

相关字段：

- `issue_ports`：compute EXU issue port 数量。
- `load_ports`：每 cycle load issue capacity。
- `store_ports`：每拍 store 发射容量。
- `IDU_window_width`：IDU window 容量。
- `IDU_issue_width`：IDU dispatch 宽度。
- `LDQ_width`：当前 load/store 指令使用的 LSQ 容量。
- `vreg_num`：物理向量寄存器数量。
- `shq_depth`：compute 和 store-like 路径共享的 SHQ credit depth。
- `exq_depth`：每个 port 的 EXQ wait queue depth。
- `idu_to_ooo_delay`：IDU 到 OoO transport delay。
- `vloop_to_dispatch_delay`：VLOOP start 到 loop-body dispatch 可见的 delay。
- `exq_recv_delay`：SHQ 到 EXQ receive delay。
- `shq_to_exq_port_per_cycle`：每个 port 的 SHQ 到 EXQ enqueue bandwidth。
- `exq_issue_inflight_cap_per_port`：每个 port 的 compute inflight cap。
- `enable_shq_credit_model`：启用共享 SHQ 信用计数。
- `enable_credit_visibility_delay`：启用 preg/SHQ credit 回传到 IDU 的延迟可见。
- `mem_bar_mode`：memory ordering mode，目前包含 `strong`。

## 当前 LSU 分类

当前实现通过 `core/isa_traits.py` 做资源分类。首选来源是 `configs/isa.json`：

```text
op_class = LOAD    -> LSQ load path
op_class = STORE   -> LSQ store path
op_class = COMPUTE -> SHQ / EXQ / EXU 计算路径
```

兼容行为：

- `VLDS` 在 metadata 缺失时 fallback 为 load-like。
- `VSTS` 在 metadata 缺失时 fallback 为 store-like。
- `VLD` / `VST` 由公开 CCE alias 规范化为 `VLDS` / `VSTS`；`VSTUS` / `VSTAS` 保留真实 op name 和 store semantic forms。后两者 timing 未校准时使用统一默认参数并记录 warning。
- unknown op 为兼容旧路径会 fallback 为 compute-like。

历史 JSON 输入曾用 `VLD` 和 `VST` 表示实际应为 `VLDS` 和 `VSTS` 的 case。当前公开示例应使用真实 ISA 名称。

### CCE Adapter

当前 `api/cce_adapter.py` 会保留大写后的 callee name：

```text
vlds -> VLDS
vsts -> VSTS
vcvt -> VCVT_*，前提是 form 可推断
```

### IDU

`core/idu.py` 使用统一资源分类进行 credit gate：

- load-like：只消耗 LSQ。
- store-like：消耗 LSQ 和 shared SHQ credit。
- compute-like：消耗 compute SHQ queue 和 shared SHQ credit。

### Simulator Runner

`core/simulator_runner.py` 在估算 IDU 到 OOO 的在途预约时使用同一组辅助函数。

### OoO Dependency Timing

`core/ooo.py` 通过 `ParamDB.get_forwarding_cycles(...)` 计算生产者到消费者的就绪时间，并支持按 form 查询：

```text
producer_OP.form -> consumer_OP.form
```

ISU 队列模型开启时，队列级计算唤醒使用 `forwarding - 1` 对齐。理论上界的旧版转发变体使用直接 forwarding 值。

### OoO Load/Store Execution

`core/ooo_mainline.py` 当前行为：

- load-like 和 store-like op 进入 `LSQ`。
- compute-like op 进入 `SHQ`。
- load-like op 会跟踪 memory dependency。
- store-like op 会跟踪 outstanding store。
- load-like op 通过 `load_ports` 发射；当前活跃路径使用该 load op 自身 ISA `latency`。
- store-like op 通过 `store_ports` 发射；当前活跃路径使用该 store op 自身 ISA `latency`。
- producer kind 记录为 `"LOAD"` 或 `"COMPUTE"`。

## 目标架构

### ISA 级 op 分类

当前覆盖到的 op 已经具备 ISA metadata：

```json
{
  "op_class": "LOAD"
}
```

或：

```json
{
  "op_class": "STORE"
}
```

compute 指令保留 EXU metadata：

```json
{
  "op_class": "COMPUTE",
  "EXU": "ALU",
  "dispatch_exu": "EXU01"
}
```

推荐语义：

- `op_class = "COMPUTE"`：compute 指令，进入 SHQ / EXQ / EXU 路径。
- `op_class = "LOAD"`：load 指令，进入 LSQ load 路径。
- `op_class = "STORE"`：store 指令，进入 LSQ store 路径。

配置迁移期间的兼容规则：

- `VLDS` metadata 缺失时默认为 `LOAD`。
- `VSTS` metadata 缺失时默认为 `STORE`。
- `VLD`、`VST`、`VSTUS`、`VSTAS` 是真实 ISA op name；在校准/配置条目加入前，其 timing data 有意不覆盖。
- unknown op 不应在 ISA 查找失败后静默变成 compute；当前 fallback 是兼容旧路径，后续可以收紧。

### 统一资源分类

统一 helper 位于 `core/isa_traits.py`：

```python
get_op_class(op, dtype) -> "LOAD" | "STORE" | "COMPUTE"
is_compute_op(op, dtype) -> bool
is_load_op(op, dtype) -> bool
is_store_op(op, dtype) -> bool
uses_lsq(op, dtype) -> bool
uses_shq_queue(op, dtype) -> bool
uses_shared_shq_credit(op, dtype) -> bool
```

当前活跃路径中，IDU、simulator runner、OoO 都调用同一组 helper。

### 统一 Producer-Consumer Timing

load-to-compute 和 compute-to-store timing 现在已经通过 `forwarding.json` 的 schema v2 `OP.form` key 查询。剩余工作主要是扩大校准覆盖，并决定是否把 `forwarding` 重命名为更宽泛的 dependency-delay 名称。

目标 ready 规则：

```text
consumer_ready = max(
  vf_startup_cost,
  producer.start + dependency_delay[producer_op][consumer_op]
)
```

示例：

```text
VLDS -> VADD
VLDS -> VEXP
VADD -> VSTS
VEXP -> VSTS
VADD -> VEXP
```

这样 `pipeline_startup_cost` 和 `pipeline_drain_cost` 可以逐步变成迁移输入，而不是活跃的特殊语义。

命名选择仍需确认：

- 继续使用 `forwarding.json`，并扩展其含义。
- 或新增 `dependency_delay.json`，同时把 `forwarding.json` 作为兼容来源。

第二种语义更清晰，但会触及更多调用点。

### LSU 执行时序

load/store 执行可以继续分开，因为资源不同：

```text
类 LOAD 的 LSU：
  LSQ + load_ports + load op 自身 ISA latency

类 STORE 的 LSU：
  LSQ + store_ports + store op 自身 ISA latency
```

当前 Python 和 C++ native 主线已经统一为第一种口径：所有 load/store-like 指令
使用指令自身 `latency`。`data_load_cost`、`data_store_cost` 不再作为主线执行时长
来源；旧 `load_done_latency` 配置已经端到端删除。

### 内存依赖和屏障

内存依赖逻辑也应继续使用 LSU 分类：

- 内存源操作数的类 load 指令可以依赖同一内存 key 的前序 store。
- 内存目的操作数的类 store 指令会更新最后 store 映射。
- strong 模式下的中间内存 block 释放应作用于类 load 指令，而不是只作用于 `VLD`。

## 当前配置 Schema

示例：

```json
"instructions": {
  "VLDS": {
    "op_class": "LOAD",
    "forms": {
      "fp32": {
        "op_class": "LOAD",
        "latency": 9,
        "src_dtypes": ["ub"],
        "dst_dtypes": ["fp32"],
        "dtype": "fp32"
      },
      "fp16": {
        "op_class": "LOAD",
        "latency": 9,
        "src_dtypes": ["ub"],
        "dst_dtypes": ["fp16"],
        "dtype": "fp16"
      }
    }
  }
}
```

```json
"instructions": {
  "VSTS": {
    "op_class": "STORE",
    "forms": {
      "fp32": {
        "op_class": "STORE",
        "latency": 9,
        "src_dtypes": ["fp32"],
        "dst_dtypes": ["ub"],
        "dtype": "fp32"
      },
      "fp16": {
        "op_class": "STORE",
        "latency": 9,
        "src_dtypes": ["fp16"],
        "dst_dtypes": ["ub"],
        "dtype": "fp16"
      }
    }
  }
}
```

`forwarding.json` 和 `InitiationInterval.json` 的 pair table 使用 `OP.form` key，例如 `VADDS.fp32`、`VLDS.fp32`、`VSTS.fp16`。

具体 latency 和 dependency 值应来自校准。

## 重构状态

### 阶段 0：基线采集

状态：历史阶段，已完成。代码修改前记录代表性 case 输出：

```bash
python3 main.py --trace VFtest/VADD_oneloop.json --out_dir results/baseline_vadd_oneloop
python3 main.py --trace VFtest/GeLU_poly.json --out_dir results/baseline_gelu_poly
python3 tools/run_cost_model_regression.py --tier smoke
```

这些输出作为兼容性重构期间的行为锚点。

### 阶段 1：集中 ISA 分类

状态：活跃路径已完成。统一 op-class 辅助函数位于 `core/isa_traits.py`。

当前兼容规则：

- `VLDS` 是 load-like。
- `VSTS` 是 store-like。
- ISA `op_class = LOAD` 是 load-like。
- ISA `op_class = STORE` 是 store-like。
- ISA `op_class = COMPUTE` 是 compute-like。
- `VSTUS`、`VSTAS` 在加入校准配置前按 store fallback 执行并记录 timing warning。

### 阶段 2：旧版 LSU 指令的 ISA 条目

状态：`VLDS` 和 `VSTS` 已完成。

其他真实 LSU ISA 指令，例如 `VLD`、`VST`、`VSTUS`、`VSTAS`，应在时序数据准备好后再加入。

兼容映射：

- `VLDS.latency` 应匹配 load 完成延迟。
- `VSTS.latency` 应匹配 store 完成延迟。
- 修改 producer `data_store_cost` 不应改变 store done cycle。

### 阶段 3：统一依赖时序

状态：部分完成。生产者到消费者的就绪查询已经通过 `forwarding.json` 的 v2 `OP.form` key。剩余工作是扩大校准覆盖，并决定表名是否从 `forwarding` 改成依赖延迟术语。

迁移旧行为：

- 旧 `VLD -> compute`：
  - 从类 load 生产者到各计算消费者写入表项，值来自旧消费者 `pipeline_startup_cost`。
- 旧 compute -> `VST`：
  - 从各计算生产者到类 store 消费者写入表项，值来自旧生产者 `pipeline_drain_cost`。
- 旧 compute -> compute：
  - 保留现有 forwarding 条目。

队列级 `-1` 对齐需要保留或显式重新定义。如果保留，应一致应用到队列级计算消费者的依赖表唤醒。

### 阶段 4：保留真实 CCE LSU 指令

状态：当前支持子集已完成。`api/cce_adapter.py` 保留：

- `vlds` -> `VLDS`
- `vsts` -> `VSTS`
- 其他向量指令名称保留其大写规范指令名

后续如需支持更多 LSU op，再补充 ISA 和 dependency 条目。

### 阶段 5：LSU 路径清理

有用时可以继续清理内部概念命名：

- `LSQ` 可以继续作为 queue 名称。
- 注释应使用类 load / 类 store 的 LSU 指令，而不是泛称 `VLD/VST`。
- 活跃路径中的 producer kind 已经使用 `"LOAD"` 或 `"COMPUTE"`。
- store 跟踪应基于 `is_store_op`。
- load 内存依赖应基于 `is_load_op`。

该阶段结束时，除旧版兼容代码和测试之外，`grep` 不应在模型关键路径中看到直接检查 `op == "VLD"` 或 `op == "VST"`。

## 历史清理计划

LSU op-class 重构后，代码库仍带有一些历史兼容路径。应在保持当前 `queue_level4` 主线结果的前提下逐步移除。

### 1. 旧版释放规则路径

当前主线 source release 行为是：

```text
consumer.start_cycle + consumer_release_start_offset
```

旧的基于完成时刻的替代路径已不再是独立支持模型。可清理：

- 已移除的 `consumer_release_from_start = false` 行为。
- 已移除的 `consumer_done_release_delay`。
- 已移除的 `release_done_delay`。
- `PregLifecycleController.on_uop_done` 中旧的基于完成时刻的源寄存器释放逻辑。

保留 `consumer_release_start_offset` 作为唯一主线 uarch 参数。

### 2. 旧命名残留

部分类名/文件名曾携带旧 `consumer_done` 语义，但主线行为已经是基于开始时刻的释放和 queue-level4 时序。逻辑清理后，命名应收敛到主线名称，例如：

- `OoOCoreMainline`
- `OoOCoreQueueLevel4`

当前活跃 class 已重命名为 `OoOCoreMainline`，活跃文件是 `core/ooo_mainline.py`。

### 3. 旧 LSU 名称残留

如果 `VLD` / `VST` 只是泛化 load/store 标签，应从活跃模型代码中移除：

- load 执行时长已统一到 ISA load latency；旧的独立 load duration 配置和 fallback 表述已经移除。
- 注释从 `VLD/VST` 改成 load/store LSU 指令。
- 只有在指真实 ISA op 时，才保留 `VLD`、`VST`、`VLDS`、`VSTS`、`VSTUS`、`VSTAS` 这些名称。

### 4. 历史模型标签残留

当前模拟器只有一个具体主线 backend：`queue_level4`。`consumer-done`、`queue_level1`、`queue_level2`、`queue_level3` 等历史标签，如果不再选择独立行为，应从活跃代码路径中移除。

历史报告和文档可以作为归档上下文保留，但活跃 CLI/API 代码不应暗示这些仍是独立维护模式。

## 重要开放问题

1. 依赖表是否继续叫 `forwarding.json`，还是新增更清晰的 `dependency_delay.json`？
2. 类 store 指令的执行时长应使用 store 指令自身 `latency`，还是需要生产者到 store 的执行时长表？
3. `VSTS`、`VSTAS`、`VSTUS` 是否共享一个 store port pool，还是需要单独 LSU 子资源模型？
4. load-like 指令是否应包含 `VLDS` 之外的更多指令，是否有指令需要独立 load port pool？
5. 队列级 `forwarding - 1` 对齐是否适用于所有依赖指令对，还是只适用于进入 SHQ/EXQ 的计算消费者？

## 验证清单

每个 phase 后运行：

```bash
python3 -m py_compile main.py api/*.py core/*.py
python3 main.py --trace VFtest/VADD_oneloop.json --out_dir results/sanity_vadd_oneloop
python3 main.py --trace VFtest/GeLU_poly.json --out_dir results/sanity_gelu_poly
python3 tools/run_cost_model_regression.py --tier smoke
```

第一阶段期望行为：

- 已迁移到 `VLDS` / `VSTS` 的 JSON trace 应保持 cycle 兼容。
- 除非某个阶段明确改变时序语义，否则 `idu_to_ooo.json`、`start_by_cycle.json`、`done_by_cycle.json` 应保持旧版 case 的指令顺序。

## 总结

目标模型边界是：

```text
op 是一等 ISA instruction
ISA 把 op 分类为 LOAD、STORE 或 COMPUTE
uarch 定义 queue、port、credit capacity
依赖表定义生产者到消费者的就绪时序
core 执行分类和 timing rule，不再硬编码 LSU op 名称
```

这样既保留硬件资源差异，又消除模拟器实现中不必要的 op 名称特判。
