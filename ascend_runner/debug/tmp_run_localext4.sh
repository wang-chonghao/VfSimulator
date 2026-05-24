#!/usr/bin/env bash
set -e
SRC=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
DST=/home/lenovo/msprof_run/GeLU_optimized_pto_local
rm -rf "$DST"
mkdir -p /home/lenovo/msprof_run
cp -a "$SRC" "$DST"
cd /mnt/d/VfSimulator
bash ascend_runner/run_pto_sim_ptostyle.sh "$DST/GeLU_optimized_sim" > "$DST/run_localext4.log" 2>&1 || true
echo ===KEY===
grep -n "CORE_WRAPPER\|config file=\.|Config file \[1982_cloud_config\|cube_core_num\|CONFIG STARS SIM_CFG\|could not open file" "$DST/run_localext4.log" || true
echo ===TAIL===
tail -n 120 "$DST/run_localext4.log" || true