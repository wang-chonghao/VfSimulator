#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_cal_disas.log
CMDS=$APPROOT/gdb_cal_disas.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty off
file /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libChiRingFabric.so
set disassembly-flavor intel
disas _ZN17totem_v2_chi_ring13TCrossStation18calOutputDirectionEj
disas _ZN17totem_v2_chi_ring10TMultiRing14pickSrcRouteDstEjSt6vectorIjSaIjEES3_
quit
EOF
gdb -q -batch -x "$CMDS" > "$LOG" 2>&1 || true
python3 - <<'PY'
from pathlib import Path
p = Path('/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min/gdb_cal_disas.log')
print(p.read_text(errors='ignore'))
PY
