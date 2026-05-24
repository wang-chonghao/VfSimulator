#!/usr/bin/env bash
set -e
nm -D /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libChiRingFabric.so | grep 'TCrossStation' | head -n 60 || true
echo '===='
nm -D /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libAXI_STREAM_BUS.so | grep 'TNodeWrrSchdMgr' | head -n 60 || true
