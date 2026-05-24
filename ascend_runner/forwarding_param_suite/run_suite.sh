#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

python3 "${SCRIPT_DIR}/generate_cases.py"

run_one() {
  local dsl="$1"
  local stem
  stem="$(basename "${dsl}")"
  stem="${stem%.*}"

  echo "[CASE] ${stem}"
  bash "${REPO_ROOT}/ascend_runner/current/build_native_simexec.sh" "${dsl}" "${stem}"

  set +e
  bash "${REPO_ROOT}/ascend_runner/current/run_native_simexec.sh" \
    "${REPO_ROOT}/ascend_runner/build/${stem}_native_simexec/${stem}_simexec" \
    "${REPO_ROOT}/ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" \
    foo_add 1 1 64
  local rc=$?
  set -e

  if [[ ${rc} -ne 0 ]]; then
    echo "[WARN] ${stem} runtime returned ${rc}; keep going"
  fi
}

for dsl in "${SCRIPT_DIR}"/cases/fwd_*.dsl; do
  run_one "${dsl}"
done

echo "[DONE] forwarding suite finished"
