#!/usr/bin/env python3
"""Write fp16 config sections into configs/*.json from measured CSVs with fallback marks."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


OPS = [
    "VADDS",
    "VMULS",
    "VADD",
    "VEXP",
    "VCMAX",
    "VCMIN",
    "VCADD",
    "VDIV",
    "VABS",
    "VSUB",
    "VMUL",
    "VMAXS",
    "VMINS",
    "VMAX",
    "VMIN",
]


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--isa-csv", required=True)
    ap.add_argument("--fwd-csv", required=True)
    ap.add_argument("--ii-csv", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    isa_json_path = root / "configs" / "isa.json"
    fwd_json_path = root / "configs" / "forwarding.json"
    ii_json_path = root / "configs" / "InitiationInterval.json"

    isa_db = json.loads(isa_json_path.read_text(encoding="utf-8"))
    fwd_db = json.loads(fwd_json_path.read_text(encoding="utf-8"))
    ii_db = json.loads(ii_json_path.read_text(encoding="utf-8"))

    isa_rows = load_csv(Path(args.isa_csv))
    fwd_rows = load_csv(Path(args.fwd_csv))
    ii_rows = load_csv(Path(args.ii_csv))

    isa_map = {r["isa_op"].upper(): r for r in isa_rows if r.get("isa_op")}
    fwd_map = {(r["producer"].upper(), r["consumer"].upper()): int(r["measured"]) for r in fwd_rows if r.get("producer") and r.get("consumer")}
    ii_map = {(r["prev"].upper(), r["cur"].upper()): int(r["measured"]) for r in ii_rows if r.get("prev") and r.get("cur")}

    fallback_rows: list[dict[str, str]] = []

    # isa.json: add instructions.*.fp16
    for op in OPS:
        ins = isa_db["instructions"][op]
        fp32 = ins["fp32"]
        if op in isa_map:
            r = isa_map[op]
            ins["fp16"] = {
                "pipeline_startup_cost": int(r["pipeline_startup_cost"]),
                "latency": int(r["latency"]),
                "throughput": int(fp32["throughput"]),
                "pipeline_drain_cost": int(r["pipeline_drain_cost"]),
                "data_load_cost": int(r["data_load_cost"]),
                "data_store_cost": int(r["data_store_cost"]),
                "EXU": fp32["EXU"],
                "dispatch_exu": r.get("dispatch_exu") or fp32.get("dispatch_exu", "EXU01"),
            }
        else:
            ins["fp16"] = {
                "pipeline_startup_cost": int(fp32["pipeline_startup_cost"]),
                "latency": int(fp32["latency"]),
                "throughput": int(fp32["throughput"]),
                "pipeline_drain_cost": int(fp32["pipeline_drain_cost"]),
                "data_load_cost": int(fp32["data_load_cost"]),
                "data_store_cost": int(fp32["data_store_cost"]),
                "EXU": fp32["EXU"],
                "dispatch_exu": fp32.get("dispatch_exu", "EXU01"),
            }
            fallback_rows.append({"section": "isa", "key": op, "source": "fp32", "reason": "missing_dump"})

    # forwarding.json: add forwarding.fp16
    fp32_fwd = fwd_db.get("forwarding", {}).get("fp32", {})
    fp16_fwd: dict[str, dict[str, int]] = {}
    for prod in OPS:
        row: dict[str, int] = {}
        for cons in OPS:
            k = (prod, cons)
            if k in fwd_map:
                row[cons] = int(fwd_map[k])
            else:
                row[cons] = int(fp32_fwd.get(prod, {}).get(cons, fwd_db.get("defaults", 3)))
                fallback_rows.append(
                    {"section": "forwarding", "key": f"{prod}->{cons}", "source": "fp32", "reason": "missing_dump"}
                )
        fp16_fwd[prod] = row
    fwd_db.setdefault("forwarding", {})["fp16"] = fp16_fwd

    # InitiationInterval.json: add InitiationInterval.fp16
    fp32_ii = ii_db.get("InitiationInterval", {}).get("fp32", {})
    fp16_ii: dict[str, dict[str, int]] = {}
    default_ii = int(ii_db.get("defaults", 1))
    for prev in OPS:
        row = {}
        for cur in OPS:
            k = (prev, cur)
            if k in ii_map:
                row[cur] = int(ii_map[k])
            else:
                row[cur] = int(fp32_ii.get(prev, {}).get(cur, default_ii))
                fallback_rows.append(
                    {"section": "ii", "key": f"{prev}->{cur}", "source": "fp32", "reason": "missing_dump"}
                )
        fp16_ii[prev] = row
    ii_db.setdefault("InitiationInterval", {})["fp16"] = fp16_ii

    isa_json_path.write_text(json.dumps(isa_db, indent=2) + "\n", encoding="utf-8")
    fwd_json_path.write_text(json.dumps(fwd_db, indent=2) + "\n", encoding="utf-8")
    ii_json_path.write_text(json.dumps(ii_db, indent=2) + "\n", encoding="utf-8")

    fb_path = out_dir / "fp16_fallbacks.csv"
    with fb_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["section", "key", "source", "reason"])
        w.writeheader()
        w.writerows(fallback_rows)

    summary = {
        "isa_measured": len(isa_map),
        "isa_expected": len(OPS),
        "forwarding_measured": len(fwd_map),
        "forwarding_expected": len(OPS) * len(OPS),
        "ii_measured": len(ii_map),
        "ii_expected": len(OPS) * len(OPS),
        "fallback_count": len(fallback_rows),
        "fallback_csv": str(fb_path),
    }
    (out_dir / "fp16_coverage_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

