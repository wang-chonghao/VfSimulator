#!/usr/bin/env bash
set -e
cd /mnt/d/VfSimulator
bash ascend_runner/run_pto_sim_ptostyle.sh /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/GeLU_optimized_sim > /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_ptostyle.log 2>&1 || true
echo ===KEY===
grep -n "CORE_WRAPPER\|cube_core_num\|CONFIG STARS SIM_CFG\|model_cfg\|stars_cfg\|ffts_cfg\|Assertion failed" /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_ptostyle.log || true
echo ===TAIL===
tail -n 120 /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_ptostyle.log || true