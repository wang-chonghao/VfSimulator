#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "results" / "VEXP512_formula_compare_a6"
TRIP_COUNTS = [2, 4, 8, 16, 32, 64, 128]
LONG_FWD = 13.0
ALPHA = 6.0
N0 = 8.0
N1 = 0.7
K0 = 8.0
K1 = 0.6


def dump_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def weff(n: int) -> float:
    return 1.0 + K0 / (((n / float(N0)) ** N1) + K1)


def sync_cost(trip_count: int) -> float:
    return 7.0 + ALPHA * math.log(trip_count, 2)


def formula_score(trip_count: int, loops: int, total_insts: int = 512) -> float:
    n = total_insts // loops
    c = LONG_FWD * n
    segments = loops
    return (trip_count * c / weff(n) + sync_cost(trip_count)) * segments


def plot_trip(trip_dir: Path, rows, trip_count: int):
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    rows = sorted(rows, key=lambda r: r["insts_per_loop"])
    xs = [r["insts_per_loop"] for r in rows]
    sim = [r["cycles_norm"] for r in rows]
    frm = [r["formula_norm"] for r in rows]

    plt.figure(figsize=(10, 7))
    plt.plot(xs, sim, marker="o", linewidth=2.5, markersize=8, label="Simulator")
    plt.plot(xs, frm, marker="s", linewidth=2.5, markersize=7, linestyle="--", label="Formula")
    plt.xscale("log", base=2)
    plt.xlabel("VEXP per Loop (log2 scale)", fontsize=16)
    plt.ylabel("Normalized U-curve", fontsize=16)
    plt.title(f"512-VEXP Chain U-curve Compare (I={trip_count})", fontsize=18)
    plt.xticks(xs, [str(x) for x in xs], fontsize=13)
    plt.yticks(fontsize=13)
    plt.legend(fontsize=13)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(trip_dir / f"u_curve_compare_i{trip_count}_vexp_a6.png", dpi=220)
    plt.close()


def plot_overview(all_rows):
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    show_is = [2, 8, 16, 64]
    for ax, trip_count in zip(axes.flat, show_is):
        rows = sorted(all_rows[trip_count], key=lambda r: r["insts_per_loop"])
        xs = [r["insts_per_loop"] for r in rows]
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
    fig.supxlabel("VEXP per Loop (log2 scale)", fontsize=16)
    fig.supylabel("Normalized U-curve", fontsize=16)
    fig.suptitle("512-VEXP Chain: Formula vs Simulator", fontsize=18)
    plt.tight_layout()
    plt.savefig(RESULTS_ROOT / "u_curve_compare_overview_vexp_a6.png", dpi=220)
    plt.close()


def main():
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    all_rows = {}

    for trip_count in TRIP_COUNTS:
        src_rows = json.loads((ROOT / "results" / "VEXP_test" / f"I{trip_count}" / "sweep_results_with_ipc.json").read_text(encoding="utf-8"))
        trip_dir = RESULTS_ROOT / f"I{trip_count}"
        trip_dir.mkdir(parents=True, exist_ok=True)

        rows = []
        for r in src_rows:
            loops = int(r["loops"])
            insts_per_loop = int(r["insts_per_loop"])
            rows.append({
                "loops": loops,
                "insts_per_loop": insts_per_loop,
                "chain_cost": LONG_FWD * insts_per_loop,
                "formula_score": formula_score(trip_count, loops),
                "cycles": float(r["cycles"]),
            })

        sim_min = min(r["cycles"] for r in rows)
        frm_min = min(r["formula_score"] for r in rows)
        for r in rows:
            r["cycles_norm"] = r["cycles"] / sim_min
            r["formula_norm"] = r["formula_score"] / frm_min

        rows.sort(key=lambda r: r["loops"])
        dump_json(trip_dir / "compare_results_vexp_a6.json", rows)
        plot_trip(trip_dir, rows, trip_count)
        all_rows[trip_count] = rows

    plot_overview(all_rows)


if __name__ == "__main__":
    main()
