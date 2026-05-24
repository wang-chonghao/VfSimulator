#!/usr/bin/env bash
set -euo pipefail
sym='_Z22safe_memcpy_with_checkPvmPKvm'
scan_dirs=(
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/lib64
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/devlib/device
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
  /lib/x86_64-linux-gnu
  /usr/lib/x86_64-linux-gnu
)
for d in "${scan_dirs[@]}"; do
  [[ -d "$d" ]] || continue
  while IFS= read -r -d '' f; do
    if grep -a -q "$sym" "$f" 2>/dev/null; then
      echo "$f"
    fi
  done < <(find "$d" -maxdepth 1 -type f \( -name '*.so' -o -name '*.so.*' -o -perm -111 \) -print0)
done