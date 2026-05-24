#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
LOG=$APPROOT/gdb_createNi_disas.log
CMDS=$APPROOT/gdb_createNi_disas.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty off
file /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel/libChiRingFabric.so
set disassembly-flavor intel
disas _ZN17totem_v2_chi_ring13TCrossStation8createNiEj
quit
EOF
gdb -q -batch -x "$CMDS" > "$LOG" 2>&1 || true
python3 - <<'PY'
from pathlib import Path
p = Path('/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min/gdb_createNi_disas.log')
print(p.read_text(errors='ignore'))
PY
