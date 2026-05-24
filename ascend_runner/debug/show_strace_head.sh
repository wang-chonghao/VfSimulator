#!/usr/bin/env bash
set -euo pipefail
LOG=/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/strace_manyring.log
if [[ ! -f "$LOG" ]]; then
  echo NO_LOG
  exit 0
fi
sed -n '1,200p' "$LOG"
