#!/usr/bin/env bash
set -e
APP=/mnt/e/PTO/pass/ptoas_sample_cpp/ptoas_sample_cpp/online_update_fused_a5/build/online_update_fused_a5_sim
if [[ ! -f "$APP" ]]; then
  echo "APP_NOT_FOUND: $APP"
  exit 0
fi
cd /mnt/d/VfSimulator
bash ascend_runner/run_pto_sim_ptostyle.sh "$APP" > /mnt/d/VfSimulator/ascend_runner/pto_app_with_our_runner.log 2>&1 || true
echo ===KEY===
grep -n "CORE_WRAPPER\|config file=\.|cube_core_num\|CONFIG STARS SIM_CFG\|could not open file\|symbol lookup error\|TMultiRing\|vector::_M_range_check" /mnt/d/VfSimulator/ascend_runner/pto_app_with_our_runner.log || true
echo ===TAIL===
tail -n 120 /mnt/d/VfSimulator/ascend_runner/pto_app_with_our_runner.log || true