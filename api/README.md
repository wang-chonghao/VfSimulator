# API Layer

This directory contains the simulator input API layer.

Current goal:
- support reading VF structure from `CCE` source files
- keep the existing JSON-trace input path as a fallback

Recommended responsibility split:
- `cce_adapter.py`: find/select `__VEC_SCOPE__` kernels and parse supported CCE
  vector code into `VFInfo`
- `simulator_costmodel.py`: run `VFInfo` objects through the current core
  simulator and return predicted cycles
- `json_*`: load legacy JSON traces into the same internal form
- `vf_lowering.py`: lower public `VFInfo` objects into the current core payload
- shared helpers: normalize the parsed result into the shape expected by `main.py`

Planned interface direction:
- one entry for `CCE` input
- one entry for legacy `JSON` input
- both return the same normalized payload:
  - `dtype`
  - `params`
  - `program`
  - optional `uarch`

This folder is intentionally lightweight for now. We are creating the interface
boundary first, then wiring concrete parsers into it step by step.
