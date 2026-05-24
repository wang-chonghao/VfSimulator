#!/usr/bin/env bash
set -euo pipefail
for f in /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/libc_sec.so /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/devlib/device/libc_sec.so; do
  [[ -f "$f" ]] || continue
  echo "=== $f ==="
  strings "$f" | egrep 'safe_mem|memcpy_s|memset_s|securec' | head -n 50 || true
  echo
done