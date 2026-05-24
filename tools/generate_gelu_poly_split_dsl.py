import argparse
import json
from pathlib import Path


V4_ADD_CONSTS = [7.5517, 101.62809, 1393.8015, 5063.7915, 29639.3848]
V5_ADD_CONSTS = [31.2128582, 308.569641, 3023.12476, 14243.3662, 26267.2246]


def fmt_const(x):
    if isinstance(x, float) and x.is_integer():
        return f"{x:.1f}"
    return str(x)


def infer_line(inst, state, mem_ptrs):
    op = inst["op"]
    dst = inst.get("dst", [])
    src = inst.get("src", [])

    def mem_name(name):
        return mem_ptrs[name]

    if op == "VLD":
        return f"            vlds(vec_{dst[0][1:].lower()}, {mem_name(src[0])}, 64 * i, NORM);"
    if op == "VST":
        return f"            vsts(vec_{src[0][1:].lower()}, {mem_name(dst[0])}, 64 * i, NORM_B32, pat_all_b32);"
    if op == "VMULS":
        reg = dst[0]
        if reg == "V1" and src[0] == "V0":
            c = "0.5"
        elif reg == "V2" and src[0] == "V0":
            c = "0.7071"
        elif reg == "V4" and src[0] == "V3":
            c = "0.5344"
        else:
            raise ValueError(f"Unknown VMULS pattern: {inst}")
        return f"            vmuls(vec_{reg[1:].lower()}, vec_{src[0][1:].lower()}, {c}, pat_all_b32);"
    if op == "VMINS":
        return f"            vmins(vec_{dst[0][1:].lower()}, vec_{src[0][1:].lower()}, 3.92, pat_all_b32);"
    if op == "VMAXS":
        return f"            vmaxs(vec_{dst[0][1:].lower()}, vec_{src[0][1:].lower()}, -3.92, pat_all_b32);"
    if op == "VMUL":
        return f"            vmul(vec_{dst[0][1:].lower()}, vec_{src[0][1:].lower()}, vec_{src[1][1:].lower()}, pat_all_b32);"
    if op == "VDIV":
        return f"            vdiv(vec_{dst[0][1:].lower()}, vec_{src[0][1:].lower()}, vec_{src[1][1:].lower()}, pat_all_b32);"
    if op == "VADDS":
        d = dst[0]
        s = src[0]
        if d == "V4":
            c = V4_ADD_CONSTS[state["v4_add_idx"]]
            state["v4_add_idx"] += 1
        elif d == "V5":
            if s == "V3":
                c = V5_ADD_CONSTS[0]
                state["v5_add_idx"] = max(state["v5_add_idx"], 1)
            elif s == "V5":
                if state["after_div"]:
                    c = 1.0
                else:
                    c = V5_ADD_CONSTS[state["v5_add_idx"]]
                    state["v5_add_idx"] += 1
            else:
                raise ValueError(f"Unknown V5 VADDS pattern: {inst}")
        else:
            raise ValueError(f"Unknown VADDS pattern: {inst}")
        return f"            vadds(vec_{d[1:].lower()}, vec_{s[1:].lower()}, {fmt_const(c)}, pat_all_b32);"
    raise ValueError(f"Unsupported op: {op}")


def generate(trace_path: Path, output_path: Path, simd_name: str):
    obj = json.loads(trace_path.read_text(encoding="utf-8"))
    loops = obj["program"]
    repeat_times = int(obj["params"]["I"])
    tensor_bytes = repeat_times * 64 * 4

    mem_names = ["memA", "memB"]
    for loop in loops:
        for inst in loop["body"]:
            for x in inst.get("src", []) + inst.get("dst", []):
                if isinstance(x, str) and x.startswith("mem_inter") and x not in mem_names:
                    mem_names.append(x)

    ptr_map = {}
    for idx, name in enumerate(mem_names):
        if name == "memA":
            ptr_map[name] = "memA"
            continue
        if name == "memB":
            ptr_map[name] = "memB"
            continue

    inter_names = [n for n in mem_names if n not in ("memA", "memB")]
    for name in inter_names:
        ptr_map[name] = name

    lines = []
    lines += [
        "#ifdef __CCE_KT_TEST__",
        "#define __aicore__",
        "#else",
        "#define __aicore__ [aicore]",
        "#endif",
        "",
        f"__attribute__((always_inline)) inline [aicore] void {simd_name}(",
        "    __ubuf__ float *memA,",
        "    __ubuf__ float *memB,",
    ]
    for name in inter_names:
        lines.append(f"    __ubuf__ float *{name},")
    lines += [
        "    int repeat_times) {",
        "",
        "    __VEC_SCOPE__ {",
        "        vector_bool pat_all_b32 = pset_b32(PAT_ALL);",
        "        vector_f32 vec_0;",
        "        vector_f32 vec_1;",
        "        vector_f32 vec_2;",
        "        vector_f32 vec_3;",
        "        vector_f32 vec_4;",
        "        vector_f32 vec_5;",
        "        vector_f32 vec_6;",
        "",
    ]

    state = {"v4_add_idx": 0, "v5_add_idx": 0, "after_div": False}
    for loop_idx, loop in enumerate(loops):
        if loop_idx > 0:
            lines.append("        mem_bar(VST_VLD);")
        lines.append("        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {")
        for inst in loop["body"]:
            lines.append(infer_line(inst, state, ptr_map))
            if inst["op"] == "VDIV":
                state["after_div"] = True
        lines.append("        }")
        lines.append("")

    lines += [
        "    }",
        "}",
        "",
        'extern "C" __global__ __aicore__ void foo_add(',
        "    __gm__ float* __restrict__ for_loop0_input0,",
        "    __gm__ float* __restrict__ for_loop0_input1,",
        "    __gm__ float* __restrict__ for_loop0_output0)",
        "{",
    ]

    ub_names = [("ub_data_x_addr_0", 0x0), ("ub_data_y_addr_0", tensor_bytes)]
    for idx, name in enumerate(inter_names):
        ub_names.append((f"ub_{name}_addr_0", tensor_bytes * (idx + 2)))
    for name, addr in ub_names:
        lines.append(f"    __ubuf__ float *{name} = (__ubuf__ float*)get_imm(0x{addr:x});")
    lines += [
        f"    int repeat_times = {repeat_times};",
        "    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_x_addr_0, (__gm__ float*)for_loop0_input0,",
        "                            0, 1, repeat_times*64*4, 0, 0, 0, 0, 0, 0);",
        "    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);",
        "    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);",
        "",
        f"    {simd_name}(",
        "        ub_data_x_addr_0,",
        "        ub_data_y_addr_0,",
    ]
    for idx, name in enumerate(inter_names):
        comma = "," if idx < len(inter_names) - 1 else ","
        lines.append(f"        ub_{name}_addr_0{comma}")
    lines += [
        "        repeat_times);",
        "",
        "    pipe_barrier(PIPE_ALL);",
        "    copy_ubuf_to_gm_align_v2(",
        "        (__gm__ float*)for_loop0_output0,",
        "        (__ubuf__ float*)ub_data_y_addr_0,",
        "        0, 1, repeat_times*64*4, 0, 0, 0);",
        "    pipe_barrier(PIPE_ALL);",
        "}",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace")
    parser.add_argument("output")
    parser.add_argument("--simd-name", required=True)
    args = parser.parse_args()
    generate(Path(args.trace), Path(args.output), args.simd_name)


if __name__ == "__main__":
    main()
