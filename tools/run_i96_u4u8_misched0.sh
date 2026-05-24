#!/usr/bin/env bash
set -euo pipefail

REPO=/mnt/d/VfSimulator
ACL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
CCEC="$ACL/x86_64-linux/bin/ccec"
LDLLD="$ACL/x86_64-linux/bin/ld.lld"

for u in 4 8; do
  stem="GeLU_poly_I96_unroll${u}_misched0"
  dsl="$REPO/cce_code/GeLU_poly_I96_unroll${u}.dsl"
  build="$REPO/ascend_runner/build/${stem}_native_simexec"
  cce="$build/${stem}.cce"
  aiv="$build/${stem}_mix_aiv.o"
  mix="$build/${stem}_mix.o"
  sim="$build/${stem}_simexec"

  bash "$REPO/ascend_runner/current/build_native_simexec.sh" "$dsl" "$stem"

  "$CCEC" -g -std=c++17 -c -O2 "$cce" -o "$aiv" \
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

  "$LDLLD" -Ttext=0 "$aiv" -static -o "$mix"

  bash "$REPO/ascend_runner/current/run_native_simexec.sh" \
    "$sim" "$mix" foo_add 2 1 6144

  src="/home/lenovo/msprof_run/${stem}_native_simexec"
  dst="$REPO/results/unroll_test/cce_dump/I96_unroll${u}_misched0"
  mkdir -p "$dst"
  cp -f "$src/core0.veccore0.instr_log.dump" "$dst/"
  cp -f "$src/core0.veccore0.instr_popped_log.dump" "$dst/"
  cp -f "$src/core0.veccore0.rvec.simd.ifu.dump" "$dst/"
  cp -f "$src/core0.veccore0.rvec.IDU.dump" "$dst/"
done

