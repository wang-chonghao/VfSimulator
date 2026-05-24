# Log Field Cheatsheet

This file summarizes the logs most often used for VF cost-model debugging.

## Model Logs

### `start_by_cycle.json`

Use for model execution-start timing.

Typical structure:

- key: cycle.
- value: list of instructions that start execution in that cycle.
- useful fields: instruction id, op name, source/destination operands, EXU/port metadata when present.

Use it to inspect:

- compute IPC.
- VLD/VST issue pressure.
- EXU utilization.
- instruction ordering after SHQ selection and ISU issue.

### `done_by_cycle.json`

Use for model completion timing.

Typical use:

- confirm long-latency ops complete when expected.
- compute total completed instruction rate.
- debug dependency-release timing.

### `idu_to_ooo.json`

Use for IDU-to-OOO/SHQ timing and register pressure.

Useful fields usually include:

- cycle.
- instruction id/name.
- available physical-register count or related vreg pressure field.
- dispatch/stall evidence.

Use it when the model is slower because IDU cannot feed OOO.

### `model_warnings.json`

Use for low-confidence cases.

Common warning:

- expanded vreg namespace is larger than physical register count.

## CCE/Camodel Logs

### `core0.veccore0.instr_popped_log.dump`

Often used as start/pop evidence.

Typical parse fields:

- leading cycle, usually in square brackets.
- instruction id.
- RV opcode token, such as `RV_VADDS`.

### `core0.veccore0.instr_log.dump`

Often used as done/completion evidence.

Match with popped log by instruction id when possible.

### `core0.veccore0.rvec.EXU.dump`

Use for EXU-side issue behavior.

Typical parse fields:

- launch cycle.
- `instr_name RV_*`.
- `exu_id:<n>`.

Use this for:

- determining whether an op is `EXU0_ONLY` or `EXU01`.
- validating dual-issue/three-issue behavior.
- compute IPC curves.

### `core0.veccore0.rvec.IDU.dump`

Use for CCE-side IDU dispatch and register-pressure clues.

This is the closest CCE-side counterpart to model `idu_to_ooo.json`.

## IPC Plotting Rules

For compute IPC:

- Exclude VLD and VST.
- Exclude SEND and PSET-like instructions.
- Align curves by first non-zero compute issue cycle.
- Default sliding window: 25 cycles.

Useful tool:

```bash
python tools/plot_cce_model_ipc_compare.py ^
  --cce-exu-dump <core0.veccore0.rvec.EXU.dump> ^
  --model-start-log <model_out_dir>/start_by_cycle.json ^
  --window 25 ^
  --align-start ^
  --out-png results/ipc_compare/cce_vs_model.png ^
  --out-csv results/ipc_compare/cce_vs_model.csv
```

## Common Pitfalls

- Mixing logs from different runs.
- Reading done cycle from popped/start logs.
- Comparing CCE multi-vloop output with model single-loop input without acknowledging the structural difference.
- Comparing two-port model output against `--three-ports` output.
- Including VLD/VST when the question is compute IPC.
