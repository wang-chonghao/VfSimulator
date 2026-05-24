#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <case_list_file>"
  exit 2
fi

list_file="$1"
cd /mnt/d/VfSimulator

while IFS= read -r stem; do
  stem="${stem//$'\r'/}"
  [[ -z "$stem" ]] && continue
  [[ "$stem" =~ ^# ]] && continue

  dsl="ascend_runner/forwarding_param_suite/cases/${stem}.dsl"
  if [[ ! -f "$dsl" ]]; then
    echo "[SKIP] missing DSL: $dsl"
    continue
  fi

  echo "[CASE] $stem"
  bash ascend_runner/current/build_native_simexec.sh "$dsl" "$stem"
  bash ascend_runner/current/run_native_simexec.sh \
    "ascend_runner/build/${stem}_native_simexec/${stem}_simexec" \
    "ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" \
    foo_add 1 1 64 || true
done < "$list_file"

echo "[DONE] list run finished"