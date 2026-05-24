#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import math
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "results"
DEST_ROOT = ROOT / "results" / "VADDS_test"
TRIP_COUNTS = [2, 4, 8, 16, 32, 64, 128]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def copy_tree(src: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def plot_overview(all_rows):
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    plt.figure(figsize=(11, 8))
    xticks = sorted({r["vadds_per_loop"] for rows in all_rows.values() for r in rows})
    for trip_count in sorted(all_rows):
        rows = sorted(all_rows[trip_count], key=lambda r: r["vadds_per_loop"])
        xs = [r["vadds_per_loop"] for r in rows]
        ys = [r["cycles"] / (512 * trip_count) for r in rows]
        plt.plot(xs, ys, marker="o", linewidth=2.2, markersize=6, label=f"I={trip_count}")
    plt.xscale("log", base=2)
    plt.xlabel("VADDS per Loop (log2 scale)", fontsize=16)
    plt.ylabel("Cycles / (512 * I)", fontsize=16)
    plt.title("VADDS Sweet Spot Overview", fontsize=18)
    plt.xticks(xticks, [str(x) for x in xticks], fontsize=13)
    plt.yticks(fontsize=13)
    plt.legend(fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(DEST_ROOT / "fusion_sweep_vadds_multii_vadds_overview.png", dpi=220)
    plt.close()


def main():
    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    all_rows = {}

    for trip_count in TRIP_COUNTS:
        src_dir = SRC_ROOT / f"fusion_sweep_singlev1_i{trip_count}_20260321"
        dst_dir = DEST_ROOT / f"I{trip_count}"
        copy_tree(src_dir, dst_dir)
        all_rows[trip_count] = load_json(dst_dir / "sweep_results_with_ipc.json")

    plot_overview(all_rows)


if __name__ == "__main__":
    main()
