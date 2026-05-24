#!/usr/bin/env bash
set -euo pipefail
star=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/tools/simulator/Ascend950PR_9599/camodel/libstars.so
echo '=== NEEDED ==='
readelf -d "$star" | grep NEEDED || true
echo '=== RUNPATH/RPATH ==='
readelf -d "$star" | egrep 'RPATH|RUNPATH' || true
echo '=== UNDEFINED hard_code_toml_cfg_map ==='
readelf -Ws "$star" | grep 'hard_code_toml_cfg_map' || true
echo '=== LDD ==='
ldd "$star" || true