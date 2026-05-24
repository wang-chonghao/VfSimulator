#!/usr/bin/env bash
set -euo pipefail
LIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libChiRingFabric.so
objdump -dC --start-address=0xa4d30 --stop-address=0xa5200 "$LIB"
