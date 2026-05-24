#!/usr/bin/env python3
"""Extract fp16 forwarding timings from fwd_*_fp16 native-sim dumps."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


RV_MAP = {
    "VADDS": "RV_VADDS",
    "VMULS": "RV_VMULS",
    "VADD": "RV_VADD",
    "VEXP": "RV_VEXP",
    "VCMAX": "RV_VCMAX",
    "VCMIN": "RV_VCMIN",
    "VCADD": "RV_VCADD",
    "VDIV": "RV_VDIV",
    "VABS": "RV_VABS_FP",
    "VSUB": "RV_VSUB",
    "VMUL": "RV_VMUL",
    "VMAXS": "RV_VMAXS",
    "VMINS": "RV_VMINS",
    "VMAX": "RV_VMAX",
    "VMIN": "RV_VMIN",
}

LINE_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\).*?\s(RV_[A-Z0-9_]+)\b")
CASE_RE = re.compile(r"^fwd_([a-z0-9]+)_to_([a-z0-9]+)_fp16$")


def parse_events(path: Path) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = LINE_RE.search(line)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), m.group(3)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--msprof-root", default="/home/lenovo/msprof_run")
    ap.add_argument("--csv-out", required=True)
    args = ap.parse_args()

    root = Path(args.msprof_root)
    rows: list[dict[str, int | str]] = []

    for rd in sorted(root.glob("fwd_*_native_simexec")):
        stem = rd.name.removesuffix("_native_simexec")
        m = CASE_RE.match(stem)
        if not m:
            continue
        prod = m.group(1).upper()
        cons = m.group(2).upper()
        if prod not in RV_MAP or cons not in RV_MAP:
            continue
        popped = rd / "core0.veccore0.instr_popped_log.dump"
        if not popped.exists():
            continue
        events = parse_events(popped)
        p = next((e for e in events if e[2] == RV_MAP[prod]), None)
        c = next((e for e in events if p is not None and e[2] == RV_MAP[cons] and e[1] != p[1] and e[0] >= p[0]), None)
        if p is None or c is None:
            continue
        rows.append(
            {
                "case": stem,
                "producer": prod,
                "consumer": cons,
                "measured": c[0] - p[0],
                "run_dir": str(rd),
            }
        )

    out = Path(args.csv_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["case", "producer", "consumer", "measured", "run_dir"])
        w.writeheader()
        w.writerows(rows)
    print(f"rows={len(rows)}")
    print(f"csv={out}")


if __name__ == "__main__":
    main()

