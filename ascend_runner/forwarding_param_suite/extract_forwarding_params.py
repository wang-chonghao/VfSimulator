#!/usr/bin/env python3
"""Extract forwarding(prod,cons) from minimal forwarding cases and compare with configs/forwarding.json."""

from __future__ import annotations

import argparse
import csv
import json
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
    ap.add_argument("--fwd-json", default="configs/forwarding.json")
    ap.add_argument("--csv-out", default="")
    args = ap.parse_args()

    msprof_root = Path(args.msprof_root)
    cfg = json.loads(Path(args.fwd_json).read_text(encoding="utf-8"))
    fwd = cfg["forwarding"]["fp32"]

    rows: list[dict[str, str | int | bool]] = []
    print("case,producer,consumer,measured,forwarding_json,match")

    for prod, cons_map in fwd.items():
        if prod not in RV_MAP:
            continue
        for cons, cfg_v in cons_map.items():
            if cons not in RV_MAP:
                continue
            stem = f"fwd_{prod.lower()}_to_{cons.lower()}"
            popped = msprof_root / f"{stem}_native_simexec" / "core0.veccore0.instr_popped_log.dump"
            if not popped.exists():
                continue

            events = parse_events(popped)
            p = next((e for e in events if e[2] == RV_MAP[prod]), None)
            c = next((e for e in events if e[2] == RV_MAP[cons] and p is not None and e[1] != p[1] and e[0] >= p[0]), None)
            if p is None or c is None:
                continue

            measured = c[0] - p[0]
            ok = int(cfg_v) == measured
            print(f"{stem},{prod},{cons},{measured},{int(cfg_v)},{ok}")
            rows.append(
                {
                    "case": stem,
                    "producer": prod,
                    "consumer": cons,
                    "measured": measured,
                    "forwarding_json": int(cfg_v),
                    "match": ok,
                }
            )

    if args.csv_out:
        out = Path(args.csv_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["case", "producer", "consumer", "measured", "forwarding_json", "match"],
            )
            writer.writeheader()
            writer.writerows(rows)
        print(f"CSV saved to {out}")


if __name__ == "__main__":
    main()






