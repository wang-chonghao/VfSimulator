# 寄存器释放规则说明（consumer-done vs start+5）

本文只讲一件事：**物理寄存器什么时候可以回收复用**。  
当前项目里主要有两套规则：

- `consumer-done`（原始默认思路）
- `start+5`（start-based 规则，当前在 `devstart5` 分支重点实验）

---

## 1. 术语统一

- `producer`：写某个物理寄存器 `preg` 的指令
- `consumer`：读取该 `preg` 的后续指令
- `last consumer`：最后一个使用该 `preg` 的 consumer
- `release`：把该 `preg` 放回空闲池，可被新重命名分配

注意：这里说的是**源寄存器回收**（producer 的旧 `preg` 何时回收），不是指令完成本身。

---

## 2. consumer-done 规则

### 2.1 定义

`preg` 的回收资格时间按最后一个 consumer 的 `done` 来定：

- `eligible_cycle = last_consumer_done + consumer_done_release_delay`
- 常见配置：`consumer_done_release_delay = 0`

只有到 `eligible_cycle` 及之后，且该 `preg` 的引用计数已清零，才会真正进入 free list。

### 2.2 直觉

这是偏保守、稳定的规则。  
意思是“最后一个读它的指令彻底执行完，再允许回收”。

### 2.3 代码位置

- 主实现：`core/ooo_consumer_done.py`
- 关键字段：
  - `consumer_done_release_delay`
  - `preg_release_eligible_cycle`

---

## 3. start+5 规则（start-based）

### 3.1 定义

`preg` 的回收资格不再等 consumer done，而是锚定 consumer start：

- `eligible_cycle = consumer_start + consumer_release_start_offset`
- 当前实验口径通常为：`consumer_release_start_offset = 5`

即“consumer 开始后固定若干拍（例如 +5）就可触发源回收资格”。

### 3.2 直觉

这是比 `consumer-done` 更激进的回收。  
对应你们在 CCE 观察到的“源数据在执行早期已被消费，未必需要等 done 才回收”的建模方向。

### 3.3 代码位置

- 主实现：`core/ooo_consumer_done.py`
- 开关与参数：
  - `consumer_release_from_start = true`
  - `consumer_release_start_offset = 5`

---

## 4. 为什么 start+5 需要“版本号（generation）”

在 start-based 模式里，回收事件是“延迟触发”的。  
若一个 `preg` 已经被回收并重新分配，旧事件晚到就可能误伤新映射。

所以实现里对事件做了 `(preg, generation)` 绑定：

- 事件触发时先校验 `generation`
- 只有版本一致才减计数/回收
- 版本不一致视为过期事件，丢弃

这一步是为了避免“旧事件释放新寄存器”的错误。

---

## 5. 两套规则的核心差别

- `consumer-done`：以 `done` 为锚点，偏保守
- `start+5`：以 `start + offset` 为锚点，偏激进

可以简单理解为：

- `consumer-done` 更不容易误回收，但可能高估寄存器压力
- `start+5` 更贴近“早消费早回收”假设，但实现需要更严格的一致性保护

---

## 6. 命令行参数对应关系

`main.py` 支持下列控制项：

- `--consumer-release-from-start` / `--no-consumer-release-from-start`
- `--consumer-release-start-offset <N>`
- `--consumer-done-release-delay <N>`

常见组合：

1. consumer-done（传统）
   - `--no-consumer-release-from-start`
   - `--consumer-done-release-delay 0`
2. start+5
   - `--consumer-release-from-start`
   - `--consumer-release-start-offset 5`

---

## 7. 建议的口径约束（避免再出现“同名不同数据”）

每次跑数必须同时固定以下四项：

1. Git commit
2. `configs/*.json` 版本
3. CLI 参数（特别是上面三个 release 参数）
4. 输出目录（单一来源，不混批次拼接）

这样 `consumer-done` 和 `start+5` 的结果才能可复现、可对比。

---

## 8. queue_level2 规则（当前代码口径）

`queue_level2` 不是新的寄存器释放规则，而是在 `start+5` 或 `consumer-done` 的寄存器释放规则外，再加一层 `SHQ` 容量与释放建模。

### 8.1 它新增了什么

- `SHQ` 有限容量
- 非 `VLD` 指令进入 `SHQ` 后会占用 `SHQ credit`
- `SHQ credit` 不是永久占用，而是会在后续某个时刻释放

### 8.2 哪些指令占用 SHQ

按当前实现：

- 计算指令占用 `SHQ`
- `VST` 占用 `SHQ`
- `VLD` 不占用 `SHQ`

### 8.3 SHQ 何时释放

当前代码里，`queue_level2_shq_release_delay` 默认是 `1`。

可理解为：

1. 计算指令从 `SHQ` 发往 `EXQ` 后  
   过 `queue_level2_shq_release_delay` 个 cycle，释放 1 个 `SHQ credit`
2. `VST` 开始执行后  
   过 `queue_level2_shq_release_delay` 个 cycle，释放 1 个 `SHQ credit`

所以 `queue_level2` 的本质是：

- 继续使用原本的寄存器释放规则
- 另外再限制 `SHQ` 容量，并给 `SHQ` 单独做一个延迟释放

### 8.4 它没有新增什么

`queue_level2` 当前**没有**显式建模：

- `IDU -> OOO/SHQ` 的 2-cycle 传输延迟
- `OOO -> IDU` 的 credit 可见延迟

也就是说，`queue_level2` 主要关注的是：

- `SHQ` 会不会满
- `SHQ` credit 什么时候回来

而不是“IDU 什么时候看见它回来”。

---

## 9. queue_level3 规则（当前代码口径）

`queue_level3` 是在 `queue_level2` 基础上，再加一层“credit 回传到 IDU 的可见延迟”。

### 9.1 它新增了什么

当前默认：

- `queue_level3_idu_visible_delay = 2`

意思是：

- `OOO` 侧虽然已经释放了资源
- 但 `IDU` 还要再过 2 个 cycle 才能“看见”这个资源变空

### 9.2 哪些资源有可见延迟

当前实现里有两类：

1. `preg free`
   - 物理寄存器真正回收到 free list 后
   - 不会立刻让 `IDU` 看见
   - 而是 `+2 cycle` 后进入 `visible_preg_free`
2. `SHQ credit release`
   - `SHQ` credit 真正释放后
   - 不会立刻让 `IDU` 看见
   - 而是 `+2 cycle` 后进入 `visible_shq_release_events`

### 9.3 它带来的效果

这会让 `queue_level3` 比 `queue_level2` 更保守：

- 从 `OOO` 视角看，资源已经释放
- 从 `IDU` 视角看，这个释放信息还在路上

因此在某些周期里，`IDU` 会因为“还没看到 credit 回来”而停止继续发射。

### 9.4 它和寄存器释放规则的关系

要注意区分两件事：

1. `preg` 什么时候**真正释放**
   - 这由 `consumer-done` 或 `start+5` 决定
2. `IDU` 什么时候**看见释放结果**
   - 这由 `queue_level3_idu_visible_delay` 决定

所以：

- `start+5` 决定“释放发生的时机”
- `queue_level3` 决定“前端什么时候观察到这个释放”

### 9.5 已确认的实现 bug：`visible_delay = 0` 时当前拍事件丢失

这个 bug 只会出现在 `queue_level3`，不会影响：

- `consumer-done`
- 纯 `start+5`
- `queue_level1`
- `queue_level2`

触发条件：

- 开启 `queue_level3`
- 并且把下面任意一个 delay 设成 `0`
  - `queue_level3_preg_visible_delay = 0`
  - `queue_level3_shq_visible_delay = 0`

旧实现中的问题：

- `update_idu_visibility(cycle)` 是在每个 cycle 开头执行的
- 如果某个 release 事件在同一个 cycle 内被安排到 `t = current_cycle + 0`
- 这个事件会进入 `visible_preg_free_events[current_cycle]` 或 `visible_shq_release_events[current_cycle]`
- 但由于本拍的 `update_idu_visibility()` 已经跑完，这条事件不会再被消费
- 结果就是“本来应该立即可见的 free preg / free SHQ credit 永远不再可见”

表现：

- `IDU` 侧 credit 会异常偏紧
- 模型会出现不合理的大空泡
- 严重时可能长时间卡住，导致结果明显失真

修复方式：

- 当 `visible_delay <= 0` 时，不再把事件排入 `visible_*_events[current_cycle]`
- 而是直接当拍更新：
  - `visible_preg_free += released_count`
  - `visible_shq_used -= released_count`

结论：

- 这是 `queue_level3 + zero visible delay` 组合特有的实现 bug
- 不是 release 原理本身的问题
- 后续如果继续扫 `level3` 参数，必须保留这个修复，否则 `0-delay` 的结果不可信

---

## 10. 三种口径的关系

### 10.1 start+5（无 queue）

- 只关心 `preg` 何时可回收
- 不关心 `SHQ` 容量
- 不关心 credit 回传延迟

### 10.2 start+5 + queue_level2

- 关心 `preg` 何时可回收
- 关心 `SHQ` 容量
- 关心 `SHQ` credit 何时释放
- 不关心 `IDU` 看到 release 的延迟

### 10.3 start+5 + queue_level3

- 关心 `preg` 何时可回收
- 关心 `SHQ` 容量
- 关心 `SHQ` credit 何时释放
- 还关心 `IDU` 什么时候看见这些 release

可以用一句话概括：

- `start+5`：只管“释放”
- `level2`：在“释放”之外，再管 `SHQ`
- `level3`：在 `level2` 之外，再管“release 信息多久被 IDU 看见”
