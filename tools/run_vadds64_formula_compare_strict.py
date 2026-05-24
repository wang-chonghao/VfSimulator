#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "results" / "VADDS64_formula_compare"
RESULTS_ROOT = ROOT / "results" / "VADDS64_formula_compare_strict"
TRIP_COUNTS = [2, 4, 8, 16, 32, 64, 128]
N0 = 60.0
N1 = 1.0


def strict_weff(n: float) -> float:
    return 1.0 + 1.0 / (1.0 + pow(2.718281828459045, (n - N0) / N1))


def plot_trip(trip_dir: Path, rows, trip_count: int):
    rows = sorted(rows, key=lambda r: r["vadds_per_loop"])
    xs = [r["vadds_per_loop"] for r in rows]
    sim = [r["cycles_norm"] for r in rows]
    frm = [r["formula_norm"] for r in rows]

    plt.figure(figsize=(10, 7))
    plt.plot(xs, sim, marker="o", linewidth=2.5, markersize=8, label="Simulator")
    plt.plot(xs, frm, marker="s", linewidth=2.5, markersize=7, linestyle="--", label="Strict Formula")
    plt.xscale("log", base=2)
    plt.xlabel("VADDS per Loop (log2 scale)", fontsize=16)
    plt.ylabel("Normalized U-curve", fontsize=16)
    plt.title(f"64-VADDS Chain Strict Compare (I={trip_count})", fontsize=18)
    plt.xticks(xs, [str(x) for x in xs], fontsize=13)
    plt.yticks(fontsize=13)
    plt.legend(fontsize=13)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(trip_dir / f"u_curve_compare_i{trip_count}_strict.png", dpi=220)
    plt.close()


def plot_overview(all_rows):
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    show_is = [2, 8, 16, 64]
    for ax, trip_count in zip(axes.flat, show_is):
        rows = sorted(all_rows[trip_count], key=lambda r: r["vadds_per_loop"])
        xs = [r["vadds_per_loop"] for r in rows]
        sim = [r["cycles_norm"] for r in rows]
        frm = [r["formula_norm"] for r in rows]
        ax.plot(xs, sim, marker="o", linewidth=2.4, markersize=7, label="Simulator")
        ax.plot(xs, frm, marker="s", linewidth=2.2, markersize=6, linestyle="--", label="Strict Formula")
        ax.set_xscale("log", base=2)
        ax.set_title(f"I={trip_count}", fontsize=16)
        ax.set_xticks(xs)
        ax.set_xticklabels([str(x) for x in xs], fontsize=11)
        ax.tick_params(axis="y", labelsize=11)
        ax.grid(True, alpha=0.3)
    axes[0, 0].legend(fontsize=11)
    fig.supxlabel("VADDS per Loop (log2 scale)", fontsize=16)
    fig.supylabel("Normalized U-curve", fontsize=16)
    fig.suptitle("64-VADDS Chain: Strict Formula vs Simulator", fontsize=18)
    plt.tight_layout()
    plt.savefig(RESULTS_ROOT / "u_curve_compare_overview_strict.png", dpi=220)
    plt.close()


def main():
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    all_rows = {}
    for trip_count in TRIP_COUNTS:
        src_json = SRC_ROOT / f"I{trip_count}" / "compare_results.json"
        rows = json.loads(src_json.read_text())
        for r in rows:
            n = r["vadds_per_loop"]
            c = r["chain_cost"]
            segments = r["loops"]
            r["formula_score"] = (trip_count * c / strict_weff(n) + 7.0) * segments
        fmin = min(r["formula_score"] for r in rows)
        smin = min(r["cycles"] for r in rows)
        for r in rows:
            r["formula_norm"] = r["formula_score"] / fmin
            r["cycles_norm"] = r["cycles"] / smin
        trip_dir = RESULTS_ROOT / f"I{trip_count}"
        trip_dir.mkdir(parents=True, exist_ok=True)
        (trip_dir / "compare_results_strict.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        plot_trip(trip_dir, rows, trip_count)
        all_rows[trip_count] = rows
    plot_overview(all_rows)


if __name__ == "__main__":
    main()
