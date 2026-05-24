#!/usr/bin/env bash
set -euo pipefail

cd /mnt/d/VfSimulator

for u in 1 2 4 8; do
  stem="GeLU_poly_I96_unroll${u}"
  dsl="/mnt/d/VfSimulator/cce_code/${stem}.dsl"
  echo "=== ${stem} ==="
  bash /mnt/d/VfSimulator/ascend_runner/current/build_native_simexec.sh "${dsl}" "${stem}"
  bash /mnt/d/VfSimulator/ascend_runner/current/run_native_simexec.sh \
    /mnt/d/VfSimulator/ascend_runner/build/${stem}_native_simexec/${stem}_simexec \
    /mnt/d/VfSimulator/ascend_runner/build/${stem}_native_simexec/${stem}_mix.o \
    foo_add 2 1 6144
done
