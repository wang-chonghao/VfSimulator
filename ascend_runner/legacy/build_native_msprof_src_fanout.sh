#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <input.dsl|input.cce> [output-name]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ACL_PATH="${ACL_PATH:-/home/lenovo/Ascend/ascend-toolkit/latest}"
if [[ ! -d "${ACL_PATH}" ]]; then
  ACL_PATH="/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"
fi

SET_ENV_SH="${ACL_PATH}/set_env.sh"
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

BUILD_DIR="${REPO_ROOT}/ascend_runner/build/${STEM}_native_msprof"
mkdir -p "${BUILD_DIR}"

CCE_PATH="${BUILD_DIR}/${STEM}.cce"
AIV_OBJ="${BUILD_DIR}/${STEM}_mix_aiv.o"
AIC_OBJ="${BUILD_DIR}/${STEM}_mix_aic.o"
KERNEL_BIN="${BUILD_DIR}/${STEM}_mix.o"
HOST_APP="${BUILD_DIR}/${STEM}_sim.o"

cp "${INPUT_PATH}" "${CCE_PATH}"
rm -f "${AIC_OBJ}" "${KERNEL_BIN}" "${HOST_APP}"

CCEC="${ACL_PATH}/x86_64-linux/bin/ccec"
LD_LLD="${ACL_PATH}/x86_64-linux/bin/ld.lld"

CANN_LIB="${ACL_PATH}/lib64"
if [[ ! -d "${CANN_LIB}" ]]; then
  CANN_LIB="${ACL_PATH}/x86_64-linux/lib64"
fi

CANN_INC="${ACL_PATH}/include"
HOST_INC="${ACL_PATH}/x86_64-linux/include"
MSPROF_INC="${HOST_INC}/experiment/msprof"
PKG_INC="${ACL_PATH}/x86_64-linux/pkg_inc"
NPU_TYPE="${NPU_TYPE:-Ascend950PR_9599}"
CORE_ARCH="${CORE_ARCH:-dav-c310-vec}"

SIM_LIB1="${ACL_PATH}/lib64/../simulator/${NPU_TYPE}/lib"
SIM_LIB2="${ACL_PATH}/tools/simulator/${NPU_TYPE}/lib"
SIM_CAMODEL1="${ACL_PATH}/tools/simulator/${NPU_TYPE}/camodel"
SIM_LIB3="${ACL_PATH}/x86_64-linux/simulator/dav_3510/lib"
SIM_CAMODEL2="${ACL_PATH}/x86_64-linux/simulator/dav_3510/camodel"
DEVLIB1="${CANN_LIB}/../devlib"
DEVLIB2="${ACL_PATH}/x86_64-linux/devlib/device"
DEVLIB3="${ACL_PATH}/x86_64-linux/lib64/device/lib64"

MAIN_CPP="${REPO_ROOT}/ascend_runner/native_runtime_1in1out_main.cpp"

echo "[INFO] ACL_PATH=${ACL_PATH}"
echo "[INFO] NPU_TYPE=${NPU_TYPE}"
echo "[INFO] CORE_ARCH=${CORE_ARCH}"
echo "[INFO] BUILD_DIR=${BUILD_DIR}"

"${CCEC}" -g -std=c++17 -c -O2 "${CCE_PATH}" -o "${AIV_OBJ}" \
  -I/usr/include/c++/11 \
  -I/usr/include/aarch64-linux-gnu/c++/11 \
  --cce-aicore-arch="${CORE_ARCH}" \
  --cce-aicore-only \
  -mllvm -cce-aicore-function-stack-size=16000 \
  -mllvm -cce-aicore-record-overflow=false \
  -mllvm -cce-aicore-addr-transform \
  -mllvm -cce-aicore-jump-expand=true \
  --cce-simd-vf-fusion=false

LINK_INPUTS=("${AIV_OBJ}")
if [[ -f "${AIC_OBJ}" ]]; then
  LINK_INPUTS=("${AIC_OBJ}" "${AIV_OBJ}")
fi

"${LD_LLD}" -Ttext=0 "${LINK_INPUTS[@]}" -static -o "${KERNEL_BIN}"

g++ -std=c++17 -O2 -Wl,--allow-shlib-undefined -o "${HOST_APP}" "${MAIN_CPP}" \
  -I"${HOST_INC}" \
  -I"${MSPROF_INC}" \
  -I"${CANN_INC}" \
  -I"${PKG_INC}" \
  -I"${PKG_INC}/runtime" \
  -L"${CANN_LIB}" \
  -L"${SIM_LIB1}" \
  -L"${SIM_LIB2}" \
  -L"${SIM_LIB3}" \
  -lascendcl -lruntime -lruntime_common -lprofapi \
  -lplatform -lerror_manager -lascend_dump

cat > "${BUILD_DIR}/run_native_env.sh" <<EOF
export ACL_PATH="${ACL_PATH}"
export ASCEND_TOOLKIT_HOME="${ACL_PATH}"
export CANN_LIB="${CANN_LIB}"
export CANN_INC="${CANN_INC}"
export NPU_TYPE="${NPU_TYPE}"
export LD_LIBRARY_PATH="${SIM_LIB1}:${SIM_LIB2}:${SIM_CAMODEL1}:${SIM_LIB3}:${SIM_CAMODEL2}:${CANN_LIB}:${DEVLIB1}:${DEVLIB2}:${DEVLIB3}:\${LD_LIBRARY_PATH:-}"
EOF

echo "[DONE] Built native CCE msprof artifacts:"
echo "  kernel bin : ${KERNEL_BIN}"
echo "  host app   : ${HOST_APP}"
echo "  kernel name: ${STEM}"
