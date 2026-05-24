#!/usr/bin/env bash
set -euo pipefail
f=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libUtility.so
readelf -Ws "$f" | awk '$4=="NOTYPE" || $4=="FUNC" || $4=="OBJECT" {print}' | grep ' UND ' | head -n 120