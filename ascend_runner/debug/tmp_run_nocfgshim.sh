#!/usr/bin/env bash
set -e
cd /mnt/d/VfSimulator
bash ascend_runner/run_pto_sim_nocfgshim.sh /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/GeLU_optimized_sim > /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_nocfgshim.log 2>&1 || true
tail -n 120 /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_nocfgshim.log || true