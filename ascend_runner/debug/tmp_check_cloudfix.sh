#!/usr/bin/env bash
set -e
cd /mnt/d/VfSimulator
APPDIR=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim
echo ===FILES===
ls -l "$APPDIR" | sed -n '1,80p'
echo ===LOGSIZE===
wc -c "$APPDIR/run_after_cloudfix.log" || true
echo ===LOGTAIL===
tail -n 120 "$APPDIR/run_after_cloudfix.log" || true