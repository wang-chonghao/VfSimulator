#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <app-path>"
  exit 1
fi

ASCEND_HOME_PATH="${ASCEND_HOME_PATH:-/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1}"
SET_ENV_SH="${ASCEND_HOME_PATH}/set_env.sh"
if [[ ! -f "${SET_ENV_SH}" ]]; then
  echo "Cannot find CANN env script: ${SET_ENV_SH}"
  exit 2
fi

set +u
source "${SET_ENV_SH}"
set -u

APP_PATH="$1"
if [[ ! -f "${APP_PATH}" ]]; then
  echo "App not found: ${APP_PATH}"
  exit 3
fi

APP_DIR="$(cd "$(dirname "${APP_PATH}")" && pwd)"
APP_NAME="$(basename "${APP_PATH}")"
ROOT_SIM="${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510"
CAMODEL="${ROOT_SIM}/camodel"
SIMLIB="${ROOT_SIM}/lib"
COMMON="${ASCEND_HOME_PATH}/x86_64-linux/lib64"
DEVICE="${ASCEND_HOME_PATH}/x86_64-linux/lib64/device/lib64"

export LD_LIBRARY_PATH="${APP_DIR}:${CAMODEL}:${SIMLIB}:${COMMON}:${DEVICE}"

echo "[INFO] Running minimal CCE app"
echo "[INFO] App: ${APP_DIR}/${APP_NAME}"
(
  cd "${APP_DIR}"
  "./${APP_NAME}"
)
