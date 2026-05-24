#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

out_dir="${1:-results/fp16_config_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "${out_dir}"
log_file="${out_dir}/run.log"

run_case() {
  local dsl="$1"
  local stem="$2"
  local num_inputs="$3"
  local total_elems="$4"

  echo "[CASE] ${stem}" | tee -a "${log_file}"
  if ! bash ascend_runner/current/build_native_simexec.sh "${dsl}" "${stem}" >>"${log_file}" 2>&1; then
    echo "[WARN] build failed: ${stem}" | tee -a "${log_file}"
    return 1
  fi
  if ! bash ascend_runner/current/run_native_simexec.sh \
      "ascend_runner/build/${stem}_native_simexec/${stem}_simexec" \
      "ascend_runner/build/${stem}_native_simexec/${stem}_mix.o" \
      foo_add "${num_inputs}" 1 "${total_elems}" >>"${log_file}" 2>&1; then
    echo "[WARN] run returned non-zero (kept dumps): ${stem}" | tee -a "${log_file}"
  fi
  return 0
}

# full single-op fp16 (15 ops)
for dsl in ascend_runner/single_op_param_suite/cases/fp16/singleop_*.dsl; do
  b="$(basename "${dsl}" .dsl)"
  inputs=1
  case "${b}" in
    singleop_vadd|singleop_vsub|singleop_vmul|singleop_vmax|singleop_vmin|singleop_vdiv) inputs=2 ;;
  esac
  run_case "${dsl}" "${b}_fp16" "${inputs}" 128 || true
done

# forwarding fp16: VADDS producer row
for dsl in ascend_runner/forwarding_param_suite/cases/fp16/fwd_vadds_to_*.dsl; do
  b="$(basename "${dsl}" .dsl)"
  run_case "${dsl}" "${b}_fp16" 1 128 || true
done

# II fp16: VADDS prev row
for dsl in ascend_runner/ii_param_suite/cases/fp16/ii_vadds_to_*.dsl; do
  b="$(basename "${dsl}" .dsl)"
  run_case "${dsl}" "${b}_fp16" 2 2048 || true
done

echo "[DONE] fp16 batch finished. log=${log_file}" | tee -a "${log_file}"
echo "${out_dir}"

