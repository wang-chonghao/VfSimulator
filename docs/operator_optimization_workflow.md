# Operator Optimization Workflow

This document defines the standard end-to-end workflow for optimizing a VF operator in this repo.

The goal is to make every optimization result reportable in a uniform way:

1. verify baseline numerical correctness
2. collect baseline timings
3. run optimizer
4. verify optimized numerical correctness
5. collect optimized timings
6. keep the optimized DAG and DSL artifacts
7. optionally generate IPC comparison plots for baseline vs optimized

The workflow below uses `GeLU_poly` as the concrete example, but the structure is intended to be reused for other operators.

## 1. Inputs and Required Artifacts

For one optimization task, we typically have:

- source DSL:
  - `cce_code/<op>.dsl`
- optimizer input trace:
  - `VFtest/<op>.json`
- simulator config:
  - `configs/uarch.json`
  - `configs/isa.json`
- CCE simulator route:
  - `ascend_runner/CCE_simulator_src_fanout_probe_guide.md`

Before optimizing, make sure:

- the trace JSON is semantically consistent with the source DSL
- the baseline DSL can pass CCE simulator golden check

For `GeLU_poly`, the optimizer now contains a semantic guard and will reject a drifted `VFtest/GeLU_poly.json`.

## 2. Timing Definitions

We use three timing numbers.

### 2.1 Model predicted time

Produced by our VF simulator:

- `cycles_executed`
- `VF end cycle (with drain)`

When we say "model predicted total time", use:

```text
model predicted total time = cycles_executed + vf_drain_cost
```

In current config:

- `vf_drain_cost = 12`

### 2.2 CCE simulator time

Produced by the Ascend CCE simulator.

Preferred extraction rule:

- VF start time: from `core0.veccore0.instr_popped_log.dump`
- VF end time: from `core0.veccore0.instr_log.dump`
- VF time = `end - start`

You can also use the `vf_execute_time` field on the `VF` line in `instr_log.dump` if present.

### 2.3 "Theoretical limit"

In team discussions we still call this "理论极限", but it is really:

- current model under `--theoretical-limit`

It is a model-side optimistic lower bound, not a hardware-proof lower bound.

## 3. Standard Flow

### 3.1 Baseline: numerical verification

First verify the unoptimized operator with CCE simulator.

Build:

```bash
cd /mnt/d/VfSimulator
bash ascend_runner/build_native_simexec.sh cce_code/GeLU_poly.dsl GeLU_poly
```

Run:

```bash
bash ascend_runner/run_native_simexec.sh \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_native_simexec/GeLU_poly_simexec \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_native_simexec/GeLU_poly_mix.o \
  foo_add \
  2 1 1024
```

Check:

- `golden PASS`
- `mismatches = 0`

If baseline does not pass, do not optimize yet. Fix source DSL / trace mismatch first.

### 3.2 Baseline: model predicted time

Run baseline through our simulator with the intended trip count.

If the trace file already has the correct `params.I`, run directly:

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/GeLU_poly_baseline
```

If you need another trip count, create a temporary trace with adjusted `params.I`.

Example for `I=64`:

```powershell
$j = Get-Content d:\VfSimulator\VFtest\GeLU_poly.json -Raw | ConvertFrom-Json
$j.params.I = 64
$j | ConvertTo-Json -Depth 100 | Set-Content d:\VfSimulator\results\GeLU_poly_I64_baseline_trace.json
```

Then run:

```bash
python main.py --trace results/GeLU_poly_I64_baseline_trace.json --out_dir results/GeLU_poly_I64_baseline
```

Record:

- `cycles_executed`
- `VF end cycle (with drain)`

### 3.3 Baseline: CCE simulator time

If the baseline DSL already has the right `repeat_times`, reuse it.

If not, create a dedicated baseline DSL copy for that trip count.

Example:

- `cce_code/consumer_done/GeLU_poly_I64_baseline_recheck.dsl`

Then build and run through CCE simulator:

```bash
bash ascend_runner/build_native_simexec.sh cce_code/consumer_done/GeLU_poly_I64_baseline_recheck.dsl GeLU_poly_I64_baseline_recheck

bash ascend_runner/run_native_simexec.sh \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_I64_baseline_recheck_native_simexec/GeLU_poly_I64_baseline_recheck_simexec \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_I64_baseline_recheck_native_simexec/GeLU_poly_I64_baseline_recheck_mix.o \
  foo_add \
  2 1 4096
```

Extract VF time from:

- `/home/lenovo/msprof_run/<stem>_native_simexec/core0.veccore0.instr_popped_log.dump`
- `/home/lenovo/msprof_run/<stem>_native_simexec/core0.veccore0.instr_log.dump`

Example grep:

```bash
grep -n "VF" /home/lenovo/msprof_run/<stem>_native_simexec/core0.veccore0.instr_popped_log.dump
grep -n "VF" /home/lenovo/msprof_run/<stem>_native_simexec/core0.veccore0.instr_log.dump
```

### 3.4 Baseline: theoretical-limit timing

Run:

```bash
python main.py --trace results/GeLU_poly_I64_baseline_trace.json --out_dir results/GeLU_poly_I64_theoretical --theoretical-limit
```

Record:

- `cycles_executed`
- `VF end cycle (with drain)`

### 3.5 Run optimization

Current recommended default path:

- optimizer:
  - `optimizer/generic_heuristic_split_optimizer.py`
- OoO model:
  - `consumer-done`
- cut penalty:
  - usually start from `off`

Example for `I=64`:

```bash
python optimizer/generic_heuristic_split_optimizer.py \
  VFtest/GeLU_poly.json \
  --trip-count 64 \
  --ooo-model consumer-done \
  --cut-penalty off \
  --output results/GeLU_poly_split_only_I64_generic_consumer_done.json \
  --meta-out results/GeLU_poly_split_only_I64_generic_consumer_done_meta.json
```

Record:

- `best_cuts`
- `cycles`
- `slot_count`
- `ub_bytes`

### 3.6 Generate optimized DAG and DSL

Generate DAG:

```bash
D:\miniconda3\envs\vfsim\python.exe tools/visualize_dag.py \
  results/GeLU_poly_split_only_I64_generic_consumer_done.json \
  --output results/GeLU_poly_split_only_I64_generic_consumer_done_dag.png
```

Generate DSL:

```bash
python tools/generate_gelu_poly_split_dsl.py \
  results/GeLU_poly_split_only_I64_generic_consumer_done.json \
  cce_code/consumer_done/GeLU_poly_split_only_I64_generic_consumer_done.dsl \
  --simd-name gelu_poly_split_only_i64_generic_consumer_done_simd_ub
```

Keep these artifacts:

- optimized JSON
- optimized meta JSON
- optimized DAG PNG
- optimized DSL

### 3.7 Optimized: numerical verification

Run the generated optimized DSL through CCE simulator.

Example:

```bash
bash ascend_runner/build_native_simexec.sh cce_code/consumer_done/GeLU_poly_split_only_I64_generic_consumer_done.dsl GeLU_poly_I64_generic_consumer_done

bash ascend_runner/run_native_simexec.sh \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_I64_generic_consumer_done_native_simexec/GeLU_poly_I64_generic_consumer_done_simexec \
  /mnt/d/VfSimulator/ascend_runner/build/GeLU_poly_I64_generic_consumer_done_native_simexec/GeLU_poly_I64_generic_consumer_done_mix.o \
  foo_add \
  2 1 4096
```

Check:

- `golden PASS`
- `mismatches = 0`

If optimized golden fails:

- do not trust its timing result yet
- fix semantic mismatch first

### 3.8 Optimized: model predicted time

This comes directly from optimizer output.

Example:

- `cycles = 1301`
- model predicted total time = `1301 + 12 = 1313`

### 3.9 Optimized: CCE simulator time

Extract VF time from the optimized run dump the same way as baseline:

- `instr_popped_log.dump`
- `instr_log.dump`

Example:

```bash
grep -n "VF" /home/lenovo/msprof_run/GeLU_poly_I64_generic_consumer_done_native_simexec/core0.veccore0.instr_popped_log.dump
grep -n "VF" /home/lenovo/msprof_run/GeLU_poly_I64_generic_consumer_done_native_simexec/core0.veccore0.instr_log.dump
```

Record:

- start cycle
- end cycle
- VF time

### 3.10 Optimized: IPC comparison plot

After both baseline and optimized CCE simulator runs are available, generate a retire-based IPC comparison plot from:

- `core0.veccore0.rvec.EXU.dump`

Recommended environment:

```bash
D:\miniconda3\envs\vfsim\python.exe
```

The plotting tool is:

- `tools/plot_cce_ipc_compare.py`

Recommended preparation:

- copy or archive the raw EXU dumps into:
  - `results/cce_IPC/raw/`

Example file names:

- `results/cce_IPC/raw/GeLU_poly_I64_baseline_core0.veccore0.rvec.EXU.dump`
- `results/cce_IPC/raw/GeLU_poly_I64_optimized_core0.veccore0.rvec.EXU.dump`

Example plotting command:

```bash
D:\miniconda3\envs\vfsim\python.exe tools/plot_cce_ipc_compare.py \
  results/cce_IPC/raw/GeLU_poly_I64_baseline_core0.veccore0.rvec.EXU.dump \
  results/cce_IPC/raw/GeLU_poly_I64_optimized_core0.veccore0.rvec.EXU.dump \
  --label-a baseline \
  --label-b optimized \
  --window 20 \
  --stem GeLU_poly_I64_baseline_vs_optimized_core0_veccore0_rvec_EXU_retire_win20 \
  --title "GeLU_poly I=64" \
  --out-dir results/cce_IPC
```

Current plotting conventions:

- `baseline = red`
- `optimized = blue`
- retire-based sliding window IPC
- x-range padded to:
  - `vf_start - window`
  - `vf_end + window`

Generated artifacts:

- comparison CSV
- comparison SVG
- comparison PNG

These plots are useful for:

- visually comparing throughput phases
- checking whether optimization mainly improves warm-up, steady-state, or tail
- communicating optimizer impact in reports and slides

## 4. Minimum Final Report Format

For any optimized operator, the final summary should include these items.

### 4.1 Baseline

- numerical verification:
  - pass / fail
- model predicted time:
  - `cycles_executed`
  - `VF end cycle (with drain)`
- CCE simulator VF time
- theoretical-limit time

### 4.2 Optimized

- numerical verification:
  - pass / fail
- `best_cuts`
- model predicted time:
  - `cycles`
  - predicted total time
- CCE simulator VF time
- optimized DAG path
- optimized DSL path
- IPC comparison plot path if generated

## 5. Current GeLU Example

As a validated example after trace repair:

### `GeLU_poly`, `I=16`

- baseline measured VF:
  - `446`
- optimized best cuts:
  - `[9]`
- optimized model:
  - `cycles = 412`
  - predicted total = `424`
- optimized CCE VF:
  - `429`
- theoretical limit:
  - `374`
- optimized `golden`:
  - PASS

### `GeLU_poly`, `I=64`

- baseline measured VF:
  - `1502`
- baseline model:
  - `1553 / 1565`
- optimized best cuts:
  - `[8, 16]`
- optimized model:
  - `1301 / 1313`
- optimized CCE VF:
  - `1344`
- theoretical limit:
  - `1179 / 1191`
- optimized `golden`:
  - PASS

## 6. Common Failure Modes

### 6.1 Trace semantics drift from source DSL

Symptom:

- timing looks plausible
- but CCE simulator golden fails

Fix:

- repair `VFtest/<op>.json`
- add semantic guard if the trace is long-lived and hand-maintained

### 6.2 Pathological split introducing unnecessary `mem_inter`

Symptom:

- very slow optimizer evaluation
- weird boundary values like `VLD -> VST(mem_inter) -> VLD`

Fix:

- rematerialize load-origin values by direct re-`VLD`
- do not spill original input loads to `mem_inter`

### 6.3 Comparing wrong timing definitions

Symptom:

- one report says `412`
- another says `429`
- another says `3036`

Fix:

- explicitly label:
  - model `cycles_executed`
  - model `VF end cycle (with drain)`
  - CCE `VF time`
  - CCE block time

Do not mix them.


