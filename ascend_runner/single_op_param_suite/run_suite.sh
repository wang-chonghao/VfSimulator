#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ ! -d "${SCRIPT_DIR}/cases" ]]; then
  echo "[INFO] generating DSL cases"
  python3 "${SCRIPT_DIR}/generate_cases.py"
fi

if [[ ! -d "${SCRIPT_DIR}/cases" ]]; then
  echo "cases dir not found"
  exit 2
fi

run_one() {
  local dsl="$1"
  local stem
  stem="$(basename "${dsl}")"
  stem="${stem%.*}"

  local inputs=1
  if [[ "${stem}" == "singleop_vadd" || "${stem}" == "singleop_vsub" || "${stem}" == "singleop_vmul" || "${stem}" == "singleop_vmax" || "${stem}" == "singleop_vmin" || "${stem}" == "singleop_vdiv" ]]; then
    inputs=2
  fi

  echo "[CASE] ${stem} (inputs=${inputs})"
  bash "${REPO_ROOT}/ascend_runner/current/build_native_simexec.sh" "${dsl}" "${stem}"

  set +e
  bash "${REPO_ROOT}/ascend_runner/current/run_native_simexec.sh"     "${REPO_ROOT}/ascend_runner/build/${stem}_native_simexec/${stem}_simexec"     "${REPO_ROOT}/ascend_runner/build/${stem}_native_simexec/${stem}_mix.o"     foo_add "${inputs}" 1 64
  local rc=$?
  set -e

  if [[ ${rc} -ne 0 ]]; then
    echo "[WARN] ${stem} runtime returned ${rc}; keep going for dump extraction"
  fi
}

for dsl in "${SCRIPT_DIR}"/cases/singleop_*.dsl; do
  run_one "${dsl}"
done

echo "[DONE] all single-op cases finished"
