#!/usr/bin/env bash
set -euo pipefail
base1=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/tools/simulator/Ascend950PR_9599
base2=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510
scan_dirs=(
  "$base1/camodel"
  "$base1/lib"
  "$base2/camodel"
  "$base2/lib"
)
found=0
for d in "${scan_dirs[@]}"; do
  if [[ -d "$d" ]]; then
    while IFS= read -r -d '' f; do
      if readelf -Ws "$f" 2>/dev/null | grep -q 'hard_code_toml_cfg_map'; then
        echo "=== $f ==="
        readelf -Ws "$f" 2>/dev/null | grep 'hard_code_toml_cfg_map' || true
        found=1
      fi
    done < <(find "$d" -maxdepth 1 -type f \( -name '*.so' -o -perm -111 \) -print0)
  fi
done
if [[ "$found" -eq 0 ]]; then
  echo '__NOT_FOUND__'
fi