#!/usr/bin/env bash
set -e
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
echo ===SIM_BIN===
strings "$APPDIR/GeLU_optimized_sim" | grep -n '1982_cloud_config\|1981_sim_ffts_plus\|etc/' | sed -n '1,120p' || true
echo ===KERNEL_SO===
strings "$APPDIR/libGeLU_optimized_kernel.so" | grep -n '1982_cloud_config\|1981_sim_ffts_plus\|etc/' | sed -n '1,120p' || true