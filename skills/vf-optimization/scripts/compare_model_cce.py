#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare model summary with CCE vf_end labels from cases json")
    ap.add_argument("--summary", required=True, help="summary.json from run_cost_model_batch.py")
    ap.add_argument("--cases", required=True, help="cases json with optional cce_vf_end per case")
    ap.add_argument("--out-csv", required=True)
    args = ap.parse_args()

    summary_path = Path(args.summary)
    if not summary_path.is_absolute():
        summary_path = (ROOT / summary_path).resolve()
    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = (ROOT / cases_path).resolve()
    out_csv = Path(args.out_csv)
    if not out_csv.is_absolute():
        out_csv = (ROOT / out_csv).resolve()

    summary = load_json(summary_path)
    cases = load_json(cases_path)
    case_map = {c.get("id"): c for c in (cases.get("cases", []) or [])}

    rows = []
    for r in (summary.get("rows", []) or []):
        cid = r.get("id")
        c = case_map.get(cid, {})
        cce_vf_end = c.get("cce_vf_end")
        row: Dict[str, Any] = {
            "id": cid,
            "ok": r.get("ok", False),
            "model_vf_end": r.get("vf_end"),
            "cce_vf_end": cce_vf_end,
            "abs_err": "",
            "rel_err_pct": "",
            "note": "",
        }
        mv = r.get("vf_end")
        if (not r.get("ok")) or mv is None:
            row["note"] = "model_failed"
        elif cce_vf_end is None:
            row["note"] = "missing_cce_vf_end"
        else:
            abs_err = abs(float(mv) - float(cce_vf_end))
            rel = abs_err / max(1.0, abs(float(cce_vf_end))) * 100.0
            row["abs_err"] = f"{abs_err:.0f}"
            row["rel_err_pct"] = f"{rel:.2f}"
        rows.append(row)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    keys = ["id", "ok", "model_vf_end", "cce_vf_end", "abs_err", "rel_err_pct", "note"]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    print(f"[OK] wrote: {out_csv}")


if __name__ == "__main__":
    main()

