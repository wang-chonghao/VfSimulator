#!/usr/bin/env python3
import json
import re
import subprocess
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


def parse_kernel_name(dsl: Path) -> str:
    txt = dsl.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'extern\s+"C"\s+__global__\s+__aicore__\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', txt)
    if not m:
        raise RuntimeError(f"cannot parse kernel name from {dsl}")
    return m.group(1)


def write_trace(case_dir: Path, I: int, U: int) -> Path:
    obj = {
        "dtype": "fp32",
        "params": {"I": I, "U": U},
        "program": [
            {
                "type": "loop",
                "iters": "I",
                "unroll": "U",
                "body": [
                    {"type": "inst", "op": "VLDS", "dst": ["V0"], "src": ["memA"]},
                    {"type": "inst", "op": "VADDS", "dst": ["V1"], "src": ["V0"]},
                    {"type": "inst", "op": "VSTS", "dst": ["memB"], "src": ["V1"]},
                ],
            }
        ],
    }
    path = case_dir / "trace_input.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_dsl(case_dir: Path, stem: str, I: int, U: int) -> Path:
    pragma = "" if U <= 1 else f"#pragma unroll({U})\n            "
    txt = f"""#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void {stem}_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    int repeat_times) {{

    __VEC_SCOPE__ {{
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_0;
        vector_f32 vec_1;

        {pragma}for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {{
            vlds(vec_0, memA, 64 * i, NORM);
            vadds(vec_1, vec_0, 1.0f, pat_all_b32);
            vsts(vec_1, memB, 64 * i, NORM_B32, pat_all_b32);
        }}
    }}
}}

extern "C" __global__ __aicore__ void {stem}(
    __gm__ float* __restrict__ input0,
    __gm__ float* __restrict__ input1,
    __gm__ float* __restrict__ output0) {{
    __ubuf__ float *ub_data_0_addr_0 = (__ubuf__ float*)get_imm(0x00000);
    __ubuf__ float *ub_data_1_addr_0 = (__ubuf__ float*)get_imm(0x10000);

    int repeat_times = {I};

    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_0_addr_0, (__gm__ float*)input0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);

    {stem}_simd_ub(ub_data_0_addr_0, ub_data_1_addr_0, repeat_times);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2((__gm__ float*)output0, (__ubuf__ float*)ub_data_1_addr_0,
                             0, 1, repeat_times * 64 * 4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}}
"""
    path = case_dir / f"{stem}.dsl"
    path.write_text(txt, encoding="utf-8")
    return path


def parse_model_vf_end(model_dir: Path) -> int:
    last_cy = None
    for line in (model_dir / "done_by_cycle.json").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        last_cy = int(row["cy"])
    if last_cy is None:
        raise RuntimeError(f"cannot parse model vf_end from {model_dir}")
    return last_cy


def parse_cce_vf_end(run_out: str) -> int:
    patterns = [
        r"vf_total_cycles\s*[:=]\s*(\d+)",
        r"vf_end\s*[:=]\s*(\d+)",
        r"total cycles\s*[:=]\s*(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, run_out, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
    raise RuntimeError(f"cannot parse CCE vf time from output:\n{run_out}")


def main():
    I = 120
    U = 8
    stem = f"simple_ld_vadds_vst_i{I}_u{U}"
    case_dir = ROOT / "results" / "micro_cases" / stem
    case_dir.mkdir(parents=True, exist_ok=True)

    trace_path = write_trace(case_dir, I, U)
    dsl_path = write_dsl(case_dir, stem, I, U)

    model_dir = case_dir / "model"
    run(
        [
            "python",
            str(ROOT / "main.py"),
            "--trace",
            str(trace_path),
            "--out_dir",
            str(model_dir),
            "--ooo-model",
            "queue_level3",
        ]
    )
    model_vf = parse_model_vf_end(model_dir)

    kernel = parse_kernel_name(dsl_path)
    build_out = run(
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            f"cd /mnt/d/VfSimulator && CCEC_EXTRA_FLAGS=\"-mllvm -cce-aicore-vec-misched=0\" "
            f"bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(dsl_path)} {stem}",
        ]
    )
    run_out = run(
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
    )
    cce_vf = parse_cce_vf_end(run_out)

    summary = {
        "case": stem,
        "I": I,
        "U": U,
        "model_vf_end": model_vf,
        "cce_vf_end": cce_vf,
        "error_pct": abs(model_vf - cce_vf) / cce_vf * 100.0,
        "workdir": str(case_dir.resolve()),
    }
    (case_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
