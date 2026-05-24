#!/usr/bin/env python3
"""Generate minimal forwarding DSL cases from op set (fp32/fp16)."""

from __future__ import annotations

from pathlib import Path

HEADER = """#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif
"""

# op -> (api, kind)
# kind: unary | imm | binary
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

DTYPE_CFG = {
    "fp32": {
        "elem_type": "float",
        "vec_type": "vector_f32",
        "lane": 64,
        "bytes_per_elem": 4,
        "pat_decl": "vector_bool pat_all_b32 = pset_b32(PAT_ALL);",
        "pat_name": "pat_all_b32",
        "store_norm": "NORM_B32",
    },
    "fp16": {
        "elem_type": "half",
        "vec_type": "vector_f16",
        "lane": 128,
        "bytes_per_elem": 2,
        "pat_decl": "vector_bool pat_all_b16 = pset_b16(PAT_ALL);",
        "pat_name": "pat_all_b16",
        "store_norm": "NORM_B16",
    },
}


def op_line(op_name: str, dst: str, src: str, pat_name: str) -> str:
    api, kind = OP_SPEC[op_name]
    if kind == "unary":
        return f"        {api}({dst}, {src}, {pat_name});"
    if kind == "imm":
        imm = IMM_VAL[op_name]
        return f"        {api}({dst}, {src}, {imm}, {pat_name});"
    if kind == "binary":
        return f"        {api}({dst}, {src}, {src}, {pat_name});"
    raise ValueError(op_name)


def render_case(prod: str, cons: str, dtype: str) -> str:
    cfg = DTYPE_CFG[dtype]
    elem_type = cfg["elem_type"]
    vec_type = cfg["vec_type"]
    lane = int(cfg["lane"])
    bytes_per_batch = lane * int(cfg["bytes_per_elem"])
    pat_decl = cfg["pat_decl"]
    pat_name = cfg["pat_name"]
    store_norm = cfg["store_norm"]

    stem = f"fwd_{prod.lower()}_to_{cons.lower()}"
    body_prod = op_line(prod, "vec_p", "vec_a", pat_name)
    body_cons = op_line(cons, "vec_out", "vec_p", pat_name)

    return (
        HEADER
        + f"""
__attribute__((always_inline)) inline [aicore] void {stem}_simd_ub(
    __ubuf__ {elem_type} *memA,
    __ubuf__ {elem_type} *memOut,
    int repeat_times) {{

    __VEC_SCOPE__ {{
        {pat_decl}
        {vec_type} vec_a;
        {vec_type} vec_p;
        {vec_type} vec_out;

        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {{
            vlds(vec_a, memA, {lane} * i, NORM);
{body_prod}
{body_cons}
            vsts(vec_out, memOut, {lane} * i, {store_norm}, {pat_name});
        }}
    }}
}}

extern "C" __global__ __aicore__ void foo_add(
    __gm__ {elem_type}* __restrict__ input0,
    __gm__ {elem_type}* __restrict__ input1,
    __gm__ {elem_type}* __restrict__ output0)
{{
    __ubuf__ {elem_type} *ub_data_a = (__ubuf__ {elem_type}*)get_imm(0x0);
    __ubuf__ {elem_type} *ub_data_out = (__ubuf__ {elem_type}*)get_imm(0x2000);
    int repeat_times = 1;
    copy_gm_to_ubuf_align_v2((__ubuf__ {elem_type}*)ub_data_a, (__gm__ {elem_type}*)input0,
                            0, 1, repeat_times * {bytes_per_batch}, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    {stem}_simd_ub(
        ub_data_a,
        ub_data_out,
        repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ {elem_type}*)output0,
        (__ubuf__ {elem_type}*)ub_data_out,
        0, 1, repeat_times * {bytes_per_batch}, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}}
"""
    )


def main() -> None:
    out_dir = Path(__file__).resolve().parent / "cases"
    out_dir.mkdir(parents=True, exist_ok=True)

    n = 0
    ops = sorted(OP_SPEC.keys())
    for dtype in ("fp32", "fp16"):
        dtype_dir = out_dir / dtype
        dtype_dir.mkdir(parents=True, exist_ok=True)
        for prod in ops:
            for cons in ops:
                stem = f"fwd_{prod.lower()}_to_{cons.lower()}"
                (dtype_dir / f"{stem}.dsl").write_text(render_case(prod, cons, dtype), encoding="utf-8")
                n += 1
    print(f"Generated {n} forwarding DSL files in {out_dir}")


if __name__ == "__main__":
    main()





