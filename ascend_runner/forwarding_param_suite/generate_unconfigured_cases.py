#!/usr/bin/env python3
"""Generate forwarding DSL cases for pairs missing in configs/forwarding.json."""

from __future__ import annotations

import json
from pathlib import Path

HEADER = """#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif
"""

OP_SPEC = {
    "VADDS": ("vadds", "imm"),
    "VMULS": ("vmuls", "imm"),
    "VADD": ("vadd", "binary"),
    "VEXP": ("vexp", "unary"),
    "VCMAX": ("vcmax", "unary"),
    "VCMIN": ("vcmin", "unary"),
    "VCADD": ("vcadd", "unary"),
    "VDIV": ("vdiv", "binary"),
    "VABS": ("vabs", "unary"),
    "VSUB": ("vsub", "binary"),
    "VMUL": ("vmul", "binary"),
    "VMAXS": ("vmaxs", "imm"),
    "VMINS": ("vmins", "imm"),
    "VMAX": ("vmax", "binary"),
    "VMIN": ("vmin", "binary"),
}

IMM_VAL = {
    "VADDS": "0.125f",
    "VMULS": "0.5f",
    "VMAXS": "0.75f",
    "VMINS": "0.75f",
}


def op_line(op_name: str, dst: str, src: str) -> str:
    api, kind = OP_SPEC[op_name]
    if kind == "unary":
        return f"        {api}({dst}, {src}, pat_all_b32);"
    if kind == "imm":
        return f"        {api}({dst}, {src}, {IMM_VAL[op_name]}, pat_all_b32);"
    if kind == "binary":
        return f"        {api}({dst}, {src}, {src}, pat_all_b32);"
    raise ValueError(op_name)


def render_case(prod: str, cons: str, stem: str) -> str:
    return (
        HEADER
        + f"""
__attribute__((always_inline)) inline [aicore] void {stem}_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memOut,
    int repeat_times) {{

    __VEC_SCOPE__ {{
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_a;
        vector_f32 vec_p;
        vector_f32 vec_out;

        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {{
            vlds(vec_a, memA, 64 * i, NORM);
{op_line(prod, 'vec_p', 'vec_a')}
{op_line(cons, 'vec_out', 'vec_p')}
            vsts(vec_out, memOut, 64 * i, NORM_B32, pat_all_b32);
        }}
    }}
}}

extern "C" __global__ __aicore__ void foo_add(
    __gm__ float* __restrict__ input0,
    __gm__ float* __restrict__ input1,
    __gm__ float* __restrict__ output0)
{{
    __ubuf__ float *ub_data_a = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_out = (__ubuf__ float*)get_imm(0x2000);
    int repeat_times = 1;
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_a, (__gm__ float*)input0,
                            0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    {stem}_simd_ub(
        ub_data_a,
        ub_data_out,
        repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)output0,
        (__ubuf__ float*)ub_data_out,
        0, 1, repeat_times * 64 * 4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}}
"""
    )


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    cfg = json.loads((repo / "configs" / "forwarding.json").read_text(encoding="utf-8"))["forwarding"]["fp32"]
    configured = {(p, c) for p, cs in cfg.items() for c in cs.keys()}
    ops = sorted(OP_SPEC.keys())

    out_dir = Path(__file__).resolve().parent / "cases"
    out_dir.mkdir(parents=True, exist_ok=True)

    stems: list[str] = []
    for p in ops:
        for c in ops:
            if (p, c) in configured:
                continue
            stem = f"fwdx_{p.lower()}_to_{c.lower()}"
            (out_dir / f"{stem}.dsl").write_text(render_case(p, c, stem), encoding="utf-8")
            stems.append(stem)

    list_path = repo / "tools" / "fwd_unconfigured_cases.txt"
    list_path.write_text("\n".join(stems) + "\n", encoding="utf-8")
    print(f"Generated {len(stems)} unconfigured forwarding cases")
    print(f"Case list: {list_path}")


if __name__ == "__main__":
    main()





