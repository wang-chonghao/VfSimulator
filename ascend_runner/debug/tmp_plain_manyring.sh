#!/usr/bin/env bash
set -u
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/plain_run
LOG=$APPDIR/plain_manyring.log
cp -f /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/libshim_cfg_manyring.so "$APPDIR/"
export LD_LIBRARY_PATH="$APPDIR:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64"
export LD_PRELOAD="$APPDIR/libshim_cfg_manyring.so"
cd "$APPDIR"
./GeLU_optimized_sim > "$LOG" 2>&1
code=$?
echo EXIT_CODE=$code
tail -n 120 "$LOG"
exit 0
