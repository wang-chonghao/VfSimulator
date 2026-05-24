# Cost Model and CCE End-to-End Workflow

This document describes the current end-to-end flow from CCE source to model prediction and model-vs-CCE comparison.

## 1. Model Path From CCE Source

The mainline model can read CCE/DSL directly:

```bash
python main.py ^
  --cce cce_code/GeLU_poly.dsl ^
  --out_dir results/tmp_model/gelu_poly_cce
```

Internally this path is:

```text
CCE/DSL source
  -> extract __VEC_SCOPE__ kernel
  -> api.cce_adapter.parse_cce_vf_info
  -> VFInfo
  -> api.vf_lowering.VFInfoLowerer
  -> simulator payload
  -> core simulator
```

If the file contains multiple VF kernels:

```bash
python main.py ^
  --cce cce_code/example.dsl ^
  --cce-kernel selected_kernel ^
  --out_dir results/tmp_model/selected_kernel
```

Useful API calls:

```python
from api.cce_adapter import list_cce_vf_kernels, parse_cce_vf_info
from api.simulator_costmodel import CoreVfCostModel, predict_cce_file_cycles

kernels = list_cce_vf_kernels("cce_code/example.dsl")
vf_info = parse_cce_vf_info("cce_code/example.dsl", kernel_name=kernels[0])
cycles = CoreVfCostModel(out_dir="results/api_costmodel/example").predict_vf_cycles(vf_info)
```

## 2. Direct VFInfo Path

You can bypass JSON/CCE and construct `VFInfo` directly:

```python
from api.vf_costmodel import MemInfo, VFInfo, VFInst, VFLoop
from api.simulator_costmodel import CoreVfCostModel

vf = VFInfo(
    context=[
        VFLoop(
            count=16,
            unroll=1,
            body=[
                VFInst("VLD", src=[MemInfo("x", "UB")], dst=[MemInfo("a", "Register")]),
                VFInst("VADDS", src=[MemInfo("a", "Register")], dst=[MemInfo("b", "Register")]),
                VFInst("VST", src=[MemInfo("b", "Register")], dst=[MemInfo("y", "UB")]),
            ],
        )
    ]
)

cycles = CoreVfCostModel(out_dir="results/api_costmodel/direct").predict_vf_cycles(vf)
```

This is useful for unit tests and micro-architecture experiments.

## 3. Running CCE/Camodel

CCE native simulation is still a separate environment-dependent path. The common helper is:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_cce_round.ps1 `
  -DslPath cce_code\GeLU_poly.dsl `
  -RoundTag gelu_poly_probe `
  -TotalElems 6144
```

If WSL distribution naming or user context causes launch failures, pass the distro explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_cce_round.ps1 `
  -DslPath cce_code\GeLU_poly.dsl `
  -RoundTag gelu_poly_probe `
  -TotalElems 6144 `
  -WslDistro "-Ubuntu"
```

Known caveat: Codex may run outside the normal Windows user context, so WSL commands that work manually can fail from the agent process. In that case, run the CCE command manually and let the model workflow consume the generated dumps.

Common CCE/camodel dump files:

- `core0.veccore0.instr_popped_log.dump`
- `core0.veccore0.instr_log.dump`
- `core0.veccore0.rvec.EXU.dump`
- `core0.veccore0.rvec.IDU.dump`

CCE timing is usually compared as:

```text
CCE VF total = VF_end - VF_start
```

Model timing is:

```text
VF end cycle (with drain)
```

## 4. IPC Comparison

For CCE EXU dump versus model start log:

```bash
python tools/plot_cce_model_ipc_compare.py ^
  --cce-exu-dump <core0.veccore0.rvec.EXU.dump> ^
  --model-start-log <model_out_dir>/start_by_cycle.json ^
  --window 25 ^
  --align-start ^
  --out-png results/ipc_compare/cce_vs_model.png ^
  --out-csv results/ipc_compare/cce_vs_model.csv
```

The IPC comparison should usually count compute instructions only. Exclude VLD/VST, SEND, and PSET-like instructions when the goal is compute-issue behavior.

## 5. JSON Trace Fallback

JSON remains supported for regression tests and hand-written micro cases:

```json
{
  "dtype": "fp32",
  "params": { "I": 16, "U": 1 },
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

Run it with:

```bash
python main.py --trace VFtest/GeLU_poly.json --out_dir results/tmp_model/gelu_poly_json
```

## 6. Accuracy Comparison Workflow

For a regression suite:

1. Make sure each case has a CCE/camodel reference time in the manifest or result table.
2. Run the model on the same logical VF structure, loop count, unroll, and precision.
3. Compare `model_vf_end` with CCE/camodel VF total.
4. Inspect outliers using `start_by_cycle.json`, `idu_to_ooo.json`, and CCE IDU/EXU dumps.

Be careful when comparing CCE-generated dumps with model inputs: CCE may split or transform loops even when source code visually contains a single loop. Always verify actual `vloop_pc` segments or equivalent dump evidence before claiming one-to-one loop structure.
