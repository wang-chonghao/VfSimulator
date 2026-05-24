# FREE_REG CCE Experiment Plan

## Goal

用一组最小化、可解释的 CCE 实验，回答两个问题：

1. `ISU_SCB_FREE_REG` 到底在什么时刻发出？
2. 它是否存在额外的“收口条件”后才允许发出？

这里的“收口条件”指的是：

- 不是单纯 `consumer start + 4`
- 还需要满足某个额外条件，例如：
  - 该 `preg` 的所有 consumer 都已经出现
  - 某个 overwrite 已经发生
  - 到达 loop 边界
  - store / compute 类型不同，规则不同
  - 某些 `src` 可释放，某些 `src` 不可释放


## Current Observations

基于已经跑过的 probe 和日志分析，目前比较明确的点：

1. 当某条指令确实发出了 `ISU_SCB_FREE_REG` 时，它的时间通常非常稳定地贴近 `consumer start + 4`。
2. 但不是所有 consumer 都一定发 `ISU_SCB_FREE_REG`。
3. 一个 `preg` 是否能被释放，核心不只是“某个 consumer 开始了”，还取决于：
   - 这个 `preg` 后面是否还有其他 consumer
   - overwrite 是在同 iter 发生还是跨 iter 发生
   - 该 consumer 读取的是旧 `preg` 还是已经 rename 后的新 `preg`

最典型的例子是：

- `vld(v0)`
- `vadds(v1, v0, ...)`
- `vadds(v0, v0, ...)`
- `vmuls(v0, v0, ...)`
- `vadd(v0, v0, v1)`
- `vst(v0)`

这里最初 `VLD` 产生的旧 `preg`，最后一个使用者并不是末尾 `vadd`，而是中间那个 `vadds(v0, v0, ...)`。
因此旧 `preg` 在第二条 `vadds` 的 `start + 4` 触发 `FREE_REG` 是合理的。


## Experiment Axes

后续实验按 4 个轴来拆：

1. Consumer count
   - 单 consumer
   - 双 consumer
   - 多 consumer

2. Overwrite placement
   - 同 iter 内 overwrite
   - 跨 iter overwrite
   - 无 overwrite

3. Consumer type
   - 纯计算 consumer
   - store consumer
   - 计算后再 store

4. Lifetime shape
   - 短 live range
   - 长 live range
   - 同名 vreg 多次 rename


## Experiment Matrix

### E1: 单 consumer 基线

目的：

- 验证最简单情况下，`FREE_REG` 是否稳定等于 `consumer start + 4`

形式：

- `producer -> single compute consumer`
- `producer -> single store consumer`

判据：

- 如果都稳定是 `start + 4`，说明“触发时机”本身大概率是对的
- 后面的复杂差异就不是基础时延问题

现有 case：

- `reg_release_single_consumer`
- `reg_release_e4_store_only`


### E2: 双 consumer，旧 preg 被两个指令共同读取

目的：

- 判断 `FREE_REG` 是在“第一个 consumer”还是“最后一个 consumer”之后出现

形式：

- `producer(v0)`
- `consumer_a(..., v0, ...)`
- `consumer_b(..., v0, ...)`

控制点：

- `consumer_a` 和 `consumer_b` 尽量使用不同 op，便于在日志中区分

判据：

- 如果 `FREE_REG` 跟着 `consumer_a`
  - 说明规则过宽，不需要等待所有 consumer
- 如果 `FREE_REG` 跟着 `consumer_b`
  - 说明至少要等最后一个真实 consumer

现有 case：

- `reg_release_two_consumers`


### E3: 同 iter overwrite

目的：

- 判断“同 iter 内 overwrite”是否构成旧 preg 的收口条件

形式：

- `producer(v0)`
- `use_a(v1, v0)`
- `overwrite(v0, v0)`
- `later_use(new_v0)`

关键点：

- 确认 `later_use` 读的是新 preg 还是旧 preg
- 重点不是看 vreg 名字，而是看 `p_idx`

判据：

- 如果旧 preg 在 `overwrite` 这条 consumer 的 `start + 4` 释放
  - 说明 overwrite consumer 本身就是最后一个旧 preg 使用者
- 如果还要等更后面的事件
  - 说明存在额外收口条件

现有 case：

- `reg_release_overwrite_chain`
- `reg_release_inplace_chain_u8_i64`


### E4: 跨 iter overwrite

目的：

- 判断“下一轮循环的 overwrite”是否会拖慢上一轮旧 preg 的释放

形式：

- 本 iter 产生 `v0`
- 本 iter 最后只读不写 `v0`
- 下一 iter 才第一次写回同名 `v0`

这是当前最怀疑会引入误差的一类场景。

判据：

- 如果 `FREE_REG` 必须等到下一 iter 的 overwrite 才出现
  - 说明跨 iter overwrite 会形成强收口
- 如果在本 iter 最后一个 consumer 后就释放
  - 说明 loop 边界可能本身就是一个隐式收口

需要新增 case：

- `reg_release_cross_iter_overwrite.dsl`


### E5: store-only terminal value

目的：

- 判断“末尾只有 VST 使用某个 preg”的情况下，释放是否跟 store 直接绑定

形式：

- `producer(v0)`
- 若干计算
- `vst(v0)`
- 本 iter 内不再出现该值

判据：

- 如果 `VST start + 4` 就触发 `FREE_REG`
  - 说明 store 也可直接承担收口
- 如果不行
  - 说明 store 型 consumer 可能有额外限制

现有 case：

- `reg_release_e4_store_only`


### E6: compute then store

目的：

- 判断同一个 preg 既被 compute consumer 用，又被 store consumer 用时，最终是跟谁走

形式：

- `producer(v0)`
- `compute(..., v0, ...)`
- `vst(v0)`

判据：

- 如果释放跟着 compute
  - store 没有参与旧 preg 生命周期
- 如果释放跟着 store
  - store 仍被视作最后一个 consumer

现有 case：

- `reg_release_e4_compute_then_store`


### E7: gap / spacing 实验

目的：

- 判断 `FREE_REG` 是否只依赖最后 consumer 本身，还是会受 consumer 之间的间隔影响

形式：

- `producer`
- `consumer`
- 中间插入 0 / 8 / 16 条无关指令
- 再观察 `FREE_REG`

判据：

- 如果始终锁定 `最后真实 consumer start + 4`
  - 说明只与真实依赖有关
- 如果 gap 会改变触发逻辑
  - 说明可能还有队列/记账/流水级联条件

现有 case：

- `reg_release_e3_gap0`
- `reg_release_e3_gap8`


### E8: 双 consumer 快慢混合

目的：

- 判断“最后一个 consumer”是按程序顺序算，还是按真实开始执行时间算

形式：

- `producer(v0)`
- `fast_consumer(..., v0, ...)`
- `slow_consumer(..., v0, ...)`

以及反过来：

- 程序顺序上 slow 在前、fast 在后

判据：

- 如果跟“真实最后开始使用旧 preg 的 consumer”走
  - 说明规则是动态的
- 如果跟“程序上最后一个 consumer”走
  - 说明规则更像静态封口

现有 case：

- `reg_release_e2_dual_slowvdiv`


### E9: loop 边界是否为隐式收口

目的：

- 验证 loop 结束是否会作为“本 iter 旧值不再被后续使用”的边界信号

形式：

- 构造一个值只在本 iter 内使用
- 本 iter 末尾不 overwrite
- 下一 iter 使用完全不同 vreg

判据：

- 如果本 iter 尾部后就能释放
  - loop 边界可能是隐式收口
- 如果仍然拖到后续 overwrite/rename
  - loop 边界不是收口条件

需要新增 case：

- `reg_release_loop_boundary_seal.dsl`


## Minimal New Cases To Add

为了尽快回答“有没有收口条件”，我建议优先只补 3 个新增 case：

1. `reg_release_cross_iter_overwrite`
   - 验证跨 iter overwrite 是否拖住释放

2. `reg_release_loop_boundary_seal`
   - 验证 loop 边界是否可单独作为收口

3. `reg_release_dual_consumer_reordered`
   - 验证释放跟“最后真实使用者”还是“程序顺序最后使用者”

这 3 个 case 跑出来，基本就能把当前最关键的不确定性收窄很多。


## How To Read The Result

每个 case 不只看总时间，主要看 4 类日志：

1. `instr_popped_log.dump`
   - 指令开始时间

2. `instr_log.dump`
   - 指令结束时间

3. `rvec.ISU.dump`
   - 是否出现 `ISU_SCB_FREE_REG`
   - 哪条指令触发
   - 释放的是哪个 `p_idx`

4. `rvec.IDU.dump`
   - 释放对 IDU 什么时候可见
   - preg 数量是否在 `FREE_REG` 后约 2 cycle 增加

最终要回答的不是“这条指令快不快”，而是：

- 哪个 `preg` 在什么时候被释放
- 它为什么恰好在那个时刻释放
- 是不是必须等某个额外的“封口事件”


## Expected Outcome

我预期这组实验最终会把规则收敛成下面两类之一：

### Model A: 最后真实 consumer 模型

- 某个旧 `preg` 的所有真实 consumer 都已经发出
- 最后一个真实 consumer 在 `start + 4` 发 `FREE_REG`
- 再过约 2 cycle，IDU 可见

### Model B: 最后真实 consumer + 收口模型

- 除了最后一个真实 consumer 到达 `start + 4`
- 还需要一个额外条件，例如：
  - 同 iter overwrite 已经发生
  - loop boundary 到达
  - 某类 store/rename 状态成立

当前更值得重点验证的是 Model B。


## Next Step

下一步最值得做的，不是再改模型，而是先把下面 3 个新增 case 补上并跑掉：

1. `reg_release_cross_iter_overwrite`
2. `reg_release_loop_boundary_seal`
3. `reg_release_dual_consumer_reordered`

这样我们就能比较干净地回答：

- `FREE_REG` 的触发是不是永远 `最后真实 consumer start + 4`
- “收口条件”到底是 overwrite、loop 边界，还是根本不存在
