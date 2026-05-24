#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_connectRingBridge.log
CMDS=$APPROOT/gdb_connectRingBridge.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty on
break _ZN17totem_v2_chi_ring10TMultiRing17connectRingBridgeENS_12TConnectNodeEb
commands
silent
printf "\n=== connectRingBridge ===\n"
printf "this=%p rsi=0x%llx rdx=0x%llx rcx=0x%llx r8=0x%llx r9=0x%llx\n", $rdi, (unsigned long long)$rsi, (unsigned long long)$rdx, (unsigned long long)$rcx, (unsigned long long)$r8, (unsigned long long)$r9
x/8gx $rsp
bt 8
continue
end
break _ZN17totem_v2_chi_ring13TCrossStation8createNiEj
commands
silent
printf "\n=== createNi === this=%p direction=%llu ===\n", $rdi, (unsigned long long)$rsi
bt 4
continue
end
run
bt 20
EOF
export LD_LIBRARY_PATH="$APPROOT:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPROOT/libshim_catDiePortId.so:$APPROOT/libshim_axi_ctor.so"
cd "$APPROOT"
gdb -q -batch -x "$CMDS" --args ./src_fanout_probe_cce_min > "$LOG" 2>&1 || true
sed -n '1,360p' "$LOG"