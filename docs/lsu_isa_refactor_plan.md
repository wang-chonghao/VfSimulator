# LSU / ISA Refactor Plan

## Purpose

This document records the current simulator implementation around load/store
vector instructions and proposes a refactor that makes LSU instructions
first-class ISA instructions.

The hardware distinction must remain:

- load/store style vector instructions use LSU resources
- compute vector instructions use EXU resources

The simulator implementation distinction should be reduced:

- `VLDS`, `VSTS`, `VSTAS`, `VSTUS`, and future LSU instructions should be
  represented by ISA metadata, not by scattered `op == "VLD"` / `op == "VST"`
  checks
- producer-consumer timing should be represented through dependency tables
  instead of special `pipeline_startup_cost` and `pipeline_drain_cost` paths

The goal is to make LSU op support extensible without changing hardware
semantics.

## Current Model Summary

The current mainline flow is:

```text
JSON / CCE
  -> API adapter
  -> vreg live-range normalization
  -> flatten
  -> IFU dynamic instruction generation
  -> IDU dispatch and credit gates
  -> OoO rename / SHQ / LSQ
  -> ISU / EXQ / EXU for compute
  -> LSU direct load/store issue
  -> VF end cycle
```

Relevant files:

- `main.py`: CLI entry and model selection
- `api/cce_adapter.py`: parses CCE `__VEC_SCOPE__`
- `api/vf_lowering.py`: lowers public VF API into simulator program payload
- `core/flatten.py`: static program to linear IR
- `core/ifu.py`: dynamic loop and unroll expansion
- `core/idu.py`: IDU window, VLOOP gates, and credit gates
- `core/ooo.py`: base OoO utilities and dependency timing helpers
- `core/ooo_mainline.py`: mainline rename, preg lifecycle, SHQ/LSQ/ROB,
  load/store path
- `core/isu.py`: compute SHQ -> EXQ -> EXU path
- `core/param_db.py`: configuration database for ISA, uarch, forwarding, and II

## Current Configuration Meaning

### `configs/isa.json`

Current instruction fields include:

- `latency`: compute start-to-done latency
- `pipeline_startup_cost`: currently used mostly for `VLD` producer to compute
  consumer readiness
- `pipeline_drain_cost`: currently used mostly for compute producer to `VST`
  readiness
- `data_store_cost`: currently used as `VST` duration based on the producer op
- `data_load_cost`: present, but not used by the mainline load path
- `EXU`: compute functional unit class, usually `ALU` or `SFU`
- `dispatch_exu`: legal EXU ports, such as `EXU0_ONLY`, `EXU01`, `EXU012`
- `throughput`: present, but mainline issue spacing is mostly controlled by
  `InitiationInterval.json`

Global defaults:

- `vf_startup_cost`
- `vf_drain_cost`

### `configs/forwarding.json`

Current meaning:

```text
consumer_ready_cycle = producer_start_cycle + forwarding[producer_op][consumer_op]
```

Today this is effectively compute-producer to compute-consumer forwarding.
If a pair is missing, the fallback is:

```text
max(0, producer.latency - forwarding.defaults)
```

In queue-level mode, compute wakeup uses:

```text
producer_start + max(0, forwarding - 1)
```

### `configs/InitiationInterval.json`

Current meaning:

```text
cycle(cur_op) >= last_issue_cycle(prev_op_on_same_port) + II(prev_op, cur_op)
```

This is a same-port structural issue-spacing constraint. It is not a data
dependency rule.

### `configs/uarch.json`

Relevant fields:

- `issue_ports`: compute EXU issue ports
- `load_ports`: load issue capacity per cycle
- `store_ports`: store issue capacity per cycle
- `IDU_window_width`: IDU window capacity
- `IDU_issue_width`: IDU dispatch width
- `LDQ_width`: current LSQ capacity for load/store instructions
- `vreg_num`: physical vector register count
- `shq_depth`: shared SHQ credit depth for compute and store-like paths
- `exq_depth`: per-port EXQ wait queue depth
- `idu_to_ooo_delay`: IDU to OoO transport delay
- `vloop_to_dispatch_delay`: VLOOP start to loop-body dispatch visibility
- `exq_recv_delay`: SHQ to EXQ receive delay
- `shq_to_exq_port_per_cycle`: per-port SHQ to EXQ enqueue bandwidth
- `exq_issue_inflight_cap_per_port`: per-port compute inflight cap
- `enable_shq_credit_model`: enables shared SHQ credit accounting
- `enable_credit_visibility_delay`: enables delayed preg/SHQ credit visibility
  back to IDU
- `mem_bar_mode`: memory ordering mode, currently including `strong`

## Current LSU Special Cases

The implementation currently treats only two op spellings as LSU operations:

```text
VLD = load
VST = store
everything else = compute
```

Historical JSON inputs used `VLD` and `VST` for cases that were actually
`VLDS` and `VSTS`. Those JSON inputs should be migrated to the real ISA names.
`VLD`, `VST`, `VLDS`, `VSTS`, `VSTUS`, and `VSTAS` are all ISA instruction
names, not internal load/store class names.

This appears in several layers.

### CCE Adapter

Older `api/cce_adapter.py` normalized:

```text
VLDS -> VLD
VLD  -> VLD
VSTS -> VST
VST  -> VST
```

That loses the original op identity. The adapter should preserve the uppercase
callee name so `VLD`, `VST`, `VLDS`, `VSTS`, `VSTAS`, `VSTUS`, and future LSU
instructions can reach the core as distinct ISA instructions.

### IFU

`core/ifu.py` classifies unrolled innermost body instructions as:

```text
VLD -> LD
VST -> ST
other -> ALU
```

The unroll expansion emits loads first, compute second, stores last. This
scheduling shape may still be desired, but the classification source should be
ISA metadata or a central op-class helper.

### IDU

`core/idu.py` uses hard-coded credit gates:

- `VLD`: consumes LSQ only
- `VST`: consumes LSQ and shared SHQ credit
- other ops: consume compute SHQ queue and shared SHQ credit

This should become resource-class driven:

- load-like LSU op
- store-like LSU op
- compute EXU op

### Simulator Runner

`core/simulator_runner.py` estimates in-flight IDU-to-OOO reservations using
hard-coded `VLD` and `VST` checks. This logic must use the same central resource
classification as IDU.

### OoO Dependency Timing

`core/ooo.py` currently has distinct readiness paths:

```text
VLD -> compute:
  ready = VLD.start + consumer.pipeline_startup_cost

compute -> compute:
  ready = producer.start + forwarding[producer][consumer]

compute -> VST:
  ready = producer.start + producer.pipeline_drain_cost
```

This is the main conceptual issue. `pipeline_startup_cost` and
`pipeline_drain_cost` are acting as hidden producer-consumer timing tables.

### OoO Load/Store Execution

`core/ooo_mainline.py` currently:

- places `VLD` and `VST` into `LSQ`
- places other ops into `SHQ`
- tracks memory dependencies only for `VLD`
- tracks outstanding stores only for `VST`
- historically issued `VLD` with `load_ports` and `VLD_COST`; the active path
  now uses `load_done_latency`
- issues `VST` with `store_ports` and producer `data_store_cost`
- records producer kind as `"VLD"` or `"COMPUTE"`

This hard-codes both op identity and producer kind.

## Target Architecture

### ISA-Level Op Classification

Add ISA metadata for each op:

```json
{
  "op_class": "LOAD"
}
```

or:

```json
{
  "op_class": "STORE"
}
```

Compute instructions keep EXU metadata:

```json
{
  "op_class": "COMPUTE",
  "EXU": "ALU",
  "dispatch_exu": "EXU01"
}
```

Suggested canonical meanings:

- `op_class = "COMPUTE"`: compute instruction, enters SHQ / EXQ / EXU path
- `op_class = "LOAD"`: load instruction, enters LSQ load path
- `op_class = "STORE"`: store instruction, enters LSQ store path

Compatibility while configs are migrating:

- `VLDS` should default to `LOAD` if metadata is absent
- `VSTS` should default to `STORE` if metadata is absent
- `VLD`, `VST`, `VSTUS`, and `VSTAS` are real ISA op names, but their timing
  data is intentionally not covered until calibration/config entries are added
- unknown ops should not silently become compute unless ISA lookup succeeds

### Central Resource Classification

Add central helpers, probably in `ParamDB` or a small `core/isa_traits.py`:

```python
get_op_class(op, dtype) -> "LOAD" | "STORE" | "COMPUTE"
is_compute_op(op, dtype) -> bool
is_load_op(op, dtype) -> bool
is_store_op(op, dtype) -> bool
uses_lsq(op, dtype) -> bool
uses_shq_queue(op, dtype) -> bool
uses_shared_shq_credit(op, dtype) -> bool
```

IDU, simulator runner, IFU, and OoO should all call the same helpers.

### Unified Producer-Consumer Timing

Move load-to-compute and compute-to-store timing into `forwarding.json` or a
renamed dependency table.

The target readiness rule should be:

```text
consumer_ready = max(
  vf_startup_cost,
  producer.start + dependency_delay[producer_op][consumer_op]
)
```

Examples:

```text
VLDS -> VADD
VLDS -> VEXP
VADD -> VSTS
VEXP -> VSTS
VADD -> VEXP
```

This lets `pipeline_startup_cost` and `pipeline_drain_cost` become migration
inputs rather than active special-case semantics.

Open naming choice:

- keep the file named `forwarding.json` and broaden its meaning
- or introduce `dependency_delay.json` while keeping `forwarding.json` as a
  compatibility source

The second option is cleaner semantically but touches more call sites.

### LSU Execution Timing

Load/store execution can remain separate because the resources differ:

```text
LOAD-like LSU:
  LSQ + load_ports + op.latency

STORE-like LSU:
  LSQ + store_ports + op.latency
```

The current `VST` behavior uses producer `data_store_cost` as store duration.
The refactor needs an explicit decision:

1. use store op `latency` for all store-like instructions
2. keep a producer-to-store duration table
3. temporarily derive store op `latency` from old producer `data_store_cost`
   during config migration

Recommended first step:

- use store op `latency`
- choose `VST` / `VSTS` latency values that reproduce current baseline cases
  as closely as possible
- preserve old behavior behind a temporary compatibility flag only if needed

### Memory Dependency and Barriers

Memory dependency logic should also use LSU classification:

- load-like op with memory src may depend on previous store to same memory key
- store-like op with memory dst updates last-store map
- strong intermediate-memory block release should apply to load-like ops, not
  only `VLD`

## Proposed Config Schema

Example:

```json
"VLDS": {
  "fp32": {
    "op_class": "LOAD",
    "latency": 9
  },
  "fp16": {
    "op_class": "LOAD",
    "latency": 9
  }
}
```

```json
"VSTS": {
  "fp32": {
    "op_class": "STORE",
    "latency": 9
  },
  "fp16": {
    "op_class": "STORE",
    "latency": 9
  }
}
```

```json
"VSTAS": {
  "fp32": {
    "op_class": "STORE",
    "latency": 9
  }
}
```

The exact latency and dependency values should come from calibration. The
schema is the important first boundary.

## Proposed Refactor Phases

### Phase 0: Baseline Capture

Before code changes, record current outputs for representative cases:

```bash
python3 main.py --trace VFtest/VADD_oneloop.json --out_dir results/baseline_vadd_oneloop
python3 main.py --trace VFtest/GeLU_poly.json --out_dir results/baseline_gelu_poly
python3 tools/run_cost_model_regression.py --tier smoke
```

Keep these as behavior anchors during compatibility refactor.

### Phase 1: Central ISA Traits

Add central op-class helpers.

Initial compatibility rules:

- `VLD` is load-like
- `VLDS` is load-like
- `VST` is store-like
- `VSTS`, `VSTUS`, and `VSTAS` are store-like
- ISA `op_class = LOAD` is load-like
- ISA `op_class = STORE` is store-like
- ISA `op_class = COMPUTE` or missing `op_class` for known existing compute ops is compute

Do not change behavior yet.

Expected code changes:

- `core/param_db.py` or new `core/isa_traits.py`
- `core/ifu.py`
- `core/idu.py`
- `core/simulator_runner.py`
- `core/ooo_mainline.py`

Validation target:

- existing `VLD/VST` JSON cases produce unchanged cycles

### Phase 2: ISA Entries for Legacy LSU Ops

Add `VLDS` and `VSTS` entries to `isa.json` as explicit LSU ops. Other real
LSU ISA ops such as `VLD`, `VST`, `VSTUS`, and `VSTAS` should be added only
when their timing data is ready.

Compatibility mapping:

- `VLDS.latency` or `load_done_latency` should match the historical load done
  latency
- `VSTS.latency` needs careful handling because current duration is producer
  `data_store_cost`

At this phase, it is acceptable to keep store duration compatibility if needed,
but the classification should already be ISA-driven.

### Phase 3: Unified Dependency Timing

Replace special readiness rules with unified producer-consumer dependency
lookup.

Migrate old behavior:

- old `VLD -> compute`:
  - write table entries from `VLD` to each compute consumer using the old
    consumer `pipeline_startup_cost`
- old compute -> `VST`:
  - write table entries from each compute producer to `VST` using old producer
    `pipeline_drain_cost`
- old compute -> compute:
  - keep existing forwarding entries

Queue-level `-1` alignment needs to be preserved or explicitly redefined. If
preserved, apply it consistently to dependency-table wakeup for queue-level
compute consumers.

### Phase 4: Preserve Real CCE LSU Ops

Stop normalizing CCE ops to `VLD` / `VST`.

Change `api/cce_adapter.py` so:

- `vlds` becomes `VLDS`
- `vsts` becomes `VSTS`
- `vstas` becomes `VSTAS`
- `vstus` becomes `VSTUS`
- other vector op names preserve their uppercase canonical op

Add ISA and dependency entries for the newly preserved ops.

### Phase 5: LSU Path Cleanup

Rename internal concepts where useful:

- `LSQ` can remain as the queue name
- comments should refer to load-like / store-like LSU ops instead of `VLD/VST`
- producer kind should avoid hard-coded `"VLD"` / `"COMPUTE"` if op names are
  sufficient
- store tracking should be based on `is_store_op`
- load memory dependency should be based on `is_load_op`

At the end of this phase, grep should show no model-critical direct checks of
`op == "VLD"` or `op == "VST"` outside legacy compatibility code and tests.

## Historical Cleanup Plan

After the LSU op-class refactor, the codebase still carries several historical
compatibility paths. These should be removed step by step while preserving the
current `queue_level4` mainline results.

### 1. Release Rule Legacy Path

Current mainline behavior is source release at:

```text
consumer.start_cycle + consumer_release_start_offset
```

The old done-based alternative is no longer a separate supported model. Remove:

- removed `consumer_release_from_start = false` behavior
- removed `consumer_done_release_delay`
- removed `release_done_delay`
- removed done-based source release logic in `PregLifecycleController.on_uop_done`

Keep `consumer_release_start_offset` as the single mainline uarch parameter.

### 2. Old Naming Residue

The class/file names still carry the old `consumer_done` wording even though the
mainline behavior is start-based release and queue-level4 timing. After logic
cleanup, rename toward a mainline name such as:

- `OoOCoreMainline`
- `OoOCoreQueueLevel4`

The active class has been renamed to `OoOCoreMainline`, and the active file is
now `core/ooo_mainline.py`.

### 3. Old LSU Name Residue

Remove old `VLD` / `VST` terminology from active model code where it is only a
generic load/store label:

- remove `VLD_COST` fallback after `load_done_latency` or ISA load latency is
  established (completed for the active `load_done_latency` path)
- update comments from `VLD/VST` to load/store LSU op where appropriate
- keep real ISA names `VLD`, `VST`, `VLDS`, `VSTS`, `VSTUS`, and `VSTAS` only
  when referring to actual ops

### 4. Historical Model Label Residue

The current simulator has one concrete mainline backend: `queue_level4`.
Historical labels such as `consumer-done`, `queue_level1`, `queue_level2`, and
`queue_level3` should be removed from active code paths where they no longer
select distinct behavior.

Historical reports and docs can remain as archived context, but active CLI/API
code should not imply that those are still separate maintained modes.

## Important Open Questions

1. Should the dependency table stay named `forwarding.json`, or should we add a
   clearer `dependency_delay.json`?
2. Should store-like instruction duration be the store op's `latency`, or should
   there be a producer-store duration table?
3. Do `VSTS`, `VSTAS`, and `VSTUS` share one store port pool, or do any of them
   need separate LSU sub-resource modeling?
4. Should load-like instructions include more than `VLDS`, and do any require
   separate load port pools?
5. Is the queue-level `forwarding - 1` alignment still valid for all dependency
   pairs, or only for compute consumers entering SHQ/EXQ?

## Validation Checklist

After each phase:

```bash
python3 -m py_compile main.py api/*.py core/*.py
python3 main.py --trace VFtest/VADD_oneloop.json --out_dir results/sanity_vadd_oneloop
python3 main.py --trace VFtest/GeLU_poly.json --out_dir results/sanity_gelu_poly
python3 tools/run_cost_model_regression.py --tier smoke
```

Expected first-phase behavior:

- migrated JSON traces using `VLDS` / `VSTS` should remain cycle-compatible
- `idu_to_ooo.json`, `start_by_cycle.json`, and `done_by_cycle.json` should
  preserve instruction ordering for legacy cases unless a phase explicitly
  changes timing semantics

## Summary

The desired model boundary is:

```text
op is a first-class ISA instruction
ISA classifies op as LOAD, STORE, or COMPUTE
uarch defines queue, port, and credit capacity
dependency table defines producer-consumer ready timing
core executes classification and timing rules without hard-coded LSU op names
```

This preserves hardware non-equivalence while removing simulator implementation
non-equivalence.
