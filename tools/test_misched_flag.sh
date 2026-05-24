#!/usr/bin/env bash
set -euo pipefail

CCEC="/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/bin/ccec"
SRC_DSL="/mnt/d/VfSimulator/cce_code/GeLU_poly.dsl"
OUT_DIR="/mnt/d/VfSimulator/ascend_runner/build/flag_try"
SRC_CCE="${OUT_DIR}/GeLU_poly_for_flag_test.cce"
OUT_OBJ="${OUT_DIR}/GeLU_poly_misched0.o"

mkdir -p "${OUT_DIR}"
cp "${SRC_DSL}" "${SRC_CCE}"

"${CCEC}" -g -std=c++17 -c -O2 "${SRC_CCE}" -o "${OUT_OBJ}" \
  -I/usr/include/c++/11 \
  -I/usr/include/aarch64-linux-gnu/c++/11 \
  --cce-aicore-arch=dav-c310-vec \
  --cce-aicore-only \
  -mllvm -cce-aicore-function-stack-size=16000 \
  -mllvm -cce-aicore-record-overflow=false \
  -mllvm -cce-aicore-addr-transform \
  -mllvm -cce-aicore-jump-expand=true \
  -mllvm -cce-aicore-vec-misched=0 \
  --cce-simd-vf-fusion=false

echo "OK: ${OUT_OBJ}"
