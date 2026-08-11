# OOO 主线模型

本文说明当前公开的 OoO 模型接口。旧版本 VF Simulator 曾经暴露多个 `--ooo-model` 选项，例如 `consumer-done`、`default`、`last-use`、`npu-hybrid`、`queue_level1/2/3/4`。当前 `main.py` 已不再暴露这个 selector。

## 当前默认模型

当前公开主线只有一个 queue-level 模型：

- `queue_level4`
- consumer release rule：consumer start cycle + 4
- `vreg` 活跃范围规范化：开启
- `shq_depth = 58`
- `exq_depth = 26`
- `issue_ports = 2`
- `load_ports = 2`
- `store_ports = 1`
- `exq_issue_inflight_cap_per_port = 7`

因此默认 CLI 直接使用：

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/gelu_poly
```

CCE/DSL 输入使用：

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu_poly_cce
```

## 实现拆分

- `core/ooo_factory.py`：规范化 uarch 配置，并创建唯一支持的 OoO core。
- `core/uarch_normalize.py`：集中维护主线默认值和理论上界覆盖配置。
- `core/ooo_mainline.py`：负责重命名、物理寄存器生命周期、SHQ/LSQ/ROB、就绪计算、load/store 路径和源寄存器释放记录。
- `core/isu.py`：负责计算指令离开 SHQ 之后的路径，包括 SHQ 到 EXQ 入队、EXQ 仲裁、EXQ 到 EXU 启动、端口选择和 II 检查。

## 公开变体

当前活跃的公开变体是上界参考或实验模式，不是另一个真实硬件默认模型：

```bash
python main.py --trace VFtest/GeLU_poly.json \
  --theoretical-limit-vloop-only \
  --out_dir results/theory_vloop_only
```

```bash
python main.py --trace VFtest/GeLU_poly.json \
  --theoretical-limit-vloop-only-legacy-forwarding-direct-issue \
  --out_dir results/theory_direct_issue
```

```bash
python main.py --trace VFtest/GeLU_poly.json \
  --three-ports \
  --out_dir results/three_ports
```

## 历史说明

`consumer-done`、`default`、`last-use`、`npu-hybrid`、`queue_level1/2/3` 等历史名称仍然有助于理解旧报告和归档实验，但不应再被写成当前 `main.py` 的可用选项。

部分优化器脚本仍保留旧版 `--ooo-model` 参数。在这些脚本明确迁移到当前队列级主线前，应把它们看作优化器内部的兼容开关。

## 当前建议

正常模拟、回归和 API 使用都应直接使用默认队列级主线，不传模型选择参数。理论上界模式只用于估计优化上界；`--three-ports` 只用于实验三端口模型。
