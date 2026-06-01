# CCE Simulator Guide

This document records the currently verified path for running CCE DSL kernels through the Ascend simulator from this repository.

The route is now generic enough to cover different kernel argument shapes, as long as we tell the host runner:

- kernel name
- number of GM inputs
- number of GM outputs
- total element count per tensor

Two validated examples are already covered by this route:

- `cce_code/src_fanout_probe.dsl`
  - kernel name: `src_fanout_probe`
  - shape: `1 input + 1 output`
- `cce_code/GeLU_poly.dsl`
  - kernel name: `foo_add`
  - shape: `2 inputs + 1 output`

## 1. Environment

This guide assumes:

- OS for simulator run: WSL Ubuntu
- CANN version: `9.0.0-beta.1`
- simulator target: `Ascend950PR_9599`
- aicore arch used by `ccec`: `dav-c310-vec`

Toolkit root used by the scripts:

```bash
/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1
```

The build script also accepts `latest`, but the validated path was run against `cann-9.0.0-beta.1`.

## 2. Verified Route

The route that works is:

1. `dsl -> cce`
2. `ccec`
3. `ld.lld`
4. build a direct host executable linked with `runtime_camodel`
5. run that executable directly
6. collect simulator dump files and host-side golden-check results

What we are **not** relying on for the main path anymore:

- `msprof op simulator` child-process loading as the primary execution route
- large manual `LD_PRELOAD` repair chains

## 3. Files Involved

### 3.1 Input DSL

Any `.dsl` or `.cce` file can be used as input.

Validated examples:

- `cce_code/src_fanout_probe.dsl`
- `cce_code/GeLU_poly.dsl`

### 3.2 Build script

- [build_native_simexec.sh](d:/VfSimulator/ascend_runner/current/build_native_simexec.sh)

This script:

- copies the input DSL into a build directory as `.cce`
- runs `ccec`
- runs `ld.lld`
- builds a host executable linked with `runtime_camodel`
- writes a small environment file for runtime launch

### 3.3 Run script

- [run_native_simexec.sh](d:/VfSimulator/ascend_runner/current/run_native_simexec.sh)

This script:

- copies the built sim executable and kernel binary into a WSL-local run directory
- sources the runtime environment written during build
- directly runs the simulator executable
- accepts the kernel runtime shape parameters on the command line

### 3.4 Generic host runner

- [native_runtime_generic_main.cpp](d:/VfSimulator/ascend_runner/current/native_runtime_generic_main.cpp)

Current capabilities:

- supports arbitrary `N inputs + M outputs` at runtime
- reads kernel binary path and kernel name from argv
- optionally reads:
  - `num-inputs`
  - `num-outputs`
  - `total-elems`
- uses runtime registration APIs:
  - `rtDevBinaryRegister`
  - `rtFunctionRegister`
  - `rtGetFunctionByName`
  - `rtKernelLaunch`

## 4. Build Steps

Run in WSL Ubuntu from repo root:

```bash
cd /mnt/d/VfSimulator
bash ascend_runner/current/build_native_simexec.sh <path-to.dsl-or.cce> [output-stem]
```

Example 1:

```bash
bash ascend_runner/current/build_native_simexec.sh cce_code/src_fanout_probe.dsl src_fanout_probe
```

Example 2:

```bash
bash ascend_runner/current/build_native_simexec.sh cce_code/GeLU_poly.dsl GeLU_poly
```

What this produces:

- build dir:
  - `ascend_runner/build/<stem>_native_simexec`
- generated `.cce`:
  - `ascend_runner/build/<stem>_native_simexec/<stem>.cce`
- kernel binary:
  - `ascend_runner/build/<stem>_native_simexec/<stem>_mix.o`
- host sim executable:
  - `ascend_runner/build/<stem>_native_simexec/<stem>_simexec`
- runtime env helper:
  - `ascend_runner/build/<stem>_native_simexec/run_simexec_env.sh`

### 4.1 Build command details

#### `ccec`

```bash
ccec -g -std=c++17 -c -O2 <kernel>.cce -o <kernel>_mix_aiv.o \
  -I/usr/include/c++/11 \
  -I/usr/include/aarch64-linux-gnu/c++/11 \
  --cce-aicore-arch=dav-c310-vec \
  --cce-aicore-only \
  -mllvm -cce-aicore-function-stack-size=16000 \
  -mllvm -cce-aicore-record-overflow=false \
  -mllvm -cce-aicore-addr-transform \
  -mllvm -cce-aicore-jump-expand=true \
  --cce-simd-vf-fusion=false
```

#### `ld.lld`

```bash
ld.lld -Ttext=0 <kernel>_mix_aiv.o -static -o <kernel>_mix.o
```

#### host sim executable

The host executable is linked against:

- `runtime_camodel`
- `ascendcl`
- `platform`
- `c_sec`
- `dl`
- `nnopbase`

and uses `rpath` to point at:

- `${ACL_PATH}/lib64`
- `${ACL_PATH}/aarch64-linux/simulator/Ascend950PR_9599/lib`
- `${ACL_PATH}/simulator/Ascend950PR_9599/lib`
- `${ACL_PATH}/tools/simulator/Ascend950PR_9599/lib`
- relevant `devlib` directories

## 5. Run Steps

Run in WSL Ubuntu from repo root:

```bash
cd /mnt/d/VfSimulator
bash ascend_runner/current/run_native_simexec.sh <sim-exec> [kernel-bin] [kernel-name] [num-inputs] [num-outputs] [total-elems]
```

### 5.1 Runtime arguments

`run_native_simexec.sh` now supports:

```bash
bash ascend_runner/current/run_native_simexec.sh \
  <sim-exec> \
  [kernel-bin] \
  [kernel-name] \
  [num-inputs] \
  [num-outputs] \
  [total-elems]
```

Meaning:

- `sim-exec`: host simulator executable
- `kernel-bin`: linked kernel binary, defaults to `<stem>_mix.o`
- `kernel-name`: kernel symbol inside the `.cce`
- `num-inputs`: number of GM input pointers
- `num-outputs`: number of GM output pointers
- `total-elems`: element count per tensor

### 5.2 Example: `src_fanout_probe`

```bash
bash ascend_runner/current/run_native_simexec.sh \
  /mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_native_simexec/src_fanout_probe_simexec \
  /mnt/d/VfSimulator/ascend_runner/build/src_fanout_probe_native_simexec/src_fanout_probe_mix.o \
  src_fanout_probe \
  1 1 4096
```

### 5.3 Example: `GeLU_poly`

```bash
bash ascend_runner/current/run_native_simexec.sh \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_native_simexec/GeLU_poly_simexec \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_native_simexec/GeLU_poly_mix.o \
  foo_add \
  2 1 1024
```

### 5.4 WSL-local run directory

The run script copies artifacts into a WSL-local directory:

```text
/home/lenovo/msprof_run/<stem>_native_simexec
```

This is used because simulator execution is more stable from WSL-local filesystem than from `/mnt/d/...`.

## 6. What Successful Run Looks Like

A successful run prints lines like:

```text
[INFO] Top sim cfg file: ... Ascend950pr_9599_sim.toml
[INFO] Model cfg file: ... Ascend950pr_9599_model.toml
[INFO] FFTS plus cfg file: ... Ascend950pr_9599_ffts_plus.toml
...
Model Init Success
...
[info] [block_start] : AIV, task_id=0, core_id=0, block_id=0
[info] [block_end]   : AIV, task_id=0, core_id=0, block_id=0
...
[INFO] Model stopped successfully.
```

## 7. Precision Validation

The current direct-sim route supports host-side golden validation.

The validated flow is:

1. build deterministic host input tensors
2. run the kernel in simulator
3. copy device output back to host
4. compute CPU golden on the host when a built-in rule exists
5. compare elementwise with dual thresholds
6. emit host-side binary artifacts into the WSL-local run directory

Current thresholds in the generic runner are:

- absolute tolerance: `1e-3`
- relative tolerance: `1e-3`

Comparison rule:

- count a mismatch only when `abs_err > 1e-3` and `rel_err > 1e-3`

Reported stats:

- mismatch count
- first mismatch index and value pair
- max absolute error
- max relative error

### 7.1 Current built-in golden rules

The generic runner currently has built-in CPU golden logic for:

- `src_fanout_probe`
- `foo_add` from `GeLU_poly.dsl`

### 7.2 Example successful checks

For `src_fanout_probe`:

```text
[CHECK] kernel=src_fanout_probe mismatches=0 max_abs_err=0 max_rel_err=0
[CHECK] PASS
```

For `GeLU_poly` / `foo_add`:

```text
[CHECK] kernel=foo_add mismatches=0 max_abs_err=1.1920929e-07 max_rel_err=9.558908e-08
[CHECK] PASS
```

### 7.3 Files written by the host runner

The generic runner writes host-side artifacts such as:

- `input0.bin`
- `input1.bin` if present
- `output0.bin`
- `golden.bin` when a built-in golden rule exists

## 8. Where The Dump Files Are

### 8.1 WSL-local run directory

Direct run output is first generated under:

```text
/home/lenovo/msprof_run/<stem>_native_simexec
```

### 8.2 Workspace copy for inspection

We copy results back into the repo under:

```text
d:\VfSimulator\cce_dump\<case_name>
```

Validated examples:

- [cce_dump/src_fanout_probe](d:/VfSimulator/cce_dump/src_fanout_probe)
- [cce_dump/GeLU_poly](d:/VfSimulator/cce_dump/GeLU_poly)

Examples of dump files there:

- `core0.veccore0.rvec.IDU.dump`
- `core0.veccore0.rvec.OOO.dump`
- `core0.veccore0.rvec.simd.IDU.DISP.dump`
- `core0.veccore0.rvec.simd.idu.TRACE.dump`
- `core0.veccore0.instr_log.dump`
- `core0.veccore0.instr_popped_log.dump`
- `core0.veccore0_su_perf_summary_log`

## 9. Important Notes

### 9.1 This is not the `msprof` route

The route that is now verified is the direct simulator executable route.

What worked:

- `ccec`
- `ld.lld`
- host executable linked with `runtime_camodel`
- direct execution of that host executable
- host-side golden verification

What did **not** prove robust for this repository as the main route:

- entering simulator mainly through `msprof op simulator` and then manually fixing the child-process loader chain via `LD_PRELOAD`

### 9.2 Kernel name must match exactly

The host runner uses the kernel name passed on the command line to:

- register the function
- resolve the stub
- launch the kernel
- select built-in golden logic when available

So the kernel name must exactly match the symbol in the `.cce` file.

### 9.3 Kernel arg layout must still match

The runner is generic at the level of:

- number of inputs
- number of outputs
- tensor element count

But the runtime argument list still must match the kernel signature order exactly:

- all GM input pointers first
- then all GM output pointers

If a future kernel has a different calling convention, the runner must be extended accordingly.

## 10. Quick Command Summary

### Build

```bash
cd /mnt/d/VfSimulator
bash ascend_runner/current/build_native_simexec.sh <dsl-or-cce> [output-stem]
```

### Run

```bash
cd /mnt/d/VfSimulator
bash ascend_runner/current/run_native_simexec.sh <sim-exec> [kernel-bin] [kernel-name] [num-inputs] [num-outputs] [total-elems]
```

### Inspect dumps in Windows workspace

```text
d:\VfSimulator\cce_dump\<case_name>
```

## 11. Recommended Next Step

After these validated cases, the next migration path is:

1. keep this exact build/run route
2. point it at a target DSL
3. pass the correct kernel name and runtime shape
4. if needed, add a new CPU golden rule for that kernel
5. reuse the same direct simulator executable route

