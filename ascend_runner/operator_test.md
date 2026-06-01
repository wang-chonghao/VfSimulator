# 指令测试手册（修订版）

本手册用于指导如何通过 CCE/CAModel simulator 测试并标定 VF cost model 所需参数，主要包括：
- 单指令参数：`pipeline_startup_cost`、`latency`、`pipeline_drain_cost`、`data_load_cost`、`data_store_cost`
- 指令对参数：`forwarding`、`initiation interval (II)`

测试流程参考：`d:/VfSimulator/ascend_runner/CCE_simulator_src_fanout_probe_guide.md`
测试环境：A5（Ascend950PR_9599，dav-c310）

---

## 1. 微架构基础信息

VF（Vector Function）在昇腾 NPU 的 vector core 上执行。典型数据路径为：
GM -> UB -> Vector Register/EXU -> UB -> GM。

当前 A5 代际（本手册测试口径）可按如下理解：
- Load 端口：2
- 计算端口（EXU）：2
- Store 端口：1

具体某条指令在哪个 EXU 执行、开始/结束周期，可在 CCE dump 日志中查看（`core0`）。

---

## 2. 单指令参数测试

### 2.1 目标字段与定义

```json
{
  "defaults": {
    "vf_startup_cost": 23,
    "vf_drain_cost": 12
  },
  "instructions": {
    "VADDS": {
      "fp32": {
        "pipeline_startup_cost": 6,
        "latency": 7,
        "pipeline_drain_cost": 5,
        "data_load_cost": 9,
        "data_store_cost": 10,
        "EXU": "ALU"
      }
    }
  }
}
```

| 字段 | 含义 |
| :--- | :--- |
| `vf_startup_cost` | VF 启动开销（VF 开始到第一条可执行 VLD 的等待） |
| `vf_drain_cost` | VF 排空开销（最后一条动态指令完成后到 VF 结束的尾部开销） |
| `pipeline_startup_cost` | 源 VLD 开始后，到下游计算指令最早可开始的间隔 |
| `latency` | 计算指令开始到其结果可用的延迟 |
| `pipeline_drain_cost` | 计算指令开始后，到其结果可被 VST 消费的间隔 |
| `data_load_cost` | VLD 完整执行时间（VLD done - VLD start） |
| `data_store_cost` | **与 producer op 绑定**的存储完成时间（见下面“重要说明”） |
| `EXU` | 指令归属功能单元：`ALU` 或 `SFU` |

> 注意：`throughput` 字段已废弃，连续发射能力统一由 `configs/InitiationInterval.json` 描述。

### 2.2 重要说明（和模型实现一致）

在当前 cost model 实现中，`data_store_cost` 不是按 `VST` 指令名单独查，而是按“该 VST 所写结果的 producer op”查。

也就是说：
- 测试上你仍可从 VST 的 start/done 直接量到存储耗时；
- 落到模型配置时，这个值写在对应 producer op 的 `data_store_cost` 下。

### 2.3 单操作数指令测试模板

```dsl
__VEC_SCOPE__ {
    vector_bool pat_all_b32 = pset_b32(PAT_ALL);
    vector_f32 vec_1;
    vector_f32 vec_3;
    for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
        vlds(vec_1, memA, 64 * i, NORM);
        vadds(vec_3, vec_1, 0.1f, pat_all_b32);
        vsts(vec_3, memC, 64 * i, NORM_B32, pat_all_b32);
    }
}
```

建议日志：
- `core0.veccore0.instr_popped_log.dump`（start）
- `core0.veccore0.instr_log.dump`（done）
- `core0.veccore0.rvec.EXU.dump`（EXU 归属与执行信息）

参数计算：
- `pipeline_startup_cost = first_compute_start - first_vld_start`
- `latency = compute_done - compute_start`
- `pipeline_drain_cost = first_vst_start - first_compute_start`
- `data_load_cost = vld_done - vld_start`
- `data_store_cost = vst_done - vst_start`

### 2.4 双操作数指令模板

```dsl
__VEC_SCOPE__ {
    vector_bool pat_all_b32 = pset_b32(PAT_ALL);
    vector_f32 vec_1;
    vector_f32 vec_3;
    for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
        vlds(vec_1, memA, 64 * i, NORM);
        vadd(vec_3, vec_1, vec_1, pat_all_b32);
        vsts(vec_3, memC, 64 * i, NORM_B32, pat_all_b32);
    }
}
```

按本手册统一口径，`pipeline_startup_cost` 使用“第一个 OP 与第一个 VLD 的 start 差值”：
- `pipeline_startup_cost = first_op_start - first_vld_start`。

---

## 3. 指令间参数测试（Forwarding 与 II）

## 3.1 Forwarding

定义：有 RAW 依赖时，
`forwarding(producer, consumer) = consumer_start - producer_start`。

推荐模板（单操作数 -> 单操作数）：

```dsl
#pragma unroll(2)
for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
    vlds(vec_1, memA, 64 * i, NORM);
    vadds(vec_2, vec_1, 0.1f, pat_all_b32);   // producer
    vmuls(vec_3, vec_2, 0.1f, pat_all_b32);   // consumer
    vsts(vec_3, memB, 64 * i, NORM_B32, pat_all_b32);
}
```

`unroll(2)` 的作用：提高同拍样本密度，便于在 EXU0/EXU1 同时观察。

其余组合：
- producer 双输入：`vadd -> vmuls`
- consumer 双输入：`vadds -> vmul`
- 双输入到双输入：`vadd -> vmul`

都可同样统计 `consumer_start - producer_start`。

## 3.2 Initiation Interval (II)

定义：同一 EXU 上前后两条无依赖指令的最小发射间隔。

测试思路：构造“无依赖、可并行”的两条计算指令，强制在日志里按 EXU 过滤后统计间隔。

示例：
```dsl
#pragma unroll(2)
for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
    vlds(vec_1, memA, 64 * i, NORM);
    vadds(vec_2, vec_1, 0.1f, pat_all_b32);
    vmuls(vec_3, vec_1, 0.1f, pat_all_b32);
    vsts(vec_2, memB, 64 * i, NORM_B32, pat_all_b32);
    vsts(vec_3, memC, 64 * i, NORM_B32, pat_all_b32);
}
```

从 `core0.veccore0.rvec.EXU.dump` 中按“同一个 EXU”提取两类指令的开始周期差，得到 `II(vadds, vmuls)`。

### 3.3 防编译器合并优化

测试相同指令对（如 `II(vadds, vadds)`）时，若两条指令完全相同，可能被编译器合并。
建议修改立即数或源寄存器，避免语义等价。

例如：
- 第一条 `vadds(..., 0.1f)`
- 第二条 `vadds(..., 0.2f)`

---

## 4. 常见错误与修正

1. `vmuls` 是乘法，不是减法。
2. `vadd` 不能带立即数（带立即数应使用 `vadds`）。
3. 代码里使用了 `vec_4` 时必须先声明 `vector_f32 vec_4;`。
4. 中间输出和最终 `copy_ubuf_to_gm` 的 buffer 要一致，避免“测的不是实际写出的数据流”。
5. 文件名统一为 `configs/InitiationInterval.json`（不是 `IniationInterval.json`）。
6. 统计forwarding时，必须看producer-consumer的启动时间差，最简单方式为仅看第一个producer指令与第一个consumer指令，或者循环次数改为1，仅有一个producer和consumer，避免统计成II。

---

## 5. 输出归档建议

每次参数测试建议保存以下信息：
- 测试 DSL
- CCE 编译参数（是否开/关 misched）
- 三份关键日志（popped/instr/EXU）
- 提取后的参数结果表（含样本条数与统计方式）

最终将标定结果回填到：
- `configs/isa.json`
- `configs/InitiationInterval.json`


---

## 6. 最小可复现实验清单（建议按表执行）

下表给出每类参数最小测试组合。建议每项至少采 2 组重复样本，避免偶发调度噪声。

| 测试目标 | 最小 Kernel 形态 | 关键日志 | 提取公式 | 回填位置 |
| :--- | :--- | :--- | :--- | :--- |
| `pipeline_startup_cost(op)` | `VLD -> op -> VST` | `instr_popped` | `op_start - vld_start` | `configs/isa.json` |
| `latency(op)` | `VLD -> op -> VST` | `instr_popped` + `instr_log` | `op_done - op_start` | `configs/isa.json` |
| `pipeline_drain_cost(op)` | `VLD -> op -> VST` | `instr_popped` | `vst_start - op_start` | `configs/isa.json` |
| `data_load_cost(op)` | `VLD -> op -> VST` | `instr_popped` + `instr_log` | `vld_done - vld_start` | `configs/isa.json` |
| `data_store_cost(op)` | `VLD -> op -> VST` | `instr_popped` + `instr_log` | `vst_done - vst_start` | `configs/isa.json`（写在 producer op 下） |
| `forwarding(prod, cons)` | `... -> prod -> cons -> ...`（有 RAW） | `instr_popped` | `cons_start - prod_start` | `configs/forwarding.json` |
| `II(prev, cur)` | `prev` 与 `cur` 无 RAW，且可在同 EXU 观测 | `rvec.EXU.dump` | 同 EXU 相邻发射差值 | `configs/InitiationInterval.json` |

### 6.1 执行顺序建议

1. 先测单指令参数：`startup -> latency -> drain -> load/store`
2. 再测 forwarding（按 producer/consumer 组合）
3. 最后测 II（按 EXU、按指令对）

### 6.2 记录模板（每次实验至少记录）

- 测试 DSL 文件名
- 编译参数（是否 `misched0`，是否 `unroll`）
- 输入循环次数 `repeat_times`
- 关键样本行号（日志中的 start/done 记录）
- 计算得到的参数值
- 写回到哪个配置文件的哪个键

### 6.3 快速自检（防止无效样本）

- 两条“同指令”是否被编译器合并（如立即数是否区分）
- 统计 II 时，是否确认是同一个 EXU
- 统计 forwarding 时，是否确认 producer/consumer 有真实 RAW 依赖
- `VST` 输出地址是否与最终 copy 地址一致（避免测错数据路径）


---

## 7. 本次校准经验（防踩坑，2026-04）

以下规则来自本轮 `single_op` 参数复核，建议作为后续标定的强约束。

### 7.1 单指令标定用例固定模板

- 单操作数指令：`VLD -> OP -> VST`
- 双操作数指令（用于测 startup/drain/load/store 的最小模板）：`VLD(vec_a) -> OP(vec_out, vec_a, vec_a) -> VST`
- 不要在参数标定 case 中引入多余数据流（例如第二路真实输入），避免“最近 VLD”归属歧义。

推荐模板：

```dsl
for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {
    vlds(vec_a, memA, 64 * i, NORM);
    vadd(vec_out, vec_a, vec_a, pat_all_b32);  // 二元指令示例
    vsts(vec_out, memOut, 64 * i, NORM_B32, pat_all_b32);
}
```

### 7.2 日志口径必须统一（start/done 分离）

- `start` 一律取 `core0.veccore0.instr_popped_log.dump`
- `done` 一律取 `core0.veccore0.instr_log.dump`
- 同一个指标内，必须使用同一条指令的同一 `ID` 做配对。

参数口径：

- `pipeline_startup_cost = first_op_start(popped) - first_vld_start(popped)`
- `latency = first_op_done(instr_log) - first_op_start(popped)`
- `pipeline_drain_cost = first_vst_start(popped) - first_op_start(popped)`
- `data_load_cost = first_vld_done(instr_log) - first_vld_start(popped)`
- `data_store_cost = first_vst_done(instr_log) - first_vst_start(popped)`

### 7.3 关于 `#pragma unroll` 的结论

- `#pragma unroll(2)` 会改变调度与重叠关系，可能导致 `VST` 的观测时长变化（例如 `10 -> 9`）。
- 这不一定表示硬件常数变了，而是编译后流水重叠方式不同。
- 结论：
- `isa.json` 参数标定时，统一使用“无 unroll 或固定同一 unroll 配置”的口径；不要混用。

### 7.4 常见误判来源（本次已验证）

- 把 `VLD` 的耗时当成 `VST` 的耗时。
- 在 `instr_popped_log` 里同时读 start/done（done 应来自 `instr_log`）。
- 用不同 core 或不同 veccore 的行做差。
- 二元指令样例有两路 load 时，错误选择了非真实依赖的 VLD。

### 7.5 提交前自检清单

1. 是否使用了最小模板（`VLD -> OP -> VST`）？
2. 是否禁用了会改变调度口径的额外优化（或保证全程一致）？
3. start/done 是否分别来自 `popped` 和 `instr_log`？
4. 做差的两端是否同一 `ID`、同一 core、同一 veccore？
5. 是否只取“第一条有效样本”并记录其日志行号？


