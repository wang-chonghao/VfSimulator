#!/usr/bin/env bash
set -u
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
LOG=$APPDIR/run_manyring_axi_logcat.log
MAPLOG=/tmp/src_fanout_catDiePortId.log
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
SRC_SHIM_DIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim

for so in libshim_cfg_manyring.so libshim_axi_ctor.so libshim_cfg_axi_abi0.so; do
  cp -f "$SRC_SHIM_DIR/$so" "$APPDIR/"
done
cp -f /mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min/libshim_log_catDiePortId.so "$APPDIR/"
mkdir -p "$APPDIR/log_ca" "$APPDIR/parameter" "$APPDIR/soft"
: > "$APPDIR/parameter/Tg_Model.conf"
: > "$APPDIR/parameter/check_point.cfg"
: > "$MAPLOG"
export LD_LIBRARY_PATH="$APPDIR:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPDIR/libshim_log_catDiePortId.so:$APPDIR/libshim_cfg_manyring.so:$APPDIR/libshim_axi_ctor.so:$APPDIR/libshim_cfg_axi_abi0.so:/lib/x86_64-linux-gnu/libz.so.1:$CAMODEL/libUtility.so:$SIMLIB/libffts_model.so:$SIMLIB/libstars_pv.so:$SIMLIB/libnpu_drv.so:$SIMLIB/libmcu_loop.so:$SIMLIB/libmcu_wrapper.so:$CAMODEL/libmodel_api.so:$CAMODEL/libEslTop.so:$CAMODEL/libSoC.so:$CAMODEL/libHISI_CHI_IF.so:$CAMODEL/libcommon.so:$CAMODEL/libstars_wrapper.so:$CAMODEL/libstars.so:$CAMODEL/libSMMU.so:$CAMODEL/libAA.so:$CAMODEL/libSCHE.so:$CAMODEL/libPMU.so:$CAMODEL/libTgWrapper.so:$CAMODEL/libqtest_api.so:$CAMODEL/libMATA.so:$CAMODEL/libDDR_Inf.so:$CAMODEL/libSDMAA.so:$CAMODEL/libSDMAM.so:$CAMODEL/libAXI_STREAM_BUS.so:$CAMODEL/libNcMpi.so:$CAMODEL/libUB.so:$CAMODEL/libTaskSched.so:$CAMODEL/libDVPP_CA.so:$CAMODEL/libSLLC.so:$CAMODEL/libL2Buf.so:$CAMODEL/libPCIE.so:$CAMODEL/libChiRingFabric.so:$CAMODEL/libaicpu_wrapper.so:$CAMODEL/libaicpu.so:$CAMODEL/libPowerModel.so:$CAMODEL/libCuberWrapper.so:$CAMODEL/libbailusim.so:$CAMODEL/liblpddrsim.so:$CAMODEL/libmemsys.so:$CAMODEL/libParallelScheduler.so:$CAMODEL/libpem_davinci.so"
cd "$APPDIR"
./src_fanout_probe_cce_min > "$LOG" 2>&1
code=$?
echo EXIT_CODE=$code
tail -n 180 "$LOG"
echo '--- catDie map ---'
sed -n '1,200p' "$MAPLOG"
exit 0
