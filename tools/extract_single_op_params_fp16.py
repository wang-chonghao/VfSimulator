#!/usr/bin/env python3
"""Extract fp16 ISA params from single-op native-sim dumps."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


CASE_META = {
    "singleop_vadds": {"rv_op": "RV_VADDS", "isa_op": "VADDS"},
    "singleop_vexp": {"rv_op": "RV_VEXP", "isa_op": "VEXP"},
    "singleop_vcmax": {"rv_op": "RV_VCMAX", "isa_op": "VCMAX"},
    "singleop_vcmin": {"rv_op": "RV_VCMIN", "isa_op": "VCMIN"},
    "singleop_vcadd": {"rv_op": "RV_VCADD", "isa_op": "VCADD"},
    "singleop_vadd": {"rv_op": "RV_VADD", "isa_op": "VADD"},
    "singleop_vmuls": {"rv_op": "RV_VMULS", "isa_op": "VMULS"},
    "singleop_vdiv": {"rv_op": "RV_VDIV", "isa_op": "VDIV"},
    "singleop_vabs": {"rv_op": "RV_VABS_FP", "isa_op": "VABS"},
    "singleop_vsub": {"rv_op": "RV_VSUB", "isa_op": "VSUB"},
    "singleop_vmul": {"rv_op": "RV_VMUL", "isa_op": "VMUL"},
    "singleop_vmaxs": {"rv_op": "RV_VMAXS", "isa_op": "VMAXS"},
    "singleop_vmins": {"rv_op": "RV_VMINS", "isa_op": "VMINS"},
    "singleop_vmax": {"rv_op": "RV_VMAX", "isa_op": "VMAX"},
    "singleop_vmin": {"rv_op": "RV_VMIN", "isa_op": "VMIN"},
}

LINE_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\).*?\s(RV_[A-Z0-9_]+)\b")
CYCLE_ID_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\)")
EXU_RE = re.compile(r"\[(\d+)\].*?instr_name\s+(RV_[A-Z0-9_]+).*?exu_id:(\d+)")


def parse_events(path: Path) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = LINE_RE.search(line)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), m.group(3)))
    return out


def parse_cycles(path: Path) -> dict[int, int]:
    out: dict[int, int] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = CYCLE_ID_RE.search(line)
        if m:
            out[int(m.group(2))] = int(m.group(1))
    return out


def parse_exu_ids(path: Path, rv_op: str) -> set[int]:
    exu_ids: set[int] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = EXU_RE.search(line)
        if m and m.group(2) == rv_op:
            exu_ids.add(int(m.group(3)))
    return exu_ids


def dispatch_label(exu_ids: set[int]) -> str:
    if not exu_ids:
        return ""
    if exu_ids == {0}:
        return "EXU0_ONLY"
    if exu_ids.issubset({0, 1}):
        return "EXU01"
    return "EXU_MULTI"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--msprof-root", default="/home/lenovo/msprof_run")
    ap.add_argument("--stem-suffix", default="_fp16")
    ap.add_argument("--csv-out", required=True)
    args = ap.parse_args()

    root = Path(args.msprof_root)
    rows: list[dict[str, int | str]] = []

    for case, meta in CASE_META.items():
        run_dir = root / f"{case}{args.stem_suffix}_native_simexec"
        popped = run_dir / "core0.veccore0.instr_popped_log.dump"
        instr = run_dir / "core0.veccore0.instr_log.dump"
        exu = run_dir / "core0.veccore0.rvec.EXU.dump"
        if not popped.exists() or not instr.exists():
            continue

        events = parse_events(popped)
        done_events = parse_events(instr)
        starts = parse_cycles(popped)
        dones = parse_cycles(instr)

        rv_op = meta["rv_op"]
        first_vld = next(e for e in events if e[2].startswith("RV_VLD") and e[1] in starts and e[1] in dones)
        first_op = next(e for e in events if e[2] == rv_op and e[1] in starts and e[1] in dones)
        first_vst = next(
            e
            for e in events
            if e[2].startswith("RV_VST") and e[1] in starts and e[1] in dones and starts[e[1]] >= starts[first_op[1]]
        )
        first_vst_done = next(e for e in done_events if e[2].startswith("RV_VST") and e[1] in starts and e[1] in dones)

        op_id = first_op[1]
        vld_id = first_vld[1]
        vst_id = first_vst[1]
        op_start = starts[op_id]
        op_done = dones[op_id]
        vld_start = starts[vld_id]
        vld_done = dones[vld_id]
        vst_start = starts[vst_id]
        vst_done = dones[first_vst_done[1]]
        exu_ids = parse_exu_ids(exu, rv_op) if exu.exists() else set()

        rows.append(
            {
                "case": case,
                "isa_op": meta["isa_op"],
                "rv_op": rv_op,
                "pipeline_startup_cost": op_start - vld_start,
                "latency": op_done - op_start,
                "pipeline_drain_cost": vst_start - op_start,
                "data_load_cost": vld_done - vld_start,
                "data_store_cost": vst_done - vst_start,
                "dispatch_exu": dispatch_label(exu_ids),
                "exu_ids": ",".join(str(x) for x in sorted(exu_ids)),
                "run_dir": str(run_dir),
            }
        )

    out = Path(args.csv_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "case",
                "isa_op",
                "rv_op",
                "pipeline_startup_cost",
                "latency",
                "pipeline_drain_cost",
                "data_load_cost",
                "data_store_cost",
                "dispatch_exu",
                "exu_ids",
                "run_dir",
            ],
        )
        w.writeheader()
        w.writerows(rows)
    print(f"rows={len(rows)}")
    print(f"csv={out}")


if __name__ == "__main__":
    main()

