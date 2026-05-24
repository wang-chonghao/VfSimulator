#!/usr/bin/env python3
import csv
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


def parse_kernel_name(dsl: Path):
    txt = dsl.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'extern\s+"C"\s+__global__\s+__aicore__\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', txt)
    if not m:
        raise RuntimeError(f"cannot parse kernel name from {dsl}")
    return m.group(1)


def build_case_dsl(case_dir: Path, name: str, I: int, J: int, chain_len: int):
    if chain_len <= 0:
        raise ValueError("chain_len must be >=1")

    decls = []
    for i in range(1, chain_len + 2):
        decls.append(f"        vector_f32 vec_{i};")

    body = []
    body.append("                vlds(vec_1, memA, 64 * idx, NORM);")
    if chain_len == 1:
        body.append("                vsts(vec_1, memB, 64 * idx, NORM_B32, pat_all_b32);")
    else:
        for i in range(2, chain_len + 2):
            body.append(f"                vadds(vec_{i}, vec_{i-1}, 1.0, pat_all_b32);")
        body.append(f"                vsts(vec_{chain_len + 1}, memB, 64 * idx, NORM_B32, pat_all_b32);")

    dsl = f"""#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif

__attribute__((always_inline)) inline [aicore] void micro_simd_ub(
    __ubuf__ float *memA,
    __ubuf__ float *memB,
    int repeat_outer,
    int repeat_inner) {{

    __VEC_SCOPE__ {{
        vector_bool pat_all_b32 = pset_b32(PAT_ALL);
{chr(10).join(decls)}
        for (uint16_t i = 0; i < uint16_t(repeat_outer); ++i) {{
            for (uint16_t j = 0; j < uint16_t(repeat_inner); ++j) {{
                uint32_t idx = uint32_t(i) * uint32_t(repeat_inner) + uint32_t(j);
{chr(10).join(body)}
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
    micro_simd_ub(ub_data_x_addr_0, ub_data_y_addr_0, repeat_outer, repeat_inner);
    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(
        (__gm__ float*)for_loop0_output0,
        (__ubuf__ float*)ub_data_y_addr_0,
        0, 1, repeat_outer * repeat_inner * 64 * 4, 0, 0, 0);
    pipe_barrier(PIPE_ALL);
}}
"""
    dsl_path = case_dir / f"{name}.dsl"
    dsl_path.write_text(dsl, encoding="utf-8")
    return dsl_path


def build_case_json(case_dir: Path, I: int, J: int, chain_len: int):
    body = [{"type": "inst", "op": "VLD", "dst": ["V1"], "src": ["memA"]}]
    if chain_len == 1:
        body.append({"type": "inst", "op": "VST", "dst": ["memB"], "src": ["V1"]})
    else:
        for i in range(2, chain_len + 2):
            body.append({"type": "inst", "op": "VADDS", "dst": [f"V{i}"], "src": [f"V{i-1}"]})
        body.append({"type": "inst", "op": "VST", "dst": ["memB"], "src": [f"V{chain_len + 1}"]})

    obj = {
        "dtype": "fp32",
        "params": {"I": I, "J": J, "U": 1},
        "program": [
            {"type": "loop", "iters": "I", "unroll": 1, "body": [
                {"type": "loop", "iters": "J", "unroll": "U", "body": body}
            ]}
        ],
    }
    p = case_dir / "trace_input.json"
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p, len(body)


def parse_inner_vloop_and_vld(popped_path: Path):
    lines = popped_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    inner = []
    vld = []
    for ln in lines:
        mcy = re.search(r"\[(\d{8})\]", ln)
        if not mcy:
            continue
        cy = int(mcy.group(1))
        if "RV_VLOOPv2" in ln and "layer: 0001" in ln:
            inner.append(cy)
        if "RV_VLD" in ln:
            vld.append(cy)
    first_vld_after = []
    for c in inner:
        nxt = next((x for x in vld if x >= c), None)
        first_vld_after.append(nxt)
    return inner, first_vld_after


def parse_idu_blocks(idu_path: Path):
    lines = idu_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out = []
    for ln in lines:
        if "IDU_BLOCK" not in ln:
            continue
        m = re.search(r"\[(\d{10})\]", ln)
        if not m:
            m = re.search(r"\[(\d{8})\]", ln)
        if not m:
            continue
        cy = int(m.group(1))
        mr = re.search(r"REASON:([^\r\n]+)", ln)
        reason = mr.group(1).strip() if mr else ""
        out.append((cy, reason))
    return out


def run_one(exp_name: str, I: int, J: int, chain_len: int, out_root: Path):
    tag = f"{exp_name}_I{I}_J{J}"
    case_dir = out_root / tag
    case_dir.mkdir(parents=True, exist_ok=True)

    dsl = build_case_dsl(case_dir, tag, I, J, chain_len)
    trace_json, body_len = build_case_json(case_dir, I, J, chain_len)

    model_dir = case_dir / "model"
    run(["python", str(ROOT / "main.py"), "--trace", str(trace_json), "--out_dir", str(model_dir), "--ooo-model", "consumer-done"])

    stem = tag
    kname = parse_kernel_name(dsl)
    total = I * J * 64
    run(["wsl", "-d", "Ubuntu", "--", "bash", "-lc",
         f"cd /mnt/d/VfSimulator && CCEC_EXTRA_FLAGS=\"-mllvm -cce-aicore-vec-misched=0\" bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(dsl)} {stem}"])
    run(["wsl", "-d", "Ubuntu", "--", "bash", "-lc",
         f"cd /mnt/d/VfSimulator && bash ascend_runner/current/run_native_simexec.sh /mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_simexec /mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_mix.o {kname} 2 1 {total}"],
        allow_fail=True)

    cce_dir = case_dir / "cce_dump"
    cce_dir.mkdir(exist_ok=True)
    src = f"/home/lenovo/msprof_run/{stem}_native_simexec"
    dst = to_wsl_path(cce_dir)
    for fn in (
        "core0.veccore0.instr_popped_log.dump",
        "core0.veccore0.rvec.IDU.dump",
    ):
        run(["wsl", "-d", "Ubuntu", "--", "bash", "-lc", f"if [ -f {src}/{fn} ]; then cp -f {src}/{fn} {dst}/{fn}; fi"], allow_fail=True)

    inner, cce_first = parse_inner_vloop_and_vld(cce_dir / "core0.veccore0.instr_popped_log.dump")
    idu_blocks = parse_idu_blocks(cce_dir / "core0.veccore0.rvec.IDU.dump")

    model_vloop = [json.loads(x) for x in (model_dir / "vloop_trace.json").read_text(encoding="utf-8").splitlines()]
    model_loop1 = [int(x["start_cycle"]) for x in model_vloop if x.get("loop_id") == "loop1"]
    starts = [json.loads(x) for x in (model_dir / "start_by_cycle.json").read_text(encoding="utf-8").splitlines()]
    start_map = {int(x["inst_id"]): int(x["cy"]) for x in starts}
    model_first = [start_map.get(i * J * body_len) for i in range(I)]

    rows = []
    for idx in range(max(0, len(inner) - 1)):
        a = inner[idx]
        b = inner[idx + 1]
        seg = [r for cy, r in idu_blocks if a <= cy < b]
        rows.append({
            "idx": idx,
            "gap": b - a,
            "no_vreg": sum(1 for r in seg if "OOO no avail phy vreg" in r),
            "vec_dispatch": sum(1 for r in seg if "VEC dispatch number reached" in r),
            "total_block": len(seg),
        })

    return {
        "case": tag,
        "I": I,
        "J": J,
        "chain_len": chain_len,
        "cce_inner_vloop": inner,
        "cce_first_vld_after_inner": cce_first,
        "model_loop1_start": model_loop1,
        "model_first_vld_outer": model_first,
        "interval_block_stats": rows,
        "workdir": str(case_dir.resolve()),
    }


def main():
    out_root = ROOT / "results" / "nested_loop_test" / "vloop_micro"
    out_root.mkdir(parents=True, exist_ok=True)

    exps = [
        ("micro_ld_st", 1),
        ("micro_ld_adds_st", 2),
        ("micro_ld_adds32_st", 33),
    ]
    pairs = [(16, 3), (16, 16)]

    all_rows = []
    for exp_name, chain_len in exps:
        for I, J in pairs:
            print(f"[run] {exp_name} I={I} J={J}", flush=True)
            all_rows.append(run_one(exp_name, I, J, chain_len, out_root))

    out_json = out_root / "vloop_micro_summary.json"
    out_json.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out_csv = out_root / "vloop_micro_summary.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "case", "I", "J", "chain_len", "avg_gap_cce", "avg_gap_model", "avg_no_vreg", "avg_vec_dispatch", "workdir"
        ])
        w.writeheader()
        for r in all_rows:
            c = r["cce_inner_vloop"]
            m = r["model_loop1_start"]
            c_gap = [c[i + 1] - c[i] for i in range(len(c) - 1)] if len(c) > 1 else []
            m_gap = [m[i + 1] - m[i] for i in range(len(m) - 1)] if len(m) > 1 else []
            stats = r["interval_block_stats"]
            w.writerow({
                "case": r["case"],
                "I": r["I"],
                "J": r["J"],
                "chain_len": r["chain_len"],
                "avg_gap_cce": (sum(c_gap) / len(c_gap)) if c_gap else 0.0,
                "avg_gap_model": (sum(m_gap) / len(m_gap)) if m_gap else 0.0,
                "avg_no_vreg": (sum(x["no_vreg"] for x in stats) / len(stats)) if stats else 0.0,
                "avg_vec_dispatch": (sum(x["vec_dispatch"] for x in stats) / len(stats)) if stats else 0.0,
                "workdir": r["workdir"],
            })

    print(f"[done] {out_csv}")


if __name__ == "__main__":
    main()

