import json
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXE = Path(r"D:\miniconda3\envs\vfsim\python.exe")
TRACE_ROOT = ROOT / "VFtest" / "vadds_group_segment_tests"
RESULT_ROOT = ROOT / "results" / "VADDS_group_segment_test"


def inst(op: str, dst: str, src: str) -> dict:
    return {"type": "inst", "op": op, "dst": [dst], "src": [src]}


def divisors(n: int) -> list[int]:
    return [d for d in [1, 2, 4, 8, 16, 32, 64, 128, 256, 512] if d <= n and n % d == 0]


def generate_trace(branch_count: int, branch_depth: int, group_size: int, segment_size: int, trip_count: int) -> dict:
    assert branch_count % group_size == 0
    assert branch_depth % segment_size == 0

    num_groups = branch_count // group_size
    num_segments = branch_depth // segment_size
    regs = [f"V{i}" for i in range(1, branch_count + 1)]

    program = []
    for group_idx in range(num_groups):
        group_regs = regs[group_idx * group_size : (group_idx + 1) * group_size]
        for seg_idx in range(num_segments):
            body = []
            for reg_idx, reg in enumerate(group_regs):
                global_branch_idx = group_idx * group_size + reg_idx
                if seg_idx == 0:
                    src_mem = f"mem_in_{global_branch_idx}"
                else:
                    src_mem = f"mem_inter_g{group_idx}_b{global_branch_idx}"
                body.append(inst("VLD", reg, src_mem))

            for _ in range(segment_size):
                for reg in group_regs:
                    body.append(inst("VADDS", reg, reg))

            for reg_idx, reg in enumerate(group_regs):
                global_branch_idx = group_idx * group_size + reg_idx
                if seg_idx == num_segments - 1:
                    dst_mem = f"mem_out_{global_branch_idx}"
                else:
                    dst_mem = f"mem_inter_g{group_idx}_b{global_branch_idx}"
                body.append({"type": "inst", "op": "VST", "dst": [dst_mem], "src": [reg]})

            program.append({"type": "loop", "iters": "I", "unroll": 1, "body": body})

    return {"dtype": "fp32", "params": {"I": trip_count}, "program": program}


def parse_cycles(out_dir: Path) -> int:
    done_path = out_dir / "done_by_cycle.json"
    max_done = 0
    with done_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            max_done = max(max_done, int(obj.get("cycle", obj.get("cy", 0))))
    return max_done


def run_case(trace_path: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(PYTHON_EXE), str(ROOT / "main.py"), "--trace", str(trace_path), "--out_dir", str(out_dir)],
        check=True,
        cwd=str(ROOT),
    )
    return parse_cycles(out_dir)


def plot_heatmap(rows: list[dict], out_path: Path, trip_count: int, branch_count: int, branch_depth: int) -> None:
    group_sizes = sorted({row["group_size"] for row in rows})
    segment_sizes = sorted({row["segment_size"] for row in rows}, reverse=True)
    heat = np.full((len(segment_sizes), len(group_sizes)), np.nan, dtype=float)

    best = min(rows, key=lambda row: row["cycles"])
    for row in rows:
        y = segment_sizes.index(row["segment_size"])
        x = group_sizes.index(row["group_size"])
        heat[y, x] = row["cycles_per_op"]

    fig, ax = plt.subplots(figsize=(10, 7))
    im = ax.imshow(heat, cmap="viridis", aspect="auto")

    ax.set_xticks(range(len(group_sizes)))
    ax.set_xticklabels(group_sizes, fontsize=13)
    ax.set_yticks(range(len(segment_sizes)))
    ax.set_yticklabels(segment_sizes, fontsize=13)
    ax.set_xlabel("Group Size (branches per loop)", fontsize=15)
    ax.set_ylabel("Segment Size (ops per branch per loop)", fontsize=15)
    ax.set_title(
        f"VADDS 2D Sweet Spot Heatmap\nB={branch_count}, depth={branch_depth}, I={trip_count}",
        fontsize=18,
    )

    for row in rows:
        y = segment_sizes.index(row["segment_size"])
        x = group_sizes.index(row["group_size"])
        text = f"{row['cycles_per_op']:.3f}"
        if row["group_size"] == best["group_size"] and row["segment_size"] == best["segment_size"]:
            text += "\n*"
        ax.text(x, y, text, ha="center", va="center", color="white", fontsize=10, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Cycles / (512 * I)", fontsize=13)
    cbar.ax.tick_params(labelsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def main() -> None:
    branch_count = 8
    branch_depth = 64
    trip_count = 16
    group_sizes = divisors(branch_count)
    segment_sizes = divisors(branch_depth)

    if TRACE_ROOT.exists():
        shutil.rmtree(TRACE_ROOT)
    TRACE_ROOT.mkdir(parents=True, exist_ok=True)
    if RESULT_ROOT.exists():
        shutil.rmtree(RESULT_ROOT)
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)

    rows = []
    trace_dir = TRACE_ROOT / f"B{branch_count}_D{branch_depth}_I{trip_count}"
    trace_dir.mkdir(parents=True, exist_ok=True)
    result_dir = RESULT_ROOT / f"B{branch_count}_D{branch_depth}_I{trip_count}"
    result_dir.mkdir(parents=True, exist_ok=True)

    for group_size in group_sizes:
        for segment_size in segment_sizes:
            trace_name = f"VADDS_branch8_depth64_G{group_size}_S{segment_size}_I{trip_count}.json"
            trace_path = trace_dir / trace_name
            trace_obj = generate_trace(branch_count, branch_depth, group_size, segment_size, trip_count)
            trace_path.write_text(json.dumps(trace_obj, indent=2), encoding="utf-8")

            out_dir = result_dir / trace_path.stem
            cycles = run_case(trace_path, out_dir)
            rows.append(
                {
                    "branch_count": branch_count,
                    "branch_depth": branch_depth,
                    "trip_count": trip_count,
                    "group_size": group_size,
                    "segment_size": segment_size,
                    "ops_per_loop": group_size * segment_size,
                    "cycles": cycles,
                    "cycles_per_op": cycles / (branch_count * branch_depth * trip_count),
                }
            )

    rows.sort(key=lambda row: (row["group_size"], row["segment_size"]))
    (result_dir / "sweep_results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with (result_dir / "sweep_results.txt").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(
                f"{row['group_size']} {row['segment_size']} {row['ops_per_loop']} "
                f"{row['cycles']} {row['cycles_per_op']:.6f}\n"
            )

    plot_heatmap(rows, result_dir / "group_segment_heatmap.png", trip_count, branch_count, branch_depth)


if __name__ == "__main__":
    main()
