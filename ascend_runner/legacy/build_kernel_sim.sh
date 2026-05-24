#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <input.dsl|input.cce> [output-name]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ASCEND_HOME_PATH="${ASCEND_HOME_PATH:-/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1}"
ASCEND_TOOLKIT_HOME="${ASCEND_TOOLKIT_HOME:-${ASCEND_HOME_PATH}}"
ASCEND_SIMULATOR_PATH="${ASCEND_SIMULATOR_PATH:-${ASCEND_HOME_PATH}/x86_64-linux/simulator/Ascend950PR_9599}"
SET_ENV_SH="${ASCEND_HOME_PATH}/set_env.sh"

if [[ ! -f "${SET_ENV_SH}" ]]; then
  echo "Cannot find CANN env script: ${SET_ENV_SH}"
  exit 2
fi

set +u
source "${SET_ENV_SH}"
set -u
export ASCEND_HOME_PATH
export ASCEND_TOOLKIT_HOME
export ASCEND_SIMULATOR_PATH
export LD_LIBRARY_PATH="${ASCEND_HOME_PATH}/x86_64-linux/lib64:${ASCEND_HOME_PATH}/x86_64-linux/devlib/device:${ASCEND_HOME_PATH}/x86_64-linux/lib64/device/lib64:${LD_LIBRARY_PATH:-}"

INPUT_PATH="$1"
if [[ "${INPUT_PATH}" != /* ]]; then
  INPUT_PATH="${REPO_ROOT}/${INPUT_PATH}"
fi

if [[ ! -f "${INPUT_PATH}" ]]; then
  echo "Input file not found: ${INPUT_PATH}"
  exit 3
fi

if [[ $# -ge 2 ]]; then
  STEM="$2"
else
  STEM="$(basename "${INPUT_PATH}")"
  STEM="${STEM%.*}"
fi

BUILD_DIR="${REPO_ROOT}/ascend_runner/build/${STEM}"
mkdir -p "${BUILD_DIR}"

CCE_PATH="${BUILD_DIR}/${STEM}.cce"
OBJ_PATH="${BUILD_DIR}/${STEM}.o"
SIM_OBJ_PATH="${BUILD_DIR}/${STEM}_sim.o"

cp "${INPUT_PATH}" "${CCE_PATH}"

CCEC="${ASCEND_HOME_PATH}/x86_64-linux/bin/ccec"
LD_LLD="${ASCEND_HOME_PATH}/x86_64-linux/bin/ld.lld"
LD_LAYOUT_ARG="${LD_LAYOUT_ARG:--Ttext=0}"

echo "[INFO] Source file    : ${CCE_PATH}"
echo "[INFO] CANN home      : ${ASCEND_HOME_PATH}"
echo "[INFO] Simulator path : ${ASCEND_SIMULATOR_PATH}"

"${CCEC}" \
  -g -std=cce -c -O2 "${CCE_PATH}" \
  -o "${OBJ_PATH}" \
  --cce-aicore-arch=dav-c310-vec \
  --cce-aicore-only \
  -mllvm -cce-aicore-stack-size=0x8000 \
  -mllvm -cce-aicore-record-overflow=false \
  -mllvm -cce-aicore-addr-transform \
  -mllvm --cce-aicore-jump-expand=true \
  --cce-simd-vf-fusion=false

"${LD_LLD}" "${LD_LAYOUT_ARG}" "${OBJ_PATH}" -static -o "${SIM_OBJ_PATH}"

echo "[DONE] Built:"
echo "  ${OBJ_PATH}"
echo "  ${SIM_OBJ_PATH}"