#!/usr/bin/env bash
set -euo pipefail
libs=(
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/libc_sec.so
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/devlib/device/libc_sec.so
)
for f in "${libs[@]}"; do
  [[ -f "$f" ]] || continue
  echo "=== $f ==="
  readelf -Ws "$f" | egrep '_Z22safe_memcpy_with_checkPvmPKvm|_Z22safe_memset_with_checkPvmim' || echo '__nohit__'
  echo
done