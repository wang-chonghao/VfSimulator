# Dispatch EXU Test Plan

Purpose: determine whether an instruction is unrestricted across normal vector EXUs or constrained to EXU0.

## 1. Target Labels

The simulator uses these ISA labels in `configs/isa.json`:

- `EXU01`: normal vector instruction. In two-port mode it may use EXU0/EXU1. In `--three-ports` mode it may use EXU0/EXU1/EXU2.
- `EXU0_ONLY`: constrained instruction. It may only enter EXQ0 and execute on EXU0.

Reduction-like instructions such as `VCMAX`, `VCMIN`, and `VCADD` should always be tested explicitly.

## 2. Test Method

1. Build a no-RAW high-density issue kernel for the target op.
2. Add an unroll pragma, usually `#pragma unroll(2)` for two-port validation or `#pragma unroll(3)` for three-port validation.
3. Run CCE/camodel and collect `core0.veccore0.rvec.EXU.dump`.
4. Parse rows whose `instr_name` matches the target RV opcode.
5. Collect unique `exu_id` values.
6. Classify the instruction and update `configs/isa.json`.

## 3. Expected Observations

Two-port mode:

- `VADD`/normal arithmetic should show EXU0 and EXU1 when enough independent work exists.
- `VCMAX`/`VCMIN`/`VCADD` should show EXU0 only if they are constrained.

Three-port model validation:

- `EXU01` instructions should be able to use EXU0/EXU1/EXU2.
- `EXU0_ONLY` instructions should remain EXU0 only.
- VLD can issue up to 3 per cycle.
- VST remains 1 per cycle.

## 4. Pitfalls

- RAW dependencies can hide dispatch capability by serializing instructions.
- Too little unroll can make a dual/three-port op look single-port.
- Do not infer constrained behavior from one sparse kernel.
- Keep compiler knobs fixed across one dispatch campaign.
- Compare the same RV opcode token used by CCE logs, not only the high-level intrinsic name.
