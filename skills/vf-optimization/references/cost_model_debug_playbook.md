# VF Cost Model Debug Playbook

Use this playbook when a model result does not match CCE/camodel, or when a structural refactor may have changed behavior.

## 1. First Confirm The Comparison Is Fair

Before reading low-level logs, verify:

- Same operator and same logical VF body.
- Same loop count and unroll factor.
- Same dtype.
- Same CCE compiler knobs, especially scheduling/misched options.
- Same input path: JSON trace versus CCE `__VEC_SCOPE__` can differ if CCE split or transformed loops.
- Same model mode: normal mainline, `--three-ports`, or one of the two theoretical-limit variants.

If CCE source visually has one loop, still confirm actual CCE dumps. CCE may emit multiple `vloop_pc` segments after compilation.

## 2. Model Logs To Inspect

Core model logs:

- `start_by_cycle.json`: execution-start cycles selected by ISU/EXU.
- `done_by_cycle.json`: completion cycles.
- `idu_to_ooo.json`: IDU dispatch into OOO/SHQ and visible free-register pressure.
- `model_warnings.json`: low-confidence warnings such as expanded vreg namespace exceeding physical register count.
- `model_stdout.log`: printed model mode, theoretical-limit config, normalization stats, and headline timing.

Interpretation:

- If timing differs but `idu_to_ooo.json` matches expectation, inspect ISU/EXQ/EXU issue behavior.
- If IDU stalls early, inspect SHQ depth/credit, physical register availability, and loop exposure timing.
- If register pressure differs after refactor, check vreg live-range normalization and consumer-done release events.

## 3. CCE/Camodel Logs To Inspect

Common CCE logs:

- `core0.veccore0.instr_popped_log.dump`: instruction popped/start-like evidence.
- `core0.veccore0.instr_log.dump`: done/completion evidence.
- `core0.veccore0.rvec.EXU.dump`: EXU launch and `exu_id`.
- `core0.veccore0.rvec.IDU.dump`: IDU dispatch and register-pressure evidence.

Rules:

- Do not mix files from different cores or different runs.
- Use one consistent timing source per comparison.
- For EXU dispatch capability, use `rvec.EXU.dump`.
- For model-vs-CCE IPC, align by first non-zero compute issue cycle.

## 4. Common Root Causes

Input mismatch:

- JSON trace and CCE source are not equivalent.
- CCE emits multiple `vloop_pc` segments while the model input is one logical loop.
- `#pragma unroll` differs between model and CCE.

Queue/resource mismatch:

- SHQ depth or SHQ credit differs from the intended `configs/uarch.json` values.
- EXQ depth or EXQ receive delay changed.
- EXU inflight limit is active in one run and effectively absent in another.

Dispatch mismatch:

- `EXU0_ONLY` instruction was allowed to use other EXUs.
- `EXU01` instruction was overly restricted.
- `--three-ports` changed load/compute issue width but the comparison expected two-port behavior.

Register-release mismatch:

- Mainline release is consumer-done with `consumer start + 4`.
- If experimenting with last-use behavior, make sure the only intended difference is the absence of the overwrite/seal condition.
- vreg live-range normalization is enabled by default; disabling it or running old reports may change pressure.

Parameter mismatch:

- `configs/isa.json` latency/startup/drain data is stale.
- `configs/forwarding.json` producer-consumer value is stale.
- `configs/InitiationInterval.json` pairwise II is stale.

## 5. Debug Order

1. Re-run the smallest reproducing case and save `model_stdout.log`.
2. Compare headline model time with CCE/camodel time.
3. Compare compute IPC with a 25-cycle sliding window.
4. Inspect `idu_to_ooo.json` for stalls or register-pressure cliffs.
5. Inspect `start_by_cycle.json` for EXU utilization and ordering changes.
6. Inspect CCE `rvec.EXU.dump` for actual EXU distribution.
7. Only after structural logs match, tune ISA/forwarding/II parameters.

## 6. WSL/CCE Caveat

CCE native simulation may fail from the Codex process even when it works in the user's terminal, because the agent may not run under the same Windows user context. If that happens:

- Ask the user to run the CCE command manually.
- Keep generated dumps under the repo or a known results directory.
- Continue analysis from the dump files rather than blocking on WSL execution.
