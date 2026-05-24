#!/usr/bin/env bash
set -euo pipefail
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
  find "$d" -maxdepth 1 -type f \( -iname '*secure*' -o -iname '*c_sec*' -o -iname '*bounds*' -o -iname '*check*' \) -printf '%p\n' || true
done | sort -u