# Core Refactor Plan

## Goal

Keep the current mainline behavior unchanged while making the project easier to read and maintain.

Current mainline assumptions:

- mainline model: `queue_level4`
- release rule: `consumer done + start+4`
- `vreg live-range normalization`: enabled by default
- front-to-back flow: `IFU -> IDU -> OOO(SHQ) -> ISU(EXQ) -> EXU`

Regression anchor values that must stay unchanged during refactor:

- `gelu_poly_i16_u1 = 392`
- `gelu_i16_u1 = 189`
- `online_update_i64_u1 = 430`
- `gelu_poly_i96_u4 = 1682`
- `swiglu_i96_u1 = 652`
- `vadds_longchain_i64_8x64 = 39755`
- smoke run: `VF end cycle (with drain) = 1682`

## Refactor Principles

1. Refactor structure first, behavior second.
2. Prefer moving code behind clear module boundaries before changing algorithms.
3. Keep the mainline flow easy to follow:
   - CLI entry
   - program analysis
   - uarch normalization
   - simulation runner
   - OOO core
   - ISU path
4. After each step, run a smoke check and keep the regression anchors stable.

## Target Structure

```text
VfSimulator/
  main.py
  configs/
    isa.json
    uarch.json
    forwarding.json
    InitiationInterval.json
  core/
    param_db.py
    flatten.py
    ifu.py
    idu.py
    program_analysis.py
    uarch_normalize.py
    simulator_runner.py
    ooo.py
    ooo_mainline.py
    isu.py
```

## Current Status

Already completed:

1. `main.py` main simulation loop was extracted into [core/simulator_runner.py](/D:/VfSimulator/core/simulator_runner.py)
2. mainline uarch normalization was extracted into [core/uarch_normalize.py](/D:/VfSimulator/core/uarch_normalize.py)
3. program analysis helpers were extracted into [core/program_analysis.py](/D:/VfSimulator/core/program_analysis.py)
4. `IQ` naming was removed from the mainline path; mainline now uses `SHQ`
5. `npu_hybrid` and separate `last_use` backend were removed
6. theoretical-limit CLI was reduced to two kept variants
7. compute-side issue logic after `SHQ` was extracted into [core/isu.py](/D:/VfSimulator/core/isu.py)
8. SHQ credit bookkeeping was folded back into [core/ooo_mainline.py](/D:/VfSimulator/core/ooo_mainline.py) because it is naturally part of OOO

Current structure after the latest cleanup:

- [main.py](/D:/VfSimulator/main.py): thin CLI entry
- [core/program_analysis.py](/D:/VfSimulator/core/program_analysis.py): loop and vreg analysis
- [core/uarch_normalize.py](/D:/VfSimulator/core/uarch_normalize.py): mainline config normalization and theoretical-limit override
- [core/simulator_runner.py](/D:/VfSimulator/core/simulator_runner.py): simulation orchestration
- [core/ooo.py](/D:/VfSimulator/core/ooo.py): base OOO utilities and shared state
- [core/ooo_mainline.py](/D:/VfSimulator/core/ooo_mainline.py): mainline OOO-side logic
- [core/isu.py](/D:/VfSimulator/core/isu.py): EXQ / issue / EXU dispatch path

## OOO / ISU Boundary

### OOO owns

- rename / RAT / freelist
- `preg` lifecycle
- source-release scheduling
- overwrite closeout
- `ROB`, `LSQ`, `SHQ`
- ready-cycle computation
- `VLD` and `VST` path
- SHQ credit state and IDU-visible credit updates

### ISU owns

- direct compute issue from `SHQ` to `EXU` in direct-issue mode
- `SHQ -> EXQ` enqueue
- `EXQ` arbitration
- per-port / per-FU issue
- inflight cap checks
- `EXQ -> EXU` launch

### Current implementation split

In [core/isu.py](/D:/VfSimulator/core/isu.py):

- `issue_direct_from_shq(...)`
- `enqueue_shq_to_exq(...)`
- `issue_exq_to_exu(...)`
- `remove_issued(...)`

In [core/ooo_mainline.py](/D:/VfSimulator/core/ooo_mainline.py):

- OOO-side state
- accept / rename / source release
- `preg` recycle
- `VLD` / `VST`
- call-out to ISU helpers from `step()`

## Next Suggested Steps

### Step 1: tighten module documentation

- add clear module-level comments to `ooo_mainline.py` and `isu.py`
- keep future readers from re-deriving the boundary

### Step 2: reduce glue inside `ooo_mainline.step()`

- keep behavior the same
- gradually turn large inline blocks into named OOO-side helpers
- goal is not more files, but clearer control flow

### Step 3: keep SHQ credit inside OOO unless it grows again

Current choice:

- keep SHQ credit bookkeeping inside `ooo_mainline.py`
- only split it out again if it starts growing independently and hurts readability

## Validation Checklist

### Compile

```powershell
python -m py_compile main.py core\*.py
```

### Smoke run

```powershell
python main.py --trace VFtest\GeLU_poly.json
```

Expected:

- `VF end cycle (with drain) = 1682`

### Spot checks

- `gelu_poly_i16_u1 = 392`
- `gelu_i16_u1 = 189`
- `online_update_i64_u1 = 430`
- `gelu_poly_i96_u4 = 1682`
- `swiglu_i96_u1 = 652`
- `vadds_longchain_i64_8x64 = 39755`

## Notes

- This refactor plan now reflects the current structure, not the old many-small-modules draft.
- The project currently prefers the larger and more hardware-natural split:
  - `OOO = rename + preg + SHQ`
  - `ISU = EXQ + issue + EXU dispatch`
