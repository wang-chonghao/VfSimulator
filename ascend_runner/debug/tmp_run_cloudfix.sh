#!/usr/bin/env bash
set -e
cd /mnt/d/VfSimulator
bash ascend_runner/run_pto_sim.sh /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/GeLU_optimized_sim > /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_after_cloudfix.log 2>&1 || true
tail -n 200 /mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/run_after_cloudfix.log || true