#!/usr/bin/env python3
"""Extract measured forwarding for unconfigured pairs (fwdx_* cases)."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

RV_MAP = {
    "vadds": "RV_VADDS",
    "vmuls": "RV_VMULS",
    "vadd": "RV_VADD",
    "vexp": "RV_VEXP",
    "vcmax": "RV_VCMAX",
    "vcmin": "RV_VCMIN",
    "vcadd": "RV_VCADD",
    "vdiv": "RV_VDIV",
    "vabs": "RV_VABS_FP",
    "vsub": "RV_VSUB",
    "vmul": "RV_VMUL",
    "vmaxs": "RV_VMAXS",
    "vmins": "RV_VMINS",
    "vmax": "RV_VMAX",
    "vmin": "RV_VMIN",
}

LINE_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\).*?\s(RV_[A-Z0-9_]+)\b")
NAME_RE = re.compile(r"^fwdx_([a-z0-9]+)_to_([a-z0-9]+)$")


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
    ap.add_argument("--csv-out", default="")
    args = ap.parse_args()

    msprof_root = Path(args.msprof_root)
    run_dirs = sorted(msprof_root.glob("fwdx_*_native_simexec"))

    rows: list[dict[str, str | int]] = []
    print("case,producer,consumer,measured")
    for rd in run_dirs:
        stem = rd.name.removesuffix("_native_simexec")
        m = NAME_RE.match(stem)
        if not m:
            continue
        prod, cons = m.group(1), m.group(2)
        if prod not in RV_MAP or cons not in RV_MAP:
            continue
        pop = rd / "core0.veccore0.instr_popped_log.dump"
        if not pop.exists():
            continue

        ev = parse_events(pop)
        p = next((e for e in ev if e[2] == RV_MAP[prod]), None)
        c = next((e for e in ev if e[2] == RV_MAP[cons] and p is not None and e[1] != p[1] and e[0] >= p[0]), None)
        if p is None or c is None:
            continue
        measured = c[0] - p[0]

        print(f"{stem},{prod.upper()},{cons.upper()},{measured}")
        rows.append({
            "case": stem,
            "producer": prod.upper(),
            "consumer": cons.upper(),
            "measured": measured,
        })

    if args.csv_out:
        out = Path(args.csv_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["case", "producer", "consumer", "measured"])
            w.writeheader()
            w.writerows(rows)
        print(f"CSV saved to {out}")


if __name__ == "__main__":
    main()





