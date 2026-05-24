#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_bridge_pairs.log
CMDS=$APPROOT/gdb_bridge_pairs.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty off
break _ZN17totem_v2_chi_ring10TMultiRing17connectRingBridgeENS_12TConnectNodeEb
commands
silent
printf "BRIDGE this=%p a=%u b=%u p0=%p p1=%p tail=%u\n", $rdi, *(unsigned int*)$rsi, *(unsigned int*)($rsi+4), *(void**)($rsi+8), *(void**)($rsi+16), *(unsigned int*)($rsi+28)
continue
end
break _ZN17totem_v2_chi_ring13TCrossStation8createNiEj
commands
silent
printf "CREATE station=%p dir=%u\n", $rdi, (unsigned int)$rsi
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
log = Path('/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min/gdb_bridge_pairs.log').read_text(errors='ignore').splitlines()
count=0
for line in log:
    if line.startswith('BRIDGE ') or line.startswith('CREATE '):
        print(line)
        count += 1
        if count >= 120:
            break
PY