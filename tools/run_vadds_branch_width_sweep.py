import json
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXE = Path(r"D:\miniconda3\envs\vfsim\python.exe")
TRACE_ROOT = ROOT / "VFtest" / "vadds_branch_width_tests"
RESULT_ROOT = ROOT / "results" / "VADDS_branch_test"


def make_inst(op, dst, src):
    return {"type": "inst", "op": op, "dst": [dst], "src": [src]}


def generate_trace(branch_width: int, segments: int, trip_count: int) -> dict:
    total_ops = 512
    branch_depth = total_ops // branch_width
    assert branch_depth % segments == 0
    ops_per_segment = branch_depth // segments
    regs = [f"V{i}" for i in range(1, branch_width + 1)]

    program = []
    for seg in range(segments):
        body = []
        for idx, reg in enumerate(regs):
            src_mem = f"mem_in_{idx}" if seg == 0 else f"mem_inter_{idx}"
            body.append(make_inst("VLD", reg, src_mem))

        for _ in range(ops_per_segment):
            for reg in regs:
                body.append(make_inst("VADDS", reg, reg))

        for idx, reg in enumerate(regs):
            dst_mem = f"mem_out_{idx}" if seg == segments - 1 else f"mem_inter_{idx}"
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


def plot_branch_width_overview(results_by_width: dict[int, list[dict]], out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    axes = axes.flatten()

    for ax, branch_width in zip(axes, sorted(results_by_width)):
        data = results_by_width[branch_width]
        x = [item["ops_total_per_loop"] for item in data]
        y = [item["cycles_per_op"] for item in data]
        best = min(data, key=lambda item: item["cycles"])

        ax.plot(x, y, marker="o", linewidth=2.5, markersize=8)
        ax.scatter(
            [best["ops_total_per_loop"]],
            [best["cycles_per_op"]],
            color="red",
            marker="*",
            s=220,
            zorder=5,
            label=f"best: {best['segments']} seg",
        )
        ax.set_xscale("log", base=2)
        ax.set_title(f"Branch Width = {branch_width}", fontsize=18)
        ax.set_xlabel("Total VADDS per Loop (log2 scale)", fontsize=15)
        ax.set_ylabel("Cycles / (512 * I)", fontsize=15)
        ax.tick_params(axis="both", labelsize=13)
        ax.grid(True, which="both", alpha=0.35)
        ax.legend(fontsize=12)

    fig.suptitle("VADDS Branch-Width Sweet Spot (I=16)", fontsize=22)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def main() -> None:
    trip_count = 16
    branch_widths = [1, 2, 4, 8]

    if TRACE_ROOT.exists():
        shutil.rmtree(TRACE_ROOT)
    TRACE_ROOT.mkdir(parents=True, exist_ok=True)

    if RESULT_ROOT.exists():
        shutil.rmtree(RESULT_ROOT)
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)

    results_by_width = {}

    for branch_width in branch_widths:
        branch_depth = 512 // branch_width
        segment_candidates = [s for s in [1, 2, 4, 8, 16, 32, 64, 128, 256, 512] if s <= branch_depth and branch_depth % s == 0]
        width_trace_dir = TRACE_ROOT / f"I{trip_count}" / f"B{branch_width}"
        width_trace_dir.mkdir(parents=True, exist_ok=True)
        width_result_dir = RESULT_ROOT / f"I{trip_count}" / f"B{branch_width}"
        width_result_dir.mkdir(parents=True, exist_ok=True)

        rows = []
        for segments in segment_candidates:
            ops_per_branch_per_loop = branch_depth // segments
            ops_total_per_loop = 512 // segments
            trace_name = f"VADDS_branch_B{branch_width}_{segments}segments_{ops_total_per_loop}ops.json"
            trace_path = width_trace_dir / trace_name
            trace_obj = generate_trace(branch_width, segments, trip_count)
            trace_path.write_text(json.dumps(trace_obj, indent=2), encoding="utf-8")

            out_dir = width_result_dir / trace_path.stem
            cycles = run_case(trace_path, out_dir)
            rows.append(
                {
                    "branch_width": branch_width,
                    "segments": segments,
                    "ops_per_branch_per_loop": ops_per_branch_per_loop,
                    "ops_total_per_loop": ops_total_per_loop,
                    "cycles": cycles,
                    "cycles_per_op": cycles / (512 * trip_count),
                }
            )

        rows.sort(key=lambda item: item["segments"])
        (width_result_dir / "sweep_results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        with (width_result_dir / "sweep_results.txt").open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(
                    f"{row['branch_width']} {row['segments']} {row['ops_per_branch_per_loop']} "
                    f"{row['ops_total_per_loop']} {row['cycles']} {row['cycles_per_op']:.6f}\n"
                )

        fig, ax = plt.subplots(figsize=(10, 7))
        x = [item["ops_total_per_loop"] for item in rows]
        y = [item["cycles_per_op"] for item in rows]
        best = min(rows, key=lambda item: item["cycles"])
        ax.plot(x, y, marker="o", linewidth=2.5, markersize=8, label=f"B={branch_width}")
        ax.scatter(
            [best["ops_total_per_loop"]],
            [best["cycles_per_op"]],
            color="red",
            marker="*",
            s=220,
            zorder=5,
            label=f"best: {best['segments']} seg",
        )
        ax.set_xscale("log", base=2)
        ax.set_title(f"VADDS Branch Width {branch_width} (I=16)", fontsize=18)
        ax.set_xlabel("Total VADDS per Loop (log2 scale)", fontsize=15)
        ax.set_ylabel("Cycles / (512 * I)", fontsize=15)
        ax.tick_params(axis="both", labelsize=13)
        ax.grid(True, which="both", alpha=0.35)
        ax.legend(fontsize=12)
        fig.tight_layout()
        fig.savefig(width_result_dir / f"branch_width_{branch_width}_overview.png", dpi=200)
        plt.close(fig)

        results_by_width[branch_width] = rows

    plot_branch_width_overview(results_by_width, RESULT_ROOT / f"I{trip_count}" / "branch_width_overview.png")


if __name__ == "__main__":
    main()
