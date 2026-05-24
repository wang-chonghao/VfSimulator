# CCE 寄存器释放时机规则实验手册（可执行版）

## 1. 目标与范围

目标：通过 CCE dump 日志（IDU/ISU/EXU/instr）提取“寄存器何时可复用（release timing）”的经验规则，并形成可复用实验流程。

口径约束：
- 默认 `misched=0`
- 不改主模型逻辑，仅跑/分析实验 case
- release 事件不可直接观测时，用 `IDU 可用 vreg 上跳` 作为可观测代理（proxy）

---

## 2. 环境与命令模板（默认 misched=0）

仓库根目录：`/mnt/d/VfSimulator`

关键脚本：
- `ascend_runner/current/build_native_simexec.sh`
- `ascend_runner/current/run_native_simexec.sh`

CCE 二次编译模板（确保 `misched=0`）：

```bash
REPO=/mnt/d/VfSimulator
ACL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
CCEC="$ACL/x86_64-linux/bin/ccec"
LDLLD="$ACL/x86_64-linux/bin/ld.lld"

stem="<case>_misched0"
dsl="$REPO/cce_code/reg_release_probe/<case>.dsl"
build="$REPO/ascend_runner/build/${stem}_native_simexec"
cce="$build/${stem}.cce"
aiv="$build/${stem}_mix_aiv.o"
mix="$build/${stem}_mix.o"
sim="$build/${stem}_simexec"

bash "$REPO/ascend_runner/current/build_native_simexec.sh" "$dsl" "$stem"

"$CCEC" -g -std=c++17 -c -O2 "$cce" -o "$aiv" \
  -I/usr/include/c++/11 \
  -I/usr/include/aarch64-linux-gnu/c++/11 \
  --cce-aicore-arch=dav-c310-vec \
  --cce-aicore-only \
  -mllvm -cce-aicore-function-stack-size=16000 \
  -mllvm -cce-aicore-record-overflow=false \
  -mllvm -cce-aicore-addr-transform \
  -mllvm -cce-aicore-jump-expand=true \
  -mllvm -cce-aicore-vec-misched=0 \
  --cce-simd-vf-fusion=false

"$LDLLD" -Ttext=0 "$aiv" -static -o "$mix"

bash "$REPO/ascend_runner/current/run_native_simexec.sh" \
  "$sim" "$mix" "<kernel_name>" 2 1 6144
```

---

## 3. 必抓 dump 文件

每个 case 至少保留以下 5 份：
- `core0.veccore0.rvec.IDU.dump`
- `core0.veccore0.rvec.ISU.dump`
- `core0.veccore0.rvec.EXU.dump`
- `core0.veccore0.instr_popped_log.dump`
- `core0.veccore0.instr_log.dump`

推荐额外：
- `core0.veccore0.rvec.simd.ifu.dump`（定位指令流问题时）

---

## 4. 最小实验矩阵（6 case）

| case | dsl | kernel | producer | consumers（候选 last-use） | 设计意图 |
|---|---|---|---|---|---|
| E1 | `reg_release_e1_single_fast` | `reg_release_e1_single_fast` | `RV_VADDS` | `RV_VEXP` | 单消费者快速路径 |
| E2 | `reg_release_e2_dual_slowvdiv` | `reg_release_e2_dual_slowvdiv` | `RV_VADDS` | `RV_VMULS`,`RV_VDIV` | 双消费者，慢消费者主导 |
| E3a | `reg_release_e3_gap0` | `reg_release_e3_gap0` | `RV_VADDS` | `RV_VDIV` | 无 gap 基线 |
| E3b | `reg_release_e3_gap8` | `reg_release_e3_gap8` | `RV_VADDS` | `RV_VDIV` | 插入 gap，检验与 last-consumer 的耦合 |
| E4a | `reg_release_e4_store_only` | `reg_release_e4_store_only` | `RV_VADDS` | `RV_VST` | 仅 store 消费 |
| E4b | `reg_release_e4_compute_then_store` | `reg_release_e4_compute_then_store` | `RV_VADDS` | `RV_VMULS`,`RV_VST` | compute+store 混合消费 |

---

## 5. 每个 case 的关键字段与判据

关键字段：
- IDU：
  - dispatch：`instr send to OOO ... instr.id=... instr.name=...`
  - 可用寄存器：`ooo=(preg:*, vreg:*)`
  - 阻塞原因：`IDU_BLOCK ... REASON:OOO no avail phy vreg`
- ISU：
  - `ISU_RECV / ISU_ISSUE / ISU_WAKEUP_BLOCK`（验证消费者确实占用关键路径）
- EXU：
  - `[PERF] [cycle] EXU instr_name ... instr_id ... retire ... exu_id`
- popped：
  - 指令 start cycle（按 ID）
- instr_log：
  - 指令 done cycle（按 ID）

判据（每个 case）：
1. 找到 producer 首次动态实例 `producer_id`（默认 `RV_VADDS` 第一条）。
2. 找到所有依赖该 producer 的消费者候选中，`done` 最晚的一条，记为 `last_consumer_done`。
3. 在 IDU dispatch 序列中，从 `last_consumer_done` 往后找第一个 `vreg` 上跳时刻，记为 `release_proxy_cycle`。
4. 计算 `delta_done = release_proxy_cycle - last_consumer_done`。
5. 若 `delta_done` 在不同 case 近似稳定（小方差），支持规则：  
   `release ~ last_consumer_done + k`。

---

## 6. 从日志推导 release timing 的公式与步骤

定义：
- `S(i)`: 指令 i 的 start cycle（`instr_popped_log`）
- `D(i)`: 指令 i 的 done cycle（`instr_log`）
- `A(t)`: IDU dispatch 时刻 t 看到的可用 `vreg` 计数（来自 `IDU` 的 `ooo=(..., vreg:x)`）

步骤：
1. 识别 `producer_id` 与其消费者集合 `C`（按 op 名和指令时序过滤）。
2. 求 `t_last_done = max(D(c)) , c in C`。
3. 求 `t_release_proxy = min{ t | t >= t_last_done 且 A(t) > A(t_prev_dispatch) }`。
4. 计算偏移：
   - `k_done = t_release_proxy - t_last_done`
   - `k_start = t_release_proxy - max(S(c))`
5. 跨 case 汇总 `k_done`（均值/P50/P90）形成经验参数。

输出建议：
- 主规则：`release_cycle ≈ last_consumer_done + k_done_median`
- 保守规则：`release_cycle ≈ last_consumer_done + k_done_p90`

---

## 7. 自动化执行与汇总

使用脚本：

```powershell
python d:\VfSimulator\tools\run_reg_release_rule_suite.py `
  --output-root d:\VfSimulator\results\tmp_reg_release_rule_probe `
  --cases reg_release_e1_single_fast reg_release_e2_dual_slowvdiv `
  --analyze-only
```

全矩阵（尝试执行+分析）：

```powershell
python d:\VfSimulator\tools\run_reg_release_rule_suite.py `
  --output-root d:\VfSimulator\results\tmp_reg_release_rule_probe `
  --run
```

产物：
- `.../summary.csv`：全量 case 汇总
- `.../quick_results.csv`：快速记录（可手工追加）

---

## 8. 当前已知限制

- 当前会话下 WSL 提权未获执行确认时，只能做 `analyze-only` 或脚本骨架验证。
- `release_proxy_cycle` 是可观测代理，不是硬件内部“真实释放信号”；需多 case 稳定性验证后再写入模型规则。
