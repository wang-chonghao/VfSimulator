#!/usr/bin/env python3
import csv
from pathlib import Path


ROOT = Path("results")
OUT = ROOT / "unroll_test" / "accuracy_report_start5.md"

MODEL_U1248 = ROOT / "unroll_test" / "sweep_u1248_start5_modelonly_20260417" / "summary.csv"
MODEL_U36 = ROOT / "unroll_test" / "sweep_u36_i96_start5_modelonly_20260417" / "summary.csv"
CCE_U1248 = ROOT / "unroll_test" / "sweep_u1248_misched0_unrollfix" / "summary.csv"
CCE_U36 = ROOT / "unroll_test" / "sweep_u123468_i96_modelonly_20260417" / "summary.csv"

CASES = [
    "GeLU",
    "GeLU_poly",
    "SiLU",
    "SwiGLU",
    "VADDS_chain64",
    "VEXP_chain8",
    "binary_ops_dominant",
    "mixed_long_short",
    "online_update",
]

NON_COMPARABLE = {
    ("GeLU", 8),
    ("online_update", 6),
    ("online_update", 8),
}
NON_COMPARABLE_TEXT = "N/A (vreg > 68, abnormal)"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pct_abs(model_v: int, cce_v: int) -> str:
    if cce_v == 0:
        return "N/A"
    return f"{abs(model_v - cce_v) / cce_v * 100:.2f}%"


def main():
    m1248 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["model_vf_end"])) for r in read_csv(MODEL_U1248)}
    m36 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["model_vf_end"])) for r in read_csv(MODEL_U36)}
    c1248 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["cce_vf_end"])) for r in read_csv(CCE_U1248)}
    c36 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["cce_vf_end"])) for r in read_csv(CCE_U36)}

    lines = []
    lines.append("# Unroll Accuracy Report (start+5, refreshed)")
    lines.append("")
    lines.append("Model results are recomputed on 2026-04-17 in `devstart5` with latest configs; CCE values reuse misched0 baselines.")
    lines.append("")

    for u in [1, 2, 4, 8]:
        lines.append(f"## U={u}, I=16/64/96")
        lines.append("| Case | I=16 Model | I=16 CCE | I=16 Error | I=64 Model | I=64 CCE | I=64 Error | I=96 Model | I=96 CCE | I=96 Error |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for c in CASES:
            row = [c]
            for i in [16, 64, 96]:
                mk = (c, i, u)
                mv = m1248.get(mk)
                cv = c1248.get(mk)
                if mv is None or cv is None:
                    row.extend(["N/A", "N/A", "N/A"])
                    continue
                err = NON_COMPARABLE_TEXT if (c, u) in NON_COMPARABLE else pct_abs(mv, cv)
                row.extend([str(mv), str(cv), err])
            lines.append(
                f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]} | {row[6]} | {row[7]} | {row[8]} | {row[9]} |"
            )
        lines.append("")

    lines.append("## Single Loop (I=96, U=1/2/3/4/6/8)")
    lines.append("")
    for c in CASES:
        lines.append(f"### {c}")
        lines.append("| Metric | U1 | U2 | U3 | U4 | U6 | U8 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        model_row = []
        cce_row = []
        err_row = []
        for u in [1, 2, 3, 4, 6, 8]:
            if u in (1, 2, 4, 8):
                mv = m1248.get((c, 96, u))
                cv = c1248.get((c, 96, u))
            else:
                mv = m36.get((c, 96, u))
                cv = c36.get((c, 96, u))
            if mv is None or cv is None:
                model_row.append("N/A")
                cce_row.append("N/A")
                err_row.append("N/A")
                continue
            model_row.append(str(mv))
            cce_row.append(str(cv))
            err_row.append(NON_COMPARABLE_TEXT if (c, u) in NON_COMPARABLE else pct_abs(mv, cv))
        lines.append("| Model VF End | " + " | ".join(model_row) + " |")
        lines.append("| CCE VF End | " + " | ".join(cce_row) + " |")
        lines.append("| Abs Error / CCE | " + " | ".join(err_row) + " |")
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
