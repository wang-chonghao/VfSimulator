#!/usr/bin/env bash
set -e
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
echo ===SIM_MATCH===
find "$SIMLIB" -maxdepth 1 -type f | grep '1981_sim_ffts_plus\|9599_sim\|cloud_config' | sort || true
echo ===APP_1981===
ls -l "$APPDIR/1981_sim_ffts_plus.toml" || true
sed -n '1,120p' "$APPDIR/1981_sim_ffts_plus.toml" || true