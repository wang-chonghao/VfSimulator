#!/usr/bin/env bash
set -euo pipefail
sym='_ZN10SPD_LOGGER7esl_log13is_log_enableESsSs'
root=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
find "$root" -type f \( -name '*.so' -o -perm -111 \) -print0 | while IFS= read -r -d '' f; do
  if readelf -Ws "$f" 2>/dev/null | grep -q "$sym"; then
    echo "=== $f ==="
    readelf -Ws "$f" 2>/dev/null | grep "$sym" || true
  fi
done