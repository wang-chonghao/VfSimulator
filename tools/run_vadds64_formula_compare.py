#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "results" / "VADDS64_formula_compare"
TRACE_ROOT = RESULTS_ROOT / "traces"
LOOP_CHOICES = [1, 2, 4, 8, 16, 32, 64]
TRIP_COUNTS = [2, 4, 8, 16, 32, 64, 128]
TOTAL_VADDS = 64
SHORT_FWD = 3.5
N0 = 208.0
N1 = 144.0


def dump_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def build_trace(trip_count: int, loops: int):
    vadds_per_loop = TOTAL_VADDS // loops
    program = []
    for idx in range(loops):
        body = []
        if idx == 0:
            body.append({
                "type": "inst",
                "op": "VLDS",
                "dst": ["V1"],
                "src": ["memA"],
            })
        else:
            body.append({
                "type": "inst",
                "op": "VLDS",
                "dst": ["V1"],
                "src": [f"mem_inter_{idx % 2}"],
            })
        for _ in range(vadds_per_loop):
            body.append({
                "type": "inst",
                "op": "VADDS",
                "dst": ["V1"],
                "src": ["V1"],
            })
        body.append({
            "type": "inst",
            "op": "VSTS",
            "dst": ["memB" if idx == loops - 1 else f"mem_inter_{(idx + 1) % 2}"],
            "src": ["V1"],
        })
        program.append({
            "type": "loop",
            "iters": "I",
            "unroll": 1,
            "body": body,
        })
    return {
        "dtype": "fp32",
        "params": {"I": trip_count},
        "program": program,
    }


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


def formula_score(trip_count: int, loops: int):
    chain_cost = SHORT_FWD * (TOTAL_VADDS // loops)
    n = TOTAL_VADDS // loops
    segments = loops
    weff = 1.0 + pow(2.718281828459045, (N0 - n) / N1)
    return (trip_count * chain_cost / weff + 7.0) * segments


def plot_trip(trip_dir: Path, rows, trip_count: int):
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    rows = sorted(rows, key=lambda r: r["vadds_per_loop"])
    xs = [r["vadds_per_loop"] for r in rows]
    sim = [r["cycles_norm"] for r in rows]
    frm = [r["formula_norm"] for r in rows]

    plt.figure(figsize=(10, 7))
    plt.plot(xs, sim, marker="o", linewidth=2.5, markersize=8, label="Simulator")
    plt.plot(xs, frm, marker="s", linewidth=2.5, markersize=7, linestyle="--", label="Formula")
    plt.xscale("log", base=2)
    plt.xlabel("VADDS per Loop (log2 scale)", fontsize=16)
    plt.ylabel("Normalized U-curve", fontsize=16)
    plt.title(f"64-VADDS Chain U-curve Compare (I={trip_count})", fontsize=18)
    plt.xticks(xs, [str(x) for x in xs], fontsize=13)
    plt.yticks(fontsize=13)
    plt.legend(fontsize=13)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(trip_dir / f"u_curve_compare_i{trip_count}.png", dpi=220)
    plt.close()


def plot_overview(all_rows):
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    show_is = [2, 8, 16, 64]
    for ax, trip_count in zip(axes.flat, show_is):
        rows = sorted(all_rows[trip_count], key=lambda r: r["vadds_per_loop"])
        xs = [r["vadds_per_loop"] for r in rows]
        sim = [r["cycles_norm"] for r in rows]
        frm = [r["formula_norm"] for r in rows]
        ax.plot(xs, sim, marker="o", linewidth=2.4, markersize=7, label="Simulator")
        ax.plot(xs, frm, marker="s", linewidth=2.2, markersize=6, linestyle="--", label="Formula")
        ax.set_xscale("log", base=2)
        ax.set_title(f"I={trip_count}", fontsize=16)
        ax.set_xticks(xs)
        ax.set_xticklabels([str(x) for x in xs], fontsize=11)
        ax.tick_params(axis="y", labelsize=11)
        ax.grid(True, alpha=0.3)
    axes[0, 0].legend(fontsize=11)
    fig.supxlabel("VADDS per Loop (log2 scale)", fontsize=16)
    fig.supylabel("Normalized U-curve", fontsize=16)
    fig.suptitle("64-VADDS Chain: Formula vs Simulator", fontsize=18)
    plt.tight_layout()
    plt.savefig(RESULTS_ROOT / "u_curve_compare_overview.png", dpi=220)
    plt.close()


def main():
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    TRACE_ROOT.mkdir(parents=True, exist_ok=True)
    all_rows = {}

    for trip_count in TRIP_COUNTS:
        trip_dir = RESULTS_ROOT / f"I{trip_count}"
        trip_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for loops in LOOP_CHOICES:
            vadds_per_loop = TOTAL_VADDS // loops
            trace_path = TRACE_ROOT / f"VADDS64_{loops}loops_{vadds_per_loop}vadds_I{trip_count}.json"
            out_dir = trip_dir / f"VADDS64_{loops}loops_{vadds_per_loop}vadds"
            done_path = out_dir / "done_by_cycle.json"
            if not trace_path.exists():
                dump_json(trace_path, build_trace(trip_count, loops))
            if not done_path.exists():
                run_sim(trace_path, out_dir)
            rows.append({
                "loops": loops,
                "vadds_per_loop": vadds_per_loop,
                "chain_cost": SHORT_FWD * vadds_per_loop,
                "formula_score": formula_score(trip_count, loops),
                "cycles": read_cycles(done_path),
            })

        sim_min = min(r["cycles"] for r in rows)
        frm_min = min(r["formula_score"] for r in rows)
        for r in rows:
            r["cycles_norm"] = r["cycles"] / sim_min
            r["formula_norm"] = r["formula_score"] / frm_min

        rows.sort(key=lambda r: r["loops"])
        dump_json(trip_dir / "compare_results.json", rows)
        plot_trip(trip_dir, rows, trip_count)
        all_rows[trip_count] = rows

    plot_overview(all_rows)


if __name__ == "__main__":
    main()
