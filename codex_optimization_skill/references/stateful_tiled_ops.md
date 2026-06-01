# Stateful Tiled Operator Rules

Use these rules when optimizing tiled or online operators whose later tiles or downstream stages may consume state produced by earlier tiles. Examples include streaming softmax, tiled attention, blockwise normalization, recurrent reductions, and any pipeline that carries max/sum/scale/update state across tiles.

These rules prevent a single benchmark shape from making required state outputs look redundant.

## Decide Shape Scope First

Before optimization, explicitly classify the task as one of:

- **shape-specialized**: the optimized kernel is only required to be correct for the exact benchmark shape and tile count provided by the user
- **shape-general**: the optimized kernel must preserve semantics for the operator family, including other legal tile counts and boundary cases

If the user does not explicitly request shape-specialized code, use shape-general rules. A performance benchmark may still use one fixed shape, but semantic changes must remain valid for other legal shapes in the same operator contract.

## Stateful Output Inventory

Before the first optimization round, list stateful inputs and outputs. Treat them as semantic outputs by default, even if the current benchmark shape does not consume them.

For online softmax or tiled attention, typical stateful values include:

- running or new global max
- running or new global sum
- rescale factors such as `exp(old_max - new_max)`
- probability or exp tiles consumed by a later matmul
- partial output tiles and running output accumulators
- pipeline FIFOs or intermediate tiles consumed by later stages

Function parameters marked as output-like, such as `__out__`, must not be deleted or skipped just because the current shape does not read them. Deleting a stateful store is allowed only after a whole-operator dataflow check proves the value is not part of the valid output contract for all target shapes.

## Multi-Tile Dataflow Review

If the benchmark has a degenerate tile count, also reason about at least one review shape that exercises the next online/tiled path. The review shape is used to identify state dependencies and correctness risks, not necessarily as the performance benchmark.

For a tiled `S1/TILE_S1` operator, if the benchmark has:

```text
num_tiles_s1 = 1
```

then inspect a review shape with:

```text
num_tiles_s1 >= 2
```

Do this before deleting stores, moving state updates, or changing pipeline synchronization. Check whether values produced by the first tile are read by later tile code, global update code, or downstream stages.

## Review-Shape Correctness Cadence

When optimizing under a fixed benchmark shape, run correctness on a review shape at least once every 5 valid optimization rounds if:

- the operator is stateful or tiled
- the benchmark shape does not cover every semantic path
- any retained change touches state production, update logic, pipeline FIFOs, or output-like parameters

The review shape must keep the same operator family but exercise the missing path. For example, a single-tile attention benchmark may use a two-tile review shape to cover online update logic.

Review-shape correctness is a semantic guardrail. Do not use its timing as the benchmark unless the user explicitly changes the benchmark contract.

If review-shape correctness fails:

- mark the latest candidate invalid for shape-general optimization
- restore or revise the stateful change before continuing
- record the failure and the state dependency it revealed

## Allowed and Disallowed Transformations

Generally allowed when correctness holds:

- reduce temporary accumulator count without changing final state values
- reorder independent instructions while preserving state dependencies
- remove truly unconsumed temporary values after whole-operator dataflow proof
- reduce redundant computation that does not affect state carried across tiles

Generally disallowed for shape-general optimization:

- delete a store to a stateful output because only the benchmark shape is single-tile
- skip updating running max, sum, scale, or output state needed by later tiles
- remove output-like parameters from the semantic contract without caller proof
- change FIFO or pipeline state production based only on one shape's inactive path

## Round Log Requirements

For stateful/tiled operators, each relevant round log entry should include:

- benchmark shape and tile count
- review shape and which path it covers, when used
- stateful outputs affected by the change
- proof that later tiles or downstream stages still receive required state
- benchmark correctness result
- review-shape correctness result when the cadence requires it

