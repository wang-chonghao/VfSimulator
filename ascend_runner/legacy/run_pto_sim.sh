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
CAMODEL="${ROOT_SIM}/camodel"
SIMLIB="${ROOT_SIM}/lib"
COMMON="${ASCEND_HOME_PATH}/x86_64-linux/lib64"
DEVICE="${ASCEND_HOME_PATH}/x86_64-linux/lib64/device/lib64"

for f in \
  Ascend950pr_9599_sim.toml \
  Ascend950pr_9599_model.toml \
  Ascend950pr_9599_stars.toml \
  Ascend950pr_9599_ffts.toml \
  Ascend950pr_9599_ffts_plus.toml; do
  cp -f "${SIMLIB}/$f" "${APP_DIR}/$f"
done

# Some simulator components still look for the legacy-named FFTS+ entry config
# in the local run directory. Keep that filename aligned with the official
# 9599 simulator config so we do not accidentally reuse a stale simplified stub.
cp -f "${SIMLIB}/Ascend950pr_9599_sim_ffts_plus.toml" "${APP_DIR}/1981_sim_ffts_plus.toml"

# Keep the cloud config fully aligned with the simulator-provided version.
# We observed local minimal stubs here can make core_wrapper read incomplete
# ARCH/WRAPPER fields and end up with cube_core_num=0.
mkdir -p "${APP_DIR}/etc"
cp -f "${SIMLIB}/1982_cloud_config.toml" "${APP_DIR}/1982_cloud_config.toml"
cp -f "${SIMLIB}/1982_cloud_config.toml" "${APP_DIR}/etc/1982_cloud_config.toml"

export LD_LIBRARY_PATH="${APP_DIR}:${CAMODEL}:${SIMLIB}:${COMMON}:${DEVICE}"
export LD_PRELOAD="${APP_DIR}/libshim_axi_ctor.so:${APP_DIR}/libshim_cfg_axi_abi0.so:${APP_DIR}/libshim_catDiePortId_chiring_only.so:${APP_DIR}/libshim_cfg_manyring.so:${APP_DIR}/libshim_dump_manyring.so:/lib/x86_64-linux-gnu/libz.so.1:${CAMODEL}/libUtility.so:${SIMLIB}/libffts_model.so:${SIMLIB}/libstars_pv.so:${SIMLIB}/libnpu_drv.so:${SIMLIB}/libmcu_loop.so:${SIMLIB}/libmcu_wrapper.so:${CAMODEL}/libmodel_api.so:${CAMODEL}/libEslTop.so:${CAMODEL}/libSoC.so:${CAMODEL}/libHISI_CHI_IF.so:${CAMODEL}/libcommon.so:${CAMODEL}/libstars_wrapper.so:${CAMODEL}/libstars.so:${CAMODEL}/libSMMU.so:${CAMODEL}/libAA.so:${CAMODEL}/libSCHE.so:${CAMODEL}/libPMU.so:${CAMODEL}/libTgWrapper.so:${CAMODEL}/libqtest_api.so:${CAMODEL}/libMATA.so:${CAMODEL}/libDDR_Inf.so:${CAMODEL}/libSDMAA.so:${CAMODEL}/libSDMAM.so:${CAMODEL}/libAXI_STREAM_BUS.so:${CAMODEL}/libNcMpi.so:${CAMODEL}/libUB.so:${CAMODEL}/libTaskSched.so:${CAMODEL}/libDVPP_CA.so:${CAMODEL}/libSLLC.so:${CAMODEL}/libL2Buf.so:${CAMODEL}/libPCIE.so:${CAMODEL}/libChiRingFabric.so:${CAMODEL}/libaicpu_wrapper.so:${CAMODEL}/libaicpu.so:${CAMODEL}/libPowerModel.so:${CAMODEL}/libCuberWrapper.so:${CAMODEL}/libbailusim.so:${CAMODEL}/liblpddrsim.so:${CAMODEL}/libmemsys.so:${CAMODEL}/libParallelScheduler.so:${CAMODEL}/libpem_davinci.so"

echo "[INFO] Running PTO-style simulator executable"
echo "[INFO] App: ${APP_DIR}/${APP_NAME}"
(
  cd "${APP_DIR}"
  "./${APP_NAME}"
)
