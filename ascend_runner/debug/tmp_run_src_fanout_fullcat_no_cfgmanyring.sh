#!/usr/bin/env bash
set -u
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/run_fullcat_no_cfgmanyring.log
SRC_SHIM_DIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
mkdir -p "$APPROOT/etc" "$APPROOT/log_ca" "$APPROOT/parameter" "$APPROOT/soft"
cp -f "$SRC_SHIM_DIR/libshim_axi_ctor.so" "$APPROOT/"
cp -f "$SRC_SHIM_DIR/libshim_cfg_axi_abi0.so" "$APPROOT/"
cp -f "$SIMLIB/1982_cloud_config.toml" "$APPROOT/etc/1982_cloud_config.toml"
cp -f "$SIMLIB/Ascend950pr_9599_model.toml" "$APPROOT/Ascend950pr_9599_model.toml"
cp -f "$SIMLIB/Ascend950pr_9599_stars_ffts_plus.toml" "$APPROOT/Ascend950pr_9599_stars_ffts_plus.toml"
cp -f "$SIMLIB/Ascend950pr_9599_ffts.toml" "$APPROOT/Ascend950pr_9599_ffts.toml"
cp -f "$SIMLIB/Ascend950pr_9599_ffts_plus.toml" "$APPROOT/Ascend950pr_9599_ffts_plus.toml"
cat > "$APPROOT/1981_sim_ffts_plus.toml" <<'EOF'
model_cfg = "Ascend950pr_9599_model.toml"
stars_cfg = "Ascend950pr_9599_stars_ffts_plus.toml"
ffts_cfg = "Ascend950pr_9599_ffts.toml"
ffts_plus_cfg = "Ascend950pr_9599_ffts_plus.toml"
enable = 1
file_print_level = 2
screen_print_level = 2
flush_level = 2
rotating_file_size = 134217728
rotating_file_number = 2
EOF
: > "$APPROOT/parameter/Tg_Model.conf"
: > "$APPROOT/parameter/check_point.cfg"
export LD_LIBRARY_PATH="$APPROOT:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPROOT/libshim_axi_ctor.so:$APPROOT/libshim_catDiePortId.so:$APPROOT/libshim_cfg_axi_abi0.so:/lib/x86_64-linux-gnu/libz.so.1:$CAMODEL/libUtility.so:$SIMLIB/libffts_model.so:$SIMLIB/libstars_pv.so:$SIMLIB/libnpu_drv.so:$SIMLIB/libmcu_loop.so:$SIMLIB/libmcu_wrapper.so:$CAMODEL/libmodel_api.so:$CAMODEL/libEslTop.so:$CAMODEL/libSoC.so:$CAMODEL/libHISI_CHI_IF.so:$CAMODEL/libcommon.so:$CAMODEL/libstars_wrapper.so:$CAMODEL/libstars.so:$CAMODEL/libSMMU.so:$CAMODEL/libAA.so:$CAMODEL/libSCHE.so:$CAMODEL/libPMU.so:$CAMODEL/libTgWrapper.so:$CAMODEL/libqtest_api.so:$CAMODEL/libMATA.so:$CAMODEL/libDDR_Inf.so:$CAMODEL/libSDMAA.so:$CAMODEL/libSDMAM.so:$CAMODEL/libAXI_STREAM_BUS.so:$CAMODEL/libNcMpi.so:$CAMODEL/libUB.so:$CAMODEL/libTaskSched.so:$CAMODEL/libDVPP_CA.so:$CAMODEL/libSLLC.so:$CAMODEL/libL2Buf.so:$CAMODEL/libPCIE.so:$CAMODEL/libChiRingFabric.so:$CAMODEL/libaicpu_wrapper.so:$CAMODEL/libaicpu.so:$CAMODEL/libPowerModel.so:$CAMODEL/libCuberWrapper.so:$CAMODEL/libbailusim.so:$CAMODEL/liblpddrsim.so:$CAMODEL/libmemsys.so:$CAMODEL/libParallelScheduler.so:$CAMODEL/libpem_davinci.so"
cd "$APPROOT"
./src_fanout_probe_cce_min > "$LOG" 2>&1
code=$?
echo EXIT_CODE=$code
tail -n 120 "$LOG"
exit 0
