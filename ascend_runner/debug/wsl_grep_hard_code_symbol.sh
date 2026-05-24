#!/usr/bin/env bash
set -euo pipefail
root=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
find "$root" -type f \( -name '*.so' -o -perm -111 \) -print0 | while IFS= read -r -d '' f; do
  if grep -a -q 'hard_code_toml_cfg_map' "$f" 2>/dev/null; then
    echo "$f"
  fi
done