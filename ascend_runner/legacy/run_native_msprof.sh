#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <host-app> [kernel-bin] [kernel-name]"
  exit 1
fi

APP_PATH="$1"
if [[ ! -f "${APP_PATH}" ]]; then
  echo "Host app not found: ${APP_PATH}"
  exit 2
fi

APP_DIR="$(cd "$(dirname "${APP_PATH}")" && pwd)"
APP_NAME="$(basename "${APP_PATH}")"

STEM="${APP_NAME%_sim.o}"
KERNEL_BIN="${2:-${APP_DIR}/${STEM}_mix.o}"
KERNEL_NAME="${3:-${STEM}}"

if [[ ! -f "${KERNEL_BIN}" ]]; then
  echo "Kernel bin not found: ${KERNEL_BIN}"
  exit 3
fi

ACL_PATH="${ACL_PATH:-/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1}"
export ASCEND_TOOLKIT_HOME="${ASCEND_TOOLKIT_HOME:-${ACL_PATH}}"
if [[ -f "${ACL_PATH}/set_env.sh" ]]; then
  set +u
  source "${ACL_PATH}/set_env.sh"
  set -u
fi
if [[ -f "${APP_DIR}/run_native_env.sh" ]]; then
  source "${APP_DIR}/run_native_env.sh"
fi

STAR_SO="${ACL_PATH}/tools/simulator/Ascend950PR_9599/camodel/libstars.so"
COMMON_SO="${ACL_PATH}/x86_64-linux/simulator/dav_3510/camodel/libcommon.so"
UTILITY_SO="${ACL_PATH}/x86_64-linux/simulator/dav_3510/camodel/libUtility.so"
ZLIB_SO="/lib/x86_64-linux-gnu/libz.so.1"
CSEC_SO="${ACL_PATH}/x86_64-linux/lib64/libc_sec.so"
MSPROF_BIN="${ACL_PATH}/x86_64-linux/bin/msprof"
STAR_SHIM_SRC="/mnt/d/VfSimulator/ascend_runner/shim_dlopen_stars.cpp"
STAR_SHIM_SO="/home/lenovo/msprof_run/libshim_dlopen_stars.so"
SECURE_SHIM_SRC="/mnt/d/VfSimulator/ascend_runner/shim_secure_checks.cpp"
SECURE_SHIM_SO="/home/lenovo/msprof_run/libshim_secure_checks.so"
LOGGER_SHIM_SRC="/mnt/d/VfSimulator/ascend_runner/shim_spd_logger.cpp"
LOGGER_SHIM_SO="/home/lenovo/msprof_run/libshim_spd_logger.so"
if [[ ! -x "${MSPROF_BIN}" ]]; then
  echo "msprof not found: ${MSPROF_BIN}"
  exit 4
fi

mkdir -p /home/lenovo/msprof_run
if [[ -f "${STAR_SHIM_SRC}" ]]; then
  g++ -shared -fPIC -O2 -o "${STAR_SHIM_SO}" "${STAR_SHIM_SRC}" -ldl
fi
if [[ -f "${SECURE_SHIM_SRC}" ]]; then
  g++ -shared -fPIC -O2 -o "${SECURE_SHIM_SO}" "${SECURE_SHIM_SRC}" -L"$(dirname "${CSEC_SO}")" -lc_sec
fi
if [[ -f "${LOGGER_SHIM_SRC}" ]]; then
  g++ -shared -fPIC -O2 -o "${LOGGER_SHIM_SO}" "${LOGGER_SHIM_SRC}"
fi

LOCAL_ROOT="/home/lenovo/msprof_run"
LOCAL_DIR="${LOCAL_ROOT}/${STEM}_native_msprof"
rm -rf "${LOCAL_DIR}"
mkdir -p "${LOCAL_DIR}"
cp -f "${APP_PATH}" "${LOCAL_DIR}/${APP_NAME}"
cp -f "${KERNEL_BIN}" "${LOCAL_DIR}/$(basename "${KERNEL_BIN}")"
chmod 700 "${LOCAL_DIR}"
chmod 700 "${LOCAL_DIR}/${APP_NAME}"

cd "${LOCAL_DIR}"
echo "[INFO] Running native msprof simulator"
echo "[INFO] App         : ./${APP_NAME}"
echo "[INFO] Kernel bin  : ./$(basename "${KERNEL_BIN}")"
echo "[INFO] Kernel      : ${KERNEL_NAME}"
echo "[INFO] Work dir    : ${LOCAL_DIR}"
echo "[INFO] Logger shim : ${LOGGER_SHIM_SO}"
echo "[INFO] Secure shim : ${SECURE_SHIM_SO}"
echo "[INFO] Star shim   : ${STAR_SHIM_SO}"

PRELOADS=""
for so in "${LOGGER_SHIM_SO}" "${SECURE_SHIM_SO}" "${STAR_SHIM_SO}"; do
  if [[ -f "$so" ]]; then
    if [[ -n "${PRELOADS}" ]]; then
      PRELOADS="${PRELOADS}:$so"
    else
      PRELOADS="$so"
    fi
  fi
done
if [[ -n "${LD_PRELOAD:-}" ]]; then
  if [[ -n "${PRELOADS}" ]]; then
    PRELOADS="${PRELOADS}:${LD_PRELOAD}"
  else
    PRELOADS="${LD_PRELOAD}"
  fi
fi

if [[ -n "${PRELOADS}" && -f "${STAR_SO}" && -f "${UTILITY_SO}" && -f "${COMMON_SO}" && -f "${ZLIB_SO}" && -f "${CSEC_SO}" ]]; then
  env LD_LIBRARY_PATH="$(dirname "${CSEC_SO}"):${LD_LIBRARY_PATH:-}" \
      SIM_ZLIB_SO="${ZLIB_SO}" \
      SIM_COMMON_SO="${COMMON_SO}" \
      SIM_UTILITY_SO="${UTILITY_SO}" \
      SIM_STAR_SO="${STAR_SO}" \
      LD_PRELOAD="${PRELOADS}" \
      "${MSPROF_BIN}" op simulator --dump=on "./${APP_NAME}" -- "./$(basename "${KERNEL_BIN}")" "${KERNEL_NAME}"
else
  "${MSPROF_BIN}" op simulator --dump=on "./${APP_NAME}" -- "./$(basename "${KERNEL_BIN}")" "${KERNEL_NAME}"
fi