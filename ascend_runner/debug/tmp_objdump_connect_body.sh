#!/usr/bin/env bash
set -euo pipefail
LIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libChiRingFabric.so
objdump -dC "$LIB" | grep -n -A 260 'totem_v2_chi_ring::TMultiRing::connectRingBridge(totem_v2_chi_ring::TConnectNode, bool)' || true
