# Current Effective Config for `queue_level3 + vreg pass`

This note records the **actual effective behavior** used by the current
`queue_level3 + vreg pass` report path, and distinguishes it from features that
the codebase merely supports.

Relevant report:

- [accuracy_report_queue_v3_vregpass.md](/D:/VfSimulator/results/unroll_test/accuracy_report_queue_v3_vregpass.md)

Relevant code:

- [ooo_factory.py](/D:/VfSimulator/core/ooo_factory.py)
- [ooo_consumer_done.py](/D:/VfSimulator/core/ooo_consumer_done.py)
- [uarch.json](/D:/VfSimulator/configs/uarch.json)

## Short Answer

For the current `queue_level3 + vreg pass` path:

- `SHQ` capacity: enabled and finite
- `EXQ` capacity: mechanism exists, but default effective depth is near-infinite
- `EXQ recv delay`: enabled
- `SHQ -> EXQ per-port admit limit`: enabled
- `preg free visibility delay back to IDU`: enabled
- `SHQ credit visibility delay back to IDU`: enabled
- register release rule: current branch default `start+5` path, effectively `consumer start + 4`
- vreg pass: enabled when the report is generated with `--enable-vreg-live-range-normalization`

## Effective Parameters

### 1. OOO model selection

The default factory model is:

- `DEFAULT_OOO_MODEL = "queue_level3"`

Code:

- [ooo_factory.py](/D:/VfSimulator/core/ooo_factory.py)

### 2. SHQ depth

`queue_level3` inherits from `queue_level2`, and `queue_level2` explicitly turns
on finite `SHQ` modeling:

- `enable_queue_level2_shq_model = True`
- `shq_depth = queue_level2_shq_depth if provided else uarch.shq_depth`

Current `uarch` value:

- `shq_depth = 58`

So the current report path **does model finite SHQ capacity**, with effective
default:

- `SHQ depth = 58`

Code:

- [ooo_factory.py](/D:/VfSimulator/core/ooo_factory.py)
- [uarch.json](/D:/VfSimulator/configs/uarch.json)

### 3. EXQ depth

This is the important subtlety.

The base `uarch` contains:

- `exq_depth = 26`

But `queue_level2` overrides the default effective depth to:

- `exq_depth = queue_level2_exq_depth if provided else 10**9`

And `queue_level3` inherits that.

So for the current `queue_level3` report path:

- code supports finite `EXQ` depth
- but the **effective default** is:
  - `EXQ depth = 10**9`

Meaning:

- `EXQ` capacity checking code is present
- but for this report path it is effectively not constraining execution

Code:

- [ooo_factory.py](/D:/VfSimulator/core/ooo_factory.py)
- [ooo_consumer_done.py](/D:/VfSimulator/core/ooo_consumer_done.py)

### 4. EXQ capacity counts inflight?

Current `uarch` default:

- `exq_capacity_counts_inflight = false`

So even if finite `EXQ` depth were enabled, the capacity check would count:

- queued wait entries
- but not currently executing / inflight entries

Current report path therefore effectively uses:

- `EXQ depth check exists`
- `inflight not counted`
- `depth itself effectively infinite`

### 5. EXQ receive delay

Current `uarch`:

- `exq_recv_delay = 1`

This **is active** in the queue model.

Meaning:

- a uop issued from `SHQ` at cycle `t`
- reaches `EXQ` at cycle `t + 1`
- and cannot be considered for `EXU` issue before that receive point

Code:

- [uarch.json](/D:/VfSimulator/configs/uarch.json)
- [ooo_consumer_done.py](/D:/VfSimulator/core/ooo_consumer_done.py)

### 6. SHQ -> EXQ per-cycle admit limit

Current `uarch`:

- `shq_to_exq_port_per_cycle = 1`

This is active in the queue path.

Meaning:

- per cycle, each EXQ port can accept at most 1 instruction from `SHQ`

So with 2 issue ports, the current model allows at most:

- 1 into `EXQ0`
- 1 into `EXQ1`

per cycle.

Code:

- [uarch.json](/D:/VfSimulator/configs/uarch.json)
- [ooo_consumer_done.py](/D:/VfSimulator/core/ooo_consumer_done.py)

### 7. IDU -> OOO and visibility delays

Current `queue_level3` defaults:

- `vloop_to_dispatch_delay = 2`
- `idu_dispatch_start_advance = 2`
- `idu_to_ooo_delay = 1`
- `queue_level3_idu_visible_delay = 2`

And in the current branch build path:

- `queue_level3_preg_visible_delay = 0` by default in factory
- `queue_level3_shq_visible_delay = 0` by default in factory

Important nuance:

- `queue_level3_idu_visible_delay = 2` is the conceptual intended delay
- but the current factory default wiring for `preg/shq visible delay` is `0`
  unless explicitly overridden in input config

So the **actual effective value depends on what the runtime config passes in**.

For the current branch/report discussions we have been using the branch’s
current code path as the source of truth, so when you ask "does level3 include
IDU-visible delay", the correct answer is:

- yes, the mechanism exists and is active
- but the exact effective `preg/shq visible delay` must be read from the
  resolved runtime config, not inferred only from the high-level comment

### 8. Register release rule

Current branch default is the `start+5` path, but in practice the effective
release point discussed in recent validation is:

- source release anchored to `consumer start + 4`

This is still implemented inside the `OoOCoreConsumerDone` path; `queue_level3`
adds queueing and credit-visibility behavior on top of it.

### 9. Vreg pass

The report path named `queue_v3_vregpass` also enables the vreg normalization
pass:

- `--enable-vreg-live-range-normalization`

This is separate from the queue model itself.

## What Is Actually Constraining the Current Report

For the current `accuracy_report_queue_v3_vregpass` path, the main active
constraints are:

- finite `SHQ` depth
- `SHQ` credit release and visibility logic
- `SHQ -> EXQ` per-port per-cycle admit limit
- `EXQ recv delay = 1`
- queue-side scheduling policy inside `OoOCoreConsumerDone`
- current register release timing

## What Is Not Really Constraining It

Despite the code supporting it, the following is **not effectively active by
default** for this report path:

- realistic finite `EXQ depth = 26`

Because the effective default inherited from `queue_level2` is:

- `exq_depth = 10**9`

So if we are investigating why the current model is too short or too long, we
should be careful not to assume that `EXQ=26` is already in force.

## Final One-Line Summary

For the current `queue_level3 + vreg pass` report:

- `SHQ` is modeled as finite
- `EXQ` is functionally modeled, but its **capacity is effectively infinite**
  unless we explicitly override `queue_level2_exq_depth` / `exq_depth`
