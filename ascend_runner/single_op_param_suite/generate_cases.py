#!/usr/bin/env python3
"""Generate minimal single-op DSL cases: VLD -> OP -> VST (fp32/fp16)."""

from __future__ import annotations

from pathlib import Path


OPS = [
    {"name": "vadds", "api": "vadds", "kind": "imm", "imm": "0.125f"},
    {"name": "vexp", "api": "vexp", "kind": "unary"},
    {"name": "vcmax", "api": "vcmax", "kind": "unary"},
    {"name": "vcmin", "api": "vcmin", "kind": "unary"},
    {"name": "vcadd", "api": "vcadd", "kind": "unary"},
    {"name": "vadd", "api": "vadd", "kind": "binary"},
    {"name": "vmuls", "api": "vmuls", "kind": "imm", "imm": "0.5f"},
    {"name": "vdiv", "api": "vdiv", "kind": "binary"},
    {"name": "vabs", "api": "vabs", "kind": "unary"},
    {"name": "vsub", "api": "vsub", "kind": "binary"},
    {"name": "vmul", "api": "vmul", "kind": "binary"},
    {"name": "vmaxs", "api": "vmaxs", "kind": "imm", "imm": "0.75f"},
    {"name": "vmins", "api": "vmins", "kind": "imm", "imm": "0.75f"},
    {"name": "vmax", "api": "vmax", "kind": "binary"},
    {"name": "vmin", "api": "vmin", "kind": "binary"},
]


HEADER = """#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif
"""


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


def vec_body(kind: str, api: str, imm: str | None, lane: int, store_norm: str, pat_name: str) -> str:
    if kind == "unary":
        return f"""        vlds(vec_a, memA, {lane} * i, NORM);
        {api}(vec_out, vec_a, {pat_name});
        vsts(vec_out, memOut, {lane} * i, {store_norm}, {pat_name});
"""
    if kind == "imm":
        return f"""        vlds(vec_a, memA, {lane} * i, NORM);
        {api}(vec_out, vec_a, {imm}, {pat_name});
        vsts(vec_out, memOut, {lane} * i, {store_norm}, {pat_name});
"""
    if kind == "binary":
        return f"""        vlds(vec_a, memA, {lane} * i, NORM);
        {api}(vec_out, vec_a, vec_a, {pat_name});
        vsts(vec_out, memOut, {lane} * i, {store_norm}, {pat_name});
"""
    raise ValueError(f"unsupported kind: {kind}")


def kernel_signature(elem_type: str) -> str:
    return f"""extern "C" __global__ __aicore__ void foo_add(
    __gm__ {elem_type}* __restrict__ input0,
    __gm__ {elem_type}* __restrict__ input1,
    __gm__ {elem_type}* __restrict__ output0)
"""


def kernel_copy(kind: str, elem_type: str, bytes_per_batch: int) -> str:
    if kind == "binary":
        return f"""    copy_gm_to_ubuf_align_v2((__ubuf__ {elem_type}*)ub_data_a, (__gm__ {elem_type}*)input0,
                            0, 1, repeat_times * {bytes_per_batch}, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ {elem_type}*)ub_data_b, (__gm__ {elem_type}*)input1,
                            0, 1, repeat_times * {bytes_per_batch}, 0, 0, 0, 0, 0, 0);
"""
    return f"""    copy_gm_to_ubuf_align_v2((__ubuf__ {elem_type}*)ub_data_a, (__gm__ {elem_type}*)input0,
                            0, 1, repeat_times * {bytes_per_batch}, 0, 0, 0, 0, 0, 0);
"""


def render_case(op: dict[str, str], dtype: str) -> str:
    name = op["name"]
    api = op["api"]
    kind = op["kind"]
    imm = op.get("imm")
    cfg = DTYPE_CFG[dtype]
    elem_type = cfg["elem_type"]
    vec_type = cfg["vec_type"]
    lane = int(cfg["lane"])
    bytes_per_batch = lane * int(cfg["bytes_per_elem"])
    pat_decl = cfg["pat_decl"]
    pat_name = cfg["pat_name"]
    store_norm = cfg["store_norm"]

    vec_decls = f"        {vec_type} vec_a;\n        {vec_type} vec_out;\n"
    if kind == "binary":
        vec_decls = f"        {vec_type} vec_a;\n        {vec_type} vec_out;\n"

    return (
        HEADER
        + f"""
__attribute__((always_inline)) inline [aicore] void single_{name}_simd_ub(
    __ubuf__ {elem_type} *memA,
    __ubuf__ {elem_type} *memB,
    __ubuf__ {elem_type} *memOut,
    int repeat_times) {{

    __VEC_SCOPE__ {{
        {pat_decl}
{vec_decls}
        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {{
{vec_body(kind, api, imm, lane, store_norm, pat_name)}        }}
    }}
}}

{kernel_signature(elem_type)}{{
    __ubuf__ {elem_type} *ub_data_a = (__ubuf__ {elem_type}*)get_imm(0x0);
    __ubuf__ {elem_type} *ub_data_b = (__ubuf__ {elem_type}*)get_imm(0x1000);
    __ubuf__ {elem_type} *ub_data_out = (__ubuf__ {elem_type}*)get_imm(0x2000);
    int repeat_times = 16;
{kernel_copy(kind, elem_type, bytes_per_batch)}    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    single_{name}_simd_ub(
        ub_data_a,
        ub_data_b,
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
    gen_count = 0
    for dtype in ("fp32", "fp16"):
        dtype_dir = out_dir / dtype
        dtype_dir.mkdir(parents=True, exist_ok=True)
        for op in OPS:
            path = dtype_dir / f"singleop_{op['name']}.dsl"
            path.write_text(render_case(op, dtype), encoding="utf-8")
            gen_count += 1
    print(f"Generated {gen_count} DSL files in {out_dir}")


if __name__ == "__main__":
    main()






