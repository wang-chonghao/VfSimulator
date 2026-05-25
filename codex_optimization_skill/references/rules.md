# Generic Optimization Rules

These rules are operator-agnostic. Apply them to any CCE/DSL kernel unless the user gives a stricter task-specific rule.

## Baseline Requirement

If the task does not provide a working baseline, create and validate a correctness-first baseline before optimization rounds begin. Treat baseline creation as a separate phase from performance optimization. Use `source_construction.md` for CCE intrinsic lookup and baseline construction rules.

## Fixed CCE Compile Options

For CCE build/run validation, keep compiler scheduling and VF fusion controls fixed so optimization gains are attributable to source changes:

- must enable `-mllvm -cce-aicore-vec-misched=0`
- must disable VF fusion with `--cce-simd-vf-fusion=false`

Do not compare or log candidate performance from builds that omit these options unless the task explicitly changes the compiler-policy experiment.

## Round Discipline

A round is a single main hypothesis plus its validation. The hypothesis must state:

- current bottleneck class
- evidence for the bottleneck
- planned code change
- expected metric movement
- correctness argument
- main risk and monitored failure signal

Do not edit candidate source until the pre-edit hypothesis is explicit. If the bottleneck, evidence, expected metric movement, or correctness argument is unclear, gather more evidence first.

Allowed single-hypothesis examples:

- one VF fusion/split strategy
- one loop unroll strategy
- one loop split/fusion strategy
- one local instruction reorder strategy within a loop or stage
- one algorithmic instruction replacement strategy
- one provably redundant synchronization removal or relocation

Forbidden in a single round:

- changing multiple unrelated stages
- mixing cleanup/refactor with optimization
- changing workload, thresholds, or benchmark scripts
- silently retaining a candidate without logging the result
- using performance data from failed correctness/build cases

## Pre-Edit Checklist

Before each source change, write down:

- current bottleneck class
- trace or cost-model evidence for that bottleneck
- why the planned change should address it
- expected movement in CAModel metrics
- correctness and precision preservation argument
- main risk and the first failure signal to watch

After each candidate, compare the evidence with the prediction. Do not start the next round from an unresolved "maybe" conclusion.

## Candidate Scanning Within One Round

A bounded candidate scan is allowed only when all candidates are variants of the same hypothesis. Examples:

- one loop's unroll factor in `{1,2,4,8}`
- one VF fusion factor family
- one independent processing order family

The round log must identify the chosen candidate and reject the others. If candidates reveal a different bottleneck, stop the scan and start a new round.

## Correctness Gate

For a generated baseline, the same correctness gate applies before recording any baseline performance.


For every candidate:

1. build the candidate
2. run correctness/golden validation
3. only if correctness passes, parse performance
4. append the round log

If the repository runner has no golden check for the target operator, implement a task-local golden checker without weakening tolerances or changing workload.

When the runner reports fields such as `mismatches`, `max_abs_err`, `max_rel_err`, and `PASS/FAIL`, record them all. Use the task-local tolerance exactly as provided; do not relax thresholds to make a candidate valid. If correctness fails, identify and record the cause before treating a repaired version as a valid candidate.

## Per-Round CCE Checklist

Check these before and after each candidate:

- UB capacity and lifetime, including target-specific limits such as a 248 KiB UB when applicable
- dependency structure: no unintended long chains, serialization, or changed producer/consumer logic
- scheduling and dual-issue behavior: use IPC, issue gaps, or trace evidence rather than visual instruction parallelism
- synchronization: each `mem_bar`, `wait`, or `set_flag` is necessary, moved with proof, or explicitly left unchanged
- resource side effects: identify bottleneck movement such as data movement to sync, compute to register pressure, or loop overhead to scalar address work

Record the relevant checks in the round log; a faster but unexplained trace is not a complete round result.

## Rollback and Search Policy

- Compare each round against the previous valid round to classify positive/negative local movement.
- Also maintain best-so-far separately.
- If two consecutive related hypotheses regress or tie without insight, roll back to the last useful base and switch sub-direction.
- If three consecutive attempts from one base fail to improve, first try that base round's best rejected/second-best candidate if it was preserved and still has a coherent hypothesis.
- If no useful second-best candidate exists, roll back one more valid base, re-read the round log, and choose a different bottleneck class.
- Preserve rollback rounds when they establish a clean base for future rounds.

## Editable Scope

Default editable:

- target CCE/DSL source
- direct kernel-generation helper scripts
- task-local validation or plotting scripts

Default read-only unless explicitly requested:

- cost model implementation
- optimizer framework internals
- shared references or installed skills
- benchmark workload definitions
- correctness thresholds
- result parser semantics

## Required Round Log Fields

Each round should record:

- timestamp
- round id
- kernel/source path
- trace/run directory
- changed files
- current bottleneck
- evidence
- hypothesis
- planned change
- expected metric change
- main risk
- correctness result: mismatches, max_abs_err, max_rel_err
- cost-model evidence used, if any
- CCE/CAModel metric: first VF start, last VF end, total, per-VF execute, instr counts when available
- candidate vs previous round
- candidate vs best-so-far
- resource side effects: UB, register pressure, sync count, movement pattern
- decision: keep, reject, rollback, preserve
- next action

## Commit Gate

Commit an optimization only when all conditions hold:

- correctness passes under the unchanged task-local golden check
- CAModel total VF latency has a reproducible improvement
- the source change and measured gain have a clear causal explanation
- build flags, workload, thresholds, and parser semantics were unchanged
- the code remains maintainable enough for future rounds

Recommended commit message format when the repository has no stricter convention:

```text
[kernel_name] <optimization_type>: <cce_gain>
```
