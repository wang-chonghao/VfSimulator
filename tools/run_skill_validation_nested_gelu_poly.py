#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "results" / "skill_validation" / "nested_gelu_poly"


def run_cmd(cmd: List[str], cwd: Path | None = None, allow_fail: bool = False) -> str:
    p = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="ignore",
    )
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if p.returncode != 0 and not allow_fail:
        raise RuntimeError(f"cmd failed ({p.returncode}): {' '.join(cmd)}\n{out}")
    return out


def to_wsl_path(path: Path) -> str:
    s = str(path.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_nested_trace(i_outer: int, j_inner: int, unroll: int) -> Dict:
    base = json.loads((ROOT / "VFtest" / "GeLU_poly.json").read_text(encoding="utf-8-sig"))
    body = base["program"][0]["body"]
    return {
        "dtype": "fp32",
        "params": {"I": i_outer, "J": j_inner, "U": unroll},
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


def build_nested_dsl(i_outer: int, j_inner: int, unroll: int) -> str:
    pragma = "" if unroll <= 1 else f"#pragma unroll({unroll})\n            "
    return f"""#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void gelu_poly_nested_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    int repeat_outer,
    int repeat_inner) {{

    __VEC_SCOPE__ {{
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
        vector_f32 vec_0;
        vector_f32 vec_1;
        vector_f32 vec_2;
        vector_f32 vec_3;
        vector_f32 vec_4;
        vector_f32 vec_5;
        vector_f32 vec_6;
        for (uint16_t i = 0; i < uint16_t(repeat_outer); ++i) {{
            {pragma}for (uint16_t j = 0; j < uint16_t(repeat_inner); ++j) {{
                uint32_t idx = uint32_t(i) * uint32_t(repeat_inner) + uint32_t(j);
                vlds(vec_0, memA, 64 * idx, NORM);
                vmuls(vec_1, vec_0, 0.5, pat_all_b32);
                vmuls(vec_2, vec_0, 0.7071, pat_all_b32);
                vmins(vec_2, vec_2, 3.92, pat_all_b32);
                vmaxs(vec_2, vec_2, -3.92, pat_all_b32);
                vmul(vec_3, vec_2, vec_2, pat_all_b32);
                vmuls(vec_4, vec_3, 0.5344, pat_all_b32);
                vadds(vec_4, vec_4, 7.5517, pat_all_b32);
                vmul(vec_4, vec_4, vec_3, pat_all_b32);
                vadds(vec_4, vec_4, 101.62809, pat_all_b32);
                vmul(vec_4, vec_4, vec_3, pat_all_b32);
                vadds(vec_4, vec_4, 1393.8015, pat_all_b32);
                vmul(vec_4, vec_4, vec_3, pat_all_b32);
                vadds(vec_4, vec_4, 5063.7915, pat_all_b32);
                vmul(vec_4, vec_4, vec_3, pat_all_b32);
                vadds(vec_4, vec_4, 29639.3848, pat_all_b32);
                vmul(vec_4, vec_4, vec_2, pat_all_b32);
                vadds(vec_5, vec_3, 31.2128582, pat_all_b32);
                vmul(vec_5, vec_5, vec_3, pat_all_b32);
                vadds(vec_5, vec_5, 308.569641, pat_all_b32);
                vmul(vec_5, vec_5, vec_3, pat_all_b32);
                vadds(vec_5, vec_5, 3023.12476, pat_all_b32);
                vmul(vec_5, vec_5, vec_3, pat_all_b32);
                vadds(vec_5, vec_5, 14243.3662, pat_all_b32);
                vmul(vec_5, vec_5, vec_3, pat_all_b32);
                vadds(vec_5, vec_5, 26267.2246, pat_all_b32);
                vdiv(vec_5, vec_4, vec_5, pat_all_b32);
                vadds(vec_5, vec_5, 1.0, pat_all_b32);
                vmul(vec_6, vec_5, vec_1, pat_all_b32);
                vsts(vec_6, memB, 64 * idx, NORM_B32, pat_all_b32);
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
    int repeat_outer = {i_outer};
    int repeat_inner = {j_inner};
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)ub_data_x_addr_0, (__gm__ float*)for_loop0_input0,
                            0, 1, repeat_outer * repeat_inner * 64 * 4, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    gelu_poly_nested_simd_ub(ub_data_x_addr_0, ub_data_y_addr_0, repeat_outer, repeat_inner);

    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_y_addr_0,
        0, 1, repeat_outer * repeat_inner * 64 * 4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}}
"""


def parse_model_vf_end(result_json: Path) -> int:
    obj = json.loads(result_json.read_text(encoding="utf-8-sig"))
    return int(obj["vf_end"])


def parse_vf_start(popped_log: Path) -> int:
    s = popped_log.read_text(encoding="utf-8", errors="ignore")
    ms = re.findall(r"\[info\]\s+\[(\d+)\].*?\bVF\b", s)
    if not ms:
        raise RuntimeError(f"VF start not found in {popped_log}")
    return int(ms[0])


def parse_vf_end(instr_log: Path) -> int:
    s = instr_log.read_text(encoding="utf-8", errors="ignore")
    ms = re.findall(r"\[info\]\s+\[(\d+)\].*?\bVF\b", s)
    if not ms:
        raise RuntimeError(f"VF end not found in {instr_log}")
    return int(ms[-1])


def parse_check(run_stdout: str) -> Tuple[bool | None, str]:
    m = re.search(r"\[CHECK\].*mismatches=(\d+).*max_abs_err=([0-9eE\.\-]+).*max_rel_err=([0-9eE\.\-]+)", run_stdout)
    if m:
        mis = int(m.group(1))
        return (mis == 0), f"mismatches={mis}, max_abs={m.group(2)}, max_rel={m.group(3)}"
    if "[CHECK] PASS" in run_stdout:
        return True, "PASS"
    if "[CHECK] FAIL" in run_stdout:
        return False, "FAIL"
    return None, "CHECK_NOT_FOUND"


def run_one(u: int, i_outer: int = 16, j_inner: int = 16) -> Dict:
    case_id = f"GeLU_poly_nested_I{i_outer}_J{j_inner}_U{u}"
    case_dir = OUT_ROOT / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    trace = build_nested_trace(i_outer, j_inner, u)
    trace_path = case_dir / "trace_input.json"
    write_json(trace_path, trace)

    dsl_path = case_dir / f"{case_id}.dsl"
    dsl_path.write_text(build_nested_dsl(i_outer, j_inner, u), encoding="utf-8")

    model_out_dir = case_dir / "model_run"
    model_result = model_out_dir / "result.json"
    if model_result.exists():
        model_vf = parse_model_vf_end(model_result)
    else:
        run_cmd(
            [
                sys.executable,
                str(ROOT / "skills" / "vf-optimization" / "scripts" / "run_cost_model_once.py"),
                "--trace",
                str(trace_path),
                "--out-dir",
                str(model_out_dir),
                "--ooo-model",
                "consumer-done",
            ],
        )
        model_vf = parse_model_vf_end(model_result)

    stem = case_id
    total_elems = i_outer * j_inner * 64

    cce_dump = case_dir / "cce_dump"
    cce_dump.mkdir(exist_ok=True)

    popped = cce_dump / "core0.veccore0.instr_popped_log.dump"
    ilog = cce_dump / "core0.veccore0.instr_log.dump"
    if not popped.exists() or not ilog.exists():
        build_log = run_cmd(
            [
                "wsl",
                "-d",
                "Ubuntu",
                "--",
                "bash",
                "-lc",
                (
                    f"cd /mnt/d/VfSimulator && "
                    f"CCEC_EXTRA_FLAGS='-mllvm -cce-aicore-vec-misched=0' "
                    f"bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(dsl_path)} {stem}"
                ),
            ]
        )
        (case_dir / "build_stdout.log").write_text(build_log, encoding="utf-8")

        run_log = run_cmd(
            [
                "wsl",
                "-d",
                "Ubuntu",
                "--",
                "bash",
                "-lc",
                (
                    "cd /mnt/d/VfSimulator && "
                    f"bash ascend_runner/current/run_native_simexec.sh "
                    f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_simexec "
                    f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_mix.o "
                    f"foo_add 2 1 {total_elems}"
                ),
            ],
            allow_fail=True,
        )
        (case_dir / "run_stdout.log").write_text(run_log, encoding="utf-8")
        precision_pass, check_note = parse_check(run_log)

        src = f"/home/lenovo/msprof_run/{stem}_native_simexec"
        dst = to_wsl_path(cce_dump)
        for fn in (
            "core0.veccore0.instr_popped_log.dump",
            "core0.veccore0.instr_log.dump",
            "core0.veccore0.rvec.EXU.dump",
            "core0.veccore0.rvec.IDU.dump",
            "core0.veccore0.rvec.simd.ifu.dump",
        ):
            run_cmd(
                ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", f"if [ -f {src}/{fn} ]; then cp -f {src}/{fn} {dst}/{fn}; fi"],
                allow_fail=True,
            )
    else:
        run_stdout_path = case_dir / "run_stdout.log"
        if run_stdout_path.exists():
            precision_pass, check_note = parse_check(run_stdout_path.read_text(encoding="utf-8", errors="ignore"))
        else:
            precision_pass, check_note = (None, "RUN_LOG_NOT_FOUND")

    if not popped.exists() or not ilog.exists():
        raise RuntimeError(f"missing dump for {case_id}")
    vf_start = parse_vf_start(popped)
    vf_end = parse_vf_end(ilog)
    cce_vf = vf_end - vf_start

    abs_err = abs(model_vf - cce_vf)
    rel_err = abs_err / cce_vf if cce_vf else 0.0

    return {
        "case_id": case_id,
        "I": i_outer,
        "J": j_inner,
        "U": u,
        "model_vf": model_vf,
        "cce_vf": cce_vf,
        "vf_start": vf_start,
        "vf_end": vf_end,
        "abs_err": abs_err,
        "rel_err": rel_err,
        "precision_pass": precision_pass,
        "check_note": check_note,
        "case_dir": str(case_dir.resolve()),
    }


def pick_best(rows: List[Dict], key: str) -> Tuple[int, int]:
    vals = [(int(r["U"]), int(r[key])) for r in rows]
    vals.sort(key=lambda x: (x[1], x[0]))
    return vals[0]


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    rows: List[Dict] = []
    for u in (1, 2, 4, 8):
        print(f"[run] U={u}", flush=True)
        rows.append(run_one(u))

    model_best_u, model_best_v = pick_best(rows, "model_vf")
    cce_best_u, cce_best_v = pick_best(rows, "cce_vf")

    cce_at_model_best = next(int(r["cce_vf"]) for r in rows if int(r["U"]) == model_best_u)
    sweetspot_match = model_best_u == cce_best_u
    sweetspot_gap_pct = (cce_at_model_best - cce_best_v) / cce_best_v * 100.0 if cce_best_v else 0.0

    summary = {
        "case": "GeLU_poly_nested",
        "params": {"I": 16, "J": 16, "U_list": [1, 2, 4, 8]},
        "rows": rows,
        "sweetspot": {
            "model_best_u": model_best_u,
            "model_best_vf": model_best_v,
            "cce_best_u": cce_best_u,
            "cce_best_vf": cce_best_v,
            "sweetspot_match": sweetspot_match,
            "cce_at_model_best": cce_at_model_best,
            "model_best_vs_cce_best_gap_pct": sweetspot_gap_pct,
        },
    }
    write_json(OUT_ROOT / "summary.json", summary)

    with (OUT_ROOT / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["U", "model_vf", "cce_vf", "abs_err", "rel_err_pct", "precision_pass", "check_note", "case_dir"],
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "U": r["U"],
                    "model_vf": r["model_vf"],
                    "cce_vf": r["cce_vf"],
                    "abs_err": r["abs_err"],
                    "rel_err_pct": f"{float(r['rel_err']) * 100:.2f}",
                    "precision_pass": r["precision_pass"],
                    "check_note": r["check_note"],
                    "case_dir": r["case_dir"],
                }
            )

    md = [
        "# Nested GeLU_poly Skill Validation",
        "",
        "| U | model_vf | cce_vf | abs_err | rel_err | precision_pass |",
        "|---:|---:|---:|---:|---:|:---:|",
    ]
    for r in rows:
        md.append(
            f"| {r['U']} | {r['model_vf']} | {r['cce_vf']} | {r['abs_err']} | {float(r['rel_err']) * 100:.2f}% | {r['precision_pass']} |"
        )
    md.extend(
        [
            "",
            "## Sweet Spot",
            f"- model_best_u = {model_best_u}",
            f"- cce_best_u = {cce_best_u}",
            f"- sweetspot_match = {sweetspot_match}",
            f"- cce_at_model_best = {cce_at_model_best}",
            f"- model_best_vs_cce_best_gap_pct = {sweetspot_gap_pct:.2f}%",
        ]
    )
    (OUT_ROOT / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"[done] {OUT_ROOT / 'summary.csv'}")


if __name__ == "__main__":
    main()
