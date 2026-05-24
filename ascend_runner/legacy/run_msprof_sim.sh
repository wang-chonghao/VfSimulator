#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <application_executable> [kernel_name]"
  exit 1
fi

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
# Prefer camodel before other simulator libs: dav_3510 ships multiple libstars.so variants,
# and the camodel copy exports the STARS_TOP symbols expected by libstars_wrapper.so.
export LD_LIBRARY_PATH="${ASCEND_HOME_PATH}/x86_64-linux/lib64:${ASCEND_HOME_PATH}/x86_64-linux/devlib/device:${ASCEND_HOME_PATH}/x86_64-linux/lib64/device/lib64:${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510/camodel:${LD_LIBRARY_PATH:-}"

APP_PATH="$1"
KERNEL_NAME="${2:-}"
if [[ ! -f "${APP_PATH}" ]]; then
  echo "Application not found: ${APP_PATH}"
  exit 3
fi

MSPROF="${ASCEND_HOME_PATH}/x86_64-linux/bin/msprof"

echo "[INFO] Running msprof simulator"
echo "[INFO] Application    : ${APP_PATH}"
echo "[INFO] Simulator path : ${ASCEND_SIMULATOR_PATH}"

CMD=("${MSPROF}" op simulator --dump=on "--application=${APP_PATH}" --soc-version=dav_3510)
if [[ -n "${KERNEL_NAME}" ]]; then
  CMD+=("--kernel-name=${KERNEL_NAME}")
fi

"${CMD[@]}"