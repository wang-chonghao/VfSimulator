#!/usr/bin/env bash
set -euo pipefail
sym='_ZN19camodel_file_config22get_config_by_filenameERKSs'
libs=(
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libUtility.so
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libcommon.so
)
for f in "${libs[@]}"; do
  echo "=== $f ==="
  nm -A "$f" 2>/dev/null | grep "$sym" || echo '__nm_nohit__'
  readelf -Ws "$f" 2>/dev/null | grep "$sym" || echo '__dyn_nohit__'
  echo
done