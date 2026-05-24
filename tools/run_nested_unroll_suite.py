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
MISCHED0 = False
OOO_MODEL = "queue_level4"

CASES = [
    {"id": "GeLU_poly", "json": "unroll_test/json/GeLU_poly.json", "dsl": "unroll_test/dsl/GeLU_poly.dsl", "verify": "gelu_poly", "inputs": 2, "outputs": 1},
    {"id": "GeLU", "json": "unroll_test/json/GeLU.json", "dsl": "unroll_test/dsl/GeLU.dsl", "verify": "gelu", "inputs": 2, "outputs": 1},
    {"id": "online_update", "json": "unroll_test/json/online_update.json", "dsl": "unroll_test/dsl/online_update.dsl", "verify": "online_update", "inputs": 4, "outputs": 4},
    {"id": "SiLU", "json": "unroll_test/json/SiLU.json", "dsl": "unroll_test/dsl/SiLU.dsl", "verify": "silu", "inputs": 2, "outputs": 1},
    {"id": "SwiGLU", "json": "unroll_test/json/SwiGLU.json", "dsl": "unroll_test/dsl/SwiGLU.dsl", "verify": "swiglu", "inputs": 2, "outputs": 1},
    {"id": "VADDS_chain64", "json": "unroll_test/json/VADDS_chain64.json", "dsl": "unroll_test/dsl/VADDS_chain64.dsl", "verify": "vadds64", "inputs": 2, "outputs": 1},
    {"id": "VEXP_chain8", "json": "unroll_test/json/VEXP_chain8.json", "dsl": "unroll_test/dsl/VEXP_chain8.dsl", "verify": "vexp8", "inputs": 2, "outputs": 1},
    {"id": "mixed_long_short", "json": "unroll_test/json/mixed_long_short.json", "dsl": "unroll_test/dsl/mixed_long_short.dsl", "verify": "mixed", "inputs": 2, "outputs": 1},
    {"id": "binary_ops_dominant", "json": "unroll_test/json/binary_ops_dominant.json", "dsl": "unroll_test/dsl/binary_ops_dominant.dsl", "verify": "binary", "inputs": 2, "outputs": 1},
]

ABS_TOL = 1e-3
REL_TOL = 1e-3


def run(cmd, cwd=None, allow_fail=False):
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="ignore")
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if p.returncode != 0 and not allow_fail:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{out}")
    return out


def to_wsl_path(path: Path) -> str:
    s = str(path.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        rest = s[2:]
        return f"/mnt/{drive}{rest}"
    return s


def read_floats(path: Path):
    a = array("f")
    with path.open("rb") as f:
        a.frombytes(f.read())
    return a


def safe_div(a: float, b: float) -> float:
    if b == 0.0:
        if a == 0.0:
            return float("nan")
        return float("inf") if a > 0 else float("-inf")
    return a / b


def golden(kind, inp0, inp1, inp2=None, inp3=None):
    if kind == "gelu_poly":
        out = array("f")
        for x in inp0:
            x = float(x)
            xh = 0.5 * x
            t = 0.7071 * x
            t = min(t, 3.92)
            t = max(t, -3.92)
            t2 = t * t
            num = 0.5344 * t2 + 7.5517
            num = num * t2 + 101.62809
            num = num * t2 + 1393.8015
            num = num * t2 + 5063.7915
            num = num * t2 + 29639.3848
            num = num * t
            den = t2 + 31.2128582
            den = den * t2 + 308.569641
            den = den * t2 + 3023.12476
            den = den * t2 + 14243.3662
            den = den * t2 + 26267.2246
            y = (safe_div(num, den) + 1.0) * xh
            out.append(float(y))
        return out
    if kind == "gelu":
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
    if kind == "online_update":
        out = array("f")
        if inp2 is None or inp3 is None:
            raise ValueError("online_update golden requires input2/input3")
        for a, b, c, d in zip(inp0, inp1, inp2, inp3):
            a = float(a)
            b = float(b)
            c = float(c)
            d = float(d)
            v3 = max(a, b)
            v5 = math.exp(a - v3)
            v7 = math.exp(b - v3)
            v9 = v5 * c
            v11 = d * v7
            v12 = v9 + v11
            out.append(float(v12))
        return out
    if kind == "silu":
        out = array("f")
        for x in inp0:
            x = float(x)
            y = x / (1.0 + math.exp(-x))
            out.append(float(y))
        return out
    if kind == "swiglu":
        out = array("f")
        for a, b in zip(inp0, inp1):
            a = float(a)
            b = float(b)
            silu = a / (1.0 + math.exp(-a))
            out.append(float(silu * b))
        return out
    if kind == "vadds64":
        out = array("f")
        delta = 64 * 0.1
        for x in inp0:
            out.append(float(x + delta))
        return out
    if kind == "vexp8":
        out = array("f")
        for x in inp0:
            y = float(x)
            for _ in range(8):
                try:
                    y = math.exp(y)
                except OverflowError:
                    y = float("inf")
            out.append(float(y))
        return out
    if kind == "mixed":
        out = array("f")
        for a, b in zip(inp0, inp1):
            a = float(a)
            b = float(b)
            v2 = a * b
            v3 = math.exp(v2)
            v4 = v2 + 1.0
            v5 = safe_div(v4, v3)
            v6 = v5 + a
            v7 = v6 * 0.5
            out.append(float(v7))
        return out
    if kind == "binary":
        out = array("f")
        for a, b in zip(inp0, inp1):
            a = float(a)
            b = float(b)
            v2 = a * b
            v3 = v2 + a
            v4 = v3 - b
            v5 = max(v4, v2)
            v6 = min(v5, v3)
            v7 = safe_div(v6, v5)
            out.append(float(v7))
        return out
    raise ValueError(kind)


def cmp_arrays(golden_arr, out_arr):
    mismatch = 0
    max_abs = 0.0
    max_rel = 0.0
    n = min(len(golden_arr), len(out_arr))
    for i in range(n):
        g = float(golden_arr[i])
        o = float(out_arr[i])
        if (math.isinf(g) and math.isinf(o) and ((g > 0) == (o > 0))) or (math.isnan(g) and math.isnan(o)):
            ae = 0.0
            re = 0.0
        else:
            ae = abs(o - g)
            re = ae / max(abs(g), 1.0)
        max_abs = max(max_abs, ae)
        max_rel = max(max_rel, re)
        if ae > ABS_TOL and re > REL_TOL:
            mismatch += 1
    return mismatch, max_abs, max_rel


def patch_nested_json(src: Path, dst: Path, outer_i: int, inner_j: int, unroll_u: int):
    obj = json.loads(src.read_text(encoding="utf-8-sig"))
    body = obj["program"][0]["body"]
    nested = {
        "dtype": obj.get("dtype", "fp32"),
        "params": {"I": outer_i, "J": inner_j, "U": unroll_u},
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
    dst.write_text(json.dumps(nested, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _replace_loop_with_nested(txt: str, unroll_u: int) -> str:
    loop_pat = re.compile(
        r"for\s*\(\s*uint16_t\s+i\s*=\s*0\s*;\s*i\s*<\s*uint16_t\s*\(\s*repeat_times\s*\)\s*;\s*\+\+i\s*\)\s*\{",
        re.M,
    )
    m = loop_pat.search(txt)
    if not m:
        raise RuntimeError("cannot find single loop header for nested conversion")
    open_brace = txt.find("{", m.start())
    depth = 0
    end_pos = None
    for p in range(open_brace, len(txt)):
        ch = txt[p]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end_pos = p
                break
    if end_pos is None:
        raise RuntimeError("cannot match loop braces")
    loop_body = txt[open_brace + 1 : end_pos]
    loop_body = re.sub(r"64\s*\*\s*i", "64 * idx", loop_body)
    loop_body = re.sub(r"64\s*\*\s*\(\s*i\s*\)", "64 * idx", loop_body)

    nested = (
        "for (uint16_t i = 0; i < uint16_t(repeat_outer); ++i) {\n"
        f"            #pragma unroll({unroll_u})\n"
        "            for (uint16_t j = 0; j < uint16_t(repeat_inner); ++j) {\n"
        "                uint32_t idx = uint32_t(i) * uint32_t(repeat_inner) + uint32_t(j);\n"
        f"{loop_body}\n"
        "            }\n"
        "        }"
    )
    return txt[: m.start()] + nested + txt[end_pos + 1 :]


def patch_nested_dsl(src: Path, dst: Path, outer_i: int, inner_j: int, unroll_u: int):
    txt = src.read_text(encoding="utf-8-sig")
    total_repeat = outer_i * inner_j
    size_bytes = total_repeat * 64 * 4

    txt = re.sub(r"(?m)^\s*//\s*#pragma\s+unroll\s*\([^\)]*\)\s*\n?", "", txt)
    txt = re.sub(r"(?m)^\s*#pragma\s+unroll\s*\([^\)]*\)\s*\n?", "", txt)
    txt = re.sub(r"\bint\s+repeat_times\s*=\s*\d+\s*;", f"int repeat_outer = {outer_i};\n    int repeat_inner = {inner_j};", txt)
    txt = txt.replace("int repeat_times)", "int repeat_outer,\n    int repeat_inner)")
    txt = txt.replace(", repeat_times);", ", repeat_outer, repeat_inner);")

    txt = _replace_loop_with_nested(txt, unroll_u)
    txt = re.sub(r"\brepeat_times\s*\*\s*64\s*\*\s*4", "repeat_outer * repeat_inner * 64 * 4", txt)
    # Some DSL templates keep a fixed output copy size (16*1024), which truncates
    # nested outputs when I*J*64*4 exceeds 16KB.
    txt = re.sub(r"\b16\s*\*\s*1024\b", "repeat_outer * repeat_inner * 64 * 4", txt)

    if "ub_data_h_addr_0" in txt and "ub_data_y_addr_0" in txt:
        txt = txt.replace("ub_data_y_addr_0", "ub_data_h_addr_0")
    if "ub_data_7_addr_0" in txt and "ub_data_y_addr_0" in txt:
        txt = txt.replace("ub_data_y_addr_0", "ub_data_7_addr_0")

    # online_update has 8 UB slots; avoid overlap after total repeats changed.
    if "ub_data_0_addr_0" in txt and "ub_data_7_addr_0" in txt:
        for i in range(8):
            imm = hex(i * size_bytes)
            txt = re.sub(
                rf"(__ubuf__\s+float\s+\*ub_data_{i}_addr_0\s*=\s*\(__ubuf__\s+float\*\)get_imm\()\s*0x[0-9a-fA-F]+\s*(\);)",
                rf"\g<1>{imm}\2",
                txt,
            )

    dst.write_text(txt, encoding="utf-8")


def kernel_name_from_dsl(path: Path):
    txt = path.read_text(encoding="utf-8-sig")
    m = re.search(r'extern\s+"C"\s+__global__\s+__aicore__\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', txt)
    if not m:
        raise RuntimeError(f"cannot parse kernel name from {path}")
    return m.group(1)


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


def prepare_case(case, outer_i: int, inner_j: int, unroll_u: int, out_root: Path):
    cid = case["id"]
    tag = f"{cid}_I{outer_i}_J{inner_j}_U{unroll_u}"
    case_dir = out_root / tag
    case_dir.mkdir(parents=True, exist_ok=True)

    trace_path = case_dir / "trace_input.json"
    dsl_path = case_dir / f"{tag}.dsl"
    patch_nested_json(ROOT / case["json"], trace_path, outer_i, inner_j, unroll_u)
    patch_nested_dsl(ROOT / case["dsl"], dsl_path, outer_i, inner_j, unroll_u)
    return tag, case_dir, trace_path, dsl_path


def run_one(case, outer_i: int, inner_j: int, unroll_u: int, out_root: Path, run_cce=True):
    tag, case_dir, trace_path, dsl_path = prepare_case(case, outer_i, inner_j, unroll_u, out_root)
    model_out_dir = case_dir / "model"
    out = run(["python", str(ROOT / "main.py"), "--trace", str(trace_path), "--out_dir", str(model_out_dir), "--ooo-model", OOO_MODEL])
    m = re.search(r"VF end cycle \(with drain\)\s*=\s*(\d+)", out)
    if not m:
        raise RuntimeError(f"cannot parse model vf_end for {tag}")
    model_vf_end = int(m.group(1))

    cce_vf_end = None
    precision_pass = None
    check_note = ""

    if run_cce:
        kname = kernel_name_from_dsl(dsl_path)
        total = outer_i * inner_j * 64
        stem = tag

        build_cmd = [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            f"cd /mnt/d/VfSimulator && {'CCEC_EXTRA_FLAGS=\"-mllvm -cce-aicore-vec-misched=0\" ' if MISCHED0 else ''}"
            f"bash ascend_runner/current/build_native_simexec.sh {to_wsl_path(dsl_path)} {stem}",
        ]
        run(build_cmd)

        run_cmd = [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            f"cd /mnt/d/VfSimulator && bash ascend_runner/current/run_native_simexec.sh "
            f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_simexec "
            f"/mnt/d/VfSimulator/ascend_runner/build/{stem}_native_simexec/{stem}_mix.o {kname} "
            f"{case['inputs']} {case['outputs']} {total}",
        ]
        run(run_cmd, allow_fail=True)

        local_dump = case_dir / "cce_dump"
        local_dump.mkdir(exist_ok=True)
        src_dir = f"/home/lenovo/msprof_run/{stem}_native_simexec"
        dst_dir = to_wsl_path(local_dump)
        needed = ["core0.veccore0.instr_log.dump", "core0.veccore0.instr_popped_log.dump", "input0.bin", "input1.bin", "input2.bin", "input3.bin", "output0.bin"]
        for fn in needed:
            copy_one = ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", f"if [ -f {src_dir}/{fn} ]; then cp -f {src_dir}/{fn} {dst_dir}/{fn}; fi"]
            run(copy_one, allow_fail=True)

        cce_vf_end = parse_cce_vf_end(
            local_dump / "core0.veccore0.instr_popped_log.dump",
            local_dump / "core0.veccore0.instr_log.dump",
        )
        inp0 = read_floats(local_dump / "input0.bin")
        inp1 = read_floats(local_dump / "input1.bin") if (local_dump / "input1.bin").exists() else array("f", [0.0] * len(inp0))
        inp2 = read_floats(local_dump / "input2.bin") if (local_dump / "input2.bin").exists() else None
        inp3 = read_floats(local_dump / "input3.bin") if (local_dump / "input3.bin").exists() else None
        out0 = read_floats(local_dump / "output0.bin")
        g = golden(case["verify"], inp0, inp1, inp2, inp3)
        mismatch, max_abs, max_rel = cmp_arrays(g, out0)
        precision_pass = mismatch == 0
        check_note = f"mismatch={mismatch}, max_abs={max_abs:.6g}, max_rel={max_rel:.6g}"

    abs_err = None if cce_vf_end is None else abs(model_vf_end - cce_vf_end)
    rel_err = None if cce_vf_end in (None, 0) else abs_err / cce_vf_end
    return {
        "case": case["id"],
        "I": outer_i,
        "J": inner_j,
        "U": unroll_u,
        "model_vf_end": model_vf_end,
        "cce_vf_end": cce_vf_end,
        "abs_err": abs_err,
        "rel_err": rel_err,
        "precision_pass": precision_pass,
        "check_note": check_note,
        "case_dir": str(case_dir.resolve()),
    }


def sweetspot_stats(rows):
    by_case = {}
    for r in rows:
        if r.get("cce_vf_end") is None:
            continue
        by_case.setdefault(r["case"], []).append(r)
    strict = 0
    tolerant = 0
    total = len(by_case)
    detail = []
    for cid, grp in sorted(by_case.items()):
        m_best = min(grp, key=lambda x: x["model_vf_end"])
        c_best = min(grp, key=lambda x: x["cce_vf_end"])
        strict_hit = m_best["U"] == c_best["U"]
        chosen_cce = m_best["cce_vf_end"]
        best_cce = c_best["cce_vf_end"]
        tol_hit = (chosen_cce - best_cce) / best_cce <= 0.05
        strict += 1 if strict_hit else 0
        tolerant += 1 if tol_hit else 0
        detail.append(
            {
                "case": cid,
                "model_best_u": m_best["U"],
                "cce_best_u": c_best["U"],
                "strict_hit": strict_hit,
                "tol5_hit": tol_hit,
                "model_pick_cce_vf": chosen_cce,
                "cce_best_vf": best_cce,
            }
        )
    return {
        "cases_with_cce": total,
        "strict_hit_count": strict,
        "strict_hit_rate": (None if total == 0 else strict / total),
        "tol5_hit_count": tolerant,
        "tol5_hit_rate": (None if total == 0 else tolerant / total),
        "detail": detail,
    }


def write_report(rows, stats, out_root: Path):
    md = out_root / "accuracy_report.md"
    lines = []
    lines.append("# Nested Unroll Accuracy Report (I=2, J=48)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Cases with CCE: {stats['cases_with_cce']}")
    lines.append(f"- Strict sweetspot hit: {stats['strict_hit_count']} / {stats['cases_with_cce']}")
    if stats["strict_hit_rate"] is not None:
        lines.append(f"- Strict hit rate: {stats['strict_hit_rate'] * 100:.2f}%")
    lines.append(f"- 5% tolerant sweetspot hit: {stats['tol5_hit_count']} / {stats['cases_with_cce']}")
    if stats["tol5_hit_rate"] is not None:
        lines.append(f"- 5% tolerant hit rate: {stats['tol5_hit_rate'] * 100:.2f}%")
    lines.append("")
    lines.append("## Full Table")
    lines.append("")
    lines.append("| case | U | model_vf_end | cce_vf_end | abs_err | rel_err | precision_pass | check_note | error_reason |")
    lines.append("|---|---:|---:|---:|---:|---:|---|---|")
    for r in sorted(rows, key=lambda x: (x["case"], x["U"])):
        rel = "" if r["rel_err"] is None else f"{r['rel_err'] * 100:.2f}%"
        lines.append(
            f"| {r['case']} | {r['U']} | {r['model_vf_end']} | {r['cce_vf_end']} | {r['abs_err']} | {rel} | {r['precision_pass']} | {r['check_note']} | {r.get('error_reason','')} |"
        )
    lines.append("")
    lines.append("## Sweetspot Detail")
    lines.append("")
    lines.append("| case | model_best_u | cce_best_u | strict_hit | tol5_hit | model_pick_cce_vf | cce_best_vf |")
    lines.append("|---|---:|---:|---|---|---:|---:|")
    for d in stats["detail"]:
        lines.append(
            f"| {d['case']} | {d['model_best_u']} | {d['cce_best_u']} | {d['strict_hit']} | {d['tol5_hit']} | {d['model_pick_cce_vf']} | {d['cce_best_vf']} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outer-i", type=int, default=2)
    ap.add_argument("--inner-j", type=int, default=48)
    ap.add_argument("--unrolls", nargs="+", type=int, default=[1, 2, 3, 4, 6, 8])
    ap.add_argument("--out-dir", default="results/nested_unroll_test/I2_J48_u123468")
    ap.add_argument("--skip-cce", action="store_true")
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--misched0", action="store_true")
    ap.add_argument("--cases", nargs="+", default=None, help="optional subset of case ids")
    args = ap.parse_args()

    global MISCHED0
    MISCHED0 = bool(args.misched0)
    out_root = ROOT / args.out_dir
    out_root.mkdir(parents=True, exist_ok=True)
    latest_rows_path = out_root / "latest_rows.json"

    selected_cases = CASES if not args.cases else [c for c in CASES if c["id"] in set(args.cases)]
    if args.cases and len(selected_cases) != len(set(args.cases)):
        known = {c["id"] for c in CASES}
        missing = [x for x in args.cases if x not in known]
        raise RuntimeError(f"unknown case ids: {missing}")

    gen_records = []
    for case in selected_cases:
        for u in args.unrolls:
            tag, case_dir, trace_path, dsl_path = prepare_case(case, args.outer_i, args.inner_j, u, out_root)
            gen_records.append(
                {
                    "case": case["id"],
                    "U": u,
                    "tag": tag,
                    "case_dir": str(case_dir.resolve()),
                    "dsl": str(dsl_path.resolve()),
                    "trace_json": str(trace_path.resolve()),
                }
            )
    (out_root / "generated_paths.json").write_text(json.dumps(gen_records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[generated] {len(gen_records)} cases")
    print(f"[paths] {out_root / 'generated_paths.json'}")
    if args.generate_only:
        return

    rows = []
    seen = set()
    if latest_rows_path.exists():
        try:
            rows = json.loads(latest_rows_path.read_text(encoding="utf-8"))
            for r in rows:
                seen.add((r.get("case"), int(r.get("U"))))
            print(f"[resume] loaded {len(rows)} existing rows from {latest_rows_path}", flush=True)
        except Exception:
            rows = []
            seen = set()
            print("[resume] failed to load latest_rows.json, start fresh", flush=True)
    for case in selected_cases:
        for u in args.unrolls:
            if (case["id"], u) in seen:
                print(f"[skip] {case['id']} I={args.outer_i} J={args.inner_j} U={u}", flush=True)
                continue
            print(f"[run] {case['id']} I={args.outer_i} J={args.inner_j} U={u}", flush=True)
            try:
                row = run_one(case, args.outer_i, args.inner_j, u, out_root, run_cce=not args.skip_cce)
                row["error_reason"] = ""
            except Exception as e:
                row = {
                    "case": case["id"],
                    "I": args.outer_i,
                    "J": args.inner_j,
                    "U": u,
                    "model_vf_end": None,
                    "cce_vf_end": None,
                    "abs_err": None,
                    "rel_err": None,
                    "precision_pass": False,
                    "check_note": "RUN_FAILED",
                    "error_reason": str(e).replace("\n", " | "),
                    "case_dir": str((out_root / f"{case['id']}_I{args.outer_i}_J{args.inner_j}_U{u}").resolve()),
                }
                print(f"[error] {case['id']} U={u}: {e}", flush=True)
            rows.append(row)
            seen.add((case["id"], u))
            latest_rows_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    stats = sweetspot_stats(rows)
    (out_root / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_root / "sweetspot_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    csv_path = out_root / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["case", "U", "model_vf_end", "cce_vf_end", "abs_err", "rel_err", "precision_pass", "check_note", "error_reason", "case_dir"],
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "case": r["case"],
                    "U": r["U"],
                    "model_vf_end": r["model_vf_end"],
                    "cce_vf_end": r["cce_vf_end"],
                    "abs_err": r["abs_err"],
                    "rel_err": r["rel_err"],
                    "precision_pass": r["precision_pass"],
                    "check_note": r["check_note"],
                    "error_reason": r.get("error_reason", ""),
                    "case_dir": r["case_dir"],
                }
            )
    write_report(rows, stats, out_root)
    print(f"[done] {csv_path}")


if __name__ == "__main__":
    main()
