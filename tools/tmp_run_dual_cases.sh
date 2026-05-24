#!/usr/bin/env bash
set -euo pipefail
cd /mnt/d/VfSimulator
cases=(singleop_vadd singleop_vsub singleop_vmul singleop_vmax singleop_vmin singleop_vdiv)
for stem in "${cases[@]}"; do
  dsl="ascend_runner/single_op_param_suite/cases/${stem}.dsl"
  echo "[CASE] ${stem}"
  bash ascend_runner/current/build_native_simexec.sh "$dsl" "$stem"
  set +e
  bash ascend_runner/current/run_native_simexec.sh \
    "ascend_runner/build/${stem}_native_simexec/${stem}_simexec" \
    "ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" \
    foo_add 2 1 64
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    echo "[WARN] ${stem} returned ${rc}, continue"
  fi
done
