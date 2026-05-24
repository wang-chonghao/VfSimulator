import argparse
import json
from pathlib import Path


def infer_mem_names(program):
    mem_names = ["memA", "memB"]
    for loop in program:
        for inst in loop["body"]:
            for operand in inst.get("src", []) + inst.get("dst", []):
                if isinstance(operand, str) and operand.startswith("mem") and operand not in mem_names:
                    mem_names.append(operand)
    return mem_names


def emit_inst(inst, mem_map):
    op = inst["op"]
    dst = inst.get("dst", [])
    src = inst.get("src", [])

    if op == "VLD":
        return f"            vlds(vec_1, {mem_map[src[0]]}, 64 * i, NORM);"
    if op == "VST":
        return f"            vsts(vec_1, {mem_map[dst[0]]}, 64 * i, NORM_B32, pat_all_b32);"
    if op == "VADDS":
        return "            vadds(vec_1, vec_1, 0.1f, pat_all_b32);"
    if op == "VEXP":
        return "            vexp(vec_1, vec_1, pat_all_b32);"
    if op == "VMAXS":
        return "            vmaxs(vec_1, vec_1, 0.1f, pat_all_b32);"
    raise ValueError(f"Unsupported op for single-V1 DSL generation: {op}")


def generate(trace_path: Path, output_path: Path, simd_name: str) -> None:
    obj = json.loads(trace_path.read_text(encoding="utf-8"))
    program = obj["program"]
    repeat_times = int(obj["params"]["I"])
    mem_names = infer_mem_names(program)
    inter_names = [name for name in mem_names if name not in ("memA", "memB")]
    mem_map = {name: name for name in mem_names}

    lines = [
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
    lines.extend(
        [
            "    int repeat_times) {",
            "",
            "    __VEC_SCOPE__ {",
            "        vector_bool pat_all_b32 = pset_b32(PAT_ALL);",
            "        vector_f32 vec_1;",
            "",
        ]
    )

    for loop_idx, loop in enumerate(program):
        if loop_idx > 0:
            lines.append("        mem_bar(VST_VLD);")
        lines.append("        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {")
        for inst in loop["body"]:
            lines.append(emit_inst(inst, mem_map))
        lines.append("        }")
        lines.append("")

    lines.extend(
        [
            "    }",
            "}",
            "",
            'extern "C" __global__ __aicore__ void foo_add(',
            "    __gm__ float* __restrict__ for_loop0_input0,",
            "    __gm__ float* __restrict__ for_loop0_input1,",
            "    __gm__ float* __restrict__ for_loop0_output0)",
            "{",
            "    __ubuf__ float *ub_data_x_addr_0 = (__ubuf__ float*)get_imm(0x0);",
            "    __ubuf__ float *ub_data_y_addr_0 = (__ubuf__ float*)get_imm(0x4000);",
        ]
    )

    stride = 0x4000
    for idx, name in enumerate(inter_names):
        addr = stride * (idx + 2)
        lines.append(f"    __ubuf__ float *ub_{name}_addr_0 = (__ubuf__ float*)get_imm(0x{addr:x});")

    lines.extend(
        [
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
    )

    for name in inter_names:
        lines.append(f"        ub_{name}_addr_0,")

    lines.extend(
        [
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
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")


def batch_generate(input_root: Path) -> None:
    for traces_dir in sorted(input_root.rglob("traces")):
        dsl_dir = traces_dir.parent / "DSL_traces"
        dsl_dir.mkdir(parents=True, exist_ok=True)
        for trace_path in sorted(traces_dir.glob("*.json")):
            output_path = dsl_dir / f"{trace_path.stem}.dsl"
            simd_name = output_path.stem.lower() + "_simd_ub"
            generate(trace_path, output_path, simd_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DSL from single-V1 traces")
    parser.add_argument("--trace", help="Single input trace JSON")
    parser.add_argument("--output", help="Single output DSL path")
    parser.add_argument("--input-root", help="Batch-convert all traces/ folders under this root")
    args = parser.parse_args()

    if args.input_root:
        batch_generate(Path(args.input_root))
        return

    if not args.trace or not args.output:
        raise SystemExit("Use either --input-root or both --trace and --output")

    trace_path = Path(args.trace)
    output_path = Path(args.output)
    generate(trace_path, output_path, output_path.stem.lower() + "_simd_ub")


if __name__ == "__main__":
    main()
