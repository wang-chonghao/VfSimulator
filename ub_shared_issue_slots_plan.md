# UB 共享发射槽建模方案

## 1. 背景与当前误差

当前 Python OoO 模型把 load 和 store 的发射带宽分开处理：

- 每周期先从 LSQ 启动最多 `load_ports=2` 条 load；
- 计算指令发射后，再从 LSQ 启动最多 `store_ports=1` 条 store。

两个限制彼此独立，因此同一周期可能同时启动 `2 VLD + 1 VST`。根据当前硬件日志和微基准观察，UB 访问侧实际共享两个槽位，合法组合应为：

- `2 VLD`；
- `1 VLD + 1 VST`；
- `1 VLD`；
- `1 VST`。

不应出现 `2 VLD + 1 VST`。现有模型因此高估了 load/store 混合循环的 UB 吞吐。

两个 64 次循环微基准进一步说明了这个问题：

| 微基准 | UB 访问数 | CAModel | 修改前 VfSim | 现象 |
|---|---:|---:|---:|---|
| `A = B + C` | 128 load + 64 store | 160 cycle | 118 cycle | VfSim 允许 2 load 与 1 store 同拍启动 |
| `A = B + 0.1` | 64 load + 64 store | 119 cycle | 118 cycle | 每拍 1 load + 1 store 正好不超过两个共享槽 |

`A = B + C` 共 192 次 UB 访问。若只考虑两个共享槽，其理论下限是 96 个访问周期；原模型可在 64 个周期内分别消耗 load/store 端口，少计约 32 个访问周期。CAModel 中还观察到 store 等待、STU 输入缓冲积压以及物理寄存器压力，但这些属于后续阶段，本次先隔离共享槽问题。

## 2. 开发目标

在 Python 和 C++ 主线 OoO 模型中增加统一的共享 UB 发射资源：

```json
"ub_slots": 2
```

每个周期同时满足：

```text
issued_loads  <= load_ports
issued_stores <= store_ports
issued_loads + issued_stores <= ub_slots
```

默认配置为：

```text
load_ports  = 2
store_ports = 1
ub_slots    = 2
```

本次不修改指令 latency、forwarding、LSQ 容量、SHQ/EXQ 分发和物理寄存器释放规则。

## 3. 寄存器压力阈值仲裁

### 3.1 候选集合

每周期从 LSQ 收集满足以下条件的 load/store：

1. 指令已到达 `lsq_ready_cycle`；
2. 寄存器数据依赖和 forwarding 条件已满足；
3. store 已找到可用的生产者；
4. 没有被显式 `Membar` 阻塞；
5. 尚未启动或完成。

未 ready 的老指令不会阻塞后续已 ready 指令。这保持 LSQ 的乱序就绪/发射能力，避免把该策略误实现为严格队头 FIFO。

### 3.2 类别优先级

当真实空闲物理寄存器数不小于
`lsu_store_priority_preg_threshold` 时，load 类优先；低于阈值时，store
类优先。默认阈值为 1，即 freelist 为空时切换为 store 优先。

当前实现只保留这一套仲裁规则，不再提供 `oldest_ready` 策略开关。

### 3.3 年龄定义

候选指令按以下键升序排列：

```text
(stream_seq, inst_id)
```

`stream_seq` 是 IFU 展开后的动态程序序，能够区分循环不同迭代；`inst_id` 只作为稳定的次级排序键。先按当前类别优先级排序，同一类别内再按该动态年龄排序，不使用静态 PC。

### 3.4 选择规则

从最老候选开始依次扫描：

1. 共享槽已达到 `ub_slots` 时停止；
2. load 已达到 `load_ports` 时跳过该 load，继续检查后续 store；
3. store 已达到 `store_ports` 时跳过该 store，继续检查后续 load；
4. 其余候选获得本周期启动许可。

一个尚未 ready 的老 store 允许年轻 ready load 绕过。当未达寄存器压力阈值时，ready load 可以越过 ready store；达到压力阈值后，store 优先获得最多一个 store 端口，剩余共享槽仍可发射 load。

示例动态流：

```text
L0, L1, S0, L2, L3, S1, L4, L5
```

若这些指令均 ready 且尚有空闲物理寄存器，仲裁先选 load；当 freelist 为空时，先选最老 ready store，然后用剩余槽位选最老 ready load。

```text
cycle N:   L0 + L1
cycle N+1: S0 + L2
cycle N+2: L3 + S1
cycle N+3: L4 + L5
```

这正是本阶段希望建立的“两个 UB 槽 + 年龄公平”行为。

## 4. 周期内顺序

当前 `step()` 会先更新本周期完成事件，再计算就绪并执行发射。为让 load/store 使用同一个候选快照，修改后采用：

1. 处理本周期完成和 retire；
2. 更新 LSQ/SHQ ready；
3. 对当前 ready 的 load/store 执行第一次共享阈值仲裁；
4. 重新计算 compute ready 并发射计算指令，保留 load 启动后 forwarding 在本周期可见的旧时序；
5. 再次更新 store ready，使本周期计算启动产生的 forwarding 与旧模型一致；
6. 使用本周期剩余的共享槽和类别端口额度执行第二次仲裁。

两次仲裁共享同一组计数，不会各自获得新的两个槽。load 和 store 仍记录原有 `start_cycle`、`done_cycle`、源寄存器释放和 SHQ credit 事件。仅改变同周期谁能获得 UB 发射槽。

## 5. 非目标与已知边界

本阶段明确不建模：

- 深度为 6 的 `STU_IB_BUF`；
- store 从接收、排队、issue 到 pop 的内部流水级；
- UB bank conflict；
- 不同访存模式的端口占用周期差异；
- store drain 和 VF 尾部排空的额外规则；
- 更深的 C++ STU 内部缓冲建模。

因此，即使 `A = B + C` 从约 118 cycle 上升到接近 150 cycle，也不要求第一阶段立即吻合 CAModel 的 160 cycle。剩余误差需要通过日志判断是否来自 STU backpressure、物理寄存器压力或 store 尾部排空，不能继续堆叠未经验证的限制。

## 6. 影响文件

- `configs/uarch.json`
  - 增加默认 `ub_slots=2`。
- `configs/uarch_override_schema.json`
  - 声明 Python/C++ 共享的 `ub_slots` 和寄存器压力阈值。
- `core/ooo.py`
  - 读取并校验共享槽配置。
- `core/ooo_mainline.py`
  - 合并 load/store 的独立发射循环，实现共享阈值仲裁。
- `native/ParamSchema.h`、`native/ParamDB.cpp`、`native/OOO.h`、`native/OOO.cpp`
  - 同步 C++ 配置和两阶段 LSU 共享槽仲裁。
- `tests/test_ub_shared_issue_slots.py`
  - 增加共享上限、组合约束、年龄公平和未 ready 绕过测试。
- `VF_modeling.md`
  - 更新主线 LSU 发射资源说明。

## 7. 验收标准

1. 任意周期满足 `load_start_count + store_start_count <= 2`。
2. 单周期最多 2 条 load、最多 1 条 store。
3. 有空闲寄存器时 load 优先，低于阈值时 store 优先，同类内保持动态年龄顺序。
4. blocked 的老 store 不阻塞年轻 ready load。
5. `Membar` 行为保持不变，被 barrier 阻塞的 LSU 不占共享槽。
6. Python 单元测试与回归测试通过。
7. 重新运行 `A=B+C` 和 `A=B+0.1`：
   - 前者周期应明显上升；
   - 后者应基本不变；
   - 记录与 CAModel 的剩余差异，不用 STU 特例掩盖。

## 8. 历史仲裁对比实验

开发过程中曾将 `oldest-ready` 与寄存器压力阈值策略做 A/B 对比。该结果用于选择当前主线规则，`oldest-ready` 已从代码和配置契约中删除。当前固定规则为：

- 真实空闲物理寄存器数不小于 1 时，ready load 排在 ready store 前面；
- 真实空闲物理寄存器数小于 1，即 freelist 为空时，ready store 排在 ready load 前面；
- 类别内部仍按 `(stream_seq, inst_id)` 保序；
- 共享槽和 load/store 各自端口上限不变。

该实验用于判断硬件是否会长期偏向 load，只在寄存器分配即将停顿时通过 store 启动释放其源寄存器。它不是 STU 缓冲模型，也不会根据 store 队列深度主动切换优先级。

## 9. VADD/VDIV 循环长度实验

### 9.1 实验设置

每轮动态指令固定为：

```text
VLDS B[i]
VLDS C[i]
VADD/VDIV A[i], B[i], C[i]
VSTS A[i]
```

循环次数取 `16/48/64/96`。比较对象为：

- CAModel 日志中的 `vf_execute_time`；
- `oldest_ready`；
- `load_priority_store_on_preg_pressure`，阈值为 1。

所有 case 使用同一个 CCE 模板和编译参数。`VADD I=64` 重新编译后的 CAModel 结果仍为 160 cycle，与既有锚点一致。VDIV 的通用 host 输入包含零值，CAModel 报除零诊断，但 VDIV 指令仍正常进入流水线并产生 `vf_execute_time`；本实验只比较时序。

### 9.2 周期与误差

| 指令 | 循环 | CAModel | oldest-ready | 误差 | threshold=1 | 误差 |
|---|---:|---:|---:|---:|---:|---:|
| VADD | 16 | 71 | 75 | +4 / 5.63% | 75 | +4 / 5.63% |
| VADD | 48 | 133 | 122 | -11 / 8.27% | 136 | +3 / 2.26% |
| VADD | 64 | 160 | 146 | -14 / 8.75% | 165 | +5 / 3.12% |
| VADD | 96 | 219 | 194 | -25 / 11.42% | 224 | +5 / 2.28% |
| VDIV | 16 | 95 | 94 | -1 / 1.05% | 94 | -1 / 1.05% |
| VDIV | 48 | 159 | 158 | -1 / 0.63% | 158 | -1 / 0.63% |
| VDIV | 64 | 191 | 190 | -1 / 0.52% | 190 | -1 / 0.52% |
| VDIV | 96 | 255 | 254 | -1 / 0.39% | 254 | -1 / 0.39% |

聚合结果：

| 策略 | MAE | MAPE | 最大绝对误差 | 平均有符号误差 |
|---|---:|---:|---:|---:|
| oldest-ready | 7.25 cycle | 4.58% | 25 cycle | -6.25 cycle |
| threshold=1 | 2.63 cycle | 1.99% | 5 cycle | +1.63 cycle |

### 9.3 初步结论

在这 8 个定向 case 上，`threshold=1` 的总体精度明显高于 `oldest-ready`：

- VADD 中长循环会形成持续的双 load 压力，纯 oldest-ready 仍偏乐观，且循环越长低估越明显；
- load 优先策略能够复现 CAModel 中先供给计算、寄存器耗尽后处理 store 的行为，VADD 误差收敛到 3～5 cycle；
- VDIV 的 SFU 执行/发射节奏成为主要约束，两种 LSU 策略没有造成最终周期差异；
- 当前样本支持阈值策略，主线实验配置采用 `threshold=1`。后续仍应增加不同 load/store 比例、单源计算和多条 store 的 case，持续检查该阈值的泛化性。
