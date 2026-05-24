#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "VFtest" / "VADD_twoloop.json"
TRACE_DIR = ROOT / "VFtest" / "vadd_twoloop_tests"
RESULT_DIR = ROOT / "results" / "VADD_twoloop_test"
ISA_PATH = ROOT / "configs" / "isa.json"
GRID_VALUES = [2, 4, 6, 8, 16]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def extract_cycles(result_dir: Path) -> int:
    done_by_cycle_path = result_dir / "done_by_cycle.json"
    vf_drain_cost = int(load_json(ISA_PATH).get("defaults", {}).get("vf_drain_cost", 12))
    max_cycle = -1
    with done_by_cycle_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            max_cycle = max(max_cycle, int(item["cy"]))
    if max_cycle < 0:
        raise RuntimeError(f"Empty done_by_cycle: {done_by_cycle_path}")
    return max_cycle + vf_drain_cost


def make_trace(template: dict, outer_iters: int, inner_iters: int) -> dict:
    trace = deepcopy(template)
    trace["params"]["M"] = outer_iters
    trace["params"]["N"] = inner_iters
    trace["params"]["U"] = 1
    return trace


def main() -> int:
    template = load_json(TEMPLATE_PATH)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    summary = []

    for outer_iters in GRID_VALUES:
        for inner_iters in GRID_VALUES:
            case_name = f"VADD_twoloop_M{outer_iters}_N{inner_iters}"
            trace_path = TRACE_DIR / f"{case_name}.json"
            case_result_dir = RESULT_DIR / case_name

            trace = make_trace(template, outer_iters, inner_iters)
            save_json(trace_path, trace)

            cmd = [
                sys.executable,
                str(ROOT / "main.py"),
                "--trace",
                str(trace_path),
                "--out_dir",
                str(case_result_dir),
            ]
            print(f"[RUN] {case_name}")
            subprocess.run(cmd, cwd=ROOT, check=True)

            cycles = extract_cycles(case_result_dir)
            summary.append(
                {
                    "case": case_name,
                    "outer_iters": outer_iters,
                    "inner_iters": inner_iters,
                    "cycles": cycles,
                }
            )

    summary.sort(key=lambda item: (item["outer_iters"], item["inner_iters"]))
    save_json(RESULT_DIR / "summary.json", summary)

    with (RESULT_DIR / "summary.txt").open("w", encoding="utf-8") as f:
        f.write("outer_iters\tinner_iters\tcycles\tcase\n")
        for item in summary:
            f.write(
                f"{item['outer_iters']}\t{item['inner_iters']}\t"
                f"{item['cycles']}\t{item['case']}\n"
            )

    matrix = {
        str(outer_iters): {
            str(inner_iters): next(
                item["cycles"]
                for item in summary
                if item["outer_iters"] == outer_iters and item["inner_iters"] == inner_iters
            )
            for inner_iters in GRID_VALUES
        }
        for outer_iters in GRID_VALUES
    }
    save_json(RESULT_DIR / "summary_matrix.json", matrix)

    print(f"[DONE] wrote results to {RESULT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
