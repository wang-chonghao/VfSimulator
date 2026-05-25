#!/usr/bin/env python3
"""Append a structured CCE optimization round entry to a perf log."""
import argparse
from datetime import datetime
from pathlib import Path

FIELDS = [
    "round", "kernel_trace", "changed_files", "current_bottleneck", "evidence",
    "hypothesis", "planned_change", "expected_metric_change", "main_risk",
    "correctness", "model_metric", "cce_metric", "stability", "resource_side_effects",
    "result", "confidence", "conclusion", "next_step",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=Path("optimization_rounds/perf_log.md"))
    for field in FIELDS:
        ap.add_argument(f"--{field.replace('_', '-')}", default="")
    args = ap.parse_args()

    round_id = args.round or "UNKNOWN"
    entry = [f"\n\n## Round {round_id}\n"]
    entry.append(f"- **timestamp**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    labels = {
        "kernel_trace": "Kernel/Trace",
        "changed_files": "Changed files",
        "current_bottleneck": "Current bottleneck",
        "expected_metric_change": "Expected metric change",
        "main_risk": "Main risk",
        "model_metric": "Model metric",
        "cce_metric": "CCE metric",
        "resource_side_effects": "Resource side effects",
        "next_step": "Next step",
    }
    for field in FIELDS:
        if field == "round":
            entry.append(f"- **Round**: R{round_id}")
            continue
        value = getattr(args, field)
        label = labels.get(field, field.replace("_", " ").title())
        entry.append(f"- **{label}**: {value if value else 'TODO'}")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry) + "\n")


if __name__ == "__main__":
    main()
