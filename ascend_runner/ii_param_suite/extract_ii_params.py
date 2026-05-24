#!/usr/bin/env python3
"""Extract II from EXU dump for ii_* cases and compare with configs/InitiationInterval.json."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

OPS = [
    "VADDS", "VMULS", "VADD", "VEXP", "VCMAX", "VCMIN", "VCADD", "VDIV", "VABS",
    "VSUB", "VMUL", "VMAXS", "VMINS", "VMAX", "VMIN",
]

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

LINE_RE = re.compile(r"\[(\d+)\].*?instr_name\s+(RV_[A-Z0-9_]+).*?exu_id:(\d+)")
CASE_RE = re.compile(r"^ii_([a-z0-9]+)_to_([a-z0-9]+)$")


def parse_exu(path: Path) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = LINE_RE.search(line)
        if m:
            out.append((int(m.group(1)), m.group(2), int(m.group(3))))
    return out


def measure_ii(events: list[tuple[int, str, int]], prev_rv: str, cur_rv: str) -> int | None:
    best: int | None = None
    # Default: same-EXU nearest-prev to cur
    for exu in range(8):
        last_prev: int | None = None
        for cyc, rv, exu_id in events:
            if exu_id != exu:
                continue
            if rv == prev_rv:
                last_prev = cyc
            if rv == cur_rv and last_prev is not None and cyc > last_prev:
                delta = cyc - last_prev
                if best is None or delta < best:
                    best = delta

    # Self-II fallback: if no same-EXU sample found, use nearest two same-op launches globally.
    if best is None and prev_rv == cur_rv:
        last: int | None = None
        for cyc, rv, _ in events:
            if rv != prev_rv:
                continue
            if last is not None and cyc > last:
                delta = cyc - last
                if best is None or delta < best:
                    best = delta
            last = cyc

    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--msprof-root", default="/home/lenovo/msprof_run")
    ap.add_argument("--ii-json", default="configs/InitiationInterval.json")
    ap.add_argument("--csv-out", default="")
    args = ap.parse_args()

    msprof_root = Path(args.msprof_root)
    cfg = json.loads(Path(args.ii_json).read_text(encoding="utf-8"))["InitiationInterval"]["fp32"]

    rows: list[dict[str, str | int | bool]] = []
    print("case,prev,cur,measured,ii_json,has_config,match")

    for rd in sorted(msprof_root.glob("ii_*_native_simexec")):
        stem = rd.name.removesuffix("_native_simexec")
        m = CASE_RE.match(stem)
        if not m:
            continue
        prev = m.group(1).upper()
        cur = m.group(2).upper()
        if prev not in RV_MAP or cur not in RV_MAP:
            continue

        exu = rd / "core0.veccore0.rvec.EXU.dump"
        if not exu.exists():
            continue

        events = parse_exu(exu)
        measured = measure_ii(events, RV_MAP[prev], RV_MAP[cur])
        if measured is None:
            continue

        has_cfg = prev in cfg and cur in cfg[prev]
        cfg_val = int(cfg[prev][cur]) if has_cfg else -1
        ok = has_cfg and (cfg_val == measured)

        print(f"{stem},{prev},{cur},{measured},{cfg_val if has_cfg else ''},{has_cfg},{ok}")
        rows.append(
            {
                "case": stem,
                "prev": prev,
                "cur": cur,
                "measured": measured,
                "ii_json": cfg_val if has_cfg else "",
                "has_config": has_cfg,
                "match": ok,
            }
        )

    if args.csv_out:
        out = Path(args.csv_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["case", "prev", "cur", "measured", "ii_json", "has_config", "match"],
            )
            w.writeheader()
            w.writerows(rows)
        print(f"CSV saved to {out}")


if __name__ == "__main__":
    main()






