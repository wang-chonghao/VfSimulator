#!/usr/bin/env python3
"""Extract single-op ISA parameters from minimal VLD->OP->VST dumps."""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CASE_META = {
    "singleop_vadds": {"rv_op": "RV_VADDS", "isa_op": "VADDS", "src_vld_count": 1},
    "singleop_vexp": {"rv_op": "RV_VEXP", "isa_op": "VEXP", "src_vld_count": 1},
    "singleop_vcmax": {"rv_op": "RV_VCMAX", "isa_op": "VCMAX", "src_vld_count": 1},
    "singleop_vcmin": {"rv_op": "RV_VCMIN", "isa_op": "VCMIN", "src_vld_count": 1},
    "singleop_vcadd": {"rv_op": "RV_VCADD", "isa_op": "VCADD", "src_vld_count": 1},
    "singleop_vdup": {"rv_op": "RV_VDUPS", "isa_op": "VDUP", "src_vld_count": 1},
    "singleop_vadd": {"rv_op": "RV_VADD", "isa_op": "VADD", "src_vld_count": 2},
    "singleop_vmuls": {"rv_op": "RV_VMULS", "isa_op": "VMULS", "src_vld_count": 1},
    "singleop_vdiv": {"rv_op": "RV_VDIV", "isa_op": "VDIV", "src_vld_count": 2},
    "singleop_vabs": {"rv_op": "RV_VABS_FP", "isa_op": "VABS", "src_vld_count": 1},
    "singleop_vsub": {"rv_op": "RV_VSUB", "isa_op": "VSUB", "src_vld_count": 2},
    "singleop_vmul": {"rv_op": "RV_VMUL", "isa_op": "VMUL", "src_vld_count": 2},
    "singleop_vmaxs": {"rv_op": "RV_VMAXS", "isa_op": "VMAXS", "src_vld_count": 1},
    "singleop_vmins": {"rv_op": "RV_VMINS", "isa_op": "VMINS", "src_vld_count": 1},
    "singleop_vmax": {"rv_op": "RV_VMAX", "isa_op": "VMAX", "src_vld_count": 2},
    "singleop_vmin": {"rv_op": "RV_VMIN", "isa_op": "VMIN", "src_vld_count": 2},
}

LINE_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\).*?\s(RV_[A-Z0-9_]+)\b")
CYCLE_ID_RE = re.compile(r"\[(\d+)\].*\(ID:\s*(\d+)\)")


@dataclass
class Metrics:
    case: str
    isa_op: str
    rv_op: str
    pipeline_startup_cost: int
    latency: int
    pipeline_drain_cost: int
    data_load_cost: int
    data_store_cost: int


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


def extract_one(run_dir: Path, case: str, rv_op: str, isa_op: str, src_vld_count: int) -> Metrics:
    popped = run_dir / "core0.veccore0.instr_popped_log.dump"
    instr_log = run_dir / "core0.veccore0.instr_log.dump"
    if not popped.exists() or not instr_log.exists():
        raise FileNotFoundError(f"missing dumps in {run_dir}")

    events = parse_events(popped)
    done_events = parse_events(instr_log)
    starts = parse_cycles(popped)
    dones = parse_cycles(instr_log)

    first_vld = next(e for e in events if e[2].startswith("RV_VLD") and e[1] in starts and e[1] in dones)
    first_op = next(e for e in events if e[2] == rv_op and e[1] in starts and e[1] in dones)
    first_vst = next(
        e
        for e in events
        if e[2].startswith("RV_VST")
        and e[1] in starts
        and e[1] in dones
        and starts[e[1]] >= starts[first_op[1]]
    )
    # For store cost, anchor on the first completed VST in instr_log.
    first_vst_done = next(
        e for e in done_events if e[2].startswith("RV_VST") and e[1] in starts and e[1] in dones
    )

    vld_id = first_vld[1]
    op_id = first_op[1]
    vst_id = first_vst[1]

    op_start = starts[op_id]
    op_done = dones[op_id]
    vld_start = starts[vld_id]
    vld_done = dones[vld_id]
    vst_start = starts[vst_id]
    vst_done = dones[first_vst_done[1]]

    return Metrics(
        case=case,
        isa_op=isa_op,
        rv_op=rv_op,
        pipeline_startup_cost=op_start - vld_start,
        latency=op_done - op_start,
        pipeline_drain_cost=vst_start - op_start,
        data_load_cost=vld_done - vld_start,
        data_store_cost=vst_done - vst_start,
    )


def load_isa(isa_path: Path) -> dict[str, dict[str, int]]:
    raw = json.loads(isa_path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, int]] = {}
    for op, v in raw["instructions"].items():
        fp32 = v["fp32"]
        out[op] = {
            "pipeline_startup_cost": int(fp32["pipeline_startup_cost"]),
            "latency": int(fp32["latency"]),
            "pipeline_drain_cost": int(fp32["pipeline_drain_cost"]),
            "data_load_cost": int(fp32["data_load_cost"]),
            "data_store_cost": int(fp32["data_store_cost"]),
        }
    return out


def iter_results(msprof_root: Path) -> Iterable[Metrics]:
    for case, meta in CASE_META.items():
        run_dir = msprof_root / f"{case}_native_simexec"
        if not run_dir.exists():
            continue
        yield extract_one(
            run_dir=run_dir,
            case=case,
            rv_op=meta["rv_op"],
            isa_op=meta["isa_op"],
            src_vld_count=meta["src_vld_count"],
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--msprof-root", default="/home/lenovo/msprof_run")
    ap.add_argument("--isa-json", default="configs/isa.json")
    ap.add_argument("--csv-out", default="")
    args = ap.parse_args()

    msprof_root = Path(args.msprof_root)
    isa = load_isa(Path(args.isa_json))
    rows = list(iter_results(msprof_root))

    if not rows:
        raise SystemExit(f"No matching run dirs under: {msprof_root}")

    fields = [
        "pipeline_startup_cost",
        "latency",
        "pipeline_drain_cost",
        "data_load_cost",
        "data_store_cost",
    ]

    print("case,isa_op,metric,measured,isa_json,match")
    table: list[dict[str, str | int | bool]] = []
    for row in rows:
        op_cfg = isa[row.isa_op]
        for f in fields:
            measured = int(getattr(row, f))
            cfg = int(op_cfg[f])
            ok = measured == cfg
            print(f"{row.case},{row.isa_op},{f},{measured},{cfg},{ok}")
            table.append(
                {
                    "case": row.case,
                    "isa_op": row.isa_op,
                    "metric": f,
                    "measured": measured,
                    "isa_json": cfg,
                    "match": ok,
                }
            )

    if args.csv_out:
        out_path = Path(args.csv_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["case", "isa_op", "metric", "measured", "isa_json", "match"]
            )
            writer.writeheader()
            writer.writerows(table)
        print(f"CSV saved to {out_path}")


if __name__ == "__main__":
    main()





