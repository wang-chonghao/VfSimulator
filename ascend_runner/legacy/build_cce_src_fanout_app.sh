#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <input.dsl|input.cce> [output-name]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ASCEND_HOME_PATH="${ASCEND_HOME_PATH:-/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1}"
SET_ENV_SH="${ASCEND_HOME_PATH}/set_env.sh"
if [[ ! -f "${SET_ENV_SH}" ]]; then
  echo "Cannot find CANN env script: ${SET_ENV_SH}"
  exit 2
fi

set +u
source "${SET_ENV_SH}"
set -u

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

BUILD_DIR="${REPO_ROOT}/ascend_runner/build/${STEM}_cce_min"
mkdir -p "${BUILD_DIR}"

CCE_PATH="${BUILD_DIR}/${STEM}.cce"
APP_PATH="${BUILD_DIR}/${STEM}_cce_min"
CCEC="${ASCEND_HOME_PATH}/x86_64-linux/bin/ccec"

cp "${INPUT_PATH}" "${CCE_PATH}"

LAUNCH_CPP="${REPO_ROOT}/ascend_runner/cce_src_fanout_launch.cpp"
MAIN_CPP="${REPO_ROOT}/ascend_runner/cce_min_main.cpp"

"${CCEC}" \
  -x cce "${CCE_PATH}" \
  "${LAUNCH_CPP}" \
  -x c++ "${MAIN_CPP}" \
  -O2 -std=c++17 \
  --cce-aicore-arch=dav-c310-vec \
  -I"${ASCEND_HOME_PATH}/include" \
  -L"${ASCEND_HOME_PATH}/x86_64-linux/lib64" \
  -L"${ASCEND_HOME_PATH}/x86_64-linux/devlib/device" \
  -L"${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510/camodel" \
  -lruntime -lascendcl -lstdc++ \
  -o "${APP_PATH}"

echo "[DONE] Built minimal CCE app:"
echo "  ${APP_PATH}"
