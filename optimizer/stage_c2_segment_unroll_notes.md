# Stage C2 Notes: Joint Split + Per-Segment Unroll

This note extends Stage C with per-segment unroll optimization.

## Why C2 (Mainline Objective)

After loop split, different segments can have different dependency shapes:
- single-chain head/tail may prefer larger unroll (e.g. 2)
- wider parallel middle segment may prefer unroll 1

So C2 optimizes jointly:

`cuts + {U_0, U_1, ... U_{K-1}}`

where `K` is number of top-level split loops.

Important fairness rule:

- For each cut plan, first solve its own best unroll vector.
- Compare cut plans by their *own best* `(cuts, U*)` timing.

## Key Algorithm (Two-Level Search)

1. Outer search: generate candidate cut plans.
2. Inner optimization: for each cut plan, optimize per-segment unroll vector by coordinate descent.
3. Score each cut plan by its best inner result.
4. Select global best `(cuts*, U*)`.

Use strong caching:
- plan cache key: `(cuts, U_vector, mode, ooo_model, I, dtype)`
- segment proxy cache key: `(segment_signature, U, mode, ooo_model, I)`

## Current Script

- `optimizer/stage_c2_segment_unroll_optimizer.py`

Main CLI:

```bash
python optimizer/stage_c2_segment_unroll_optimizer.py \
  VFtest/GeLU_poly.json \
  --trip-count 64 \
  --loop-opt-mode loop_cut_loose \
  --u-candidates 1,2,3,4 \
  --output-dir results/stage_c2_gelu_i64
```

Optional:
- `--cuts 9,16` to force fixed cuts
- `--max-coord-iters 3`
- `--beam-width/--beam-rounds` for outer cut search breadth/depth

## Output

- `summary.json`: best cuts, best U vector, best vf_end, cache stats, search metadata
- `best_trace.json`: trace with per-segment unroll applied

## Next Steps

1. Add incremental recompute for only changed neighboring segments.
2. Add top-k candidate export for CCE validation (not just top-1).
3. Add CCE validation pipeline for Stage C2 finalists.

## Code Organization

Keep two scripts for now:

1. `stage_c_optimizer.py`:
- foundational Stage-C capabilities (mode constraints, reorder, split evaluation).

2. `stage_c2_segment_unroll_optimizer.py`:
- joint search mainline (cuts + per-segment unroll).

External recommendation:

- use `stage_c2_segment_unroll_optimizer.py` as the optimization entrypoint.

## Decision (Current)

- Yes, we keep **two Python modules** in development stage.
- But we provide **one unified CLI entrypoint** for users.

Reason:
- `stage_c_optimizer.py` is the reusable foundation (split/reorder/constraint engine).
- `stage_c2_segment_unroll_optimizer.py` is the joint optimizer (cuts + per-segment unroll).
- Forcing them into one file now would reduce readability and make iterative tuning harder.

Unified CLI:

```bash
python optimizer/run_vf_stage_c_optimization.py --algo stage_c2 ...
```

Default recommendation:
- use `--algo stage_c2` for normal optimization runs.
- use `--algo stage_c` only for debugging and ablation.
