import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt


EXU_LINE_RE = re.compile(
    r"^\[info\]\s+\[PERF\]\s+\[(?P<issue>\d+)\]\s+EXU\s+instr_name\s+(?P<instr>\S+)"
    r".*?\sinstr_id\s+(?P<instr_id>\d+)\s+PC\s+(?P<pc>[0-9a-fA-F]+)"
    r"\s+retire\s+(?P<retire>\d+)\s+.*?exu_id:(?P<exu_id>\d+)"
)


def parse_args():
    p = argparse.ArgumentParser(description="Compare CCE compute IPC (EXU issue) vs model compute IPC (start_by_cycle)")
    p.add_argument("--cce-exu-dump", required=True, help="Path to core0.veccore0.rvec.EXU.dump")
    p.add_argument("--model-start-log", required=True, help="Path to model start_by_cycle.json")
    p.add_argument("--window", type=int, default=32, help="Sliding window size in cycles")
    p.add_argument("--out-png", required=True, help="Output PNG path")
    p.add_argument("--out-csv", default=None, help="Optional output CSV path")
    p.add_argument("--title", default="CCE vs Model Compute IPC", help="Plot title")
    p.add_argument("--align-start", action="store_true", help="Align both curves by their first non-zero IPC cycle")
    return p.parse_args()


def parse_cce_issue_cycles(path: Path):
    counts = Counter()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = EXU_LINE_RE.search(line)
        if not m:
            continue
        instr = m.group("instr").upper()
        if instr in {
            "RV_PSET",
            "PSET",
            "RV_SEND",
            "SEND",
            "RV_VLD",
            "VLD",
            "RV_VST",
            "VST",
        }:
            continue
        issue_cy = int(m.group("issue"))
        counts[issue_cy] += 1
    return counts


def parse_model_compute_start_cycles(path: Path):
    counts = Counter()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            op = obj.get("op", "")
            if op in ("VLD", "VST"):
                continue
            cy = int(obj.get("cy", 0))
            counts[cy] += 1
    return counts


def build_window_ipc(counts: Counter, window: int):
    first = min(counts) if counts else 0
    last = max(counts) if counts else 0
    xs = list(range(first - window, last + window + 1))
    cur = 0
    ys = []
    for t in xs:
        cur += counts.get(t, 0)
        cur -= counts.get(t - window, 0)
        ys.append(cur / float(window))
    return xs, ys


def remap_to_union(xs, ys, union_xs):
    m = {x: y for x, y in zip(xs, ys)}
    return [m.get(x, 0.0) for x in union_xs]


def first_nonzero_x(xs, ys):
    for x, y in zip(xs, ys):
        if y > 0:
            return x
    return xs[0] if xs else 0


def main():
    args = parse_args()
    cce_path = Path(args.cce_exu_dump)
    model_path = Path(args.model_start_log)
    out_png = Path(args.out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    cce_counts = parse_cce_issue_cycles(cce_path)
    model_counts = parse_model_compute_start_cycles(model_path)
    if not cce_counts:
        raise SystemExit(f"No rows parsed from CCE EXU dump: {cce_path}")
    if not model_counts:
        raise SystemExit(f"No compute rows parsed from model start log: {model_path}")

    cce_x, cce_y = build_window_ipc(cce_counts, args.window)
    model_x, model_y = build_window_ipc(model_counts, args.window)

    if args.align_start:
        cce_t0 = first_nonzero_x(cce_x, cce_y)
        model_t0 = first_nonzero_x(model_x, model_y)
        cce_x = [x - cce_t0 for x in cce_x]
        model_x = [x - model_t0 for x in model_x]
    union_x = list(range(min(cce_x[0], model_x[0]), max(cce_x[-1], model_x[-1]) + 1))
    cce_u = remap_to_union(cce_x, cce_y, union_x)
    model_u = remap_to_union(model_x, model_y, union_x)

    plt.figure(figsize=(12, 6), constrained_layout=True)
    plt.plot(union_x, cce_u, color="#dc2626", linewidth=2.5, label="CCE (compute issue IPC)")
    plt.plot(union_x, model_u, color="#2563eb", linewidth=2.5, label="Model (compute start IPC)")
    plt.title(args.title)
    plt.xlabel("cycle" if not args.align_start else "aligned cycle (t=0 at first non-zero IPC)")
    plt.ylabel(f"IPC (window={args.window})")
    plt.grid(True, alpha=0.25)
    plt.legend(loc="best")
    plt.savefig(out_png, dpi=150)
    plt.close()

    if args.out_csv:
        out_csv = Path(args.out_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["cycle", "cce_compute_ipc", "model_compute_ipc"])
            for x, a, b in zip(union_x, cce_u, model_u):
                w.writerow([x, f"{a:.6f}", f"{b:.6f}"])

    print(f"[DONE] png: {out_png}")
    if args.out_csv:
        print(f"[DONE] csv: {args.out_csv}")


if __name__ == "__main__":
    main()
