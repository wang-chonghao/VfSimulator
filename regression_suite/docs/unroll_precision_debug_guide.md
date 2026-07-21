# Unroll 精度排查指南（CCE Dump vs Cost Model）

本文用于指导：当 `unroll` 后模型预测与 CCE simulator 结果出现明显偏差时，如何系统定位误差来源。

## 1. 先看哪些现有文档

建议按这个顺序读：

1. `README.md`
- 先确认模型入口、`--ooo-model`、优化与回归命令口径。

2. `api.md`
- 确认 JSON trace 输入格式（`params.I / params.U / program.loop.unroll`）。

3. `ascend_runner/CCE_simulator_src_fanout_probe_guide.md`
- CCE simulator 的 build/run 路线、产物目录、dump 文件位置。

4. `ascend_runner/operator_test.md`
- 如何用 CCE dump 反推 latency / forwarding / II，适合做参数回标。

5. `regression_suite/README.md`
- 回归集与 baseline 的维护方式。

## 2. 一次完整对比要产出的两套日志

## 2.1 模型日志（本仓库 cost model）

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/unroll_test/model_logs/I96_unroll4
```

关键输出：
- `done_by_cycle.json`
- `start_by_cycle.json`
- `idu_to_ooo.json`
- （可选）`sim_history.json`

## 2.2 CCE simulator 日志

参考 `ascend_runner/CCE_simulator_src_fanout_probe_guide.md` 跑 CCE，核心是拿到：
- `core0.veccore0.instr_popped_log.dump`（指令 start）
- `core0.veccore0.instr_log.dump`（指令 done + vf_execute_time）
- `core0.veccore0.rvec.IDU.dump`
- `core0.veccore0.rvec.OOO.dump`
- `core0.veccore0.rvec.EXU.dump`
- （需要看编译重排时）`core0.veccore0.rvec.simd.ifu.dump`

通常放在：`cce_dump/<case_name>/`。

## 3. 重点字段与对应关系

1. VF 总时间
- 模型：`done_by_cycle.json` 最大 cycle（也等价于终端打印 `VF end cycle (with drain)`）
- CCE：`instr_log.dump` 中 `vf_execute_time`

2. 指令开始时间对齐
- 模型：`start_by_cycle.json`
- CCE：`instr_popped_log.dump`

3. IDU/OOO 行为对齐
- 模型：`idu_to_ooo.json`
- CCE：`rvec.IDU.dump` + `rvec.OOO.dump`

4. EXU 绑定与吞吐
- 模型：从 `start_by_cycle` + 调度规则推导
- CCE：`rvec.EXU.dump`（最关键）

## 4. 建议的逐步排查流程（按优先级）

1. 先对齐“口径”
- 是否比较的是同一配置：`I`、`U`、`--ooo-model`、是否 `misched0`。
- 若做公平对比，建议固定 `misched0` 与模型都使用“无编译调度重排”假设。

2. 先比 VF 总时间
- 若总误差 <5%，先看局部不必急着改模型。
- 若总误差 >10%，进入后续分解。

3. 再比指令 start 时间分布
- 先看前 100~200 cycle 的 warmup 段。
- 再看稳态区是否存在“模型持续慢于 CCE”的固定斜率。

4. 检查 IDU 节奏与寄存器压力
- 对照 `rvec.IDU.dump` 的 vreg 占用轨迹与模型内部释放时机。
- 若模型 vreg 长期偏高，常见原因是释放规则过保守。

5. 检查 EXU 分派策略
- 对照 `rvec.EXU.dump`：同类指令是否在 CCE 中更均衡分配到两套 EXU。
- 若模型偏单边 EXU，会在高 unroll 下放大误差。

6. 检查 II / forwarding 参数
- 先盯高频指令对（例如 `VMUL->VMUL`、`VMUL->VADDS`）。
- 若稳态吞吐差异明显，通常是 II/forwarding 其中一项偏保守。

7. 最后检查编译器重排影响
- 对比 `simd.ifu.dump` 与模型预期顺序。
- 若 CCE 在 unroll 后重排明显，而模型按原序列执行，误差可能主要来自这里。

## 5. Unroll 场景常见误差来源清单

1. 编译器 `misched` 重排导致执行序与模型假设不一致。
2. 高 unroll 时 EXU/EXQ 分派策略与真实硬件策略不一致。
3. 高 unroll 下寄存器释放节奏偏保守，造成人工“卡脖子”。
4. 高频指令对的 II/forwarding 参数偏大，稳态吞吐被低估。
5. 模型未充分反映跨迭代并行窗口（特别是 unroll=4/8）。

## 6. 推荐的最小实验矩阵（用于快速定位）

针对同一 trace（例如 `GeLU_poly I=96`）：

1. U=1、U=2、U=4、U=8 分别跑模型与 CCE。
2. CCE 同时保留两套：默认 + `misched0`。
3. 每个点至少记录：
- VF 总时间
- 关键指令对齐误差（start 时间）
- EXU 使用分布（EXU0/EXU1 比例）

这样可以快速判断：误差主要来自“编译调度”还是“模型微架构参数/策略”。

## 7. 与回归集联动

已纳入回归集的 unroll case：
- `gelu_poly_i96_u2`
- `gelu_poly_i96_u4`
- `gelu_poly_i96_u8`

运行：

```bash
python tools/run_cost_model_regression.py --tier smoke
```

默认与 `precision_compare_3modes.md` 中的
`queue_level4+ooo-transfer-delay` 列比较，对应基线文件：

```text
regression_suite/cases/baseline_queue_level4_ooo_transfer_delay.json
```

更新 baseline：

```bash
python tools/run_cost_model_regression.py --tier full --update-baseline
```

说明：当前这 3 个 case 的 `cce_vf_end` 采用 `misched0` 口径，并在 case 中保留来源标记，便于后续继续做双口径跟踪。
