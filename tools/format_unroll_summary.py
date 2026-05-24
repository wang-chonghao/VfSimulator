#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_rows(csv_path: Path):
    with csv_path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fmt_pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _iter_insts(node):
    if isinstance(node, list):
        for x in node:
            yield from _iter_insts(x)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "inst":
        yield node
    body = node.get("body")
    if isinstance(body, list):
        for x in body:
            yield from _iter_insts(x)


def _base_vreg_count(trace_path: Path) -> int:
    obj = json.loads(trace_path.read_text(encoding="utf-8-sig"))
    vset = set()
    for inst in _iter_insts(obj.get("program", [])):
        for arrk in ("dst", "src"):
            arr = inst.get(arrk, [])
            if not isinstance(arr, list):
                continue
            for tok in arr:
                if isinstance(tok, str) and re.fullmatch(r"V\d+", tok):
                    vset.add(tok)
    return len(vset)


def _load_case_trace_map():
    manifest = ROOT / "unroll_test" / "cases_manifest.json"
    obj = json.loads(manifest.read_text(encoding="utf-8"))
    mp = {}
    for c in obj.get("cases", []):
        cid = c.get("id")
        jrel = c.get("json")
        if cid and jrel:
            mp[cid] = ROOT / "unroll_test" / jrel
    return mp


def _norm_case_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _resolve_case_trace(case_name: str, case_trace_map: dict):
    if case_name in case_trace_map:
        return case_trace_map[case_name]
    n = _norm_case_name(case_name)
    for k, v in case_trace_map.items():
        if _norm_case_name(k) == n:
            return v
    for _, v in case_trace_map.items():
        if _norm_case_name(v.stem) == n:
            return v
    return None


def build_report(rows, title: str, case_trace_map: dict) -> str:
    cases = sorted({r["case"] for r in rows})
    iters = sorted({int(r["I"]) for r in rows})
    unrolls = sorted({int(r["U"]) for r in rows})
    base_vregs = {}
    for c in cases:
        p = _resolve_case_trace(c, case_trace_map)
        if p is not None and p.exists():
            base_vregs[c] = _base_vreg_count(p)

    pass_cnt = sum(1 for r in rows if r.get("precision_pass") == "True")
    comparable_abs = []
    non_comparable = 0
    for r in rows:
        c = r["case"]
        u = int(r["U"])
        b = base_vregs.get(c, 0)
        if b * u > 68:
            non_comparable += 1
            continue
        comparable_abs.append(abs(float(r["rel_err"])))

    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- 样本数: `{len(rows)}`")
    lines.append(f"- 精度校验通过: `{pass_cnt}/{len(rows)}`")
    if comparable_abs:
        lines.append(f"- 可比较样本平均绝对误差: `{sum(comparable_abs)/len(comparable_abs)*100:.2f}%`")
        lines.append(f"- 可比较样本最大绝对误差: `{max(comparable_abs)*100:.2f}%`")
    else:
        lines.append("- 可比较样本平均绝对误差: `N/A`")
        lines.append("- 可比较样本最大绝对误差: `N/A`")
    lines.append(f"- 不可比较样本(估计虚拟寄存器>68): `{non_comparable}`")
    lines.append("")

    for u in unrolls:
        sub = [r for r in rows if int(r["U"]) == u]
        lines.append(f"## U={u}")
        lines.append("")
        lines.append("| Case | I=16 | I=64 | I=96 |")
        lines.append("|---|---:|---:|---:|")
        for c in cases:
            vals = []
            b = base_vregs.get(c, 0)
            for i in iters:
                hit = [r for r in sub if r["case"] == c and int(r["I"]) == i]
                if not hit:
                    vals.append("N/A")
                    continue
                r = hit[0]
                if b * u > 68:
                    vals.append("不可比较(>68 vregs)")
                else:
                    rel = fmt_pct(float(r["rel_err"]))
                    ok = "PASS" if r.get("precision_pass") == "True" else "FAIL"
                    vals.append(f"{rel} ({ok})")
            lines.append(f"| {c} | {vals[0]} | {vals[1]} | {vals[2]} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def pick_default_source() -> Path:
    preferred = ROOT / "results" / "unroll_test" / "sweep_u1248_misched0_unrollfix" / "summary.csv"
    if preferred.exists():
        return preferred
    fallback = ROOT / "results" / "unroll_test" / "sweep_u248" / "summary.csv"
    if fallback.exists():
        return fallback
    return preferred


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-csv", type=Path, default=pick_default_source())
    ap.add_argument("--out-md", type=Path, default=ROOT / "results" / "unroll_test" / "accuracy_report.md")
    args = ap.parse_args()

    source = args.source_csv if args.source_csv.is_absolute() else (ROOT / args.source_csv)
    out_md = args.out_md if args.out_md.is_absolute() else (ROOT / args.out_md)

    rows = load_rows(source)
    rows_u1 = [r for r in rows if int(r["U"]) == 1]
    rows_u248 = [r for r in rows if int(r["U"]) in (2, 4, 8)]
    case_trace_map = _load_case_trace_map()

    txt = []
    if rows_u1:
        txt.append(build_report(rows_u1, "Unroll 精度报告（U=1）", case_trace_map))
    if rows_u248:
        txt.append(build_report(rows_u248, "Unroll 精度报告（U=2/4/8）", case_trace_map))
    out_md.write_text("\n".join(txt), encoding="utf-8")
    print(out_md)


if __name__ == "__main__":
    main()
