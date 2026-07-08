# Native VF Simulator Core

This directory is the planned C++ home for the VF simulator core.

Current policy:

- Keep `configs/*.json` as the single source of truth.
- C++ code reads the same JSON files at startup and caches them in memory.
- A small internal JSON parser is preferred over a new third-party dependency
  for the first pass.
- Do not duplicate configuration values into handwritten C++ constants.
- Keep Python mainline behavior as the oracle until the C++ path matches it.

First-step scope:

- `ParamDB`: load `isa.json`, `uarch.json`, `forwarding.json`,
  `InitiationInterval.json`
- `ISATraits`: classify load/store/compute behavior from ISA metadata
- `ProgramAnalysis`: bound/unroll resolution and vreg capacity warnings
- `ProgramFlatten`: recursive flattening of loop/inst program trees
- `IFU`: dynamic unrolling and top-block metadata emission
- `IDU`: dispatch gating, VLOOP visibility, and credit accounting
- `OOO`: rename / ready / execute / retire mainline
- `SimulatorRunner`: main loop and output logs
- shared config structs: keep the schema explicit and portable

Later steps:

- `ProgramAnalysis`
- `ProgramFlatten`
- `IFU`
- `IDU`
- `OOO`
- `SimulatorRunner`
