#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_calOutputDirection.log
CMDS=$APPROOT/gdb_calOutputDirection.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty off
break _ZN17totem_v2_chi_ring13TCrossStation18calOutputDirectionEj
commands
silent
printf "CAL this=%p in=%u\n", $rdi, (unsigned int)$rsi
finish
printf "RET out=%u\n", (unsigned int)$rax
continue
end
run
EOF
export LD_LIBRARY_PATH="$APPROOT:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPROOT/libshim_catDiePortId.so:$APPROOT/libshim_axi_ctor.so"
cd "$APPROOT"
gdb -q -batch -x "$CMDS" --args ./src_fanout_probe_cce_min > "$LOG" 2>&1 || true
python3 - <<'PY'
from pathlib import Path
lines = Path('/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min/gdb_calOutputDirection.log').read_text(errors='ignore').splitlines()
count=0
for line in lines:
    if line.startswith('CAL ') or line.startswith('RET '):
        print(line)
        count += 1
        if count >= 160:
            break
PY