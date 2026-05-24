#!/usr/bin/env python3
import csv
from pathlib import Path


ROOT = Path(".")

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


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pct_abs(model_v: int, cce_v: int) -> str:
    if cce_v == 0:
        return "N/A"
    return f"{abs(model_v - cce_v) / cce_v * 100:.2f}%"


def main():
    model_u1248 = ROOT / "results" / "unroll_test" / "sweep_u1248_queue_level4_vregpass_perexq7_20260421" / "summary.csv"
    model_u36 = ROOT / "results" / "unroll_test" / "sweep_u36_queue_level4_vregpass_perexq7_20260421" / "summary.csv"
    cce_u1248 = ROOT / "results" / "unroll_test" / "sweep_u1248_misched0_unrollfix" / "summary.csv"
    cce_u36 = ROOT / "results" / "unroll_test" / "sweep_u123468_i96_modelonly_20260417" / "summary.csv"
    out_path = ROOT / "results" / "unroll_test" / "accuracy_report_queue_v4_vregpass_perexq7_20260421.md"

    m1248 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["model_vf_end"])) for r in read_csv(model_u1248)}
    m36 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["model_vf_end"])) for r in read_csv(model_u36)}
    c1248 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["cce_vf_end"])) for r in read_csv(cce_u1248)}
    c36 = {(r["case"], int(r["I"]), int(r["U"])): int(float(r["cce_vf_end"])) for r in read_csv(cce_u36)}

    lines = []
    lines.append("# Unroll Accuracy Report (queue_level4 + vreg pass, per-EXQ inflight cap=7, 2026-04-21)")
    lines.append("")
    lines.append("Model flags: `--ooo-model queue_level4 --enable-vreg-live-range-normalization`.")
    lines.append("Register recycle uses the current branch default `start+5` rule path (effective `consumer start + 4` release timing in code).")
    lines.append("Additional modeled queue/resource constraints for this `queue_level4` variant:")
    lines.append("- finite `SHQ depth = 58`")
    lines.append("- finite `EXQ depth = 26`")
    lines.append("- `EXQ recv delay = 1`")
    lines.append("- `SHQ -> EXQ` admit limit = `1` per EXQ port per cycle")
    lines.append("- per-EXQ in-flight issue cap = `7`")
    lines.append("- the in-flight cap is checked at `EXQ -> EXU issue`, not at `SHQ -> EXQ` handoff")
    lines.append("- `EXQ` capacity does **not** count already-issued inflight EXU ops")
    lines.append("CCE times are reused from existing refreshed baselines in `accuracy_report.md`.")
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
                    vals.extend([str(mv), str(cv), pct_abs(mv, cv)])
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
            else:
                model_row.append(str(mv))
                cce_row.append(str(cv))
                err_row.append(pct_abs(mv, cv))
        lines.append("| Model VF End | " + " | ".join(model_row) + " |")
        lines.append("| CCE VF End | " + " | ".join(cce_row) + " |")
        lines.append("| Abs Error / CCE | " + " | ".join(err_row) + " |")
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
