#!/usr/bin/env bash
set -euo pipefail
cd /mnt/d/VfSimulator
bash ascend_runner/current/build_native_simexec.sh results/vdup_calib/dispatch_probe/vdup_vecsrc_dispatch_probe.dsl > results/vdup_calib/dispatch_probe/build.log 2>&1
bash ascend_runner/current/run_native_simexec.sh ascend_runner/build/vdup_vecsrc_dispatch_probe_native_simexec/vdup_vecsrc_dispatch_probe_simexec > results/vdup_calib/dispatch_probe/run.log 2>&1
DUMP=/home/lenovo/msprof_run/vdup_vecsrc_dispatch_probe_native_simexec/core0.veccore0.rvec.EXU.dump
grep RV_VDUP "$DUMP" | awk -F"exu_id:" '{print $2}' | awk '{print $1}' | tr -d ',' | sort -n | uniq -c > /mnt/d/VfSimulator/results/vdup_calib/dispatch_probe/vdup_exu_hist.txt
cat /mnt/d/VfSimulator/results/vdup_calib/dispatch_probe/vdup_exu_hist.txt

