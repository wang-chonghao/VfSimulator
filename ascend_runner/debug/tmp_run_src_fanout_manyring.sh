#!/usr/bin/env bash
set -u
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
LOG=$APPDIR/run_manyring.log
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
cp -f /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/libshim_cfg_manyring.so "$APPDIR/"
export LD_LIBRARY_PATH="$APPDIR:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPDIR/libshim_cfg_manyring.so"
cd "$APPDIR"
./src_fanout_probe_cce_min > "$LOG" 2>&1
code=$?
echo EXIT_CODE=$code
tail -n 140 "$LOG"
exit 0
