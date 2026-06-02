# OOO Models

This document summarizes the current OoO register-lifetime models, their defaults,
and the most important simulator/optimizer knobs.

## Default

The current default OoO model is:

- `consumer-done`

This default is used by:

- [`main.py`](/d:/VfSimulator/main.py)
- [`optimizer/split_only_optimizer.py`](/d:/VfSimulator/optimizer/split_only_optimizer.py)
- [`optimizer/generic_heuristic_split_optimizer.py`](/d:/VfSimulator/optimizer/generic_heuristic_split_optimizer.py)
- [`core/ooo_factory.py`](/d:/VfSimulator/core/ooo_factory.py)

## Available `--ooo-model` Options

### `consumer-done`

Current recommended/default model.

Rule of thumb:

- when an architectural vreg is overwritten by a younger write, the old preg is
  sealed and will not gain new younger consumers
- that old preg is released only after:
  - it is no longer the current RAT mapping
  - all already-bound consumers have finished execution (`done`)
  - the producer itself has finished execution

Characteristics:

- does not require future/global oracle information
- more aggressive than `default`
- more realistic than `last-use`
- currently the preferred mainline model

Implementation:

- [`core/ooo_mainline.py`](/d:/VfSimulator/core/ooo_mainline.py)

### `default`

Original conservative model.

Rule of thumb:

- old preg lifetime is tied to in-order retirement behavior
- more CPU-like / textbook conservative rename-free behavior

Characteristics:

- most conservative of the current practical models
- tends to increase perceived register pressure
- tends to prefer more loop cuts during optimization

Implementation:

- [`core/ooo.py`](/d:/VfSimulator/core/ooo.py)

### `last-use`

Aggressive oracle-style model.

Rule of thumb:

- uses globally precomputed last-use information
- can release preg lifetime based on future knowledge of the dynamic trace

Characteristics:

- not hardware-realistic as a strict implementation model
- useful as an aggressive sensitivity / upper-bound style reference
- tends to reduce perceived register pressure the most

Implementation:

- [`core/ooo_last_use.py`](/d:/VfSimulator/core/ooo_last_use.py)
- last-use annotation:
  [`core/dynamic_trace.py`](/d:/VfSimulator/core/dynamic_trace.py)

### `npu-hybrid`

Experimental mixed model.

Characteristics:

- retained as an optional experiment path
- not the current recommended default

Implementation:

- [`core/ooo_npu_hybrid.py`](/d:/VfSimulator/core/ooo_npu_hybrid.py)

## CLI Defaults

### `main.py`

Current important defaults:

- `--ooo-model consumer-done`
- `--theoretical-limit` disabled by default
- `--out_dir results`

Example:

```bash
python main.py --trace VFtest/GeLU_poly.json
```

This now uses `consumer-done` unless `--ooo-model` is explicitly passed.

### Optimizers

Current optimizer defaults:

- `--ooo-model consumer-done`
- `--cut-penalty off`
- `--cut-penalty-scale 1.0`

Examples:

```bash
python optimizer/generic_heuristic_split_optimizer.py VFtest/GeLU_poly.json --trip-count 64 --output results/out.json
```

```bash
python optimizer/generic_heuristic_split_optimizer.py VFtest/GeLU_poly.json --trip-count 64 --ooo-model default --cut-penalty on --cut-penalty-scale 1.0 --output results/out.json
```

## Current uArch Defaults

From [`configs/uarch.json`](/d:/VfSimulator/configs/uarch.json):

- `issue_ports = 2`
- `load_ports = 2`
- `store_ports = 1`
- `IDU_window_width = 6`
- `IDU_issue_width = 5`
- `OoO_window_width = 2600000`
- `LDQ_width = 2400000`
- `vreg_num = 68`
- `mem_bar_mode = strong`

## Current ISA Defaults

From [`configs/isa.json`](/d:/VfSimulator/configs/isa.json):

- `vf_startup_cost = 23`
- `vf_drain_cost = 12`

## Current Recommendation

For normal simulation and optimization work:

- use `consumer-done`
- keep `cut_penalty=off` unless you explicitly want more regular/less fragmented partition shapes

For comparison/debug:

- use `default` for conservative baseline behavior
- use `last-use` as an aggressive oracle-style reference
