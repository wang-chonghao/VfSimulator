#!/usr/bin/env bash
set -e
cd /mnt/d/VfSimulator
bash ascend_runner/run_pto_sim_nolocal1982.sh /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/GeLU_optimized_sim > /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_nolocal1982.log 2>&1 || true
echo ===KEY===
grep -n "CORE_WRAPPER\|config file=\.|Config file \[1982_cloud_config\|cube_core_num\|CONFIG STARS SIM_CFG\|could not open file" /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_nolocal1982.log || true
echo ===TAIL===
tail -n 120 /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_nolocal1982.log || true