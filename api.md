# VF Simulator API

This document describes the current public input interface for VF Simulator.

The simulator supports two input paths:

1. JSON trace input through `main.py --trace`.
2. CCE/DSL input through `main.py --cce`, parsed from `__VEC_SCOPE__`.

Both paths are lowered to the same internal trace format and run through the current mainline model:

```text
queue_level4 + vreg live-range normalization + start+4 release
```

## Command Line

Run a JSON trace:

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/demo_json
```

Run a CCE/DSL file:

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/demo_cce
```

Select a named VF kernel when a CCE file has multiple `__VEC_SCOPE__` blocks:

```bash
python main.py --cce path/to/file.dsl --cce-kernel kernel_name --out_dir results/demo_kernel
```

The old `--ooo-model` selector is no longer part of the public mainline CLI. The default model is selected inside `main.py`.

## JSON Trace Format

Minimal example:

```json
{
  "dtype": "fp32",
  "params": {
    "I": 96,
    "U": 1
  },
  "program": [
    {
      "type": "loop",
      "iters": "I",
      "unroll": "U",
      "body": [
        { "type": "inst", "op": "VLD", "dst": ["V0"], "src": ["memA"] },
        { "type": "inst", "op": "VADDS", "dst": ["V1"], "src": ["V0"] },
        { "type": "inst", "op": "VST", "dst": ["memB"], "src": ["V1"] }
      ]
    }
  ]
}
```

Fields:

- `dtype`: instruction data type, commonly `fp32` or `fp16`.
- `params`: symbolic parameters referenced by `iters` and `unroll`.
- `program`: top-level VF program list.
- `type=loop`: loop block with `iters`, `unroll`, and `body`.
- `type=inst`: instruction with `op`, `dst`, and `src`.

Nested loops are supported. The analyzer infers top-level loop bounds and dispatch structure from the program tree.

## CCE/DSL Input

The CCE adapter parses vector code inside `__VEC_SCOPE__` and converts supported instructions into `VFInfo`, then into the simulator trace format.

Example:

```bash
python main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu_poly_cce
```

Programmatic use:

```python
from api.cce_adapter import parse_cce_vf_info
from api.simulator_costmodel import predict_vf_cycles

vf_info = parse_cce_vf_info("cce_code/GeLU_poly.dsl")
cycles = predict_vf_cycles(vf_info)
print(cycles)
```

Useful API files:

- `api/vf_costmodel.py`: `VFInfo`, `InstInfo`, `MemInfo` data classes.
- `api/cce_adapter.py`: CCE/DSL parser.
- `api/vf_lowering.py`: lowers `VFInfo` to simulator trace.
- `api/input_api.py`: shared CLI input loader.
- `api/simulator_costmodel.py`: programmatic simulator wrapper.

## Memory And Register Operands

The newer API can represent operand location through `MemInfo.location`, with expected values:

- `register`
- `UB`

The current lowering path still keeps compatibility with historical JSON traces that use names such as `V0`, `V1`, `memA`, and `mem_inter_*`.

Recommended naming for JSON traces:

- vector registers: `V0`, `V1`, `V2`, ...
- input/output UB memory: `memA`, `memB`, `memOut`, ...
- intermediate cross-block memory: `mem_inter_*`

## Explicit Memory Barriers

CCE parsing can represent explicit memory barrier semantics when they are visible in the source. For older JSON traces, cross-block dependency is usually modeled with:

```text
VST mem_inter_* in one block
VLD mem_inter_* in a later block
```

With `mem_bar_mode = strong`, this creates the intended ordering between blocks.

## Theoretical-Limit Options

Current public theoretical-limit flags:

```bash
--theoretical-limit-vloop-only
--theoretical-limit-vloop-only-legacy-forwarding-direct-issue
```

The older generic `--theoretical-limit` flag is not the active interface.

## Output

The canonical model timing is printed as:

```text
VF end cycle (with drain) = N
```

Common output files:

- `start_by_cycle.json`
- `done_by_cycle.json`
- `idu_to_ooo.json`
- `vloop_trace.json`
- `sim_history.json`

