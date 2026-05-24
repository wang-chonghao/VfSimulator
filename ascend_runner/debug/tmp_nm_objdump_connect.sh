#!/usr/bin/env bash
set -euo pipefail
LIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libChiRingFabric.so
nm -D --defined-only "$LIB" | c++filt | grep 'TMultiRing::connectRingBridge' || true
objdump -dC "$LIB" | grep -n 'TMultiRing::connectRingBridge' -A 220 || true
