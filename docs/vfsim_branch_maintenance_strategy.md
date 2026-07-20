# VfSimulator Repository Maintenance Strategy

本文记录 VfSimulator 仓库的长期维护方式。目标是让 Python VfSim、C++ native VfSim、TileSim 集成和 PTOAS 集成都能共享同一套模型语义，同时避免在多个分支重复修改核心逻辑。

## 当前状态

| 项目 | 当前情况 | 是否满足目标 |
| --- | --- | --- |
| Python VfSim | 主体代码在 `api/`、`core/`、`configs/`、`main.py` | 满足 |
| C++ native VfSim | 已放在 `native/`，用于 PTOAS 源码级接入 | 基本满足 |
| TileSim 集成分支 | `VfSim-tilesim` 已存在，用于 Python 打包和 TileSim glue code | 基本满足 |
| PTOAS 集成分支 | `cpp-native-vfsim` / PTOAS submodule 方向已存在 | 基本满足 |
| 主分支统一维护 | 当前 `master` 尚未完全统一 Python+C++ 两套源码 | 未完全满足 |
| 回归约束 | 已有 regression/report 目录，但还需要固定跨语言 golden case | 未完全满足 |

结论：当前仓库已经具备目标结构的雏形，但还需要把 C++ native 实现合入主分支，并明确各集成分支只维护适配层。

## 目标结构

短期保持当前目录布局，避免大规模重构：

```text
VfSimulator/
  api/              Python API 与 CCE/JSON 转换入口
  core/             Python VfSim 核心模型
  configs/          指令参数与 forwarding 配置
  native/           C++ native VfSim，用于 PTOAS 等源码级接入
  regression_suite/ 回归输入、报告和对齐结果
  docs/             设计文档与维护策略
```

长期如果需要清理包结构，可以再演进为：

```text
VfSimulator/
  python/vfsimulator/
  native/
  configs/
  integrations/
    tilesim/
    ptoas/
  regression_suite/
  docs/
```

目录重构不是当前优先级。当前优先级是先让主分支同时维护 Python 与 C++ 两套实现。

## 分支职责

| 分支 | 角色 | 应该包含 | 不应该包含 |
| --- | --- | --- | --- |
| `master` | 唯一主线 | Python VfSim、C++ native VfSim、configs、核心测试、通用文档 | 项目专用临时 patch |
| `dev/*` 或测试分支 | VfSim 迭代开发 | 新指令、新参数、新调度逻辑、实验性修复 | 长期给外部项目依赖 |
| `VfSim-tilesim` | TileSim 集成分支 | Python packaging、TileSim wrapper、TileSim smoke test | 独立修改核心模型语义 |
| `cpp-native-vfsim` | C++ native 集成准备分支 | C++ native 实现、PTOAS 所需接口验证 | 与主分支长期分叉 |
| PTOAS 中的 submodule 引用 | PTOAS 消费端固定版本 | 指向 VfSimulator 的某个 commit/tag | 直接复制一份失控源码 |

推荐最终状态：

```text
dev/*
  -> master
      -> rebase VfSim-tilesim
      -> rebase cpp-native-vfsim / PTOAS submodule update
```

## 集成方式

### TileSim

TileSim 使用 Python VfSim。

```text
TileSim
  -> VfSim-tilesim branch
      -> Python package / wrapper
      -> api/core/configs
```

约定：

- TileSim 分支只维护打包、路径和接口适配。
- VfSim 核心语义修改先进入 `master`。
- `VfSim-tilesim` 通过 rebase `master` 获取更新。
- rebase 后必须跑 TileSim packaging 和最小调用测试。

### PTOAS

PTOAS 使用 C++ native VfSim，并通过 submodule 管理源码归属。

```text
PTOAS
  -> 3rdparty/VfSimulator submodule
      -> native/
      -> configs/
```

约定：

- PTOAS 不复制 VfSim 源码，而是记录一个 submodule commit。
- PTOAS 编译时从 submodule 的 `native/` 编译 C++ costmodel。
- PTOAS 与 VfSim 的接口协议使用 IR 形式，不直接暴露 PTOAS 内部指针。
- VfSim 返回 fusion plan / unroll 信息后，由 PTOAS pass 写回 IR attr。

PTOAS submodule 更新流程：

```bash
cd PTOAS/3rdparty/VfSimulator
git fetch origin
git checkout <target-commit-or-tag>

cd ../..
git add 3rdparty/VfSimulator
git commit -m "Update VfSimulator submodule"
```

## 核心修改流程

VfSim 新功能或参数更新应按下面流程进入各消费端：

```text
1. 从 master 拉出 dev 分支
2. 在 dev 分支修改 Python/C++/configs
3. 跑 Python 回归与 C++ native smoke test
4. 合入 master
5. rebase VfSim-tilesim 并跑 TileSim smoke test
6. 更新 PTOAS submodule 指向 master 的新 commit/tag
7. 跑 PTOAS costmodel smoke test
```

推荐命令：

```bash
git switch master
git pull --ff-only origin master

git switch -c dev/<feature-name>
# 修改、测试、提交

git switch master
git merge --ff-only dev/<feature-name>
git push origin master

git switch VfSim-tilesim
git rebase master
# 跑 TileSim 验证
git push --force-with-lease origin VfSim-tilesim
```

## 什么内容进主分支

| 修改类型 | 归属 |
| --- | --- |
| opcode 支持范围 | `master` |
| latency / II / forwarding 参数解释 | `master` |
| configs schema | `master` |
| Python simulator 调度语义 | `master` |
| C++ native simulator 调度语义 | `master` |
| CCE/JSON 转换公共逻辑 | `master` |
| TileSim 包装脚本 | `VfSim-tilesim` |
| PTOAS CMake/submodule 接入 | PTOAS 仓 |
| PTOAS IR adapter | PTOAS 仓 |

## 回归要求

每次修改核心模型后，至少保留以下对齐验证：

| 验证项 | 目的 |
| --- | --- |
| Python VfSim regression | 保证 Python 主实现结果不漂移 |
| C++ native smoke test | 保证 native 版本可编译、可调用 |
| Python vs C++ golden case | 保证两套实现周期数一致 |
| TileSim packaging smoke test | 保证 `VfSim-tilesim` 仍可被 TileSim 调用 |
| PTOAS costmodel smoke test | 保证 submodule 版本可被 PTOAS 编译和调用 |

建议 golden case 起步集合：

| Case | 覆盖点 |
| --- | --- |
| single `vadd` | 单指令基础调度 |
| `vadd -> vmul` | producer-consumer forwarding |
| `tadd + tadd + tadd` | elementwise fusion |
| `tadd -> tcvt -> tadd` | dtype 随指令变化 |
| GeLU_poly | 多 elementwise op 与 unroll 搜索 |
| 含 `vbr/vdiv` 的 softmax prepare | scalar broadcast 与除法模板 |

## 版本固定

对外集成建议使用 tag 或明确 commit，不直接依赖浮动分支。

推荐 tag 命名：

```text
vfsim-core-v0.x
vfsim-tilesim-v0.x
vfsim-native-ir-v0.x
```

PTOAS submodule 应固定到一个经过验证的 commit 或 tag。这样即使 VfSimulator 主线继续迭代，PTOAS 的可复现构建也不会被破坏。

## 当前待完成事项

| 事项 | 说明 |
| --- | --- |
| 合并 C++ native 到 `master` | 让 `master` 同时维护 Python 与 C++ 实现 |
| 固定 Python/C++ golden case | 防止两套实现行为漂移 |
| 清理集成分支职责 | `VfSim-tilesim` 只保留 TileSim 适配 |
| 固定 PTOAS submodule 更新流程 | PTOAS 只记录 VfSimulator commit，不复制源码 |
| 后续再评估目录重构 | 当前不急于移动 `api/`、`core/` 等目录 |

