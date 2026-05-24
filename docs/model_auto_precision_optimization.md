# Model Auto Precision Optimization

## Goal

这份文档规定后续做模型精度优化时的标准流程，避免出现“先预埋很多轮改动，再一次性批量跑”的情况。

这里的原则很简单：

- 一次只做一轮真实迭代
- 每轮只改少量、可解释的规则
- 每轮改完后必须先跑、先看、先总结
- 只有当前一轮结论明确后，才能进入下一轮

---

## Core Principle

后续所有精度优化都遵循下面这条闭环：

1. 基于日志提出一个明确假设
2. 只做与该假设直接相关的一次改动
3. 跑固定测试用例
4. 记录改动前后结果
5. 分析收益和副作用
6. 决定保留、回退或继续细化

禁止的做法：

- 一次性预埋 5 到 10 轮改动
- 在没分析上一轮结果前继续追加下一轮改动
- 把多个不相关的规则同时改掉，导致无法归因
- 只看最终总误差，不看日志中的行为变化

---

## Fixed Evaluation Cases

当前用于快速迭代的固定 case：

- `SwiGLU I=96 U=6`
- `SwiGLU I=96 U=8`
- `SiLU I=96 U=4`

用途：

- `SwiGLU I=96 U=6`
  - 代表“当前模型本来相对接近”的 case
  - 用来防止为了修 U8 把正常 case 改坏
- `SwiGLU I=96 U=8`
  - 代表当前误差最大的高压力 case
  - 用来观察 front-end、queue、preg credit 是否出现阈值效应
- `SiLU I=96 U=4`
  - 代表另一类真实算子
  - 用来检查修改是否具有一定通用性

如果后续增加 case，也必须先说明增加原因，不能随意扩展。

---

## Per-Iteration Workflow

每一轮迭代都必须严格按下面顺序进行。

### Step 1. Define One Hypothesis

先写清楚本轮只验证一个假设，例如：

- `preg release` 过晚导致高 unroll 下 front-end 断粮
- `IDU -> OoO` 传输延迟建模过保守
- `SHQ -> EXQ` 入队规则与 CCE 不一致
- `EXQ` 容量/占用规则不符合 CCE 行为

要求：

- 假设必须能从 CCE 日志中找到依据
- 假设必须足够具体，能对应到代码里的一个明确机制

### Step 2. Make One Focused Change

只允许做与本轮假设直接相关的代码改动。

允许：

- 改一个延迟
- 改一个释放条件
- 改一个 queue 入队/出队规则
- 新增一个可控开关并只启用它

不允许：

- 同时改 release、dispatch、queue、EXQ 策略四五件事
- 混入清理代码、重构风格、命名变更等无关内容

### Step 3. Run Fixed Cases

本轮改完后，只跑固定的 3 个 case：

- `SwiGLU I=96 U=6`
- `SwiGLU I=96 U=8`
- `SiLU I=96 U=4`

当前阶段默认：

- 只跑模型
- 不重跑 CCE
- CCE 基线固定使用已有基准值

### Step 4. Record Results

每轮必须记录：

- 本轮编号
- 本轮假设
- 本轮具体改动
- 改动涉及的代码文件
- 三个 case 的模型预测时间
- 三个 case 对应的相对误差
- 与上一轮相比是变好还是变差

### Step 5. Inspect Behavior, Not Just Numbers

每轮不能只看总时间，还必须看行为是否符合预期。

至少检查：

- `idu_to_ooo.json`
- `start_by_cycle.json`

必要时对照 CCE：

- `IDU.dump`
- `ISU.dump`
- `instr_popped_log.dump`
- `instr_log.dump`

重点不是逐项一一对齐，而是判断：

- 本轮修改是否真的改变了我们想改的那一段行为
- 空泡是否减少
- 第二波/后续波次是否提前启动
- preg / SHQ credit 是否更平滑

### Step 6. Make a Decision

每轮结束必须给出一个明确结论：

- `keep`
  - 本轮改动有效，保留进入下一轮
- `revert`
  - 本轮改动无效或副作用过大，回退
- `refine`
  - 方向是对的，但参数或规则还需要继续微调

如果没有这个结论，就不能进入下一轮。

---

## Required Iteration Log Format

每轮日志建议用下面结构：

### Iteration N

- Hypothesis:
- Change:
- Files:
- Expected effect:
- Result on `SwiGLU I96 U6`:
- Result on `SwiGLU I96 U8`:
- Result on `SiLU I96 U4`:
- Observed behavior change:
- Decision:
- Next step:

---

## Curve Policy

误差曲线可以画，但只能在“真实逐轮迭代”完成后生成。

也就是说：

- 不是先设定 10 轮再批量跑
- 而是先做第 1 轮，记录
- 再做第 2 轮，记录
- ...
- 等实际完成 N 轮后，再画 `error vs iteration`

曲线只是总结工具，不是替代分析工具。

---

## Current Baseline Policy

当前重新开始时，默认基线如下：

- 分支：`dev_test`
- 模型：`start+5` / `queue_level3` 作为当前研究对象
- 但每一轮真实迭代前，必须明确说明：
  - 本轮是在什么默认代码状态上继续改
  - 是否启用了实验开关

这样做是为了防止后面再次出现“同名模式其实来自不同代码状态”的混乱。

---

## Current Priority

当前优先级最高的方向：

1. 根据 CCE 日志继续核对 `preg release` 的真实生效时机
2. 核对 `SHQ -> EXQ` 的入队限制
3. 核对 `EXQ` 内部调度和容量规则
4. 核对 `IDU` 看到 credit 返回的时机

其中每一轮只允许挑一个点做。

---

## First Restart Rule

从现在开始，重新启动精度优化流程时：

- 第 1 轮先从当前最新代码状态出发
- 只验证一个最明确的日志驱动修改
- 跑 3 个固定 case
- 写正式迭代记录
- 确认这轮是否保留

只有完成这一步，才进入第 2 轮。

