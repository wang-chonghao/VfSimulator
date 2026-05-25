# Generic Optimization Patterns

These are reusable patterns, not operator-specific recipes. Apply only when bottleneck evidence supports them.

## VF Fusion / Split

Use when many small VFs create visible launch/head-tail overhead and UB/register resources can tolerate fusion.

Workflow:

1. establish baseline VF count, `instr_num`, and total window
2. test conservative fusion first
3. monitor `instr_num`, per-VF execute, last-end drain, correctness
4. stop increasing fusion when larger VFs regress
5. consider nonuniform grouping if the total problem size is not divisible by the best factor

Avoid assuming bigger fusion is always better. Oversized VFs can hit queue, register, or drain limits.

## Shared Barrier Grouping

Use when multiple independent store phases are followed by load phases that require store-to-load ordering.

Pattern:

1. complete several independent store-producing segments
2. issue one necessary `mem_bar(VST_VLD)` or equivalent
3. run the load-consuming segments

Requirements:

- UB regions must not overlap incorrectly
- barrier type must match ordering requirement
- correctness must pass after reordering
- live values held across the shared barrier must be accounted for

## Reduction Restructuring

Use when reduction instructions are frequent or EXU0-only pressure is high.

Candidate forms:

- vector accumulation followed by one final reduction
- multiple independent accumulators merged as a tree
- replace repeated block reductions with vector ops where mathematically equivalent

Risks:

- more live accumulators increase register pressure
- merge order changes dependency depth
- numerical associativity may change; correctness tolerance must be respected

## Invariant Hoisting / Long-Op Replacement

Use when the same expensive operation is repeated across many elements or blocks.

Examples of generic transformations:

- compute an invariant scale once, apply with cheaper multiply/add
- hoist broadcast/setup outside a loop when inputs are unchanged
- replace repeated long operation with one long operation plus vector ALU ops

Requirements:

- prove the hoisted value is invariant for the transformed scope
- confirm precision/rounding remains acceptable

## Loop Unroll Tuning

Use when loop overhead or insufficient independent work is plausible.

Guidelines:

- tune one loop at a time
- prefer bounded factors such as 1, 2, 4, 8
- monitor `instr_num`, register pressure, and long-op clustering
- do not continue unroll tuning after repeated ties/regressions

Unroll can regress even when instruction count decreases, because loop structure, queue behavior, and issue spacing can change.

## Loop Split / Fusion

Horizontal split can shorten dependency chains but may add stores and barriers. Vertical split can improve unroll compatibility for awkward loop counts.

Use when evidence shows:

- a long dependent stage blocks later independent work
- one loop contains stages with different optimal unroll/order needs
- a loop bound has poor unroll behavior

Avoid split when each resulting loop becomes memory-bound or barrier-heavy.


## Hardware-Loop-Oriented Restructuring

Use when loop structure itself is suspected to limit performance, not just the operations inside the loop.

Hardware-loop-friendly loops generally require:

- simple canonical loop form
- loop variable starts from zero and increments by one
- static or simple bounds representable by the target compiler/hardware-loop mechanism
- no branches, early exits, or irregular control flow inside the hardware-loop body
- nesting depth within the target hardware-loop limit

Candidate transformations:

- combine several repeated identical loops into a two-level nested loop when it reduces loop head overhead
- flatten nested loops into one loop when nested-loop overhead or scalar offset calculation dominates
- split a loop when different sections need different unroll/order/fusion choices
- normalize loop variables and bounds to target-friendly integer types and forms

Risks:

- a structure that looks simpler may generate worse scalar address arithmetic
- flattening can obscure regular address patterns
- nesting can exceed hardware-loop limits or increase scalar offset work
- removing branches may require predication or separate boundary loops; validate correctness carefully

## POST_UPDATE Addressing

Use when vector load/store offset calculation is a plausible scalar bottleneck, especially in deeper nested loops or complex address expressions.

Generic transformation pattern:

```c
vlds(vec_x, data_x, computed_offset, NORM);
vsts(vec_x, data_y, computed_offset, NORM_B32, mask);
```

may become a post-update style form when the intrinsic supports it:

```c
vlds(vec_x, data_x, stride, NORM, POST_UPDATE);
vsts(vec_x, data_y, stride, NORM_B32, mask, POST_UPDATE);
```

Guidelines:

- Use intrinsic documentation or active CANN headers to confirm exact syntax and pointer update semantics.
- Apply only when address progression is regular and pointer mutation does not break later code.
- It is often not profitable for simple one-level or two-level loops where offset computation is cheap.
- It can help when three or more nested loops, multi-dimensional indexing, or repeated scalar offset expressions create visible scalar overhead.
- Treat it as a separate round hypothesis; do not combine with loop restructuring or unroll changes in the same round.

Risks:

- pointer state becomes part of correctness; reset or use separate pointers for each loop/segment as needed
- simple loops may regress due to post-update overhead or less favorable scheduling
- incorrect stride or pointer lifetime can silently corrupt UB/GM addressing

## Instruction Reordering

Use when topology permits moving independent instructions to reduce long-op density, store-port tail, or live range.

Rules:

- preserve producer/consumer dependencies
- preserve required memory ordering
- change only one local order dimension per round
- correctness failures imply the dependency proof was wrong or incomplete

## Processing Order / Live-Range Tuning

Use when a fused VF processes multiple independent segments and holds several intermediate values live.

Candidate directions:

- process the segment whose live value was created most recently
- process the segment whose value has longest live range
- test bounded permutations only when evidence shows order matters

Stop when permutations tie or regress; do not exhaustively search without a bottleneck reason.
