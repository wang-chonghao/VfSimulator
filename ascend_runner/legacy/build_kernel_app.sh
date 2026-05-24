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
ASCEND_SIMULATOR_PATH="${ASCEND_SIMULATOR_PATH:-${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510}"
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
export LD_LIBRARY_PATH="${ASCEND_HOME_PATH}/x86_64-linux/lib64:${ASCEND_HOME_PATH}/x86_64-linux/devlib/device:${ASCEND_HOME_PATH}/x86_64-linux/lib64/device/lib64:${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510/camodel:${LD_LIBRARY_PATH:-}"

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

BUILD_DIR="${REPO_ROOT}/ascend_runner/build/${STEM}_app"
mkdir -p "${BUILD_DIR}"

CCE_PATH="${BUILD_DIR}/${STEM}.cce"
APP_PATH="${BUILD_DIR}/${STEM}_app"
MAIN_CPP="${REPO_ROOT}/ascend_runner/main.cc"
CCEC="${ASCEND_HOME_PATH}/x86_64-linux/bin/ccec"

cp "${INPUT_PATH}" "${CCE_PATH}"

echo "[INFO] Source file    : ${CCE_PATH}"
echo "[INFO] Host source    : ${MAIN_CPP}"
echo "[INFO] CANN home      : ${ASCEND_HOME_PATH}"
echo "[INFO] Simulator path : ${ASCEND_SIMULATOR_PATH}"

"${CCEC}" \
  -x cce "${CCE_PATH}" \
  -x c++ "${MAIN_CPP}" \
  -O2 -std=c++17 \
  --cce-aicore-arch=dav-c310-vec \
  -I"${ASCEND_HOME_PATH}/include" \
  -I"${ASCEND_HOME_PATH}/compiler/tikcpp/tikcfw" \
  -I"${ASCEND_HOME_PATH}/compiler/tikcpp/tikcfw/impl" \
  -I"${ASCEND_HOME_PATH}/compiler/tikcpp/tikcfw/interface" \
  -L"${ASCEND_HOME_PATH}/x86_64-linux/lib64" \
  -L"${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510/camodel" \
  -L"${ASCEND_HOME_PATH}/x86_64-linux/devlib/device" \
  -lruntime -lascendcl -lstdc++ \
  -o "${APP_PATH}"

echo "[DONE] Built application:"
echo "  ${APP_PATH}"