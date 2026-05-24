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

BUILD_DIR="${REPO_ROOT}/ascend_runner/build/${STEM}_pto_sim"
mkdir -p "${BUILD_DIR}"

CCE_PATH="${BUILD_DIR}/${STEM}.cce"
KERNEL_SO="${BUILD_DIR}/lib${STEM}_kernel.so"
SIM_APP="${BUILD_DIR}/${STEM}_sim"

cp "${INPUT_PATH}" "${CCE_PATH}"

CCEC="${ASCEND_HOME_PATH}/bin/ccec"
BISHENG="${ASCEND_HOME_PATH}/bin/bisheng"
LAUNCH_CPP="${REPO_ROOT}/ascend_runner/gelu_launch.cpp"
MAIN_CPP="${REPO_ROOT}/ascend_runner/gelu_sim_main.cpp"

PKG_INC="${ASCEND_HOME_PATH}/pkg_inc"
RUNTIME_INC="${ASCEND_HOME_PATH}/pkg_inc/runtime/runtime"
PROF_INC="${ASCEND_HOME_PATH}/pkg_inc/profiling"
SIMLIB="${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510/lib"
CAMODEL="${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510/camodel"
LIB64="${ASCEND_HOME_PATH}/x86_64-linux/lib64"

echo "[INFO] Building PTO-style kernel shared library"
"${CCEC}" \
  -x cce "${CCE_PATH}" \
  "${LAUNCH_CPP}" \
  -O2 -std=c++17 -shared -fPIC \
  -fenable-matrix \
  --cce-aicore-enable-tl \
  --cce-aicore-arch=dav-c310-vec \
  --cce-fatobj-link \
  -DREGISTER_BASE \
  -Xhost-start -Xhost-end \
  -mllvm -cce-aicore-stack-size=0x8000 \
  -mllvm -cce-aicore-function-stack-size=0x8000 \
  -mllvm -cce-aicore-record-overflow=true \
  -mllvm -cce-aicore-addr-transform \
  -mllvm -cce-aicore-dcci-insert-for-scalar=false \
  -I"${ASCEND_HOME_PATH}/include" \
  -I"${PKG_INC}" \
  -I"${PROF_INC}" \
  -I"${RUNTIME_INC}" \
  -L"${LIB64}" \
  -o "${KERNEL_SO}"

echo "[INFO] Building simulator executable"
"${BISHENG}" \
  -std=c++17 -O2 "${MAIN_CPP}" \
  -I"${ASCEND_HOME_PATH}/include" \
  -I"${PKG_INC}" \
  -I"${RUNTIME_INC}" \
  -L"${BUILD_DIR}" \
  -L"${LIB64}" \
  -L"${SIMLIB}" \
  -L"${CAMODEL}" \
  -Wl,-rpath,'$ORIGIN' \
  -l${STEM}_kernel \
  -lruntime_camodel -lascendcl -ltiling_api -lplatform -lc_sec -ldl -lnnopbase -lm -lstdc++ \
  -o "${SIM_APP}"

echo "[DONE] Built:"
echo "  ${KERNEL_SO}"
echo "  ${SIM_APP}"
