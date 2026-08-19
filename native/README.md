# Native VF 模拟器核心

本目录包含 VF 模拟器的 C++ 核心实现。

当前约定：

- `configs/*.json` 是模型参数的唯一事实来源。
- C++ 启动时读取并缓存与 Python 相同的 JSON 配置。
- 内部使用轻量 JSON 解析器，避免为基础配置读取引入额外第三方依赖。
- 不在手写 C++ 常量中重复配置值。
- C++ 路径完全对齐前，以 Python 主线行为作为一致性基准。
- 一个加载完成的 `ParamDB` 可以跨预测线程共享：参数 bundle 只读，fallback
  `InstConfig` 按值返回，warning 聚合受互斥保护。forwarding/II 热路径 cache 属于
  每个 `OoOCore`，不写共享 `ParamDB`。

核心模块：

- `ParamDB`：读取 `isa.json`、`uarch.json`、`forwarding.json` 和
  `InitiationInterval.json`。
- `ISATraits`：根据 ISA 元数据判断 load、store 和 compute 行为。
- `ProgramAnalysis`：解析循环边界、unroll 参数和 vreg 容量告警。
- `ProgramCanonicalization`：在仅剩一个 super-iteration 时展开最内层循环。
- `ProgramFlatten`：递归展平 loop/inst 程序树。
- `IFU`：动态展开循环并生成 top-block 元数据。
- `IDU`：执行 dispatch gate、VLOOP 可见性和信用计数。
- `OOO`：执行 rename、ready、execute 和 retire 主流程。
- `SimulatorRunner`：驱动主循环并输出日志。
- `CanonicalProgramLowering`：处理已验证的 canonical definition、loop-carried
  binding、动态身份和值生命周期标记。
- `LegacyVfInfoAdapter`：把旧 `VfInfo`/legacy JSON 的逻辑值版本化为 canonical
  definition；旧输入不会形成第二套 Core 执行路径。
- 共享配置结构：显式、可移植地描述跨语言配置 schema。

编译器公开运行入口只有一个：

- `runCanonicalVfInfo()`：canonical 直接入口，跳过 legacy value lowering、vreg
  live-range normalization 和 single-super-iteration rewriting。

旧 JSON runner 和回归测试如需使用 `VfInfo`，必须在调用点显式执行
`adaptLegacyVfInfoToCanonical()`，再调用 `runCanonicalVfInfo()`；`SimulatorRunner.h`
不再公开 legacy overload，也不存在第二套 Core 执行路径。
