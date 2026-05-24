#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "VFtest" / "vadd_fusion_singlev1_tests"
RESULTS_ROOT = ROOT / "results" / "MIXED_test"
LOOP_CHOICES = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512]
TRIP_COUNTS = [2, 4, 8, 16, 32, 64, 128]
PATTERNS = {
    "1S1L": ["VADDS", "VEXP"],
    "2S1L": ["VADDS", "VADDS", "VEXP"],
    "4S1L": ["VADDS", "VADDS", "VADDS", "VADDS", "VEXP"],
    "6S1L": ["VADDS", "VADDS", "VADDS", "VADDS", "VADDS", "VADDS", "VEXP"],
    "8S1L": ["VADDS", "VADDS", "VADDS", "VADDS", "VADDS", "VADDS", "VADDS", "VADDS", "VEXP"],
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def make_mixed_trace(src_path: Path, trip_count: int, out_path: Path, pattern_name: str):
    trace = load_json(src_path)
    trace["params"]["I"] = trip_count
    seq = PATTERNS[pattern_name]
    idx = 0
    for loop in trace.get("program", []):
        for inst in loop.get("body", []):
            if inst.get("op") == "VADDS":
                inst["op"] = seq[idx % len(seq)]
                idx += 1
    dump_json(out_path, trace)


def run_sim(trace_path: Path, out_dir: Path):
    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--trace",
        str(trace_path),
        "--out_dir",
        str(out_dir),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def read_cycles(done_path: Path):
    rows = []
    with done_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        return 0
    key = "done_cycle" if "done_cycle" in rows[0] else "cy"
    return max(int(item[key]) for item in rows)


def count_dynamic_insts(done_path: Path):
    cnt = 0
    with done_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cnt += 1
    return cnt


def write_txt(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(
                f'{row["loops"]} {row["insts_per_loop"]} {row["cycles"]} '
                f'{row["dynamic_insts"]} {row["ipc"]:.6f}\n'
            )


def plot_trip_summary(trip_dir: Path, rows, trip_count: int, pattern_name: str):
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    plot_rows = sorted(rows, key=lambda r: r["insts_per_loop"])
    xs = [r["insts_per_loop"] for r in plot_rows]
    ys = [r["cycles"] / (512 * trip_count) for r in plot_rows]
    best = min(plot_rows, key=lambda r: r["cycles"])

    plt.figure(figsize=(10, 7))
    plt.plot(xs, ys, marker="o", linewidth=2.5, markersize=8, label=f"I={trip_count}")
    plt.scatter([best["insts_per_loop"]], [best["cycles"] / (512 * trip_count)], color="red", s=180, marker="*", zorder=5, label="Best")
    plt.xscale("log", base=2)
    plt.xlabel(f"{pattern_name} ops per Loop (log2 scale)", fontsize=16)
    plt.ylabel("Cycles / (512 * I)", fontsize=16)
    plt.title(f"{pattern_name} Sweet Spot (I={trip_count})", fontsize=18)
    plt.xticks(xs, [str(x) for x in xs], fontsize=13)
    plt.yticks(fontsize=13)
    plt.legend(fontsize=13)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(trip_dir / f"fusion_sweet_spot_{pattern_name}_i{trip_count}.png", dpi=200)
    plt.close()


def plot_pattern_overview(pattern_root: Path, all_rows, pattern_name: str):
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    plt.figure(figsize=(11, 8))
    xticks = sorted({r["insts_per_loop"] for rows in all_rows.values() for r in rows})
    for trip_count in sorted(all_rows):
        rows = sorted(all_rows[trip_count], key=lambda r: r["insts_per_loop"])
        xs = [r["insts_per_loop"] for r in rows]
        ys = [r["cycles"] / (512 * trip_count) for r in rows]
        plt.plot(xs, ys, marker="o", linewidth=2.2, markersize=6, label=f"I={trip_count}")
    plt.xscale("log", base=2)
    plt.xlabel(f"{pattern_name} ops per Loop (log2 scale)", fontsize=16)
    plt.ylabel("Cycles / (512 * I)", fontsize=16)
    plt.title(f"{pattern_name} Sweet Spot Overview", fontsize=18)
    plt.xticks(xticks, [str(x) for x in xticks], fontsize=13)
    plt.yticks(fontsize=13)
    plt.legend(fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(pattern_root / f"fusion_sweep_{pattern_name}_overview.png", dpi=220)
    plt.close()


def main():
    clean = "--clean" in sys.argv
    if clean and RESULTS_ROOT.exists():
        shutil.rmtree(RESULTS_ROOT)
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

    for pattern_name in PATTERNS:
        pattern_root = RESULTS_ROOT / pattern_name
        all_rows = {}
        for trip_count in TRIP_COUNTS:
            trip_dir = pattern_root / f"I{trip_count}"
            traces_dir = trip_dir / "traces"
            traces_dir.mkdir(parents=True, exist_ok=True)
            rows = []

            for loops in LOOP_CHOICES:
                insts_per_loop = 512 // loops
                src = SOURCE_DIR / f"VADD_singleV1_fusion_{loops}loops_{insts_per_loop}vadds.json"
                trace_out = traces_dir / f"{pattern_name}_singleV1_fusion_{loops}loops_{insts_per_loop}ops.json"
                case_dir = trip_dir / f"{pattern_name}_singleV1_fusion_{loops}loops_{insts_per_loop}ops"
                done_path = case_dir / "done_by_cycle.json"

                make_mixed_trace(src, trip_count, trace_out, pattern_name)
                if not done_path.exists():
                    run_sim(trace_out, case_dir)

                cycles = read_cycles(done_path)
                dynamic_insts = count_dynamic_insts(done_path)
                ipc = (dynamic_insts / cycles) if cycles else 0.0
                rows.append(
                    {
                        "loops": loops,
                        "insts_per_loop": insts_per_loop,
                        "cycles": cycles,
                        "dynamic_insts": dynamic_insts,
                        "ipc": ipc,
                    }
                )

            rows.sort(key=lambda r: r["loops"])
            all_rows[trip_count] = rows
            dump_json(trip_dir / "sweep_results_with_ipc.json", rows)
            write_txt(trip_dir / "sweep_results_with_ipc.txt", rows)
            plot_trip_summary(trip_dir, rows, trip_count, pattern_name)

        plot_pattern_overview(pattern_root, all_rows, pattern_name)


if __name__ == "__main__":
    main()
