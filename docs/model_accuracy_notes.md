# Model Accuracy Notes

This note records what parts of the current simulator are relatively accurate,
and what parts are still clearly mismatched against measurement.

## Current Default

- Default OoO model: `consumer-done`
- Default split search setting: `cut_penalty=off`
- `mem_bar_mode`: `strong`

## Relatively Accurate

### 1. Single-loop baseline-style VF kernels

The current `consumer-done` model is noticeably better than the old conservative
rename-release model on simple single-loop cases.

Observed pattern:

- Plain single-loop VF cases are generally closer to measurement than before.
- For these cases, using `consumer-done` as the default simulation model is reasonable.

### 2. Register-release probe: `src_fanout_probe`

Measured:

- `src_fanout_probe`: about `285 cycles`

Simulated:

- `default`: `389`
- `last-use`: `287`
- `consumer-done`: `287`

Conclusion:

- `consumer-done` / `last-use` are very close here.
- This suggests the model for overwrite-sealed old-version release is reasonable on this probe.

### 3. `online_update` whole-kernel trend

For `online_update`, `consumer-done` gives better whole-kernel behavior than the old conservative model.

Examples already checked:

- `I=64` baseline with `consumer-done`: `VF end = 463`
- `I=64` theoretical limit: `VF end = 389`

Also, for the split candidate previously tested:

- baseline is better than the split version under `consumer-done`

This matches the real-hardware trend direction better than the older model.

### 4. Strong mem_bar is modeled as an OoO-side readiness barrier

This part has been sanity-checked with a minimal two-loop case:

- Later-loop uops can still enter OoO early.
- Boundary `VLD(mem_inter_*)` are blocked in OoO until release.
- After release, later-loop `VLD` and compute can quickly ramp up and even dual-issue.

Conclusion:

- The simulator does not simply serialize two loops at IDU.
- The model already captures the "uops accumulate first, then execute quickly after barrier release" effect.

## Clearly Inaccurate / Still Under Investigation

### 1. Split GeLU optimization ranking for `I=96`

This is the clearest known mismatch today.

Two candidate strategies:

- Old strategy from conservative model + penalty: `[4, 8, 12]`
- New strategy from `consumer-done` + no penalty: `[6, 16]`

Simulation under current `consumer-done + off`:

- `[4,8,12]`: `VF end = 1997`
- `[6,16]`: `VF end = 1884`

Measured:

- `[4,8,12]`: `1837`
- `[6,16]`: `1920`

Conclusion:

- Current simulation not only has absolute error here.
- It gets the ordering wrong.
- This means the current model is still not reliable enough as the default cost model for this split-search case.

### 2. Standalone GeLU split partitions for `I=96`

Old plan `[4,8,12]`, standalone loop simulation:

- loop0 sim: `301`, measured: `312`, gap: `11`
- loop1 sim: `553`, measured: `504`, gap: `49`
- loop2 sim: `635`, measured: `523`, gap: `112`
- loop3 sim: `589`, measured: `571`, gap: `18`
- standalone sum sim: `2078`, measured: `1910`, gap: `168`

New plan `[6,16]`, standalone loop simulation:

- loop0 sim: `507`, measured: `567`, gap: `60`
- loop1 sim: `1285`, measured: `1153`, gap: `132`
- loop2 sim: `203`, measured: `224`, gap: `21`
- standalone sum sim: `1995`, measured: `1944`, gap: `51`

Conclusion:

- Standalone subloop accuracy is mixed, not uniformly bad.
- Old plan `[4,8,12]` is fairly close on loop0 and loop3, but optimistic on loop2.
- New plan `[6,16]` has a better standalone total-sum match, but its middle large loop is still significantly off.
- So the split-ranking problem is not explained by a simple "all subloops are wrong in the same direction" story.

### 3. GeLU sweet-spot shape may be biased toward overly fine / unbalanced cuts

Current `consumer-done + cut_penalty=off` can prefer cuts such as:

- `I=64`: `[7,16]`
- `I=96`: `[6,16]`

These solutions can leave a very short tail partition.

Current concern:

- The model may be shifting the sweet spot toward more fragmented or more unbalanced cuts than real hardware prefers.
- This is consistent with the `I=96` mismatch above.

### 4. GeLU standalone subloops with heavy `mem_inter` loads / long mixed chains

The largest known mismatches currently appear in GeLU subloops that contain some combination of:

- multiple `VLD(mem_inter_*)`
- long dependent compute chains
- `VDIV`
- mixed load / compute / store structure

Conclusion:

- For these blocks, the current model is still too optimistic.
- Possible causes include register release, but the root cause is not yet isolated.

## Current Working Hypothesis

What is likely true:

- `consumer-done` is a better default simulation model for plain single-loop VF kernels.
- The current inaccuracy is now more concentrated in:
  - GeLU-style split subloops
  - sweet-spot movement after splitting
  - whole-plan ranking between alternative cut strategies

What is not yet proven:

- That register release is the only root cause
- That multi-loop mem_bar stitching alone is the main cause

## Practical Guidance For Now

- Use `consumer-done` as the default simulator for single-loop VF performance estimation.
- Be cautious about using `consumer-done + cut_penalty=off` as the final optimization cost model for GeLU split search.
- For GeLU-like split optimization, the older conservative strategy with cut penalty is still closer to current measurement on the known `I=96` case.
