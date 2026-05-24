import argparse
import json
import math
import re
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def parse_name(path: Path) -> tuple[int, int, int]:
    m = re.search(r"_G(\d+)_S(\d+)_I(\d+)", path.stem)
    if not m:
        raise ValueError(f"Cannot parse G/S/I from {path.name}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def infer_branch_count_and_depth(obj: dict) -> tuple[int, int]:
    program = obj["program"]
    first_body = program[0]["body"]
    branch_count = sum(1 for inst in first_body if inst["op"] == "VLD")
    total_loops = len(program)
    group_size = branch_count
    # corrected by caller later if needed
    return branch_count, total_loops


def count_group_size(loop_body: list[dict]) -> int:
    return sum(1 for inst in loop_body if inst["op"] == "VLD")


def draw_box(ax, x, y, w, h, text, fc, ec="#2f2f2f", fontsize=10, lw=1.5):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.05",
        linewidth=lw,
        edgecolor=ec,
        facecolor=fc,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize)


def generate(trace_path: Path, output_path: Path) -> None:
    obj = json.loads(trace_path.read_text(encoding="utf-8"))
    group_size, segment_size, trip_count = parse_name(trace_path)

    total_branches = len({src for loop in obj["program"] for inst in loop["body"] for src in inst.get("src", []) if isinstance(src, str) and src.startswith("mem_in_")})
    if total_branches == 0:
        total_branches = 8
    branch_depth = 64
    num_groups = total_branches // group_size
    num_segments = branch_depth // segment_size

    fig_w = max(10, num_segments * 1.8 + 3)
    fig_h = max(5, total_branches * 0.6 + 2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    lane_h = 0.55
    x_gap = 0.25
    box_w = 1.2
    group_gap = 0.7

    colors = ["#dceeff", "#e8f5d6", "#fde3d0", "#efe1ff"]

    for g in range(num_groups):
        y_base = total_branches - (g * group_size) - group_size
        for lane in range(group_size):
            branch_id = g * group_size + lane
            y = y_base + (group_size - 1 - lane) * lane_h
            ax.text(-0.5, y + lane_h / 2, f"B{branch_id}", ha="right", va="center", fontsize=10)
            for s in range(num_segments):
                x = s * (box_w + x_gap)
                label = f"L{s}\n{segment_size}xVADDS"
                draw_box(ax, x, y, box_w, lane_h * 0.8, label, colors[g % len(colors)], fontsize=8)
                if s < num_segments - 1:
                    ax.annotate(
                        "",
                        xy=(x + box_w + x_gap * 0.8, y + lane_h * 0.4),
                        xytext=(x + box_w, y + lane_h * 0.4),
                        arrowprops=dict(arrowstyle="->", lw=1.4, color="#444444"),
                    )

        if g < num_groups - 1:
            sep_y = total_branches - ((g + 1) * group_size)
            ax.plot(
                [-0.2, num_segments * (box_w + x_gap) - x_gap + 0.2],
                [sep_y - 0.15, sep_y - 0.15],
                linestyle="--",
                color="#9a9a9a",
                linewidth=1.0,
            )

    total_width = num_segments * (box_w + x_gap) - x_gap
    ax.text(
        total_width / 2,
        total_branches * lane_h + 0.7,
        f"8 branches x 64 VADDS, I={trip_count}\nGroup size={group_size}, Segment size={segment_size}, Groups={num_groups}, Segments/group={num_segments}",
        ha="center",
        va="bottom",
        fontsize=13,
        fontweight="bold",
    )

    # group braces / labels
    for g in range(num_groups):
        y_base = total_branches - (g * group_size) - group_size
        y_top = y_base + (group_size - 1) * lane_h + lane_h * 0.8
        y_bottom = y_base
        x = total_width + group_gap
        ax.plot([x, x], [y_bottom, y_top], color="#666666", linewidth=1.5)
        ax.plot([x, x + 0.12], [y_bottom, y_bottom], color="#666666", linewidth=1.5)
        ax.plot([x, x + 0.12], [y_top, y_top], color="#666666", linewidth=1.5)
        ax.text(x + 0.2, (y_top + y_bottom) / 2, f"Group {g}\n{group_size} branches", va="center", fontsize=10)

    ax.set_xlim(-1.0, total_width + 2.2)
    ax.set_ylim(-0.5, total_branches * lane_h + 1.6)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, dpi=220)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace")
    parser.add_argument("output")
    args = parser.parse_args()
    generate(Path(args.trace), Path(args.output))


if __name__ == "__main__":
    main()
