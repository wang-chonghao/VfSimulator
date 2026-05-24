#!/usr/bin/env bash
set -u
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/plain_run
LOG=$APPDIR/plain_manyring_axi_res.log
mkdir -p "$APPDIR/log_ca" "$APPDIR/parameter" "$APPDIR/soft"
: > "$APPDIR/parameter/Tg_Model.conf"
: > "$APPDIR/parameter/check_point.cfg"
for so in libshim_cfg_manyring.so libshim_axi_ctor.so libshim_catDiePortId_chiring_only.so libshim_cfg_axi_abi0.so; do
  cp -f "/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/$so" "$APPDIR/"
done
export LD_LIBRARY_PATH="$APPDIR:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64:/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64"
export LD_PRELOAD="$APPDIR/libshim_cfg_manyring.so:$APPDIR/libshim_axi_ctor.so:$APPDIR/libshim_catDiePortId_chiring_only.so:$APPDIR/libshim_cfg_axi_abi0.so"
cd "$APPDIR"
./GeLU_optimized_sim > "$LOG" 2>&1
code=$?
echo EXIT_CODE=$code
tail -n 160 "$LOG"
exit 0
