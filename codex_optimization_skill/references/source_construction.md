# Baseline and Source Construction

Use this reference when the task does not provide a working baseline CCE/DSL kernel, or when an optimization requires unfamiliar CCE intrinsic syntax.

## Baseline First, Optimization Second

When no baseline is provided, build a simple correctness-first baseline before any performance tuning.

Baseline requirements:

- implements the exact requested workload and kernel signature
- uses valid CCE/DSL syntax for the target CANN/toolchain version
- has a golden correctness check
- has a reproducible CAModel build/run command
- records the initial VF timing using the standard CAModel metric
- is preserved as round 0 or the first baseline artifact

Do not optimize while the baseline is still semantically uncertain. A slow correct baseline is better than a fast unvalidated one.

## CCE Intrinsic Lookup Order

For uncertain CCE intrinsic syntax, semantics, masks, modes, or target availability, use this lookup order:

1. Official Ascend CCE intrinsic API documentation:
   `https://www.hiascend.com/document/detail/zh/canncommercial/850/API/cceintrinsicapi/cceapi_0024.html`
2. Local CANN compiler headers, for the active CANN/toolchain version, for example:
   `ascend-toolkit/cann-9.0.0-beta.1/tools/bisheng_compiler/lib/clang/15.0.5/include/__clang_cce_vector_intrinsics.h`
3. Skill-provided ISA reference: `references/isa.md`.
4. If the active task provides additional ISA evidence, use it only as supplementary context.
5. If the instruction is still unclear, ask the user before relying on it.

When local headers and web documentation disagree, prefer the local active toolchain for build availability, and record the discrepancy in the round log.

## Baseline Design Principles

- Prefer straightforward dataflow and explicit UB layout.
- Keep UB regions non-overlapping unless lifetime reuse is proven.
- Add required synchronization conservatively first; optimize barriers only after correctness is established.
- Use hardware-loop-friendly loop forms when practical: simple unsigned loop variables, static bounds, no unnecessary branches inside VF scopes.
- Avoid clever instruction substitutions until the baseline passes correctness.


## Addressing Modes and POST_UPDATE

Some vector load/store intrinsics support post-update addressing forms. Before using them, confirm the exact signature in the active CANN headers or official documentation. Record whether the pointer operand is updated, what unit the stride uses, and whether masks/modes change the overload resolution.

Do not introduce post-update addressing in a baseline unless it is needed for a simple correct implementation. Prefer using it as an optimization round after a conventional-addressing baseline passes correctness.

## Baseline Log Entry

The baseline entry should include:

- source path and kernel name
- workload shape and inputs/outputs
- CANN/build flags
- correctness result
- first VF start, last VF end, total VF cycles
- per-VF execute time and instruction counts when available
- known limitations or conservative choices
