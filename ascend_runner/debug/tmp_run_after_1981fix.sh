#!/usr/bin/env bash
set -e
cd /mnt/d/VfSimulator
bash ascend_runner/run_pto_sim.sh /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/GeLU_optimized_sim > /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_after_1981fix.log 2>&1 || true
echo ===SIMCFG===
grep -n "CONFIG STARS SIM_CFG\|config file=\./etc/1982_cloud_config.toml\|cube_core_num\|vec_core_num\|adapter_log_file_level" /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_after_1981fix.log || true
echo ===TAIL===
tail -n 120 /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_after_1981fix.log || true