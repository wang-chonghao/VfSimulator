import argparse
import csv
import re
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt


LINE_RE = re.compile(
    r"^\[info\]\s+\[PERF\]\s+\[(?P<issue>\d+)\]\s+EXU\s+instr_name\s+(?P<instr>\S+)"
    r".*?\sinstr_id\s+(?P<instr_id>\d+)\s+PC\s+(?P<pc>[0-9a-fA-F]+)"
    r"\s+retire\s+(?P<retire>\d+)\s+.*?exu_id:(?P<exu_id>\d+)"
)

FIG_WIDTH = 12
FIG_HEIGHT = 6
FONT_SIZE = 12
DEFAULT_WINDOW = 32


def parse_args():
    p = argparse.ArgumentParser(description="Plot two retire sliding-window IPC curves from CCE simulator EXU dumps")
    p.add_argument("dump_a", help="Path to baseline EXU dump")
    p.add_argument("dump_b", help="Path to optimized EXU dump")
    p.add_argument("--label-a", default="baseline", help="Legend label for dump_a")
    p.add_argument("--label-b", default="optimized", help="Legend label for dump_b")
    p.add_argument("--out-dir", help="Output directory, default: results/cce_IPC")
    p.add_argument("--stem", default=None, help="Output stem")
    p.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="Sliding window size in cycles")
    p.add_argument("--title", default=None, help="Optional chart title")
    return p.parse_args()


def parse_exu_dump(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        rows.append(
            {
                "issue": int(m.group("issue")),
                "retire": int(m.group("retire")),
                "instr": m.group("instr"),
                "instr_id": int(m.group("instr_id")),
                "pc": m.group("pc"),
                "exu_id": int(m.group("exu_id")),
            }
        )
    return rows


def build_retire_series(rows, window):
    retire_counts = Counter(r["retire"] for r in rows)
    first_cycle = min(retire_counts)
    last_cycle = max(retire_counts)
    xs = list(range(first_cycle - window, last_cycle + window + 1))

    current_sum = 0
    ipc = []
    for t in xs:
        current_sum += retire_counts.get(t, 0)
        old_t = t - window
        if old_t >= first_cycle - window:
            current_sum -= retire_counts.get(old_t, 0)
        ipc.append(current_sum / window)
    return xs, ipc, retire_counts


def build_union_series(xs, ys, retire_counts, union_xs):
    y_by_x = {x: y for x, y in zip(xs, ys)}
    return [y_by_x.get(x, 0.0) for x in union_xs], [retire_counts.get(x, 0) for x in union_xs]


def write_csv(path: Path, xs, counts_a, ipc_a, counts_b, ipc_b, label_a, label_b, window):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "cycle",
                f"{label_a}_retire_count_at_cycle",
                f"{label_a}_retire_ipc_window_{window}",
                f"{label_b}_retire_count_at_cycle",
                f"{label_b}_retire_ipc_window_{window}",
            ]
        )
        for x, ca, ia, cb, ib in zip(xs, counts_a, ipc_a, counts_b, ipc_b):
            writer.writerow([x, ca, f"{ia:.6f}", cb, f"{ib:.6f}"])


def write_plots(svg_path: Path, png_path: Path, xs, ipc_a, ipc_b, title, label_a, label_b, window):
    plt.rcParams.update(
        {
            "font.size": FONT_SIZE,
            "axes.titlesize": FONT_SIZE,
            "axes.labelsize": FONT_SIZE,
            "xtick.labelsize": FONT_SIZE,
            "ytick.labelsize": FONT_SIZE,
            "legend.fontsize": FONT_SIZE,
        }
    )

    fig, ax = plt.subplots(figsize=(FIG_WIDTH, FIG_HEIGHT), constrained_layout=True)
    ax.plot(xs, ipc_a, color="#dc2626", linewidth=2.5, label=label_a)
    ax.plot(xs, ipc_b, color="#2563eb", linewidth=2.5, label=label_b)
    ax.set_title(title)
    ax.set_xlabel("cycle")
    ax.set_ylabel("IPC (instr / cycle)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")
    ax.text(
        0.01,
        0.98,
        f"retire-based sliding window IPC, window={window} cycles",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=FONT_SIZE,
        color="#4b5563",
    )
    fig.savefig(svg_path, format="svg", dpi=150)
    fig.savefig(png_path, format="png", dpi=150)
    plt.close(fig)


def main():
    args = parse_args()
    dump_a = Path(args.dump_a)
    dump_b = Path(args.dump_b)
    if not dump_a.is_file():
        raise SystemExit(f"Dump file not found: {dump_a}")
    if not dump_b.is_file():
        raise SystemExit(f"Dump file not found: {dump_b}")
    if args.window <= 0:
        raise SystemExit("window must be > 0")

    repo_root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir) if args.out_dir else repo_root / "results" / "cce_IPC"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_a = parse_exu_dump(dump_a)
    rows_b = parse_exu_dump(dump_b)
    if not rows_a or not rows_b:
        raise SystemExit("No EXU PERF rows parsed from one or both dumps")

    xs_a, ipc_a, counts_a = build_retire_series(rows_a, args.window)
    xs_b, ipc_b, counts_b = build_retire_series(rows_b, args.window)
    union_xs = list(range(min(xs_a[0], xs_b[0]), max(xs_a[-1], xs_b[-1]) + 1))
    ipc_a_u, counts_a_u = build_union_series(xs_a, ipc_a, counts_a, union_xs)
    ipc_b_u, counts_b_u = build_union_series(xs_b, ipc_b, counts_b, union_xs)

    stem = args.stem or f"{args.label_a}_vs_{args.label_b}_core0_veccore0_rvec_EXU_retire_win{args.window}"
    title = args.title or f"{args.label_a} vs {args.label_b} core0 retire IPC"

    csv_path = out_dir / f"{stem}.csv"
    svg_path = out_dir / f"{stem}.svg"
    png_path = out_dir / f"{stem}.png"

    write_csv(csv_path, union_xs, counts_a_u, ipc_a_u, counts_b_u, ipc_b_u, args.label_a, args.label_b, args.window)
    write_plots(svg_path, png_path, union_xs, ipc_a_u, ipc_b_u, title, args.label_a, args.label_b, args.window)

    print(f"[DONE] csv : {csv_path}")
    print(f"[DONE] svg : {svg_path}")
    print(f"[DONE] png : {png_path}")


if __name__ == "__main__":
    main()
