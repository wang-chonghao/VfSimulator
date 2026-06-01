# Instruction Onboarding Reference

Purpose: provide a single place to lookup instruction semantics, test template, and known caveats before running calibration tools.

## A. What Must Be Known Before Testing One Instruction

For each instruction, fill these fields first.

1. ISA name (for example `VADDS`, `VABS`, `VDIV`).
2. RV opcode token in logs (for example `RV_VADDS`).
3. Arity category:
   - unary (`dst, src`)
   - binary (`dst, src0, src1`)
4. Candidate dispatch capability risk:
   - likely `EXU01`
   - possible `EXU0_ONLY` (especially reduction-like ops)
5. Existing config presence:
   - entry in `configs/isa.json`
   - row/col in `configs/forwarding.json`
   - row/col in `configs/InitiationInterval.json`

## B. Where To Lookup New Instruction Information

Use this order.

1. This file (historical known entries).
2. `ascend_runner/operator_test.md`.
3. Existing config JSON files under `configs/`.
4. CCE intrinsic documentation from Ascend docs site.
5. Actual simulator logs (`instr_popped_log`, `instr_log`, `rvec.EXU.dump`) from a minimal case.

If step 1-4 are inconsistent, trust measured log behavior and document the discrepancy.

## C. Template Decision Rules

- Unary instruction template:
  - `VLD(v0, memA); OP(v1, v0); VST(v1, memB)`
- Binary instruction template (for startup consistency):
  - `VLD(v0, memA); OP(v1, v0, v0); VST(v1, memB)`

Rule: for startup measurement use first OP start minus first VLD start.

## D. Tool Mapping

1. Single-op latency/pipe/load/store:
   - `python skills/vf-optimization/scripts/calibrate_isa.py --run-suite`
2. Forwarding matrix:
   - `python skills/vf-optimization/scripts/calibrate_forwarding.py`
   - optional full completion: `--generate-missing`
3. II matrix:
   - `python skills/vf-optimization/scripts/calibrate_ii.py`
4. Dispatch capability:
   - `python skills/vf-optimization/scripts/check_dispatch_exu.py --op-filter RV_`
5. Coverage summary:
   - `python skills/vf-optimization/scripts/summarize_config_coverage.py`

## E. Known Campaign Notes

- `data_store_cost` can be sensitive to compile form (for example explicit unroll pragmas can shift first VST timing by ~1 cycle).
- Keep compile knobs fixed in one campaign and never compare mixed-knob runs directly.
- Always use `instr_popped_log` for start and `instr_log` for done.

## F. Backfill Template (Copy For New Instruction)

Use this block when a new instruction is calibrated.

```md
### <INSTR_NAME>
- RV token: <RV_TOKEN>
- Arity: <unary|binary>
- Test template: <template used>
- Dispatch EXU: <EXU01|EXU0_ONLY|UNKNOWN>
- Notes: <measurement caveat>
- Source batch: <results/*.csv or run folder>
- Date: <YYYY-MM-DD>
```


### VABS
- RV token: RV_VABS
- Arity: unary
- Test template: `VLD(v0, memA); VABS(v1, v0); VST(v1, memB)`
- Dispatch EXU: EXU01 (pending dedicated reduction-style contrast campaign)
- Notes: first-VST timing interpretation must use consistent compile knobs
- Source batch: `results/single_op_param_suite_compare.csv`
- Date: 2026-04-13

### VCMAX
- RV token: RV_VCMAX
- Arity: unary (project calibration uses single-source template)
- Test template: `VLD(v0, memA); VCMAX(v1, v0); VST(v1, memB)`
- Dispatch EXU: EXU0_ONLY
- Notes: `run_native_simexec` may return non-zero with output-check mismatch, but dumps are valid and used for extraction. II column pairs `(* -> VCMAX)` for `VADDS/VADD/VSUB/VMAXS/VMINS/VMAX/VMIN` required cross-EXU fallback delta extraction because same-EXU matcher returned no sample.
- Source batch: `results/single_op_param_suite_compare_with_vcmax.csv`, `results/forwarding_unconfigured_measured_with_vcmax.csv`, `results/ii_param_suite_compare_with_vcmax.csv`, `results/ii_vcmax_cross_exu_fallback.csv`, `results/dispatch_exu_vcmax_summary.csv`
- Date: 2026-04-13

### VCMIN
- RV token: RV_VCMIN
- Arity: unary (project calibration uses single-source template)
- Test template: `VLD(v0, memA); VCMIN(v1, v0); VST(v1, memB)`
- Dispatch EXU: EXU0_ONLY
- Notes: II missing-dump items for `(* -> VCMIN)` were filled by VCMAX-equivalent fallback values.
- Source batch: `results/single_op_vcmin_measured.csv`, `results/forwarding_vcmin_pairs_measured.csv`, `results/ii_vcmin_pairs_final.csv`, `results/dispatch_exu_vcmin_summary.csv`
- Date: 2026-04-13

### VCADD
- RV token: RV_VCADD
- Arity: unary (project calibration uses single-source template)
- Test template: `VLD(v0, memA); VCADD(v1, v0, pat_all_b32); VST(v1, memB)`
- Dispatch EXU: EXU0_ONLY
- Notes: single-op rerun on 2026-04-13 measured `latency=22` and `pipeline_drain_cost=20`; non-zero simexec exit was output-check mismatch only, required dumps were complete and used for extraction.
- Source batch: `results/single_op_param_suite_compare_vcadd.csv`, `results/dispatch_exu_vcadd_summary.csv`, `/home/lenovo/msprof_run/singleop_vcadd_native_simexec/*`
- Date: 2026-04-13
## H. Semantic -> Template -> RV Mapping (Campaign-local)

Use this block as the minimal local truth source before running generators.

| ISA | Semantic shape | Template type | RV token |
| :-- | :-- | :-- | :-- |
| VADDS | unary + immediate | `imm` | `RV_VADDS` |
| VMULS | unary + immediate | `imm` | `RV_VMULS` |
| VEXP | unary | `unary` | `RV_VEXP` |
| VABS | unary | `unary` | `RV_VABS_FP` |
| VCMAX | unary (single-source in this project) | `unary` | `RV_VCMAX` |
| VCMIN | unary (single-source in this project) | `unary` | `RV_VCMIN` |
| VADD | binary | `binary` | `RV_VADD` |
| VSUB | binary | `binary` | `RV_VSUB` |
| VMUL | binary | `binary` | `RV_VMUL` |
| VDIV | binary | `binary` | `RV_VDIV` |
| VMAXS | unary + immediate | `imm` | `RV_VMAXS` |
| VMINS | unary + immediate | `imm` | `RV_VMINS` |
| VMAX | binary | `binary` | `RV_VMAX` |
| VMIN | binary | `binary` | `RV_VMIN` |

If local docs and measured dumps disagree, keep measured behavior and note the discrepancy in section G.

## I. New Instruction Backfill Minimum Keys

When adding one new instruction, ensure these keys are completed.

### I.1 Config keys
- `configs/isa.json`:
  - `instructions.<OP>.fp32.pipeline_startup_cost`
  - `instructions.<OP>.fp32.latency`
  - `instructions.<OP>.fp32.pipeline_drain_cost`
  - `instructions.<OP>.fp32.data_load_cost`
  - `instructions.<OP>.fp32.data_store_cost`
  - `instructions.<OP>.fp32.EXU`
- `configs/forwarding.json`:
  - row: `forwarding.fp32.<OP>.*`
  - column: `forwarding.fp32.*.<OP>`
- `configs/InitiationInterval.json`:
  - row: `InitiationInterval.fp32.<OP>.*`
  - column: `InitiationInterval.fp32.*.<OP>`

### I.2 Reference row template

```md
### <OP>
- RV token: <RV_TOKEN>
- Arity: <unary|binary>
- Test template: <template used>
- Dispatch EXU: <EXU01|EXU0_ONLY|UNKNOWN>
- Config keys: isa + forwarding(row/col) + II(row/col) updated
- Source batch: <results/*.csv or run folder>
- Date: <YYYY-MM-DD>
```



