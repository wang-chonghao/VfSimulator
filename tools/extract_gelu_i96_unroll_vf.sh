#!/usr/bin/env bash
set -euo pipefail

for u in 1 2 4 8; do
  stem="GeLU_poly_I96_unroll${u}_native_simexec"
  pop="/home/lenovo/msprof_run/${stem}/core0.veccore0.instr_popped_log.dump"
  log="/home/lenovo/msprof_run/${stem}/core0.veccore0.instr_log.dump"
  s=$(grep -n VF "${pop}" | head -n1 | sed -E "s/.*\[([0-9]+)\].*/\1/")
  e=$(grep -n VF "${log}" | head -n1 | sed -E "s/.*\[([0-9]+)\].*/\1/")
  t=$((10#${e} - 10#${s}))
  echo "U=${u} start=${s} end=${e} vf=${t}"
done
