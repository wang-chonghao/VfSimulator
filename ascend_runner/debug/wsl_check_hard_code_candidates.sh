#!/usr/bin/env bash
set -euo pipefail
libs=(
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libUtility.so
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libpem_davinci.so
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libstars_wrapper.so
)
for f in "${libs[@]}"; do
  echo "=== $f ==="
  readelf -Ws "$f" 2>/dev/null | grep 'hard_code_toml_cfg_map' || echo '__no_dynamic_symbol__'
  echo '-- strings hit --'
  strings "$f" | grep 'hard_code_toml_cfg_map' || echo '__no_string__'
  echo

done