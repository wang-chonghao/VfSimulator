#!/usr/bin/env bash
set -e
cd /mnt/e/PTO/pass/ptoas_sample_cpp/ptoas_sample_cpp/online_update_fused_a5
echo ====RUNLOG====
tail -n 120 run.log 2>/dev/null || true
echo ====FILES_ROOT====
ls -l . | sed -n '1,120p'
echo ====FILES_BUILD====
ls -l build | sed -n '1,120p'