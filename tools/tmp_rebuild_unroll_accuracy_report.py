#!/usr/bin/env python3
import csv
from pathlib import Path


ROOT = Path("results")
OUT = ROOT / "unroll_test" / "accuracy_report.md"

SINGLE_A = ROOT / "unroll_test" / "sweep_u1248_misched0_unrollfix" / "summary.csv"
SINGLE_B = ROOT / "unroll_test" / "sweep_u123468_i96" / "summary.csv"
NESTED = ROOT / "nested_unroll_test" / "I2_J48_u123468" / "summary.csv"

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

# 已知“虚拟寄存器命名空间超过68”风险位：base_vregs * U > 68
NON_COMPARABLE = {
    ("GeLU", 8),
    ("online_update", 6),
    ("online_update", 8),
}

NON_COMPARABLE_TEXT = "不可比较(虚拟寄存器>68风险)"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pct_abs(v: str) -> str:
    return f"{abs(float(v)) * 100:.2f}%"


def is_non_comparable(case: str, u: int) -> bool:
    return (case, u) in NON_COMPARABLE


def build_single_sections(rows_a):
    idx = {(r["case"], int(r["I"]), int(r["U"])): r for r in rows_a}
    lines = []
    lines.append("# Unroll 精度报告")
    lines.append("")
    for u in [1, 2, 4, 8]:
        lines.append(f"## U={u}，I=16/64/96")
        lines.append("| Case | I=16 |  |  | I=64 |  |  | I=96 |  |  |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        lines.append("|  | 模型 | CCE | 精度 | 模型 | CCE | 精度 | 模型 | CCE | 精度 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for c in CASES:
            vals = []
            for i in [16, 64, 96]:
                r = idx.get((c, i, u))
                if r is None:
                    vals.extend(["N/A", "N/A", "N/A"])
                else:
                    model_v = str(int(float(r["model_vf_end"])))
                    cce_v = str(int(float(r["cce_vf_end"])))
                    if is_non_comparable(c, u):
                        acc = NON_COMPARABLE_TEXT
                    else:
                        acc = pct_abs(r["rel_err"])
                    vals.extend([model_v, cce_v, acc])
            lines.append(
                f"| {c} | "
                f"{vals[0]} | {vals[1]} | {vals[2]} | "
                f"{vals[3]} | {vals[4]} | {vals[5]} | "
                f"{vals[6]} | {vals[7]} | {vals[8]} |"
            )
        lines.append("")
    return lines


def build_single_i96_tables(rows_a, rows_b):
    idx = {}
    for r in rows_a:
        if int(r["I"]) == 96:
            idx[(r["case"], int(r["U"]))] = r
    for r in rows_b:
        if int(r["I"]) == 96:
            idx[(r["case"], int(r["U"]))] = r

    lines = []
    lines.append("## 单层循环 I=96（U=1/2/3/4/6/8）")
    lines.append("")
    for c in CASES:
        lines.append(f"### {c}")
        lines.append("| 指标 | U1 | U2 | U3 | U4 | U6 | U8 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        model, cce, acc = [], [], []
        for u in [1, 2, 3, 4, 6, 8]:
            r = idx.get((c, u))
            if r is None:
                model.append("N/A")
                cce.append("N/A")
                acc.append("N/A")
                continue
            model.append(str(int(float(r["model_vf_end"]))))
            cce.append(str(int(float(r["cce_vf_end"]))))
            if is_non_comparable(c, u):
                acc.append(NON_COMPARABLE_TEXT)
            else:
                acc.append(pct_abs(r["rel_err"]))
        lines.append("| 模型预测时间 | " + " | ".join(model) + " |")
        lines.append("| CCE simulator时间 | " + " | ".join(cce) + " |")
        lines.append("| 精度(abs_err/CCE) | " + " | ".join(acc) + " |")
        lines.append("")
    return lines


def build_nested_section(rows_nested):
    idx = {(r["case"], int(r["U"])): r for r in rows_nested}
    lines = []
    lines.append("# 双层嵌套循环精度报告（I=2, J=48）")
    lines.append("")
    for c in CASES:
        lines.append(f"### {c}")
        lines.append("| 指标 | U1 | U2 | U3 | U4 | U6 | U8 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        model, cce, acc = [], [], []
        for u in [1, 2, 3, 4, 6, 8]:
            r = idx.get((c, u))
            if r is None:
                model.append("N/A")
                cce.append("N/A")
                acc.append("N/A")
                continue
            model_v = str(int(float(r["model_vf_end"])))
            cce_v = str(int(float(r["cce_vf_end"])))
            if is_non_comparable(c, u):
                acc_v = NON_COMPARABLE_TEXT
            else:
                acc_v = pct_abs(r["rel_err"])
            model.append(model_v)
            cce.append(cce_v)
            acc.append(acc_v)
        lines.append("| 模型预测时间 | " + " | ".join(model) + " |")
        lines.append("| CCE simulator时间 | " + " | ".join(cce) + " |")
        lines.append("| 精度(abs_err/CCE) | " + " | ".join(acc) + " |")
        lines.append("")
    lines.append("")
    return lines


def main():
    rows_a = read_csv(SINGLE_A)
    rows_b = read_csv(SINGLE_B)
    rows_n = read_csv(NESTED)

    lines = []
    lines.extend(build_single_sections(rows_a))
    lines.extend(build_single_i96_tables(rows_a, rows_b))
    lines.extend(build_nested_section(rows_n))
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
