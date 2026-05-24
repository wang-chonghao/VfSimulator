#!/usr/bin/env bash
set -euo pipefail
source /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/set_env.sh >/dev/null 2>&1
APPROOT=/mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_cce_min
SIMLIB=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/lib
CAMODEL=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/simulator/dav_3510/camodel
COMMON=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64
DEVICE=/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1/x86_64-linux/lib64/device/lib64
LOG=$APPROOT/gdb_createNi.log
CMDS=$APPROOT/gdb_createNi.cmds
cat > "$CMDS" <<'EOF'
set breakpoint pending on
set print pretty on
break _ZN18AXI_STREAM_BUS_SIM15TNodeWrrSchdMgrC2EjPNS_19TAxiStreamBusConfigE
commands
silent
printf "\n=== AXI ctor ===\n"
printf "this=%p node=%llu cfg=%p\n", $rdi, (unsigned long long)$rsi, $rdx
bt 4
continue
end
break _ZN17totem_v2_chi_ring13TCrossStation8createNiEj
commands
silent
printf "\n=== createNi ===\n"
printf "this=%p direction=%llu\n", $rdi, (unsigned long long)$rsi
bt 8
continue
end
run
bt 20
EOF
export LD_LIBRARY_PATH="$APPROOT:$CAMODEL:$SIMLIB:$COMMON:$DEVICE"
export LD_PRELOAD="$APPROOT/libshim_catDiePortId.so:$APPROOT/libshim_axi_ctor.so"
cd "$APPROOT"
gdb -q -batch -x "$CMDS" --args ./src_fanout_probe_cce_min > "$LOG" 2>&1 || true
sed -n '1,320p' "$LOG"