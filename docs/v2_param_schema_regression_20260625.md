# V2 Parameter Schema Regression Record

Date: 2026-06-25

## Scope

This records the regression checks for migrating VfSimulator parameter configs
to `schema_version: 2` form-based instruction parameters.

Changed files:

- `core/param_db.py`
- `configs/isa.json`
- `configs/forwarding.json`
- `configs/InitiationInterval.json`

## Configuration Checks

The migrated ISA config uses:

- `schema_version: 2`
- `instructions.<op>.forms.<form>`
- normal same-dtype compute forms: `fp32`, `fp16`
- conversion forms: `f32_to_f16`, `f16_to_f32`, `f32_to_s32`, `s32_to_f32`
- pair tables keyed by `OP.form`, for example `VADDS.fp32` and
  `VCVT_F32_TO_F16.f32_to_f16`

Old-repo-only opcodes imported:

- `VAND`
- `VCGMAX`
- `VCMP_EQ`
- `VCVT_F16_TO_F32`
- `VCVT_F32_TO_F16`
- `VCVT_F32_TO_S32`
- `VCVT_S32_TO_F32`
- `VSEL`
- `VSHLS`
- `VSHRS`

Current-branch load/store opcodes retained:

- `VLDS`
- `VSTS`

## Commands Run

### Main Regression Smoke

The main regression entry from `regression_suite/README.md` was also run:

```bash
python3 tools/run_cost_model_regression.py --tier smoke
```

Result files:

```text
results/regression_suite/latest/current_metrics.json
results/regression_suite/latest/compare_summary.json
```

This run reports `FAIL` against
`regression_suite/cases/baseline_consumer_done.json`, but that baseline is the
older consumer-done baseline.  The current model output aligns with the newer
queue-level model family used by `regression_suite/reports/precision_compare_3modes.md`.

To isolate whether the v2 schema conversion changed model results, the same
smoke suite was run again with the old JSON configs exported from `HEAD`:

```bash
mkdir -p /tmp/vfsim_old_configs
git show HEAD:configs/isa.json > /tmp/vfsim_old_configs/isa.json
git show HEAD:configs/forwarding.json > /tmp/vfsim_old_configs/forwarding.json
git show HEAD:configs/InitiationInterval.json > /tmp/vfsim_old_configs/InitiationInterval.json

ISA_JSON_PATH=/tmp/vfsim_old_configs/isa.json \
FORWARDING_JSON_PATH=/tmp/vfsim_old_configs/forwarding.json \
II_JSON_PATH=/tmp/vfsim_old_configs/InitiationInterval.json \
python3 tools/run_cost_model_regression.py \
  --tier smoke \
  --out-dir results/regression_suite/old_config_schema_check
```

Old-config result files:

```text
results/regression_suite/old_config_schema_check/current_metrics.json
results/regression_suite/old_config_schema_check/compare_summary.json
```

Strict comparison between old-config and v2-config smoke `vf_end` values:

```text
all 20 smoke cases matched exactly
value_diffs: []
```

So the v2 schema/config migration did not introduce a `vf_end` drift in the
main smoke regression suite.

### Comparison Against precision_compare_3modes.md

The v2-config smoke result was compared against the
`queue_level4+vregpass (shq=58 exq=26) VF end` column in
`regression_suite/reports/precision_compare_3modes.md`.

Observed differences for smoke-covered rows:

```text
gelu_poly_i16_u1:          392 -> 391  (-1)
gelu_poly_i64_u1:         1295 -> 1309 (+14)
gelu_i16_u1:               189 -> 188  (-1)
online_update_i64_u1:      430 -> 434  (+4)
probe_src_fanout:          282 -> 282  (+0)
probe_branch_live_range:   218 -> 218  (+0)
probe_store_capture_reuse: 317 -> 317  (+0)
vadds_longchain_i16_1x512: 22138 -> 22153 (+15)
vadds_longchain_i64_8x64:  39755 -> 40027 (+272)
vexp_longchain_i16_1x512:  95477 -> 95492 (+15)
vexp_longchain_i64_8x64:   163947 -> 163963 (+16)
gelu_poly_i96_u2:          1851 -> 1831 (-20)
gelu_poly_i96_u4:          1682 -> 1777 (+95)
gelu_poly_i96_u8:          1684 -> 1706 (+22)
silu_i16_u1:               156 -> 155  (-1)
swiglu_i16_u1:             169 -> 169  (+0)
silu_i64_u1:               406 -> 406  (+0)
silu_i96_u1:               571 -> 573  (+2)
swiglu_i64_u1:             455 -> 463  (+8)
swiglu_i96_u1:             652 -> 655  (+3)
```

These differences also appear with the old configs, so they are not caused by
the v2 schema conversion.

### Import and API Regression

```bash
python3 -B - <<'PY'
from core.param_db import ParamDB
p=ParamDB(base_dir='.')
expected={
 ('fp32','inst_latency'):7,
 ('fp32','ld_to_vadds'):6,
 ('fp32','vadds_to_vsts'):5,
 ('fp32','ii'):1,
 ('fp16','inst_latency'):7,
 ('fp16','ld_to_vadds'):6,
 ('fp16','vadds_to_vsts'):5,
 ('fp16','ii'):1,
}
actual={}
for dtype in ['fp32','fp16']:
    actual[(dtype,'inst_latency')]=p.get_inst('VADDS', dtype)['latency']
    actual[(dtype,'ld_to_vadds')]=p.get_forwarding_cycles('VLDS','VADDS',dtype)
    actual[(dtype,'vadds_to_vsts')]=p.get_forwarding_cycles('VADDS','VSTS',dtype)
    actual[(dtype,'ii')]=p.get_ii('VADDS','VMULS',dtype)
print(actual)
assert actual == expected
assert p.get_inst('VCVT_F32_TO_F16','fp32')['form'] == 'f32_to_f16'
assert p.get_inst('VCVT_S32_TO_F32','int32')['dst_dtypes'] == ['fp32']
print('api regression ok')
PY
```

Observed output:

```text
{('fp32', 'inst_latency'): 7, ('fp32', 'ld_to_vadds'): 6, ('fp32', 'vadds_to_vsts'): 5, ('fp32', 'ii'): 1, ('fp16', 'inst_latency'): 7, ('fp16', 'ld_to_vadds'): 6, ('fp16', 'vadds_to_vsts'): 5, ('fp16', 'ii'): 1}
api regression ok
```

### VADDS64 Formula Regression

```bash
python3 tools/run_vadds64_formula_compare.py
```

Result directory:

```text
results/VADDS64_formula_compare/
```

Key result files:

```text
results/VADDS64_formula_compare/I2/compare_results.json
results/VADDS64_formula_compare/I4/compare_results.json
results/VADDS64_formula_compare/I8/compare_results.json
results/VADDS64_formula_compare/I16/compare_results.json
results/VADDS64_formula_compare/I32/compare_results.json
results/VADDS64_formula_compare/I64/compare_results.json
results/VADDS64_formula_compare/I128/compare_results.json
```

Observed leading cycle values after migration:

```text
I2:   259, 256, 289
I4:   428, 362, 302
I8:   738, 564, 481
I16:  1350, 968, 758
I32:  2574, 1776, 1318
I64:  5022, 3396, 2438
I128: 9918, 6632, 4678
```

These matched the console baseline captured immediately before converting the
configs.

## Limitation

`tools/run_simple_ld_vadds_vst_case.py` was not used as a regression signal.
It failed in this environment because the script internally invokes `python`,
and that executable returned `PermissionError: [Errno 13] Permission denied`.

The checked API values still cover the relevant `VLDS -> VADDS -> VSTS`
parameter path:

```text
VLDS.fp32 -> VADDS.fp32 forwarding = 6
VADDS.fp32 -> VSTS.fp32 forwarding = 5
```
