# VfSimulator C++ PTOAS Integration

This branch contains the native C++ VfSimulator implementation and the MLIR
adapters used by PTOAS. The Python simulator and Python development tools are
not included.

## Contents

```text
api/native/                         CanonicalVfInfo and legacy C++ APIs
native/                             Simulator core and MLIR IR planners
configs/                            ISA and microarchitecture parameters
tests/fixtures/canonical_vf_info/   Native C++ test fixtures
```

The runtime parameter database reads `configs/*.json` from the source tree.
These files are required when the simulator runs.

## PTOAS Integration

PTOAS adds `native/` as a CMake subdirectory and enables the MLIR planner:

```cmake
set(VFSIM_ENABLE_MLIR_PLANNER ON)
add_subdirectory(3rdparty/VfSimulator/native)
```

The embedded build provides:

```text
vfsim::native_core
vfsim::native_legacy
vfsim::ir_planner
```

The planner exposes two generic-MLIR entry points:

```cpp
vfsim::planTileFusionIR(...);  // Legacy TileOp path
vfsim::planVmiUnrollIR(...);   // VMI low-level path
```

## Standalone Build

```bash
cmake -S native -B build -DVFSIM_BUILD_TESTS=ON
cmake --build build -j
ctest --test-dir build --output-on-failure
```

Building the MLIR planner standalone additionally requires an MLIR CMake
package and `-DVFSIM_ENABLE_MLIR_PLANNER=ON`.
