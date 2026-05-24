#!/usr/bin/env bash
set -e
SIM=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib/Ascend950pr_9599_sim_ffts_plus.toml
APP=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/1981_sim_ffts_plus.toml
cp -f "$SIM" "$APP"
echo replaced
sed -n '1,80p' "$APP"