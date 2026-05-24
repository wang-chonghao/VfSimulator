#!/usr/bin/env bash
set -euo pipefail
f=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libcommon.so
echo '=== NEEDED ==='
readelf -d "$f" | grep NEEDED || true
echo '=== LDD ==='
ldd "$f" || true