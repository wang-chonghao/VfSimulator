#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 6 ]]; then
  echo "Usage: $0 <sim-exec> [kernel-bin] [kernel-name] [num-inputs] [num-outputs] [total-elems]"
  exit 1
fi

APP_PATH="$1"
APP_DIR="$(cd "$(dirname "${APP_PATH}")" && pwd)"
APP_NAME="$(basename "${APP_PATH}")"
STEM="${APP_NAME%_simexec}"
KERNEL_BIN="${2:-${APP_DIR}/${STEM}_mix.o}"
KERNEL_NAME="${3:-${STEM}}"
NUM_INPUTS="${4:-${NUM_INPUTS:-}}"
NUM_OUTPUTS="${5:-${NUM_OUTPUTS:-}}"
TOTAL_ELEMS="${6:-${TOTAL_ELEMS:-}}"

if [[ ! -f "${APP_PATH}" ]]; then
  echo "Sim executable not found: ${APP_PATH}"
  exit 2
fi
if [[ ! -f "${KERNEL_BIN}" ]]; then
  echo "Kernel bin not found: ${KERNEL_BIN}"
  exit 3
fi

if [[ -f "${APP_DIR}/run_simexec_env.sh" ]]; then
  source "${APP_DIR}/run_simexec_env.sh"
fi

LOCAL_ROOT="/home/lenovo/msprof_run"
LOCAL_DIR="${LOCAL_ROOT}/${STEM}_native_simexec"
rm -rf "${LOCAL_DIR}"
mkdir -p "${LOCAL_DIR}"
cp -f "${APP_PATH}" "${LOCAL_DIR}/${APP_NAME}"
cp -f "${KERNEL_BIN}" "${LOCAL_DIR}/$(basename "${KERNEL_BIN}")"
chmod 700 "${LOCAL_DIR}" "${LOCAL_DIR}/${APP_NAME}"

cd "${LOCAL_DIR}"
echo "[INFO] Running native sim executable"
echo "[INFO] App       : ./${APP_NAME}"
echo "[INFO] Kernel bin: ./$(basename "${KERNEL_BIN}")"
echo "[INFO] Kernel    : ${KERNEL_NAME}"
echo "[INFO] Work dir  : ${LOCAL_DIR}"

CMD=("./${APP_NAME}" "./$(basename "${KERNEL_BIN}")" "${KERNEL_NAME}")
if [[ -n "${NUM_INPUTS}" ]]; then
  CMD+=("${NUM_INPUTS}")
fi
if [[ -n "${NUM_OUTPUTS}" ]]; then
  CMD+=("${NUM_OUTPUTS}")
fi
if [[ -n "${TOTAL_ELEMS}" ]]; then
  CMD+=("${TOTAL_ELEMS}")
fi

"${CMD[@]}"
