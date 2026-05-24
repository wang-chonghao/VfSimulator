# Queue 时序摸底（2026-04-09）

基于日志：
- `cce_dump/GeLU_poly/core0.veccore0.rvec.ISU.dump`
- `cce_dump/GeLU_poly/core0.veccore0.rvec.EXU.dump`
- `cce_dump/GeLU_poly/core0.veccore0.rvec.IDU.dump`

## 已确认的时序关系

1. `SHQ ISU_ISSUE -> EXQ ISU_RECV` 基本固定 `+1 cycle`
- 统计：450 条，`min=1 max=1 avg=1`

2. `EXQ ISU_ISSUE -> EXU start` 基本固定 `+1 cycle`
- 统计：450 条，`min=1 max=3 avg=1.004`
- 仅 1 个离群点（instr_id=911，+3）

3. `EXU retire -> EXQ ISU_RETIRE` 固定 `0 cycle`
- 统计：450 条，`min=0 max=0 avg=0`

4. `LDQ ISU_RECV_RLS -> LDQ ISU_RETIRE` 固定 `+2 cycle`
- 统计：16 条，`min=2 max=2 avg=2`

## 对模型的直接含义

1. EXQ 接收延迟设为 `1` 是对齐的（当前已是 `exq_recv_delay=1`）。
2. EXQ retire 近似与 EXU retire 同拍（当前实现按 done 释放，与日志方向一致）。
3. LDQ 不应在 issue 后立刻算“完全结束”；`RECV_RLS` 与 `RETIRE` 间还有固定尾巴。

## 本轮实现与验证

本轮新增：
- `exq_capacity_counts_inflight`（默认 `false`）
- EXQ 容量统计可选只看 `EXQ wait`，不把已下发 EXU 的 inflight 继续占 EXQ 容量。

代码位置：
- `configs/uarch.json`
- `core/ooo.py`
- `core/ooo_consumer_done.py`

A/B 结果（simulate-only 样本集）：
- 输出：`results/regression_suite/queue_ab_after_fix/queue_ab_compare.json`
- `queue_off` 平均相对误差：`0.0537`
- `queue_on` 平均相对误差：`0.1440`
- 仍显著劣化，说明 queue 模型还缺关键反馈链路。

## 结论

当前 queue 建模的主要问题不再是“EXQ 容量是否计 inflight”，而是：
- `IDU` 对 `ISU credit` 回传可见时机仍未建模完整（反馈链路延迟/记账边界）。
- 这会把前端 dispatch 过度卡死，导致全局结果偏慢。
