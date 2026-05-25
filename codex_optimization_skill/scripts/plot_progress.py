#!/usr/bin/env python3
"""Plot optimization progress from a structured round log.

Produces two PNG files: latency and optional benchmark-relative performance.
"""
import argparse
import csv
import math
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter


def extract_rounds(log_path: Path, initial_round: int | None, initial_cycles: int | None):
    text = log_path.read_text(encoding="utf-8")
    parts = re.split(r"(?=^## Round \d+\n)", text, flags=re.M)
    data = {}
    if initial_round is not None and initial_cycles is not None:
        data[initial_round] = initial_cycles
    for part in parts:
        m = re.match(r"^## Round (\d+)", part)
        if not m:
            continue
        r = int(m.group(1))
        vals = []
        for pat in [r"candidate total [`]?([0-9]+)[`]?", r"VF total = [`]?([0-9]+)[`]?"]:
            vals.extend(int(x) for x in re.findall(pat, part))
        if vals:
            data[r] = vals[-1]
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=Path("optimization_rounds/perf_log.md"))
    ap.add_argument("--out-dir", type=Path, default=Path("results/optimization_progress"))
    ap.add_argument("--benchmark-cycles", type=int, default=None)
    ap.add_argument("--initial-round", type=int, default=None)
    ap.add_argument("--initial-cycles", type=int, default=None)
    ap.add_argument("--title", default="CCE Optimization Progress")
    args = ap.parse_args()

    data = extract_rounds(args.log, args.initial_round, args.initial_cycles)
    if not data:
        raise SystemExit("No round timing data found")
    max_round = max(data)
    rounds = list(range(min(data), max_round + 1))
    per_round = [data.get(r, math.nan) for r in rounds]
    best = []
    b = math.inf
    for r in rounds:
        v = data.get(r)
        if v is not None:
            b = min(b, v)
        best.append(b)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "progress.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["round", "round_cycles", "best_cycles"])
        for r, v, bv in zip(rounds, per_round, best):
            w.writerow([r, "" if math.isnan(v) else int(v), int(bv)])

    plt.rcParams.update({"figure.dpi": 140, "savefig.dpi": 220, "axes.grid": True, "grid.alpha": 0.25})
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(rounds, per_round, "--o", color="#b9b9b9", markerfacecolor="#b9b9b9", markeredgewidth=0, label="Round result")
    ax.plot(rounds, best, "-o", color="#1f77b4", markerfacecolor="#1f77b4", markeredgewidth=0, label="Best so far")
    if args.benchmark_cycles:
        ax.axhline(args.benchmark_cycles, color="black", linestyle="--", linewidth=1.4, label=f"Benchmark {args.benchmark_cycles} cycles")
    ax.set_title(args.title + " - Latency")
    ax.set_xlabel("Round")
    ax.set_ylabel("VF total cycles")
    ax.legend()
    fig.tight_layout()
    fig.savefig(args.out_dir / "progress_latency.png")
    plt.close(fig)

    if args.benchmark_cycles:
        perf_round = [args.benchmark_cycles / v * 100 if not math.isnan(v) else math.nan for v in per_round]
        perf_best = [args.benchmark_cycles / v * 100 for v in best]
        fig, ax = plt.subplots(figsize=(14, 7))
        ax.plot(rounds, perf_round, "--o", color="#b9b9b9", markerfacecolor="#b9b9b9", markeredgewidth=0, label="Round result")
        ax.plot(rounds, perf_best, "-o", color="#1f77b4", markerfacecolor="#1f77b4", markeredgewidth=0, label="Best so far")
        ax.axhline(100, color="black", linestyle="--", linewidth=1.4, label="Benchmark = 100%")
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
        ax.set_title(args.title + " - Relative Performance")
        ax.set_xlabel("Round")
        ax.set_ylabel("Performance vs benchmark")
        ax.legend()
        fig.tight_layout()
        fig.savefig(args.out_dir / "progress_relative_performance.png")
        plt.close(fig)


if __name__ == "__main__":
    main()
