#!/usr/bin/env python3
import json
import math
import re
import subprocess
from array import array
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "results" / "overwrite_experiment" / "swiglu_i96_u8"
OOO_MODEL = "queue_level3"
I = 96
U = 8
ABS_TOL = 1e-3
REL_TOL = 1e-3


VARIANTS = {
    "baseline": {
        "desc": "Original SwiGLU write pattern used by the current unroll suite.",
        "program": [
            ("VLD", ["V0"], ["memA"]),
            ("VLD", ["V1"], ["memB"]),
            ("VMULS", ["V2"], ["V0"]),
            ("VEXP", ["V3"], ["V2"]),
            ("VADDS", ["V3"], ["V3"]),
            ("VMULS", ["V4"], ["V0"]),
            ("VADDS", ["V4"], ["V4"]),
            ("VDIV", ["V5"], ["V4", "V3"]),
            ("VMUL", ["V6"], ["V0", "V5"]),
            ("VMUL", ["V7"], ["V6", "V1"]),
            ("VST", ["memC"], ["V7"]),
        ],
        "dsl_body": [
            "vlds(vec_gate, memA, 64 * i, NORM);",
            "vlds(vec_up, memB, 64 * i, NORM);",
            "vmuls(vec_neg, vec_gate, -1.0, pat_all_b32);",
            "vexp(vec_den, vec_neg, pat_all_b32);",
            "vadds(vec_den, vec_den, 1.0, pat_all_b32);",
            "vmuls(vec_one, vec_gate, 0.0, pat_all_b32);",
            "vadds(vec_one, vec_one, 1.0, pat_all_b32);",
            "vdiv(vec_sigmoid, vec_one, vec_den, pat_all_b32);",
            "vmul(vec_silu, vec_gate, vec_sigmoid, pat_all_b32);",
            "vmul(vec_out, vec_silu, vec_up, pat_all_b32);",
            "vsts(vec_out, memC, 64 * i, NORM_B32, pat_all_b32);",
        ],
        "vecs": [
            "vector_f32 vec_gate;",
            "vector_f32 vec_up;",
            "vector_f32 vec_neg;",
            "vector_f32 vec_den;",
            "vector_f32 vec_one;",
            "vector_f32 vec_sigmoid;",
            "vector_f32 vec_silu;",
            "vector_f32 vec_out;",
        ],
    },
    "same_iter_overwrite": {
        "desc": "Aggressively reuse logical regs within the same iteration so overwrite sealing mostly happens intra-iter.",
        "program": [
            ("VLD", ["V0"], ["memA"]),
            ("VLD", ["V1"], ["memB"]),
            ("VMULS", ["V2"], ["V0"]),
            ("VEXP", ["V2"], ["V2"]),
            ("VADDS", ["V2"], ["V2"]),
            ("VMULS", ["V3"], ["V0"]),
            ("VADDS", ["V3"], ["V3"]),
            ("VDIV", ["V2"], ["V3", "V2"]),
            ("VMUL", ["V2"], ["V0", "V2"]),
            ("VMUL", ["V2"], ["V2", "V1"]),
            ("VST", ["memC"], ["V2"]),
        ],
        "dsl_body": [
            "vlds(vec_gate, memA, 64 * i, NORM);",
            "vlds(vec_up, memB, 64 * i, NORM);",
            "vmuls(vec_tmp0, vec_gate, -1.0, pat_all_b32);",
            "vexp(vec_tmp0, vec_tmp0, pat_all_b32);",
            "vadds(vec_tmp0, vec_tmp0, 1.0, pat_all_b32);",
            "vmuls(vec_tmp1, vec_gate, 0.0, pat_all_b32);",
            "vadds(vec_tmp1, vec_tmp1, 1.0, pat_all_b32);",
            "vdiv(vec_tmp0, vec_tmp1, vec_tmp0, pat_all_b32);",
            "vmul(vec_tmp0, vec_gate, vec_tmp0, pat_all_b32);",
            "vmul(vec_tmp0, vec_tmp0, vec_up, pat_all_b32);",
            "vsts(vec_tmp0, memC, 64 * i, NORM_B32, pat_all_b32);",
        ],
        "vecs": [
            "vector_f32 vec_gate;",
            "vector_f32 vec_up;",
            "vector_f32 vec_tmp0;",
            "vector_f32 vec_tmp1;",
        ],
    },
    "cross_iter_overwrite": {
        "desc": "Avoid same-iter rewrites so most logical-reg overwrites are deferred to the next iteration.",
        "program": [
            ("VLD", ["V0"], ["memA"]),
            ("VLD", ["V1"], ["memB"]),
            ("VMULS", ["V2"], ["V0"]),
            ("VEXP", ["V3"], ["V2"]),
            ("VADDS", ["V4"], ["V3"]),
            ("VMULS", ["V5"], ["V0"]),
            ("VADDS", ["V6"], ["V5"]),
            ("VDIV", ["V7"], ["V6", "V4"]),
            ("VMUL", ["V8"], ["V0", "V7"]),
            ("VMUL", ["V9"], ["V8", "V1"]),
            ("VST", ["memC"], ["V9"]),
        ],
        "dsl_body": [
            "vlds(vec_gate, memA, 64 * i, NORM);",
            "vlds(vec_up, memB, 64 * i, NORM);",
            "vmuls(vec_neg, vec_gate, -1.0, pat_all_b32);",
            "vexp(vec_exp, vec_neg, pat_all_b32);",
            "vadds(vec_den, vec_exp, 1.0, pat_all_b32);",
            "vmuls(vec_zero, vec_gate, 0.0, pat_all_b32);",
            "vadds(vec_one, vec_zero, 1.0, pat_all_b32);",
            "vdiv(vec_sigmoid, vec_one, vec_den, pat_all_b32);",
            "vmul(vec_silu, vec_gate, vec_sigmoid, pat_all_b32);",
            "vmul(vec_out, vec_silu, vec_up, pat_all_b32);",
            "vsts(vec_out, memC, 64 * i, NORM_B32, pat_all_b32);",
        ],
        "vecs": [
            "vector_f32 vec_gate;",
            "vector_f32 vec_up;",
            "vector_f32 vec_neg;",
            "vector_f32 vec_exp;",
            "vector_f32 vec_den;",
            "vector_f32 vec_zero;",
            "vector_f32 vec_one;",
            "vector_f32 vec_sigmoid;",
            "vector_f32 vec_silu;",
            "vector_f32 vec_out;",
        ],
    },
}


def run(cmd, cwd: Path | None = None, allow_fail: bool = False) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="ignore",
    )
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0 and not allow_fail:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{out}")
    return out


def to_wsl_path(path: Path) -> str:
    s = str(path.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def write_trace(case_dir: Path, program: list[tuple[str, list[str], list[str]]]) -> Path:
    obj = {
        "dtype": "fp32",
        "params": {"I": I, "U": U},
        "program": [
            {
                "type": "loop",
                "iters": "I",
                "unroll": "U",
                "body": [
                    {"type": "inst", "op": op, "dst": dst, "src": src}
                    for op, dst, src in program
                ],
            }
        ],
    }
    path = case_dir / "trace_input.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_dsl(case_dir: Path, stem: str, variant: dict) -> Path:
    vec_lines = "\n        ".join(variant["vecs"])
    body_lines = "\n            ".join(variant["dsl_body"])
    txt = f"""#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void {stem}_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    __ubuf__ float *memC,
    int repeat_times) {{

    __VEC_SCOPE__ {{
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        {vec_lines}

        #pragma unroll({U})
        for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {{
            {body_lines}
        }}
    }}
}}

extern "C" __global__ __aicore__ void {stem}(
    __gm__ float* __restrict__ for_loop0_input0,
    __gm__ float* __restrict__ for_loop0_input1,
    __gm__ float* __restrict__ for_loop0_output0)
{{
    __ubuf__ float *ub_data_a_addr_0 = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_b_addr_0 = (__ubuf__ float*)get_imm(0x10000);
    __ubuf__ float *ub_data_o_addr_0 = (__ubuf__ float*)get_imm(0x20000);
    int repeat_times = {I};

    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_a_addr_0, (__gm__ float*)for_loop0_input0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_b_addr_0, (__gm__ float*)for_loop0_input1,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    {stem}_simd_ub(ub_data_a_addr_0, ub_data_b_addr_0, ub_data_o_addr_0, repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_o_addr_0,
        0, 1, repeat_times * 64 * 4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}}
"""
    path = case_dir / f"{stem}.dsl"
    path.write_text(txt, encoding="utf-8")
    return path


def kernel_name_from_dsl(path: Path) -> str:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'extern\s+"C"\s+__global__\s+__aicore__\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', txt)
    if not m:
        raise RuntimeError(f"cannot parse kernel name from {path}")
    return m.group(1)


def parse_model_vf_end(model_dir: Path) -> int:
    out = json.loads((model_dir / "sim_history.json").read_text(encoding="utf-8"))
    return max(int(row["cy"]) for row in out if row.get("event") == "done")


def parse_vf_cycles_from_vf_markers(popped_log: Path, instr_log: Path):
    ps = popped_log.read_text(encoding="utf-8", errors="ignore")
    ins = instr_log.read_text(encoding="utf-8", errors="ignore")
    pms = re.findall(r"\[(\d{8})\].*?\bVF\b", ps, flags=re.IGNORECASE)
    ims = re.findall(r"\[(\d{8})\].*?\bVF\b", ins, flags=re.IGNORECASE)
    if not pms or not ims:
        raise RuntimeError(f"no VF markers in {popped_log} or {instr_log}")
    return int(pms[0]), int(ims[-1])


def parse_cce_vf_window(dump_dir: Path) -> int:
    instr = dump_dir / "core0.veccore0.instr_log.dump"
    popped = dump_dir / "core0.veccore0.instr_popped_log.dump"
    try:
        vf_start, vf_end = parse_vf_cycles_from_vf_markers(popped, instr)
        return vf_end - vf_start
    except Exception:
        pats = ("RV_VLOOPv2", "RV_VLD", "RV_VMULS", "RV_VEXP", "RV_VADDS", "RV_VDIV", "RV_VMUL", "RV_VST")

        def first_last(fp: Path):
            first = None
            last = None
            for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not any(p in line for p in pats):
                    continue
                m = re.search(r"\[(\d{8})\]", line)
                if not m:
                    continue
                cy = int(m.group(1))
                if first is None:
                    first = cy
                last = cy
            return first, last

        popped_first, _ = first_last(popped)
        _, instr_last = first_last(instr)
        if popped_first is None or instr_last is None:
            raise RuntimeError(f"cannot parse VF window from {dump_dir}")
        return instr_last - popped_first


def read_floats(path: Path) -> array:
    out = array("f")
    with path.open("rb") as fh:
        out.frombytes(fh.read())
    return out


def golden_swiglu(inp0: array, inp1: array) -> array:
    out = array("f")
    for a, b in zip(inp0, inp1):
        aa = float(a)
        bb = float(b)
        silu = aa / (1.0 + math.exp(-aa))
        out.append(float(silu * bb))
    return out


def cmp_arrays(golden: array, out: array) -> tuple[int, float, float]:
    mismatch = 0
    max_abs = 0.0
    max_rel = 0.0
    n = min(len(golden), len(out))
    for i in range(n):
        g = float(golden[i])
        o = float(out[i])
        ae = abs(o - g)
        re = ae / max(abs(g), 1.0)
        max_abs = max(max_abs, ae)
        max_rel = max(max_rel, re)
        if ae > ABS_TOL and re > REL_TOL:
            mismatch += 1
    return mismatch, max_abs, max_rel


def copy_from_wsl(src_dir: str, dst_dir: Path, name: str) -> None:
    run(
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            f"if [ -f {src_dir}/{name} ]; then cp -f {src_dir}/{name} {to_wsl_path(dst_dir)}/{name}; fi",
        ],
        allow_fail=True,
    )


def run_variant(name: str, variant: dict) -> dict:
    stem = f"swiglu_overwrite_{name}_i{I}_u{U}"
    case_dir = OUT_ROOT / name
    case_dir.mkdir(parents=True, exist_ok=True)

    trace = write_trace(case_dir, variant["program"])
    dsl = write_dsl(case_dir, stem, variant)

    model_dir = case_dir / "model"
    model_vf = None
    model_error = None
    try:
        run(
            [
                "python",
                str(ROOT / "main.py"),
                "--trace",
                str(trace),
                "--out_dir",
                str(model_dir),
                "--ooo-model",
                OOO_MODEL,
            ]
        )
        model_vf = parse_model_vf_end(model_dir)
    except Exception as exc:
        model_error = str(exc)

    kernel = kernel_name_from_dsl(dsl)
    run(
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            f"cd /mnt/d/VfSimulator && "
            f"CCEC_EXTRA_FLAGS='-mllvm -cce-aicore-vec-misched=0' "
            f"bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(dsl)} {stem}",
        ]
    )
    run(
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            f"cd /mnt/d/VfSimulator && "
            f"bash ascend_runner/current/run_native_simexec.sh "
            f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_simexec "
            f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_mix.o "
            f"{kernel} 2 1 {I * 64}",
        ],
        allow_fail=True,
    )

    wsl_dump = f"/home/lenovo/msprof_run/{stem}_native_simexec"
    dump_dir = case_dir / "cce_dump"
    dump_dir.mkdir(exist_ok=True)
    for name_to_copy in [
        "core0.veccore0.instr_log.dump",
        "core0.veccore0.instr_popped_log.dump",
        "input0.bin",
        "input1.bin",
        "output0.bin",
        "run.log",
    ]:
        copy_from_wsl(wsl_dump, dump_dir, name_to_copy)

    cce_vf = parse_cce_vf_window(dump_dir)
    inp0 = read_floats(dump_dir / "input0.bin")
    inp1 = read_floats(dump_dir / "input1.bin")
    out0 = read_floats(dump_dir / "output0.bin")
    golden = golden_swiglu(inp0, inp1)
    mismatch, max_abs, max_rel = cmp_arrays(golden, out0)

    row = {
        "variant": name,
        "desc": variant["desc"],
        "model_vf": model_vf,
        "cce_vf": cce_vf,
        "delta": (None if model_vf is None else model_vf - cce_vf),
        "rel_err_pct": (None if model_vf is None else abs(model_vf - cce_vf) / cce_vf * 100.0),
        "model_error": model_error,
        "precision_pass": mismatch == 0,
        "mismatch": mismatch,
        "max_abs_err": max_abs,
        "max_rel_err": max_rel,
        "case_dir": str(case_dir.resolve()),
    }
    (case_dir / "summary.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def write_report(rows: list[dict]) -> None:
    lines = [
        "# SwiGLU I96 U8 overwrite experiment",
        "",
        f"- Model: `{OOO_MODEL}`",
        f"- I = {I}, U = {U}",
        "- CCE metric: `instr_log last relevant cycle - instr_popped first relevant cycle`",
        "- Golden check: Python `silu(a) * b` compared with `output0.bin`",
        "",
        "| variant | description | model_vf | cce_vf | delta | rel_err | precision_pass | mismatch |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['variant']} | {row['desc']} | {row['model_vf']} | {row['cce_vf']} | "
            f"{row['delta']} | "
            f"{('NA' if row['rel_err_pct'] is None else f'{row['rel_err_pct']:.2f}%')} | "
            f"{row['precision_pass']} | {row['mismatch']} |"
        )
        if row.get("model_error"):
            lines.append(f"  model_error: `{row['model_error']}`")
    (OUT_ROOT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT_ROOT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, variant in VARIANTS.items():
        print(f"[run] {name}", flush=True)
        rows.append(run_variant(name, variant))
    write_report(rows)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
