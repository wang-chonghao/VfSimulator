#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

out_dir="${1:-results/fp16_config_full_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "${out_dir}"
log_file="${out_dir}/run_full.log"
msprof_root="${MSPROF_ROOT:-/home/lenovo/msprof_run}"

need_build() {
  local stem="$1"
  [[ ! -x "ascend_runner/build/${stem}_native_simexec/${stem}_simexec" ]] || [[ ! -f "ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" ]]
}

has_dump() {
  local stem="$1"
  [[ -f "${msprof_root}/${stem}_native_simexec/core0.veccore0.instr_popped_log.dump" ]]
}

run_case() {
  local dsl="$1"
  local stem="$2"
  local num_inputs="$3"
  local total_elems="$4"

  if has_dump "${stem}"; then
    echo "[SKIP] dump exists: ${stem}" | tee -a "${log_file}"
    return 0
  fi

  echo "[CASE] ${stem}" | tee -a "${log_file}"
  if need_build "${stem}"; then
    if ! bash ascend_runner/current/build_native_simexec.sh "${dsl}" "${stem}" >>"${log_file}" 2>&1; then
      echo "[WARN] build failed: ${stem}" | tee -a "${log_file}"
      return 1
    fi
  fi

  if ! bash ascend_runner/current/run_native_simexec.sh \
      "ascend_runner/build/${stem}_native_simexec/${stem}_simexec" \
      "ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" \
      foo_add "${num_inputs}" 1 "${total_elems}" >>"${log_file}" 2>&1; then
    echo "[WARN] run returned non-zero (kept dumps): ${stem}" | tee -a "${log_file}"
  fi
  return 0
}

echo "[INFO] out_dir=${out_dir}" | tee -a "${log_file}"
echo "[INFO] msprof_root=${msprof_root}" | tee -a "${log_file}"

# single-op fp16: full 15
for dsl in ascend_runner/single_op_param_suite/cases/fp16/singleop_*.dsl; do
  b="$(basename "${dsl}" .dsl)"
  inputs=1
  case "${b}" in
    singleop_vadd|singleop_vsub|singleop_vmul|singleop_vmax|singleop_vmin|singleop_vdiv) inputs=2 ;;
  esac
  run_case "${dsl}" "${b}_fp16" "${inputs}" 2048 || true
done

# forwarding fp16: full 225
for dsl in ascend_runner/forwarding_param_suite/cases/fp16/fwd_*.dsl; do
  b="$(basename "${dsl}" .dsl)"
  run_case "${dsl}" "${b}_fp16" 1 128 || true
done

# II fp16: full 225
for dsl in ascend_runner/ii_param_suite/cases/fp16/ii_*.dsl; do
  b="$(basename "${dsl}" .dsl)"
  run_case "${dsl}" "${b}_fp16" 2 4096 || true
done

echo "[DONE] fp16 full batch finished. log=${log_file}" | tee -a "${log_file}"
echo "${out_dir}"

