#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <sim_executable>"
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

SIM_APP="$1"
if [[ ! -f "${SIM_APP}" ]]; then
  echo "Simulator app not found: ${SIM_APP}"
  exit 3
fi

APP_DIR="$(cd "$(dirname "${SIM_APP}")" && pwd)"
APP_NAME="$(basename "${SIM_APP}")"
ROOT_SIM="${ASCEND_HOME_PATH}/x86_64-linux/simulator/dav_3510"
SIMLIB="${ROOT_SIM}/lib"
CAMODEL="${ROOT_SIM}/camodel"
COMMON="${ASCEND_HOME_PATH}/x86_64-linux/lib64"
DEVICE="${ASCEND_HOME_PATH}/x86_64-linux/lib64/device/lib64"

RUN_DIR="${APP_DIR}/plain_run"
rm -rf "${RUN_DIR}"
mkdir -p "${RUN_DIR}/etc"

cp -f "${APP_DIR}/${APP_NAME}" "${RUN_DIR}/${APP_NAME}"
for so in "${APP_DIR}"/lib*_kernel.so; do
  [[ -f "${so}" ]] || continue
  cp -f "${so}" "${RUN_DIR}/"
done

cat > "${RUN_DIR}/etc/1982_cloud_config.toml" <<'EOF'
[ARCH]
cube_core_num = 1
vec_core_num = 2

[WRAPPER]
adapter_log_file_level = 6
EOF

cp -f "${SIMLIB}/Ascend950pr_9599_model.toml" "${RUN_DIR}/Ascend950pr_9599_model.toml"
cp -f "${SIMLIB}/Ascend950pr_9599_stars_ffts_plus.toml" "${RUN_DIR}/Ascend950pr_9599_stars_ffts_plus.toml"
cp -f "${SIMLIB}/Ascend950pr_9599_ffts.toml" "${RUN_DIR}/Ascend950pr_9599_ffts.toml"
cp -f "${SIMLIB}/Ascend950pr_9599_ffts_plus.toml" "${RUN_DIR}/Ascend950pr_9599_ffts_plus.toml"

cat > "${RUN_DIR}/1981_sim_ffts_plus.toml" <<'EOF'
model_cfg = "Ascend950pr_9599_model.toml"
stars_cfg = "Ascend950pr_9599_stars_ffts_plus.toml"
ffts_cfg = "Ascend950pr_9599_ffts.toml"
ffts_plus_cfg = "Ascend950pr_9599_ffts_plus.toml"
enable = 1
file_print_level = 2
screen_print_level = 2
flush_level = 2
rotating_file_size = 134217728
rotating_file_number = 2
EOF

export LD_LIBRARY_PATH="${RUN_DIR}:${CAMODEL}:${SIMLIB}:${COMMON}:${DEVICE}"
unset LD_PRELOAD

echo "[INFO] Running plain PTO-style simulator executable"
echo "[INFO] Run dir: ${RUN_DIR}"
(
  cd "${RUN_DIR}"
  "./${APP_NAME}"
)