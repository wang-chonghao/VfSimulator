#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_generateRouter_fixed.log
CMDS=$APPROOT/gdb_generateRouter_fixed.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty off
break _ZN17totem_v2_chi_ring10TMultiRing14generateRouterEjNS_11TRouterNodeES1_
commands
silent
printf "GEN out=%p ring=%p u=%u n1=%p n2=%p\n", $rdi, $rsi, (unsigned int)$rdx, $rcx, $r8
printf "N1: %u %u %u %u\n", *(unsigned int*)$rcx, *(unsigned int*)($rcx+4), *(unsigned int*)($rcx+8), *(unsigned int*)($rcx+12)
printf "N2: %u %u %u %u\n", *(unsigned int*)$r8, *(unsigned int*)($r8+4), *(unsigned int*)($r8+8), *(unsigned int*)($r8+12)
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
lines = Path('/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min/gdb_generateRouter_fixed.log').read_text(errors='ignore').splitlines()
out=[]
for line in lines:
    if line.startswith('GEN ') or line.startswith('N1:') or line.startswith('N2:'):
        out.append(line)
        if len(out) >= 180:
            break
print('\n'.join(out))
PY