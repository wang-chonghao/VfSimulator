#!/usr/bin/env bash
set -u
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
APPDIR=$APPROOT/plain_run_fresh
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPDIR/run.log
rm -rf "$APPDIR"
mkdir -p "$APPDIR/etc" "$APPDIR/log_ca" "$APPDIR/parameter" "$APPDIR/soft"
cp -f "$APPROOT/GeLU_optimized_sim" "$APPDIR/"
cp -f "$APPROOT/libGeLU_optimized_kernel.so" "$APPDIR/"
for so in libshim_cfg_manyring.so libshim_axi_ctor.so libshim_catDiePortId_chiring_only.so libshim_cfg_axi_abi0.so; do
  cp -f "$APPROOT/$so" "$APPDIR/"
done
cat > "$APPDIR/etc/1982_cloud_config.toml" <<'EOF'
[ARCH]
cube_core_num = 1
vec_core_num = 2

[WRAPPER]
adapter_log_file_level = 6
EOF
cp -f "$SIMLIB/Ascend950pr_9599_model.toml" "$APPDIR/Ascend950pr_9599_model.toml"
cp -f "$SIMLIB/Ascend950pr_9599_stars_ffts_plus.toml" "$APPDIR/Ascend950pr_9599_stars_ffts_plus.toml"
cp -f "$SIMLIB/Ascend950pr_9599_ffts.toml" "$APPDIR/Ascend950pr_9599_ffts.toml"
cp -f "$SIMLIB/Ascend950pr_9599_ffts_plus.toml" "$APPDIR/Ascend950pr_9599_ffts_plus.toml"
cat > "$APPDIR/1981_sim_ffts_plus.toml" <<'EOF'
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
: > "$APPDIR/parameter/Tg_Model.conf"
: > "$APPDIR/parameter/check_point.cfg"
export LD_LIBRARY_PATH="$APPDIR:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPDIR/libshim_cfg_manyring.so:$APPDIR/libshim_axi_ctor.so:$APPDIR/libshim_catDiePortId_chiring_only.so:$APPDIR/libshim_cfg_axi_abi0.so"
cd "$APPDIR"
./GeLU_optimized_sim > "$LOG" 2>&1
code=$?
echo EXIT_CODE=$code
tail -n 180 "$LOG"
exit 0
