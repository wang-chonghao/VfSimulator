#!/usr/bin/env bash
set -euo pipefail
sym='_Z22safe_memcpy_with_checkPvmPKvm'
root=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
find "$root" -type f \( -name '*.so' -o -perm -111 \) -print0 | while IFS= read -r -d '' f; do
  if grep -a -q "$sym" "$f" 2>/dev/null; then
    echo "$f"
  fi
done