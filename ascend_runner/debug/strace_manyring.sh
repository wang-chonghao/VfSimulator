#!/usr/bin/env bash
set -euo pipefail
ASCEND_HOME=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
ROOT=$ASCEND_HOME/x86_64-linux/simulator/dav_3510
CAMODEL="$ROOT/camodel"
SIMLIB="$ROOT/lib"
COMMON=$ASCEND_HOME/x86_64-linux/lib64
DEVICE=$ASCEND_HOME/x86_64-linux/lib64/device/lib64
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
APP=./GeLU_optimized_sim
LOG="$APPDIR/strace_manyring.log"

for f in \
  Ascend950pr_9599_sim.toml \
  Ascend950pr_9599_model.toml \
  Ascend950pr_9599_stars.toml \
  Ascend950pr_9599_ffts.toml \
  Ascend950pr_9599_ffts_plus.toml; do
  cp -f "$SIMLIB/$f" "$APPDIR/$f"
done

export LD_LIBRARY_PATH="$APPDIR:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="/lib/x86_64-linux-gnu/libz.so.1:$CAMODEL/libUtility.so:$SIMLIB/libffts_model.so:$SIMLIB/libstars_pv.so:$SIMLIB/libnpu_drv.so:$SIMLIB/libmcu_loop.so:$SIMLIB/libmcu_wrapper.so:$CAMODEL/libmodel_api.so:$CAMODEL/libEslTop.so:$CAMODEL/libSoC.so:$CAMODEL/libHISI_CHI_IF.so:$CAMODEL/libcommon.so:$CAMODEL/libstars_wrapper.so:$CAMODEL/libstars.so:$CAMODEL/libSMMU.so:$CAMODEL/libAA.so:$CAMODEL/libSCHE.so:$CAMODEL/libPMU.so:$CAMODEL/libTgWrapper.so:$CAMODEL/libqtest_api.so:$CAMODEL/libMATA.so:$CAMODEL/libDDR_Inf.so:$CAMODEL/libSDMAA.so:$CAMODEL/libSDMAM.so:$CAMODEL/libAXI_STREAM_BUS.so:$CAMODEL/libNcMpi.so:$CAMODEL/libUB.so:$CAMODEL/libTaskSched.so:$CAMODEL/libDVPP_CA.so:$CAMODEL/libSLLC.so:$CAMODEL/libL2Buf.so:$CAMODEL/libPCIE.so:$CAMODEL/libChiRingFabric.so:$CAMODEL/libaicpu_wrapper.so:$CAMODEL/libaicpu.so:$CAMODEL/libPowerModel.so:$CAMODEL/libCuberWrapper.so:$CAMODEL/libbailusim.so:$CAMODEL/liblpddrsim.so:$CAMODEL/libmemsys.so:$CAMODEL/libParallelScheduler.so:$CAMODEL/libpem_davinci.so"

cd "$APPDIR"
strace -f -s 256 -e trace=openat,chdir,getcwd "$APP" > /dev/null 2> "$LOG" || true
tr -d '\000' < "$LOG" | grep -n 'manyring\.csv\|parameter\|getcwd\|chdir' | sed -n '1,240p'
