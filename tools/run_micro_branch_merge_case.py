#!/usr/bin/env python3
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="ignore")
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if p.returncode != 0:
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


def parse_model_vf_end(model_dir: Path) -> int:
    data = json.loads((model_dir / "sim_history.json").read_text(encoding="utf-8"))
    return max(int(row["cy"]) for row in data if row.get("event") == "done")


def parse_cce_vf_window(workdir: str) -> int:
    base = Path(workdir)
    instr = base / "core0.veccore0.instr_log.dump"
    popped = base / "core0.veccore0.instr_popped_log.dump"
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
        raise RuntimeError("cannot parse CCE VF window")
    return instr_last - popped_first


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
                    {"type": "inst", "op": "VLD", "dst": ["V0"], "src": ["memA"]},
                    {"type": "inst", "op": "VMULS", "dst": ["V1"], "src": ["V0"]},
                    {"type": "inst", "op": "VEXP", "dst": ["V2"], "src": ["V1"]},
                    {"type": "inst", "op": "VADDS", "dst": ["V2"], "src": ["V2"]},
                    {"type": "inst", "op": "VMULS", "dst": ["V3"], "src": ["V0"]},
                    {"type": "inst", "op": "VADDS", "dst": ["V3"], "src": ["V3"]},
                    {"type": "inst", "op": "VDIV", "dst": ["V4"], "src": ["V3", "V2"]},
                    {"type": "inst", "op": "VMUL", "dst": ["V5"], "src": ["V0", "V4"]},
                    {"type": "inst", "op": "VST", "dst": ["memB"], "src": ["V5"]},
                ],
            }
        ],
    }
    path = case_dir / "trace_input.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_dsl(case_dir: Path, stem: str, I: int, U: int) -> Path:
    pragma = "" if U <= 1 else f"#pragma unroll({U})\n        "
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
        vector_f32 vec_2;
        vector_f32 vec_3;
        vector_f32 vec_4;
        vector_f32 vec_5;

        {pragma}for (uint16_t i = 0; i < uint16_t(repeat_times); ++i) {{
            vlds(vec_0, memA, 64 * i, NORM);
            vmuls(vec_1, vec_0, 0.5f, pat_all_b32);
            vexp(vec_2, vec_1, pat_all_b32);
            vadds(vec_2, vec_2, 1.0f, pat_all_b32);
            vmuls(vec_3, vec_0, 0.75f, pat_all_b32);
            vadds(vec_3, vec_3, 1.0f, pat_all_b32);
            vdiv(vec_4, vec_3, vec_2, pat_all_b32);
            vmul(vec_5, vec_0, vec_4, pat_all_b32);
            vsts(vec_5, memB, 64 * i, NORM_B32, pat_all_b32);
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


def main():
    I = 120
    U = 8
    stem = f"branch_merge_live_set_i{I}_u{U}"
    case_dir = ROOT / "results" / "micro_cases" / stem
    case_dir.mkdir(parents=True, exist_ok=True)

    trace = write_trace(case_dir, I, U)
    dsl = write_dsl(case_dir, stem, I, U)

    model_dir = case_dir / "model"
    run(["python", str(ROOT / "main.py"), "--trace", str(trace), "--out_dir", str(model_dir), "--ooo-model", "queue_level3"])
    model_vf = parse_model_vf_end(model_dir)

    kernel = parse_kernel_name(dsl)
    run([
        "wsl", "-d", "Ubuntu", "--", "bash", "-lc",
        f"cd /mnt/d/VfSimulator && CCEC_EXTRA_FLAGS='-mllvm -cce-aicore-vec-misched=0' "
        f"bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(dsl)} {stem}"
    ])
    run([
        "wsl", "-d", "Ubuntu", "--", "bash", "-lc",
        f"cd /mnt/d/VfSimulator && "
        f"bash ascend_runner/current/run_native_simexec.sh "
        f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_simexec "
        f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_mix.o "
        f"{kernel} 2 1 {I * 64}"
    ])
    cce_vf = parse_cce_vf_window(f"/home/lenovo/msprof_run/{stem}_native_simexec")

    summary = {
        "case": stem,
        "model_vf_end": model_vf,
        "cce_vf_end": cce_vf,
        "error_pct": abs(model_vf - cce_vf) / cce_vf * 100.0,
        "workdir": str(case_dir.resolve()),
    }
    (case_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
