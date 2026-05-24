# Split Optimizer Guide

This note summarizes the current split-only optimization implementation, the
main optimizer variants, and the recommended CLI usage for comparison.

## Goal

All split optimizers in this repo target the same transformation:

- one original VF loop
- split into multiple top-level loops
- automatically insert boundary `VST` / `VLD`
- evaluate the result with the current simulator

The common evaluation path is:

1. build a DAG from the input trace
2. choose candidate cut depths
3. generate partitioned trace JSON with reusable `mem_inter_slot*`
4. simulate under the chosen OoO model
5. refine by local hill-climb

## Core Files

### Common split/evaluation flow

- [split_only_optimizer.py](/d:/VfSimulator/optimizer/split_only_optimizer.py)
- [partitioner.py](/d:/VfSimulator/optimizer/partitioner.py)

These implement:

- DAG partitioning by compute-depth cut points
- boundary-value slot reuse
- simulator-backed evaluation
- UB-capacity checking
- local `neighbors()` + `hillclimb()`

### Generic heuristic optimizer

- [generic_heuristic_split_optimizer.py](/d:/VfSimulator/optimizer/generic_heuristic_split_optimizer.py)

This is the current generic DAG-feature variant. It uses:

- width transitions
- high-latency op locations
- estimated live-out pressure
- periodic seeds inferred from serial/parallel regions

It is more generic than the old hand-seeded GeLU-style search, but its seed
generation can still bias search toward cuts that are too fine for some cases.

### New trip-aware generic optimizer

- [generic_trip_aware_split_optimizer.py](/d:/VfSimulator/optimizer/generic_trip_aware_split_optimizer.py)

This file was added as a non-destructive experiment and does **not** replace
the existing optimizer.

Main idea:

- keep the same generic DAG-feature logic
- keep the same simulator-backed evaluation
- keep the same hill-climb
- only strengthen `suggest_seeds()` with a weak trip-count-aware granularity prior

Important design choice:

- trip count only affects the **initial seeds**
- it does **not** directly force the final cut plan
- no operator-name-specific cut table is used

## Current OoO Default

Current default OoO model:

- `consumer-done`

See:

- [ooo_models.md](/d:/VfSimulator/docs/ooo_models.md)

That means if you do not pass `--ooo-model`, optimization will run under
`consumer-done`.

## What Is Shared Across All Split Optimizers

The following behavior is shared by the split optimizers:

- `strong` `mem_bar` model
- boundary values are materialized through `mem_inter_slot*`
- UB overflow is treated as a hard failure
- score is mainly simulation cycles, with optional cut penalty
- local search uses:
  - add one cut
  - remove one cut
  - move one cut by `-2/-1/+1/+2`

So when you compare optimizers, the main difference is usually:

- how seeds are generated

not:

- how plans are evaluated

## Current Optimizer Variants

### 1. Conservative / older baseline

Use:

- [split_only_optimizer.py](/d:/VfSimulator/optimizer/split_only_optimizer.py)

Characteristics:

- includes older warm-start seeds
- can still be useful for known GeLU-like cases
- often paired with `--cut-penalty on`

### 2. Generic DAG-feature heuristic

Use:

- [generic_heuristic_split_optimizer.py](/d:/VfSimulator/optimizer/generic_heuristic_split_optimizer.py)

Characteristics:

- no operator-name-specific warm-start cuts
- DAG-structure-based seed generation
- can still drift toward overly fine or unbalanced cuts on some cases

### 3. Trip-aware generic heuristic

Use:

- [generic_trip_aware_split_optimizer.py](/d:/VfSimulator/optimizer/generic_trip_aware_split_optimizer.py)

Characteristics:

- still generic
- still no operator-name-specific rules
- adds coarse / medium / fine seed families that vary weakly with `trip_count`
- meant to be a safer experiment for sweet-spot drift with different loop counts

## Cut Penalty

All split optimizers support:

- `--cut-penalty off`
- `--cut-penalty on`
- `--cut-penalty-scale {0.25,0.5,1.0}`

Current meaning:

- `off`: score mostly follows simulated cycles
- `on`: extra penalty is added for:
  - too many cuts
  - tiny partitions
  - tiny tail partitions
  - very unbalanced partition sizes

If you want to study the raw cost model, use:

- `--cut-penalty off`

If you want to suppress tiny tail blocks, use:

- `--cut-penalty on`

## Recommended Usage

### A. Run the current generic optimizer

```bash
python optimizer/generic_heuristic_split_optimizer.py VFtest/GeLU_poly.json ^
  --trip-count 96 ^
  --ooo-model consumer-done ^
  --cut-penalty off ^
  --output results/gelu_generic_i96.json ^
  --meta-out results/gelu_generic_i96_meta.json
```

### B. Run the new trip-aware optimizer

```bash
python optimizer/generic_trip_aware_split_optimizer.py VFtest/GeLU_poly.json ^
  --trip-count 96 ^
  --ooo-model consumer-done ^
  --cut-penalty off ^
  --output results/gelu_tripaware_i96.json ^
  --meta-out results/gelu_tripaware_i96_meta.json
```

### C. Run the older conservative-style search

```bash
python optimizer/split_only_optimizer.py VFtest/GeLU_poly.json ^
  --trip-count 96 ^
  --ooo-model default ^
  --cut-penalty on ^
  --cut-penalty-scale 1.0 ^
  --output results/gelu_old_i96.json ^
  --meta-out results/gelu_old_i96_meta.json
```

## How To Compare Them

For a fair comparison, keep these fixed:

- same input trace
- same `trip_count`
- same `ooo_model`
- same `cut_penalty` setting

Then only change the optimizer file.

A good comparison set is:

1. `generic_heuristic_split_optimizer.py`
2. `generic_trip_aware_split_optimizer.py`
3. `split_only_optimizer.py`

Compare:

- `best_cuts`
- `cycles`
- `slot_count`
- `ub_bytes`

These values are written to each `meta_out` JSON.

## Current Practical Advice

For single-loop performance estimation:

- `consumer-done` is a reasonable default

For split-search experiments:

- use the generic heuristic as the main baseline
- use the trip-aware generic heuristic as a new experimental branch
- keep the old conservative+penalty path as a comparison reference

For GeLU-like cases specifically:

- do not assume the newest generic result is automatically the best measured one
- compare simulated ranking with real measurements when possible

## Related Notes

- [ooo_models.md](/d:/VfSimulator/docs/ooo_models.md)
- [model_accuracy_notes.md](/d:/VfSimulator/docs/model_accuracy_notes.md)
