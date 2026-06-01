# Ascend Runner Layout

`ascend_runner` 现在按三层组织：

- `current/`
  当前已经跑通、仍在使用的主线脚本。
- `legacy/`
  之前尝试过的旧链路，包括早期 `msprof` / PTO / 特化 host runner。
- `debug/`
  历史排障脚本、shim、gdb/strace/wsl 检查工具。
- `build/`
  历次 build 产物目录，按 case 分开保留。

## 当前主线

当前推荐且已验证的链路是：

1. `dsl/.cce`
2. `ccec`
3. `ld.lld`
4. direct sim executable linked with `runtime_camodel`
5. host-side golden check

主入口文件：

- [build_native_simexec.sh](d:/VfSimulator/ascend_runner/current/build_native_simexec.sh)
- [run_native_simexec.sh](d:/VfSimulator/ascend_runner/current/run_native_simexec.sh)
- [native_runtime_generic_main.cpp](d:/VfSimulator/ascend_runner/current/native_runtime_generic_main.cpp)
- [CCE_simulator_src_fanout_probe_guide.md](d:/VfSimulator/ascend_runner/CCE_simulator_src_fanout_probe_guide.md)

## 历史材料

如果要回看之前试错过程，可以到：

- `legacy/`
- `debug/`

这些文件目前不再作为主线入口，但保留下来供参考。
