# CCE VF Cost Model API Design

This document defines the planned API boundary for using VFSim as a VF cost
model from CCE code.

The goal is to support two equivalent input paths:

1. Parse a `__VEC_SCOPE__` CCE kernel into a `VFInfo` object.
2. Construct a `VFInfo` object directly in Python.

Both paths should call the same cost model entry:

```python
cycles = model.predict_vf_cycles(vf_info)
```

The backend simulator under `core/` should not parse CCE source directly. The
API layer owns source-facing input and lowers it into the common `VFInfo`
representation.

## Architecture

```text
CCE __VEC_SCOPE__ kernel
        |
        v
CCE parser / adapter
        |
        v
VFInfo
        |
        v
VfCostModel.predict_vf_cycles(VFInfo)
        |
        v
VFInfo -> simulator program adapter
        |
        v
core/ simulator backend
        |
        v
predicted cycles
```

Direct Python usage bypasses the CCE parser:

```text
Python code constructs VFInfo
        |
        v
VfCostModel.predict_vf_cycles(VFInfo)
        |
        v
core/ simulator backend
        |
        v
predicted cycles
```

## Public Data Model

The public API data model lives in `api/vf_costmodel.py`.

### `VFInfo`

Top-level VF program container.

Fields:
- `context`: ordered list of `VFLoop`, `VFInst`, and `Membar` nodes.

Meaning:
- Represents the body of one VF region/kernel after extracting the vector
  scope.
- The order of nodes is program order.

### `VFLoop`

Structured loop node.

Fields:
- `count`: loop trip count.
- `unroll`: loop unroll factor.
- `body`: ordered list of nested `VFLoop`, `VFInst`, and `Membar` nodes.

Meaning:
- Preserves loop structure instead of requiring the API user to manually
  flatten the program.
- The backend adapter later lowers this into the simulator's existing loop
  representation.

### `VFInst`

Vector instruction node.

Fields:
- `name`: instruction name, such as `VADD`, `VMUL`, `VEXP`, `VLD`, or `VST`.
- `src`: source operands.
- `dst`: destination operands.

Meaning:
- Describes one VF instruction in program order.
- The API layer will map `name` to the simulator instruction `op`.

### `MemInfo`

Operand descriptor.

Fields:
- `name`: operand identifier.
- `location`: one of `"Register"` or `"UB"`.

Meaning:
- `"Register"` represents a vector register value.
- `"UB"` represents a UB/memory-side value.
- `name` is a public API symbol and does not need to start with `v` or `mem`.
  The API adapter maps it to the simulator's current internal operand naming.

Current Python type:

```python
Literal["Register", "UB"]
```

### `Membar`

Memory/order barrier node.

Fields:
- `type`: barrier type. The initial default is `"VST_VLD"`.

Meaning:
- Represents an ordering edge that cannot be expressed as a normal VF
  instruction.
- The first supported use case is store-to-load ordering.

## CCE Input Contract

The CCE adapter should extract exactly one VF region from a `__VEC_SCOPE__`
kernel and produce a `VFInfo`.

Initial expectations:
- Only vector-scope code is modeled.
- Scalar host code and non-VF control logic are ignored unless needed to infer
  loop bounds.
- Loop structure is preserved as `VFLoop`.
- VF operations are converted into `VFInst`.
- Explicit or inferred ordering constraints are converted into `Membar`.

The first implementation can be conservative. It is better to reject an
unsupported CCE pattern clearly than to silently produce an incorrect `VFInfo`.

## Mapping Rules

### Loops

CCE loop:

```cpp
for (int i = 0; i < I; ++i) {
    ...
}
```

Maps to:

```python
VFLoop(count=I, unroll=1, body=[...])
```

If a pragma or known CCE annotation provides unroll information, it maps to
`VFLoop.unroll`.

### VF instructions

CCE vector operation:

```cpp
VADD(dst, src0, src1);
```

Maps to:

```python
VFInst(
    name="VADD",
    src=[...],
    dst=[...],
)
```

### Registers and UB values

Vector register operand. The public name can be arbitrary:

```python
MemInfo(name="acc.tmp", location="Register")
```

UB or memory-side operand. The public name can also be arbitrary:

```python
MemInfo(name="input_tensor", location="UB")
```

The current adapter lowers these into internal names:

```text
Register symbols -> V0, V1, ...
UB symbols       -> mem0, mem1, ...
```

This keeps the public API free from backend naming restrictions while preserving
compatibility with the current core model.

### Memory barriers

An ordering constraint such as VST-before-later-VLD maps to:

```python
Membar(type="VST_VLD")
```

## Cost Model Entry

The public cost-model interface is:

```python
class VfCostModel(ABC):
    @abstractmethod
    def predict_vf_cycles(self, vf_info: VFInfo) -> int:
        pass
```

Expected implementation behavior:

1. Validate the `VFInfo`.
2. Lower `VFInfo` into the simulator program format currently used by `core/`.
3. Run the mainline simulator backend.
4. Return predicted VF cycles as an integer.

The implementation should not require callers to know about:
- `core.flatten.Flattener`
- `core.ifu.IFUUnroll`
- `core.idu.IDU`
- `core.ooo_consumer_done.OoOCoreConsumerDone`
- JSON trace internals

Those details stay behind the API boundary.

## Legacy JSON Fallback

The existing JSON trace path remains available for compatibility and debugging.

Current adapter:

```python
InputAPI.load_json_trace(path)
```

Long-term shape:
- JSON trace input and CCE input should both produce a normalized payload that
  can enter the same backend flow.
- JSON should be treated as a lower-level fallback format, not the primary user
  API.

## Example: Direct Python API

```python
from api.vf_costmodel import MemInfo, VFInfo, VFInst, VFLoop

vf_info = VFInfo(
    context=[
        VFLoop(
            count=16,
            unroll=2,
            body=[
                VFInst(
                    name="VLD",
                    src=[MemInfo("x", "UB")],
                    dst=[MemInfo("v0", "Register")],
                ),
                VFInst(
                    name="VADD",
                    src=[
                        MemInfo("v0", "Register"),
                        MemInfo("v1", "Register"),
                    ],
                    dst=[MemInfo("v2", "Register")],
                ),
                VFInst(
                    name="VST",
                    src=[MemInfo("v2", "Register")],
                    dst=[MemInfo("y", "UB")],
                ),
            ],
        )
    ]
)

cycles = model.predict_vf_cycles(vf_info)
```

## Example: Planned CCE API

```python
from api.cce_adapter import list_cce_vf_kernels, parse_cce_vf_info
from api.simulator_costmodel import CoreVfCostModel

print(list_cce_vf_kernels("cce_code/GeLU_poly.dsl"))

vf_info = parse_cce_vf_info("cce_code/GeLU_poly.dsl")
model = CoreVfCostModel()
cycles = model.predict_vf_cycles(vf_info)
```

If a file contains multiple `__VEC_SCOPE__` kernels, select one explicitly:

```python
vf_info = parse_cce_vf_info(
    "cce_code/multi_kernel.dsl",
    kernel_name="gelu_simd_ub",
)
```

The first adapter implementation supports the common DSL subset used by the
current examples:
- `__VEC_SCOPE__ { ... }`
- `#pragma unroll(N)`
- braced `for` loops with constant or inferable loop bounds
- vector register declarations such as `vector_f32 vec_0;`
- `vlds`/`vsts` load-store operations
- normal VF calls such as `vadd`, `vadds`, `vmul`, `vmuls`, `vexp`, `vdiv`

Unsupported CCE constructs should fail loudly instead of silently producing a
wrong `VFInfo`.

## Implementation Roadmap

1. Finalize the public dataclasses in `api/vf_costmodel.py`.
2. Add a `VFInfo` validator.
3. Add `VFInfo -> core program` lowering.
4. Implement a concrete `VfCostModel` backend wrapper.
5. Add CCE parsing for a small supported subset.
6. Expand CCE support case by case.

## Current Compatibility Notes

- The public API supports explicit `Membar` nodes.
- `VFInfoLowerer` preserves `Membar` as a `"membar"` node in the lowered program.
- The current backend can carry this node through flattening, but full timing
  semantics for explicit membar are still a follow-up implementation item.
- Until that timing support lands, existing memory-order behavior still comes
  mainly from the backend's current memory model and `mem_bar_mode`.

## Open Questions

- Should `VFInst.name` be renamed to `op` to match the simulator internals?
- Should `MemInfo` be renamed to `OperandInfo`, since it can represent both
  registers and UB operands?
- Which CCE memory/barrier constructs beyond `VST_VLD` should initially map to
  `Membar`?
- Should unsupported CCE constructs raise errors or warnings?
