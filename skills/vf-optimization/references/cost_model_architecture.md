# VF Cost Model Architecture

This document describes the current mainline VF simulator architecture. It intentionally describes the cleaned-up model path rather than old historical variants.

## 1. Inputs

The simulator accepts two input styles.

JSON trace:

```bash
python main.py --trace <trace.json> --out_dir <out_dir>
```

CCE/DSL file:

```bash
python main.py --cce <kernel.dsl> --out_dir <out_dir>
```

If a CCE file contains multiple `__VEC_SCOPE__` kernels, select one explicitly:

```bash
python main.py --cce <kernel.dsl> --cce-kernel <kernel_name> --out_dir <out_dir>
```

The CCE path is converted into the API object model first:

```text
CCE __VEC_SCOPE__ -> VFInfo -> internal JSON-like payload -> core simulator
```

The key API classes are in `api/vf_costmodel.py`:

- `VFInfo`: complete VF program.
- `VFLoop`: loop count, unroll factor, and nested body.
- `VFInst`: VF instruction with source and destination operands.
- `MemInfo`: operand name and location.
- `Membar`: explicit memory barrier node.

`MemInfo.location` is currently `Register` or `UB`. The lowering layer still maps these to the internal historical naming convention (`V*` and `mem*`), but future code should prefer the explicit location field over name prefixes.

## 2. Main Simulation Pipeline

The current simulator pipeline is:

```text
input payload
  -> vreg live-range normalization
  -> flatten nested loops
  -> IFUUnroll
  -> IDU
  -> OOO/rename/SHQ
  -> ISU/EXQ/EXU
  -> timing logs
```

Major modules:

- `main.py`: thin CLI entry point and high-level orchestration.
- `api/input_api.py`: JSON and CCE input loading.
- `api/cce_adapter.py`: extracts CCE `__VEC_SCOPE__` kernels and parses them into `VFInfo`.
- `api/vf_lowering.py`: lowers `VFInfo` into the simulator payload format.
- `core/flatten.py`: expands nested loop structure into the linear stream consumed by IFU.
- `core/ifu.py`: produces dynamic unrolled instruction flow.
- `core/idu.py`: dispatches instructions into the OOO side while respecting IDU and queue entry rules.
- `core/ooo.py`: shared OOO state, rename table, physical register accounting, SHQ helpers, and logging helpers.
- `core/ooo_consumer_done.py`: current mainline OOO implementation with consumer-done release semantics.
- `core/isu.py`: ISU, EXQ, EXU issue, EXU inflight limit, and per-cycle execution-start selection.
- `core/ooo_factory.py`: constructs the mainline model and applies theoretical-limit overrides.
- `core/simulator_runner.py`: cycle loop connecting IFU, IDU, OOO, and ISU.

## 3. Current Mainline Behavior

The default model is equivalent to the historical `queue_level4` path, but users should not select queue levels manually anymore.

Main defaults:

- OOO model name: `queue_level4`.
- Physical register release rule: consumer-done with release at `consumer start + 4`.
- vreg live-range normalization: enabled by default before flattening.
- Queue and delay parameters: configured through `configs/uarch.json`.
- EXQ/EXU dispatch restrictions: configured through instruction metadata in `configs/isa.json`.

Important queue/resource concepts:

- SHQ is the OOO scheduling queue. Older docs may call this IQ; in the current model they are the same conceptual structure, and SHQ is the preferred name.
- EXQ is the queue in front of each EXU.
- EXU inflight limit caps how many instructions can be active in one EXU pipeline.
- SHQ credit models delayed visibility of freed SHQ slots, rather than instantaneous reuse.
- EXQ receive delay and SHQ-to-EXQ delay model staging latency between scheduling and execution resources.

## 4. ISA and Dispatch Constraints

Instruction properties come from `configs/isa.json`, `configs/forwarding.json`, and `configs/InitiationInterval.json`.

Important ISA fields:

- latency and initiation-interval related fields determine when results become usable and when another same/op-compatible instruction may issue.
- `pipeline_startup_cost` contributes to execution timing for modeled instructions.
- `EXU0_ONLY`: instruction can only enter EXQ0 and execute on EXU0.
- `EXU01`: instruction can use any normal vector EXU in the current mode. In two-port mode this means EXU0/EXU1. In `--three-ports` mode this means EXU0/EXU1/EXU2.

VLD/VST port behavior:

- Default two-port mode: VLD follows the configured load-port count, VST remains store-port limited.
- `--three-ports`: VLD can issue up to 3 per cycle, VST remains 1 per cycle, and normal compute ops can use 3 EXUs.

## 5. Theoretical-Limit Variants

Only two theoretical-limit candidates should be treated as active:

```bash
--theoretical-limit-vloop-only
--theoretical-limit-vloop-only-legacy-forwarding-direct-issue
```

`--theoretical-limit-vloop-only` keeps VLOOP timing but removes selected cross-iteration exposure gates.

`--theoretical-limit-vloop-only-legacy-forwarding-direct-issue` additionally uses the legacy forwarding interpretation and bypasses SHQ-to-EXQ staging through a direct-issue path. This is closer to the old single-queue theoretical-limit candidate.

Avoid documenting or adding new usage of removed generic flags such as `--theoretical-limit`, old `--ooo-model` variants, or queue-level selector flags.

## 6. Outputs

Common model output files:

- `start_by_cycle.json`: instructions that start execution each cycle.
- `done_by_cycle.json`: instructions that complete each cycle.
- `idu_to_ooo.json`: IDU-to-OOO dispatch timeline and visible register pressure.
- `model_warnings.json`: low-confidence situations such as excessive expanded vreg namespace.

The headline timing printed by `main.py` is:

```text
VF end cycle (with drain) = <cycles>
```

This includes the modeled VF drain time used by the simulator.
