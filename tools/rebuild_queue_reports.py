#!/usr/bin/env python3
import csv
import json
from pathlib import Path


ROOT = Path("results")

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


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def pct_abs(model_v: int, cce_v: int) -> str:
    if cce_v == 0:
        return "N/A"
    return f"{abs(model_v - cce_v) / cce_v * 100:.2f}%"


def build_accuracy_report(label: str, model_u1248: Path, model_u36: Path, out_path: Path):
    cce_u1248 = ROOT / "unroll_test" / "sweep_u1248_misched0_unrollfix" / "summary.csv"
    cce_u36 = ROOT / "unroll_test" / "sweep_u123468_i96_modelonly_20260417" / "summary.csv"
    nested = ROOT / "nested_unroll_test" / "I2_J48_u123468" / "summary.csv"

    m1248 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["model_vf_end"])) for r in read_csv(model_u1248)}
    m36 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["model_vf_end"])) for r in read_csv(model_u36)}
    c1248 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["cce_vf_end"])) for r in read_csv(cce_u1248)}
    c36 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["cce_vf_end"])) for r in read_csv(cce_u36)}
    nrows = {(r["case"], int(r["U"])): (int(float(r["model_vf_end"])), int(float(r["cce_vf_end"]))) for r in read_csv(nested)}

    lines = []
    lines.append(f"# Unroll Accuracy Report ({label})")
    lines.append("")
    lines.append(f"Model flags: `--ooo-model {label}` (branch default keeps start+5 recycle).")
    lines.append("CCE times are reused from existing baselines.")
    lines.append("")

    for u in [1, 2, 4, 8]:
        lines.append(f"## U={u}, I=16/64/96")
        lines.append("| Case | I=16 Model | I=16 CCE | I=16 Error | I=64 Model | I=64 CCE | I=64 Error | I=96 Model | I=96 CCE | I=96 Error |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for c in CASES:
            vals = []
            for i in [16, 64, 96]:
                mv = m1248.get((c, i, u))
                cv = c1248.get((c, i, u))
                if mv is None or cv is None:
                    vals.extend(["N/A", "N/A", "N/A"])
                else:
                    err = NON_COMPARABLE_TEXT if (c, u) in NON_COMPARABLE else pct_abs(mv, cv)
                    vals.extend([str(mv), str(cv), err])
            lines.append(
                f"| {c} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} | {vals[4]} | {vals[5]} | {vals[6]} | {vals[7]} | {vals[8]} |"
            )
        lines.append("")

    lines.append("## Single Loop (I=96, U=1/2/3/4/6/8)")
    lines.append("")
    for c in CASES:
        lines.append(f"### {c}")
        lines.append("| Metric | U1 | U2 | U3 | U4 | U6 | U8 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        model_row, cce_row, err_row = [], [], []
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
            else:
                model_row.append(str(mv))
                cce_row.append(str(cv))
                err_row.append(NON_COMPARABLE_TEXT if (c, u) in NON_COMPARABLE else pct_abs(mv, cv))
        lines.append("| Model VF End | " + " | ".join(model_row) + " |")
        lines.append("| CCE VF End | " + " | ".join(cce_row) + " |")
        lines.append("| Abs Error / CCE | " + " | ".join(err_row) + " |")
        lines.append("")

    lines.append("## Nested Loop (I=2, J=48)")
    lines.append("")
    for c in CASES:
        lines.append(f"### {c}")
        lines.append("| Metric | U1 | U2 | U3 | U4 | U6 | U8 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        model_row, cce_row, err_row = [], [], []
        for u in [1, 2, 3, 4, 6, 8]:
            pair = nrows.get((c, u))
            if pair is None:
                model_row.append("N/A")
                cce_row.append("N/A")
                err_row.append("N/A")
            else:
                mv, cv = pair
                model_row.append(str(mv))
                cce_row.append(str(cv))
                err_row.append(NON_COMPARABLE_TEXT if (c, u) in NON_COMPARABLE else pct_abs(mv, cv))
        lines.append("| Model VF End | " + " | ".join(model_row) + " |")
        lines.append("| CCE VF End | " + " | ".join(cce_row) + " |")
        lines.append("| Abs Error / CCE | " + " | ".join(err_row) + " |")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def rebuild_precision_compare():
    base = read_json(Path("regression_suite/cases/baseline_consumer_done.json")).get("cases", {})
    start5 = read_json(Path("results/regression_suite/start5_eval/current_metrics.json")).get("cases", {})
    q2 = read_json(Path("results/regression_suite/queue_level2_latest/current_metrics.json")).get("cases", {})
    q3 = read_json(Path("results/regression_suite/queue_level3_latest/current_metrics.json")).get("cases", {})

    order = list(base.keys())
    # Keep any extra rows from the other runs too.
    for src in (start5, q2, q3):
        for k in src.keys():
            if k not in order:
                order.append(k)

    lines = []
    lines.append("| case | baseline(consumer-done) | start+5 | start+5+queue_level2 | start+5+queue_level3 |")
    lines.append("|---|---:|---:|---:|---:|")
    for cid in order:
        vals = []
        for src in (base, start5, q2, q3):
            case = src.get(cid, {})
            rel = case.get("error_to_cce_rel")
            vals.append("NA" if rel is None else f"{float(rel) * 100:.2f}%")
        lines.append(f"| {cid} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} |")
    out = Path("regression_suite/reports/precision_compare_3modes.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    build_accuracy_report(
        "queue_level2",
        ROOT / "unroll_test" / "sweep_u1248_start5_queue_level2_modelonly_20260417" / "summary.csv",
        ROOT / "unroll_test" / "sweep_u36_i96_start5_queue_level2_modelonly_20260417" / "summary.csv",
        ROOT / "unroll_test" / "accuracy_report_queue_v2.md",
    )
    build_accuracy_report(
        "queue_level3",
        ROOT / "unroll_test" / "sweep_u1248_start5_queue_level3_modelonly_20260417" / "summary.csv",
        ROOT / "unroll_test" / "sweep_u36_i96_start5_queue_level3_modelonly_20260417" / "summary.csv",
        ROOT / "unroll_test" / "accuracy_report_queue_v3.md",
    )
    rebuild_precision_compare()
    print("queue reports rebuilt")


if __name__ == "__main__":
    main()
