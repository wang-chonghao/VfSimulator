# Bottleneck Classification

Before editing code, classify the current bottleneck. If it is unknown, gather more evidence before modifying source.

## Common Classes

### Data Movement
Evidence:
- repeated GM/UB copies
- repeated UB loads/stores around simple arithmetic
- low compute density inside VF

Candidate directions:
- batch copies
- reuse UB-resident data
- reduce redundant stores/loads
- fuse stages if UB/register pressure permits

### VF Dispatch / Head-Tail Overhead
Evidence:
- many short VFs
- total window dominated by inter-VF launch spacing or drain
- per-VF `instr_num` small

Candidate directions:
- VF fusion
- group independent segments
- reduce unnecessary VF splits

Monitor:
- `instr_num` growth
- first-start/last-end total
- per-VF execute and drain behavior

### Long Instruction / SFU Pressure
Evidence:
- many `vexp`, `vdiv`, `vln`, or target-specific long/SFU ops
- long-op clustering after unroll

Candidate directions:
- invariant hoisting
- replace repeated long op with one setup plus cheaper ops
- interleave independent short ops if topology allows

### Reduction / EXU0-Only Pressure
Evidence:
- many `vcadd`, `vcmax`, `vcmin`, `vdup`, or ISA-marked EXU0-only ops
- serial reduction chains

Candidate directions:
- vector accumulation plus final reduction
- tree reduction
- reduce number of final scalar/broadcast reductions

### Dependency Chain
Evidence:
- loop-carried dependency on same vector register
- consumer waits dominate issue window

Candidate directions:
- multiple independent accumulators
- tree merge
- loop split when it exposes independent work

### Register Pressure
Evidence:
- larger fusion/unroll increases total despite similar or fewer instruction counts
- first VF execute or drain grows sharply
- many live intermediates across barrier or long stage

Candidate directions:
- reduce fusion/unroll factor
- change processing order to release live values sooner
- split stages if sync/movement cost is acceptable

### UB Pressure
Evidence:
- large temporary buffers
- overlapping UB regions causing correctness failures
- fusion requires new intermediate storage

Candidate directions:
- reuse old UB regions after lifetime ends
- reduce fusion factor
- recompute cheap values if storing them is worse

### Barrier / Sync
Evidence:
- store-to-load phases separated by barriers
- many `mem_bar`, `wait_flag`, `pipe_barrier`

Candidate directions:
- prove whether barriers can be moved or merged
- group independent stores before one barrier
- never remove ordering without proof and correctness rerun


### Loop Structure / Hardware Loop
Evidence:
- loops are not in a hardware-loop-friendly form
- loop variables or bounds are not simple/static enough for hardware loop generation
- branches or control flow inside the loop prevent hardware loop use
- several repeated loops introduce avoidable loop head overhead
- nested-loop shape causes repeated scalar index/offset work

Candidate directions:
- normalize loops to simple canonical forms when semantics allow
- remove avoidable branches from hot VF loops
- restructure repeated loops as nested loops, or flatten nested loops into one loop, depending on which reduces loop head overhead and scalar indexing
- split or merge loops to expose hardware-loop-friendly structure
- consider `POST_UPDATE` addressing when scalar offset calculation becomes complex

Monitor:
- correctness of address progression
- scalar instruction overhead and VF start spacing
- whether the transformed loop still maps to efficient hardware-loop behavior
- regressions from extra loop overhead or less favorable scheduling

### Queue / Inflight / Issue Limits
Evidence:
- larger VF or unroll increases `instr_num` and worsens total
- first VF execute time spikes
- reducing instruction count worsens because loop overhead or scheduling changes

Candidate directions:
- reduce VF size/unroll
- search middle fusion factors
- preserve hardware-loop-friendly structure
