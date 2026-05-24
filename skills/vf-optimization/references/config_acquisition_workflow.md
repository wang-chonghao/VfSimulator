# Config Acquisition Workflow

This workflow explains how to obtain VF simulator config values from CCE/camodel dumps. It covers both the current high-level config files and the low-level formulas used to extract instruction parameters.

## 1. Config Files

Main config files:

- `configs/uarch.json`: SHQ/EXQ depths, queue delays, issue/load/store ports, EXU inflight limit, physical register count, and other micro-architecture knobs.
- `configs/isa.json`: per-instruction latency/startup/drain/load/store cost and EXU dispatch class.
- `configs/forwarding.json`: producer-to-consumer forwarding distance.
- `configs/InitiationInterval.json`: pairwise initiation interval constraints.

The current mainline model reads queue/resource parameters from `configs/uarch.json`. Do not use old queue-level selector flags as the primary configuration mechanism.

## 2. Required Dump Files

Most extraction flows need these files from each native-sim/camodel run directory:

- `core0.veccore0.instr_popped_log.dump`: start/pop timing. Use this for `*_start`.
- `core0.veccore0.instr_log.dump`: completion timing. Use this for `*_done`.
- `core0.veccore0.rvec.EXU.dump`: EXU launch timing and `exu_id`.
- `core0.veccore0.rvec.IDU.dump`: IDU dispatch/register-pressure analysis when needed.

Important rule: do not mix dumps from different runs, cores, or compiler knobs.

The extraction scripts typically parse lines containing:

```text
[cycle] ... (ID: id) ... RV_<OP>
```

For EXU dump, they parse:

```text
[cycle] ... instr_name RV_<OP> ... exu_id:<n>
```

## 3. ISA Single-Instruction Parameters

Target file: `configs/isa.json`.

Fields collected per instruction and dtype:

- `pipeline_startup_cost`
- `latency`
- `pipeline_drain_cost`
- `data_load_cost`
- `data_store_cost`
- `EXU`

### 3.1 Test Templates

Unary or unary-immediate op:

```text
VLD(v0, memA);
OP(v1, v0);
VST(v1, memB);
```

Binary op:

```text
VLD(v0, memA);
VLD(v1, memB);
OP(v2, v0, v1);
VST(v2, memC);
```

Some historical binary calibration cases use `OP(v1, v0, v0)` for startup consistency. The important point is that the first compute op has its needed source loaded before it starts.

### 3.2 Cycle Variables

From `instr_popped_log.dump`:

- `vld_start`: start cycle of the first relevant `RV_VLD*`.
- `op_start`: start cycle of the first target compute instruction, such as `RV_VADDS`.
- `vst_start`: start cycle of the first relevant `RV_VST*` after `op_start`.

From `instr_log.dump`:

- `vld_done`: done cycle of the same VLD instruction id.
- `op_done`: done cycle of the same compute instruction id.
- `vst_done`: done cycle of the relevant VST instruction id.

Instruction ids should be used to match start and done. Do not match by opcode alone if there are multiple same-op instructions.

### 3.3 Formulas

Use these formulas for `configs/isa.json`:

```text
pipeline_startup_cost = op_start - vld_start
latency               = op_done - op_start
pipeline_drain_cost   = vst_start - op_start
data_load_cost        = vld_done - vld_start
data_store_cost       = vst_done - vst_start
```

Notes:

- `pipeline_startup_cost` means how long after the source VLD starts the first downstream compute op can start.
- `latency` is compute start to compute done.
- `pipeline_drain_cost` means how long after compute start the result can be consumed by VST.
- `data_store_cost` is recorded under the producer op in this project, because the model asks for the store behavior of the value produced by that op.
- `throughput` should not be used for continuous issue ability; use `configs/InitiationInterval.json` instead.

### 3.4 Script

Run:

```bash
python skills/vf-optimization/scripts/calibrate_isa.py --run-suite
```

The wrapper calls:

```bash
bash ascend_runner/single_op_param_suite/run_suite.sh
python3 ascend_runner/single_op_param_suite/extract_single_op_params.py
```

The extractor compares measured values against `configs/isa.json` and can emit CSV such as:

```text
results/single_op_param_suite_compare.csv
```

## 4. Forwarding Parameters

Target file: `configs/forwarding.json`.

Forwarding is measured with a true RAW dependency:

```text
VLD -> producer -> consumer -> VST
```

### 4.1 Cycle Variables

From `instr_popped_log.dump`:

- `producer_start`: start cycle of the first producer op.
- `consumer_start`: start cycle of the first dependent consumer op after the producer.

### 4.2 Formula

Project convention:

```text
forwarding(producer, consumer) = consumer_start - producer_start
```

This is intentionally start-to-start, not producer-done to consumer-start.

### 4.3 Common Pitfalls

- If the loop has many repeated producer/consumer pairs, the measurement can accidentally become an II measurement. Prefer a minimal one-producer/one-consumer case or ensure the extractor selects the first true RAW pair.
- Do not use independent producer/consumer instructions for forwarding.
- Keep compiler scheduling knobs fixed.

### 4.4 Script

Run:

```bash
python skills/vf-optimization/scripts/calibrate_forwarding.py
```

To generate and measure missing pairs:

```bash
python skills/vf-optimization/scripts/calibrate_forwarding.py --generate-missing
```

The wrapper calls:

```bash
python3 ascend_runner/forwarding_param_suite/generate_cases.py
bash ascend_runner/forwarding_param_suite/run_suite.sh
python3 ascend_runner/forwarding_param_suite/extract_forwarding_params.py
```

Output CSV examples:

- `results/forwarding_param_suite_compare.csv`
- `results/forwarding_unconfigured_measured.csv`

## 5. Initiation Interval Parameters

Target file: `configs/InitiationInterval.json`.

II is measured from dense independent issue cases, not RAW-dependent forwarding cases.

### 5.1 Test Template

Generate independent source operands so the target pair is not serialized by data dependency:

```text
... prev_op independent ...
... cur_op independent ...
```

The suite under `ascend_runner/ii_param_suite/` generates cases named:

```text
ii_<prev>_to_<cur>
```

### 5.2 Cycle Variables

From `core0.veccore0.rvec.EXU.dump`:

- `prev_launch`: launch cycle of `prev_op`.
- `cur_launch`: launch cycle of `cur_op` on the same EXU after `prev_launch`.
- `exu_id`: used to ensure the default measurement compares same-EXU neighboring launches.

### 5.3 Formula

Default:

```text
II(prev, cur) = nearest cur_launch - previous prev_launch
```

with both instructions on the same EXU.

Self-II fallback:

```text
II(op, op) = nearest same-op launch delta
```

if no same-EXU previous/cur pair is found.

### 5.4 Script

Run:

```bash
python skills/vf-optimization/scripts/calibrate_ii.py
```

The wrapper calls:

```bash
python3 ascend_runner/ii_param_suite/generate_cases.py
bash tools/run_ii_case_list.sh tools/ii_all_cases.txt
python3 ascend_runner/ii_param_suite/extract_ii_params.py
```

Output CSV example:

```text
results/ii_param_suite_compare.csv
```

## 6. Dispatch EXU Classification

Target field: `configs/isa.json -> instructions.<OP>.<dtype>.EXU`.

Supported labels:

- `EXU01`: normal vector op. In two-port mode it may use EXU0/EXU1; in `--three-ports` mode it may use EXU0/EXU1/EXU2.
- `EXU0_ONLY`: constrained op. It can only enter EXQ0 and execute on EXU0.

### 6.1 Test Method

Use high-density no-RAW issue cases. For example:

- two-port validation: `#pragma unroll(2)`.
- three-port validation: `#pragma unroll(3)`.

From `core0.veccore0.rvec.EXU.dump`, collect all `exu_id` values for the target `RV_<OP>`.

### 6.2 Classification

Two-port classification:

```text
exu_ids include 0 and 1 -> EXU01
exu_ids == [0]          -> EXU0_ONLY
otherwise              -> inspect manually
```

Reduction-like ops such as `VCMAX`, `VCMIN`, and `VCADD` should be tested explicitly; do not infer their dispatch capability from normal ALU ops.

### 6.3 Script

Run:

```bash
python skills/vf-optimization/scripts/check_dispatch_exu.py --op-filter RV_
```

Output CSV example:

```text
results/dispatch_exu_summary.csv
```

## 7. Uarch Parameters

Target file: `configs/uarch.json`.

Keep these values explicit in config:

- SHQ depth.
- SHQ credit/reuse delay.
- IDU-to-OOO delay.
- VLOOP-to-dispatch delay.
- SHQ-to-EXQ delay.
- EXQ receive delay.
- EXQ depth.
- EXU inflight limit.
- issue port count.
- VLD/load port count.
- VST/store port count.
- physical vector register count.

These are not usually extracted by the single-op ISA scripts. They are calibrated by matching structural behavior across probe cases, such as IDU dispatch timing, SHQ fill/drain behavior, EXQ backpressure, and EXU inflight saturation.

Use SHQ terminology in new docs/code. Older IQ wording refers to the same scheduling queue in this model.

## 8. Recommended Calibration Order

1. Confirm instruction semantic shape and RV token in `instruction_onboarding.md`.
2. Run single-op ISA extraction and update `configs/isa.json`.
3. Run dispatch EXU classification and update `EXU`.
4. Run forwarding extraction and update `configs/forwarding.json`.
5. Run II extraction and update `configs/InitiationInterval.json`.
6. Run coverage summary.
7. Backfill the instruction entry in `instruction_onboarding.md`.

Coverage:

```bash
python skills/vf-optimization/scripts/summarize_config_coverage.py
```

## 9. Backfill Requirement

After calibration, update `skills/vf-optimization/references/instruction_onboarding.md` with:

- instruction name and RV token.
- arity/template used.
- measured ISA config fields.
- `EXU01` or `EXU0_ONLY`.
- forwarding/II caveats if any.
- source CSV/log batch.
- date.

This avoids repeating the same dump archaeology later.
