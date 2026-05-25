# Metrics and CAModel Parsing

## Authority Boundary

- **VfSimulator/cost model**: use to understand microarchitecture, instruction classes, dependency/issue behavior, and to form hypotheses.
- **CANN CAModel/CCE trace**: use for final performance conclusions.

Never present VfSimulator timing as final kernel performance unless the user explicitly asks for model-only analysis.

## VF Total Time

For multi-VF traces, use this unified definition:

```text
VF total = last VF end from core0.veccore0.instr_log.dump
         - first VF start from core0.veccore0.instr_popped_log.dump
```

This is different from summing `vf_execute_time` and different from using only one VF's local latency. It captures the total window from first VF issue/pop to final VF completion.

## Trace Files

Typical CAModel files:

- `core0.veccore0.instr_popped_log.dump`: VF start/pop cycles.
- `core0.veccore0.instr_log.dump`: VF completion cycles, `vf_execute_time`, and often `instr_num`.

Use `scripts/parse_camodel_vf.py <run_dir>` when possible.

## Required Performance Fields

Record at least:

- first VF start
- last VF end
- total VF latency
- number of VFs
- per-VF execute times if available
- VF instruction counts if available
- benchmark reference if the task provides one

## Required Correctness Fields

Before using any performance number, record the task-local golden result. When available, include:

- final `PASS` / `FAIL` status
- `mismatches`
- `max_abs_err`
- `max_rel_err`
- the unchanged absolute and relative tolerances used by the runner

Valid performance requires the task's original golden check to pass. If the runner reports both a PASS marker and mismatch/error fields, preserve both in the log so later rounds can diagnose precision drift.

## Speedup and Benchmark Percent

For latency-like metrics, lower is better.

```text
speedup_vs_baseline = baseline_cycles / candidate_cycles
performance_vs_benchmark_percent = benchmark_cycles / candidate_cycles * 100
```

If benchmark is a latency value, values above 100% mean the candidate is faster than benchmark.

## Stability

A single CAModel run may be accepted when traces are deterministic in the repository. If trace timing changes across rebuilds/reruns, record stability as noisy and repeat the candidate enough times to identify a representative result.

## Invalid Metrics

Performance is invalid when:

- build fails
- correctness fails
- trace files are missing or malformed
- build flags differ from the required measurement contract
- workload or thresholds were changed without user approval
