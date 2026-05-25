# Generic Optimization Rules

These rules are operator-agnostic. Apply them to any CCE/DSL kernel unless the user gives a stricter task-specific rule.

## Baseline Requirement

If the task does not provide a working baseline, create and validate a correctness-first baseline before optimization rounds begin. Treat baseline creation as a separate phase from performance optimization. Use `source_construction.md` for CCE intrinsic lookup and baseline construction rules.

## Round Discipline

A round is a single main hypothesis plus its validation. The hypothesis must state:

- current bottleneck class
- evidence for the bottleneck
- planned code change
- expected metric movement
- correctness argument
- main risk and monitored failure signal

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

## Rollback and Search Policy

- Compare each round against the previous valid round to classify positive/negative local movement.
- Also maintain best-so-far separately.
- If two consecutive related hypotheses regress or tie without insight, roll back to the last useful base and switch sub-direction.
- If three consecutive attempts from a base fail to improve, re-read the perf log and choose a different bottleneck class.
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
