#!/usr/bin/env python3
import argparse
import csv
import json
import math
import re
import subprocess
from array import array
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(cmd, allow_fail=False):
    p = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="ignore")
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if p.returncode != 0 and not allow_fail:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{out}")
    return out


def to_wsl_path(path: Path) -> str:
    s = str(path.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def read_floats(path: Path):
    a = array("f")
    with path.open("rb") as f:
        a.frombytes(f.read())
    return a


def safe_div(a: float, b: float) -> float:
    if b == 0.0:
        if a == 0.0:
            return 0.0
        return float("inf") if a > 0 else float("-inf")
    return a / b


def golden_gelu(inp0):
    out = array("f")
    for x in inp0:
        x = float(x)
        v2 = abs(x)
        v3 = v2 * -1.702
        v4 = x * 0.851
        v5 = math.exp(v4)
        v6 = x * v5
        v7 = x - v2
        v8 = v7 * v6
        v9 = math.exp(v3)
        v9 = v9 + 1.0
        v10 = safe_div(v8, v9)
        out.append(float(v10))
    return out


def cmp_arrays(golden, out, abs_tol=1e-3, rel_tol=1e-3):
    mismatch = 0
    max_abs = 0.0
    max_rel = 0.0
    n = min(len(golden), len(out))
    for i in range(n):
        g = float(golden[i])
        o = float(out[i])
        if (math.isinf(g) and math.isinf(o) and ((g > 0) == (o > 0))) or (math.isnan(g) and math.isnan(o)):
            ae = 0.0
            re = 0.0
        else:
            ae = abs(o - g)
            re = ae / max(abs(g), 1.0)
        max_abs = max(max_abs, ae)
        max_rel = max(max_rel, re)
        if ae > abs_tol and re > rel_tol:
            mismatch += 1
    return mismatch, max_abs, max_rel


def parse_vf_cycles_from_vf_markers(popped_log: Path, instr_log: Path):
    ps = popped_log.read_text(encoding="utf-8", errors="ignore")
    ins = instr_log.read_text(encoding="utf-8", errors="ignore")
    pms = re.findall(r"\[(\d{8})\].*?\bVF\b", ps, flags=re.IGNORECASE)
    ims = re.findall(r"\[(\d{8})\].*?\bVF\b", ins, flags=re.IGNORECASE)
    if not pms or not ims:
        raise RuntimeError(f"no VF markers in {popped_log} or {instr_log}")
    return int(pms[0]), int(ims[-1])


def parse_cce_vf_end(popped_log: Path, instr_log: Path):
    try:
        vf_start, vf_end = parse_vf_cycles_from_vf_markers(popped_log, instr_log)
        return vf_end - vf_start
    except Exception:
        s = instr_log.read_text(encoding="utf-8", errors="ignore")
        ms = re.findall(r"vf_execute_time:\s*(\d+)", s)
        if not ms:
            raise RuntimeError(f"no usable VF timing in {popped_log} or {instr_log}")
        return int(ms[-1])


def kernel_name_from_dsl(path: Path):
    txt = path.read_text(encoding="utf-8-sig")
    m = re.search(r'extern\s+"C"\s+__global__\s+__aicore__\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', txt)
    if not m:
        raise RuntimeError(f"cannot parse kernel name from {path}")
    return m.group(1)


def build_nested_json(out_json: Path, I: int, J: int):
    src = ROOT / "unroll_test" / "json" / "GeLU.json"
    obj = json.loads(src.read_text(encoding="utf-8-sig"))
    body = obj["program"][0]["body"]
    nested = {
        "dtype": "fp32",
        "params": {"I": I, "J": J, "U": 1},
        "program": [
            {
                "type": "loop",
                "iters": "I",
                "unroll": 1,
                "body": [
                    {
                        "type": "loop",
                        "iters": "J",
                        "unroll": "U",
                        "body": body,
                    }
                ],
            }
        ],
    }
    out_json.write_text(json.dumps(nested, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_nested_dsl(out_dsl: Path, I: int, J: int):
    txt = f"""#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void gelu_nested_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    int repeat_outer,
    int repeat_inner) {{

    __VEC_SCOPE__ {{
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_1;
        vector_f32 vec_2;
        vector_f32 vec_3;
        vector_f32 vec_4;
        vector_f32 vec_5;
        vector_f32 vec_6;
        vector_f32 vec_7;
        vector_f32 vec_8;
        vector_f32 vec_9;
        vector_f32 vec_10;
        for (uint16_t i = 0; i < uint16_t(repeat_outer); ++i) {{
            for (uint16_t j = 0; j < uint16_t(repeat_inner); ++j) {{
                uint32_t idx = uint32_t(i) * uint32_t(repeat_inner) + uint32_t(j);
                vlds(vec_1, memA, 64 * idx, NORM);
                vabs(vec_2, vec_1, pat_all_b32);
                vmuls(vec_3, vec_2, -1.702, pat_all_b32);
                vmuls(vec_4, vec_1, 0.851, pat_all_b32);
                vexp(vec_5, vec_4, pat_all_b32);
                vmul(vec_6, vec_1, vec_5, pat_all_b32);
                vsub(vec_7, vec_1, vec_2, pat_all_b32);
                vmul(vec_8, vec_7, vec_6, pat_all_b32);
                vexp(vec_9, vec_3, pat_all_b32);
                vadds(vec_9, vec_9, 1.0, pat_all_b32);
                vdiv(vec_10, vec_8, vec_9, pat_all_b32);
                vsts(vec_10, memB, 64 * idx, NORM_B32, pat_all_b32);
            }}
        }}
    }}
}}

extern "C" __global__ __aicore__ void foo_add(
    __gm__ float* __restrict__ for_loop0_input0,
    __gm__ float* __restrict__ for_loop0_input1,
    __gm__ float* __restrict__ for_loop0_output0)
{{
    __ubuf__ float *ub_data_x_addr_0 = (__ubuf__ float*)get_imm(0x0);
    __ubuf__ float *ub_data_y_addr_0 = (__ubuf__ float*)get_imm(0x10000);
    int repeat_outer = {I};
    int repeat_inner = {J};
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_x_addr_0, (__gm__ float*)for_loop0_input0,
                            0, 1, repeat_outer * repeat_inner * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    gelu_nested_simd_ub(ub_data_x_addr_0, ub_data_y_addr_0, repeat_outer, repeat_inner);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_y_addr_0,
        0, 1, repeat_outer * repeat_inner * 64 * 4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}}
"""
    out_dsl.write_text(txt, encoding="utf-8")


def run_one(I: int, J: int, out_root: Path):
    tag = f"GeLU_nested_I{I}_J{J}"
    case_dir = out_root / tag
    case_dir.mkdir(parents=True, exist_ok=True)

    trace_json = case_dir / "trace_input.json"
    dsl = case_dir / f"{tag}.dsl"
    build_nested_json(trace_json, I, J)
    build_nested_dsl(dsl, I, J)

    model_dir = case_dir / "model"
    out = run(["python", str(ROOT / "main.py"), "--trace", str(trace_json), "--out_dir", str(model_dir), "--ooo-model", "consumer-done"])
    m = re.search(r"VF end cycle \(with drain\)\s*=\s*(\d+)", out)
    if not m:
        raise RuntimeError(f"cannot parse model vf_end for {tag}")
    model_vf_end = int(m.group(1))

    stem = tag
    kname = kernel_name_from_dsl(dsl)
    total = I * J * 64
    build_cmd = [
        "wsl", "-d", "Ubuntu", "--", "bash", "-lc",
        f"cd /mnt/d/VfSimulator && CCEC_EXTRA_FLAGS=\"-mllvm -cce-aicore-vec-misched=0\" bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(dsl)} {stem}",
    ]
    run(build_cmd)
    run_cmd = [
        "wsl", "-d", "Ubuntu", "--", "bash", "-lc",
        f"cd /mnt/d/VfSimulator && bash ascend_runner/current/run_native_simexec.sh /mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_simexec /mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_mix.o {kname} 2 1 {total}",
    ]
    run(run_cmd, allow_fail=True)

    cce_dump = case_dir / "cce_dump"
    cce_dump.mkdir(exist_ok=True)
    src_dir = f"/home/lenovo/msprof_run/{stem}_native_simexec"
    dst_dir = to_wsl_path(cce_dump)
    for fn in ("core0.veccore0.instr_log.dump", "core0.veccore0.instr_popped_log.dump", "input0.bin", "output0.bin"):
        run(["wsl", "-d", "Ubuntu", "--", "bash", "-lc", f"if [ -f {src_dir}/{fn} ]; then cp -f {src_dir}/{fn} {dst_dir}/{fn}; fi"], allow_fail=True)

    cce_vf_end = parse_cce_vf_end(
        cce_dump / "core0.veccore0.instr_popped_log.dump",
        cce_dump / "core0.veccore0.instr_log.dump",
    )
    inp0 = read_floats(cce_dump / "input0.bin")
    out0 = read_floats(cce_dump / "output0.bin")
    g = golden_gelu(inp0)
    mismatch, max_abs, max_rel = cmp_arrays(g, out0)
    precision_pass = (mismatch == 0)

    return {
        "case": "GeLU_nested",
        "I": I,
        "J": J,
        "model_vf_end": model_vf_end,
        "cce_vf_end": cce_vf_end,
        "delta": model_vf_end - cce_vf_end,
        "rel_err": (model_vf_end - cce_vf_end) / cce_vf_end,
        "precision_pass": precision_pass,
        "mismatch": mismatch,
        "max_abs_err": max_abs,
        "max_rel_err": max_rel,
        "workdir": str(case_dir.resolve()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="results/nested_loop_test/gelu_nested")
    args = ap.parse_args()

    out_root = ROOT / args.out_dir
    out_root.mkdir(parents=True, exist_ok=True)

    tests = [(3, 16), (16, 3), (3, 3), (16, 16)]
    rows = []
    for i, j in tests:
        print(f"[run] GeLU nested I={i}, J={j}", flush=True)
        rows.append(run_one(i, j, out_root))

    js = out_root / "summary.json"
    cs = out_root / "summary.csv"
    js.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with cs.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["case", "I", "J", "model_vf_end", "cce_vf_end", "delta", "rel_err", "precision_pass", "mismatch", "max_abs_err", "max_rel_err", "workdir"],
        )
        w.writeheader()
        w.writerows(rows)
    print(f"[done] {cs}")


if __name__ == "__main__":
    main()
