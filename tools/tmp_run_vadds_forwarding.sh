#!/usr/bin/env bash
set -euo pipefail
cd /mnt/d/VfSimulator
for dsl in ascend_runner/forwarding_param_suite/cases/fwd_vadds_to_*.dsl; do
  stem=$(basename "$dsl" .dsl)
  echo "[CASE] $stem"
  bash ascend_runner/current/build_native_simexec.sh "$dsl" "$stem"
  bash ascend_runner/current/run_native_simexec.sh \
    "ascend_runner/build/${stem}_native_simexec/${stem}_simexec" \
    "ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" \
    foo_add 1 1 64 || true
done