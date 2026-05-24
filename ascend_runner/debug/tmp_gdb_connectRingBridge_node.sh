#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_connectRingBridge_node.log
CMDS=$APPROOT/gdb_connectRingBridge_node.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty on
break _ZN17totem_v2_chi_ring10TMultiRing17connectRingBridgeENS_12TConnectNodeEb
commands
silent
printf "\n=== connectRingBridge ===\n"
printf "this=%p node_ptr=%p flag=%llu rcx=%p r8=0x%llx r9=0x%llx\n", $rdi, $rsi, (unsigned long long)$rdx, $rcx, (unsigned long long)$r8, (unsigned long long)$r9
x/16wx $rsi
x/8gx $rsi
bt 6
continue
end
run
bt 20
EOF
export LD_LIBRARY_PATH="$APPROOT:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPROOT/libshim_catDiePortId.so:$APPROOT/libshim_axi_ctor.so"
cd "$APPROOT"
gdb -q -batch -x "$CMDS" --args ./src_fanout_probe_cce_min > "$LOG" 2>&1 || true
sed -n '1,260p' "$LOG"