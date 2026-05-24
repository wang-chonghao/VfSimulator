# VfSimulator + CCE/CAModel Program

本文指明cce仿真流程，并明确区分cce仿真与本仓库的vfsimulator仿真。
核心要点：

1. 明确区分 VfSimulator 与 CANN CAModel。
2. 统一模型筛选、CCE验证、golden精度校验流程。
3. 避免编译器重排/融合干扰优化判断。

---

## 0. 名词与边界（先对齐）

1. `VfSimulator`：本仓库自研的 cost model / cycle-level 仿真器。
- 入口是 `main.py`。
- 输出口径是 `VF end cycle (with drain)`，给出VfSimulator的仿真时间。

2. `CANN CAModel`：CANN 9.0.0 软件包自带的硬件仿真（CCE/CAModel simulator）。
- 在本项目中，CAModel 结果可视作接近硬件上板的实践口径。
- 最终性能结论以 CAModel/CCE 口径为准（`VF_end - VF_start`）。

3. 关系：
- VfSimulator 用于帮助codex理解昇腾NPU vector核内微架构信息，帮助做出下一步优化策略。
- CCE/CAModel 用于优化策略验证与对外结论。

---

## 1. 两条硬规则（CCE编译必须遵守）

跑 CCE 代码时必须固定如下编译选项，否则编译器会做额外调度重排/融合，污染优化收益判断：

1. 必须开启：`-mllvm -cce-aicore-vec-misched=0`
2. 必须关闭 VF 融合：`--cce-simd-vf-fusion=false`

说明：
- 本仓库 `ascend_runner/current/build_native_simexec.sh` 默认已包含 `--cce-simd-vf-fusion=false`。
- `misched=0` 通过 `CCEC_EXTRA_FLAGS` 传入。

---

## 2. VfSimulator执行入口

- VfSimulator：`main.py`
- 候选优化：`optimizer/run_vf_stage_c_optimization.py`
- CCE build：`ascend_runner/current/build_native_simexec.sh`
- CCE run：`ascend_runner/current/run_native_simexec.sh`

建议在 WSL Ubuntu 下执行 CCE 相关命令（`/mnt/d/VfSimulator`）。

---

## 3. 标准流程

### CCE/CAModel 运行

```bash
cd /mnt/d/VfSimulator
bash ascend_runner/current/run_native_simexec.sh \
  /mnt/d/VfSimulator/ascend_runner/build/<case_name>_native_simexec/<case_name>_simexec \
  /mnt/d/VfSimulator/ascend_runner/build/<case_name>_native_simexec/<case_name>_mix.o \
  <kernel_name> <num_inputs> <num_outputs> <total_elems>
```

关键日志：
- `/home/lenovo/msprof_run/<case_name>_native_simexec/core0.veccore0.instr_popped_log.dump`
- `/home/lenovo/msprof_run/<case_name>_native_simexec/core0.veccore0.instr_log.dump`

### golden 精度校验（必须先于性能结论）

看运行输出中的：
- `[CHECK] ... mismatches=... max_abs_err=... max_rel_err=...`
- `[CHECK] PASS`

当前 runner 默认阈值：
- `abs_tol=1e-3`
- `rel_tol=1e-3`

通过条件：
- `mismatches=0` 且最终 `PASS`。

规则：
- 未通过 golden，本轮性能数据无效，不进入结论。

### CCE 性能口径提取

```bash
grep -n "vf_execute_time" /home/lenovo/msprof_run/<case_name>_native_simexec/core0.veccore0.instr_log.dump | tail -n 5
```

统一结论口径：
- 最终性能结论以 CCE/CAModel 为准（`vf latency = VF_end - VF_start`）。

---

## 4. 提交判定条件（对齐 optimization_rules）

仅当同时满足以下条件才可认定“可提交优化”：

1. 正确性通过（golden pass）。
2. CCE `vf latency` 可复现提升。
3. 变更与收益因果清晰。

---

## 5. 一句话准则

VfSimulator 仅负责帮助camodel理解vector核内结构，指令流水，CCE/CAModel 负责优化的“最终裁决”；凡是 CCE 编译未满足 `misched=0` + `vf-fusion=false` 的结果，一律不作为有效优化结论。
