---
name: cce-camodel-optimization
description: Use when optimizing Ascend CCE vector kernels with CANN CAModel/VfSimulator evidence, including iterative round-based performance tuning, golden correctness validation, VF timing extraction, bottleneck classification, and perf_log record keeping.
metadata:
  short-description: Optimize CCE kernels with CAModel evidence
---

# CCE/CAModel Optimization Skill

Use this skill for CCE/DSL kernel optimization when performance must be validated by CANN CAModel and correctness must be preserved. Do not assume any operator type, tensor shape, dataflow, or implementation structure unless the current task provides it.

## Core Workflow

1. **Read the task-local docs and code first**
   - Read the target kernel/DSL and workload definition.
   - If no baseline kernel is provided, construct a correctness-first baseline before optimization.
   - Read cost-model/uarch/ISA files before proposing the first optimization hypothesis.

2. **Establish the measurement contract**
   - CCE/CAModel is the final performance authority.
   - VfSimulator/cost-model evidence is only for understanding hardware behavior and forming hypotheses.
   - VF total for multi-VF cases is: `last VF end from instr_log.dump - first VF start from instr_popped_log.dump`.
   - Correctness validation is mandatory before any performance conclusion.

3. **Run one round at a time**
   - Each round validates one main hypothesis.
   - Do not pre-schedule many unrelated rounds even if the user asks for multiple rounds.
   - If the hypothesis is parameter tuning for one loop/fusion factor/order dimension, bounded candidates may be tested as one round only when the candidate set is declared upfront.

4. **Record every round**
   - Preserve the candidate source for every round.
   - Append a round entry to `perf_log.md` or the repo's equivalent log immediately after the round.
   - Track both `candidate vs previous round` and `candidate vs best-so-far`.

5. **Change direction on evidence**
   - After repeated ties/regressions, re-read logs and switch bottleneck class.
   - Prefer structural changes over random microtuning when local reorder/unroll attempts stop helping.
   - After every 10 completed rounds, re-read this `SKILL.md` and the relevant references before proposing the next round.

## What To Read When

- Read [references/rules.md](references/rules.md) before starting optimization or when the user asks whether the process is compliant.
- Read [references/source_construction.md](references/source_construction.md) when no baseline exists, when generating CCE/DSL code, or when intrinsic syntax/semantics are uncertain.
- Read [references/isa.md](references/isa.md) when barrier semantics or vector ISA behavior are needed.
- Read [references/metrics.md](references/metrics.md) before parsing CAModel logs, reporting speedups, or plotting progress.
- Read [references/bottlenecks.md](references/bottlenecks.md) before choosing a hypothesis.
- Read [references/patterns.md](references/patterns.md) when selecting optimization candidates.

## Provided Scripts

- `scripts/parse_camodel_vf.py`: extracts first VF start, last VF end, total VF latency, per-VF execute time, and VF instruction counts.
- `scripts/append_round_log.py`: appends a structured round entry to a perf log.
- `scripts/plot_progress.py`: plots per-round result, best-so-far, and optional benchmark lines from a perf log.

Use the scripts when possible instead of rewriting ad hoc parsers. If local log formats differ, patch scripts narrowly and keep the metric definition unchanged.

## Non-Negotiable Rules

- Do not weaken correctness thresholds, workload size, build flags, or benchmark logic to create a speedup.
- Do not report performance for build-failed or correctness-failed rounds.
- Do not remove `mem_bar`, `wait`, or `set_flag` unless the ordering proof is explicit and correctness is revalidated.
- Do not treat VfSimulator output as final performance.
- Do not mix unrelated changes in a round.
