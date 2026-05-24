# CCE + camodel 自动优化规则（Codex 执行版）

## 目标
在 **不破坏数值正确性** 和 **不改变基准公平性** 的前提下，借助CANN 9.0.0 中的camodel，持续产出可复现的性能优化结果，在优化中可参考本仓库的cost model了解算子的指令流与微架构信息。

本规则为自动优化回合的强约束文档。除非用户明确覆盖，否则每轮必须遵守。

所有问题必须有明确结论和依据时才能进行记录并开启下一轮优化，不能是“可能是什么”，“大概是什么原因造成的”，对于问题分析要有明确依据。

---

## 0. 核心原则

CCE 代码优化不是通用代码清理，而是面向硬件瓶颈(目前以CANN9.0.0 中camodel硬件仿真结果为标准)的定向改进。

每次修改前必须能回答：

1. 当前瓶颈是什么
2. 为什么这个改动能改善瓶颈
3. 预期改善哪个指标
4. 如何保证语义与精度不变

若无法回答，先不改代码。

---

## 1. 一轮只验证一个假设

每个优化 round 只允许验证一个主假设，且 round n 主假设必须在round n-1轮的基础上进行，若连续两轮round n, round n+1的主假设均为负优化，则退回到round n-1，重新作出主假设，若连续三轮基于round n-1的主假设均为负优化，则退回到round n-1轮中的次优候选，若无次优候选则退回到round n-2重新进行主假设探索。 注：round n 为正优化还是负优化仅与round n-1比，而不与目前最优比，防止进入到局部最优。

允许：

- 进行 VF scope 切分融合
- 调整一个 loop 切分融合方案
- 调整一个 loop 的 unroll 策略
- 去掉一处可证明冗余的 `mem_bar / wait / set_flag`
- 一个loop中的指令重排策略变更
- 对使用的op指令进行替换，算子算法实现变更

注意每次 round 优化仅可选择一个主假设，对此主假设进行调优，但是在主假设确定后，允许设置多个候选测试案例，例如若当前轮次选择了某个for循环的unroll tuning时可以设置unroll参数为1, 2, 3, ...等多个候选进行批量验证，选出最优unroll作为本次 round 结果。

禁止：

- 同时进行多个主假设改动
- 同时改多个互不相关模块
- 同时调整多个loop中的指令顺序
- 混入“大范围清理”伪装为优化

---

## 2. 指标口径（必须统一）

本仓库必须区分两类性能指标：

1. **模型口径**
   - 仅可读取VfSimualtor中模型文档与代码了解硬件基本信息与VF(vector function)指令计算流程

2. **CCE 口径（camodel simulator）**
   - VF总时间以：`VF latency = VF_end - VF_start`为准，`VF_end`在camodel dump出的日志文件instr_log.dump中，即VF条目对应的cycle数，`VF_start`在instr_popped_log.dump日志汇总，即该日志中VF条目对应的cycle数。若有多个VF则以instr_log.dump日志中第一个vf词条对应的时间为`VF_end`，以最后一个vf词条对应的时间为`VF_start`。

结论规则：

- 模型仿真VfSuimulator仅可作为参考对应，帮助理解vector核内硬件信息与指令流水信息
- 对外最终性能结论以 **CCE `VF_end - VF_start`** 为准

---

## 3. 正确性优先于性能

每一轮必须按顺序执行：

1. 修改cce代码
2. 跑 golden 数值校验
3. 仅当正确性通过，才讨论性能

规则：

- 正确性未通过时，性能数据无效
- 不允许“先看性能再补正确性”
- 不允许私自放宽阈值或改 workload 让 case 过
- 若精度测试未通过，必须找到精度测试失败的原因，例如没有预设足够的UB起始地址，导致数据重叠等。找到精度测试失败原因后，需要修改重跑，直到通过精度验证。
- 若遇到精度问题，在解决精度问题后，必须在perf_log.md中记录下精度问题原因与解决方法。
- 每个 Round 结果必须在该 round 结束后记录到 perf_log.md 中，并结合之前 round 结果进行详细的瓶颈分析，结合当前瓶颈给出下一步的优化方向。
- 对于有优化的 round， 记录好所做改动与解除的对应瓶颈。对于劣化的 round，记录好改动与劣化点。
- 对于不确定的指令用法，优先在网站https://www.hiascend.com/document/detail/zh/canncommercial/850/API/cceintrinsicapi/cceapi_0024.html上查找，未找到时可在CANN包中如下位置寻找：ascend-toolkit/cann-9.0.0-beta.1/tools/bisheng_compiler/lib/clang/15.0.5/include/
  __clang_cce_vector_intrinsics.h或者本仓notes/isa.md中查找，还不清楚时可直接询问。
- 必须保留每一个 round 最优的 cce 代码。

---

## 4. 默认可编辑范围

默认可编辑：

- `cce_code/`
- `VFtest/`
- 与 kernel 直接相关的生成脚本（例如 `tools/generate_*dsl*.py`）

默认只读：

- `core/`
- `optimizer/`
- `notes/`
- `skills/` 

默认不可改（除非用户明确要求）：

- 基准 workload 定义
- 评测脚本判定逻辑
- 正确性阈值
- 结果解析逻辑

禁止通过“降低测试难度”制造优化收益。

---

## 5. 瓶颈分类（改前必做）

改代码前必须先归类瓶颈为以下之一：

- UB 压力
- 计算单元利用率不足（dual-issue 不充分等）
- 队列/发射/在飞限制（share queue、execution queue、inflight）
- barrier/wait/sync 过重
- 寄存器压力
- 依赖链过长
- 长指令，规约类指令数目过多
- 未知

若为“未知”，必须先补证据（日志、DAG、调度统计）后再改。

---

## 6. CCE 必查项（每轮）

每轮优化前后都要检查：


2. UB 容量与生命周期
   - 是否超出UB 248K 容量限制

3. 指令依赖结构
   - 是否形成不必要长链
   - 是否引入额外串行化
   - 是否改变了指令的依赖逻辑

4. 调度与双发射利用
   - 重排后 dual-issue 是否实质改善 （可通过计算平均IPC来判断）
   - 不是“看起来并行”，而是周期空泡减少

5. 同步开销
   - `mem_bar / wait / set_flag` 是否可证明必要

6. 资源副作用
   - 是否出现瓶颈转移（movement -> sync，compute -> register 等）

---

## 7. 优化优先级

按以下顺序优先推进：

1. 消除冗余数据搬运
2. 消除冗余计算指令
3. 合并拆分VF结构体
4. 算子实现的算法更新，避免过多使用长指令与规约类指令
5. 调整VF结构体中loop结构(合并loop，拆分loop，展开loop)
6. 对loop中指令顺序进行调整



---


## 8. CCE 验证规范

对于进入验证的候选，必须输出：

1. golden 结果
   - `mismatches`
   - `max_abs_err`
   - `max_rel_err`

2. VF 时间口径
   - `VF start`（instr_popped）
   - `VF end`（instr_log）
   - `vf latency = VF_end - VF_start`

3. 可追溯产物
   - 生成的 DSL 路径
   - build 目录
   - 结果 summary 路径

---

## 9. 风险改动的额外约束

以下属于高风险：

- loop切分过细，切分后需要插入vlds,vsts指令，单loop中计算指令数目偏少，成为memory bound
- 激进融合导致 live range 激增，寄存器压力增大
- mem_bar指令删除或未添加，导致精度问题
- 可能影响数值行为的重排
- 在UB空间未满的情况下，拆分VF，增加VF头尾开销

高风险改动必须额外说明：

1. 风险为何可接受
2. 重点监控哪些失败模式
3. 哪个指标会首先报警

---

## 10. 失败处理规则

若连续 3 轮无有效收益：

1. 回读 `notes/perf_log.md`
2. 复核瓶颈分类是否错判
3. 判断是否只在治标（症状）而非治本（根因）
4. 改变方向，优先结构性策略：
   - 切分策略重设
   - 数据流重构
   - 依赖链缩短
   - 分段 unroll 策略重估

禁止继续随机微调。

---

## 12. 预编辑模板（必须填写）

- **Round**:
- **Kernel/Trace**:
- **Current bottleneck**:
- **Evidence**:
- **Hypothesis**:
- **Planned change**:
- **Expected metric change**:
- **Main risk**:

---

## 13. 结果记录模板（必须填写）

- **Round**:
- **Kernel/Trace**:
- **Changed files**:
- **Hypothesis**:
- **Correctness**: pass / fail
- **Model metric**: baseline `vf_end` -> candidate `vf_end`
- **CCE metric**: baseline `vf_execute_time` -> candidate `vf_execute_time`
- **Stability**: stable / noisy / inconclusive
- **Resource side effects**:
  - UB pressure:
  - register pressure:
  - sync count:
  - movement pattern:
- **Result**: win / no win / inconclusive
- **Next step**:

---

## 14. 强制日志

每轮必须追加到：

`notes/perf_log.md`

必填字段：

- timestamp
- round id
- kernel/trace
- changed files
- bottleneck
- hypothesis
- correctness result
- model result
- CCE result
- confidence
- conclusion
- next action

不允许 silent round。

---

## 15. 提交策略

仅当满足以下条件才可提交：

1. 正确性通过
2. CCE `vf_execute_time` 可复现提升
3. 变更与收益因果明确
4. 代码可维护

提交信息格式：

`[kernel_name] <optimization_type>: <cce_gain>`

示例：

- `[GeLU_poly_I96] reorder tuning: -12.3% vf latency`

---

## 16. 会话启动规则

每次新会话开始必须先做：

1. 读 `notes/optimization_rules.md`
2. 读 `notes/perf_log.md`
3. 明确当前 best（按 CCE 口径）
4. 明确最近失败假设
5. 从当前 best 状态继续，不盲目重开

---

## 17. 最终标准

好的自动优化结果必须同时满足：

- 正确
- 可测
- 可复现
- 瓶颈驱动
- 硬件可解释
- 具备维护价值

不是“看起来更复杂”或“理论上可能更快”。
