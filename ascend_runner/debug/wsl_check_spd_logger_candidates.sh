#!/usr/bin/env bash
set -euo pipefail
sym='_ZN10SPD_LOGGER7esl_log13is_log_enableESsSs'
libs=(
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libUtility.so
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libcommon.so
  /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libUB.so
)
for f in "${libs[@]}"; do
  echo "=== $f ==="
  nm -A "$f" 2>/dev/null | grep "$sym" || echo '__nm_nohit__'
  readelf -Ws "$f" 2>/dev/null | grep "$sym" || echo '__dyn_nohit__'
  echo
done