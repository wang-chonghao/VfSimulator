#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_generateRouter.log
CMDS=$APPROOT/gdb_generateRouter.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty off
break _ZN17totem_v2_chi_ring10TMultiRing14generateRouterEjNS_11TRouterNodeES1_
commands
silent
printf "GEN this=%p in=%u rdx=0x%llx rcx=0x%llx r8=0x%llx r9=0x%llx\n", $rdi, (unsigned int)$rsi, (unsigned long long)$rdx, (unsigned long long)$rcx, (unsigned long long)$r8, (unsigned long long)$r9
x/8wx $rdx
x/8wx $rcx
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
lines = Path('/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min/gdb_generateRouter.log').read_text(errors='ignore').splitlines()
out=[]
for line in lines:
    if line.startswith('GEN ') or line.startswith('0x'):
        out.append(line)
        if len(out) >= 180:
            break
print('\n'.join(out))
PY