# 寄存器释放规则说明

本文说明当前主线模型中物理寄存器的源操作数释放规则。

当前 active path 只保留一种源释放规则：

- `eligible_cycle = consumer_start + consumer_release_start_offset`
- `consumer_release_start_offset` 来自 `configs/uarch.json`
- 当前配置值为 `4`

历史上的 `consumer-done` 释放路径已经从主实现中移除。相关旧报告仍可用来理解模型演进，但不再代表当前代码口径。

---

## 1. 术语

- `producer`：写某个物理寄存器 `preg` 的指令
- `consumer`：读取该 `preg` 的后续指令
- `release`：把该 `preg` 放回空闲池，可被后续 rename 复用
- `generation`：`preg` 的分配版本，用来识别延迟释放事件是否仍对应同一次分配

这里讨论的是源寄存器回收，不是指令完成、retire 或目的寄存器覆盖释放。

---

## 2. 当前规则

当一条 consumer 指令 start 后，模型按固定 offset 为它的每个源 `preg` 安排释放事件：

```text
eligible_cycle = consumer.start_cycle + consumer_release_start_offset
```

事件到达时，只有满足以下条件才会真正释放：

- `(preg, generation)` 与事件记录一致
- 该 `preg` 不再是当前 RAT 映射
- `preg_consumer_count[preg] == 0`
- 该 `preg` 不在 pending 状态
- 当前 cycle 已达到 `preg_release_eligible_cycle`

实现位置：

- [`core/ooo_mainline.py`](/mnt/e/VfSimulator_structure/core/ooo_mainline.py)
- [`configs/uarch.json`](/mnt/e/VfSimulator_structure/configs/uarch.json) 中的 `consumer_release_start_offset`

---

## 3. 为什么需要 generation

源释放事件是延迟触发的。若一个 `preg` 已经被回收并重新分配，旧事件晚到时不能释放新的映射。

因此事件绑定 `(preg, generation)`：

- generation 一致：该事件仍对应同一次分配，可以继续检查并释放
- generation 不一致：该事件已经过期，直接丢弃

这保证了 start-based 释放不会误伤重新分配后的物理寄存器。

---

## 4. 和历史规则的区别

旧 `consumer-done` 规则按最后一个 consumer 的 done cycle 设置释放资格：

```text
eligible_cycle = last_consumer_done + consumer_done_release_delay
```

这条路径曾用于早期对比实验，但当前主线已经不再提供对应开关，也不再读取：

- `consumer_release_from_start`
- `consumer_done_release_delay`
- `release_done_delay`

当前模型始终使用 `consumer.start_cycle + consumer_release_start_offset`。

---

## 5. 和 SHQ/LSQ 的关系

寄存器释放规则独立于 SHQ/LSQ credit 释放规则。

当前 LSU/compute 分类来自 ISA 配置：

- `LOAD` 指令走 LSQ，不占共享 SHQ credit
- `STORE` 指令走 LSQ，并占共享 SHQ credit
- `COMPUTE` 指令走 SHQ/EXQ/EXU

具体分类由 [`core/isa_traits.py`](/mnt/e/VfSimulator_structure/core/isa_traits.py) 读取 `configs/isa.json` 的 `op_class` 字段决定。
