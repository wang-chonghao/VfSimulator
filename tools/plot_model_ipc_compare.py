import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def build_counts(events: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    max_cycle = max((int(e["cy"]) for e in events), default=-1) + 1
    vld = np.zeros(max_cycle, dtype=float)
    vst = np.zeros(max_cycle, dtype=float)
    comp = np.zeros(max_cycle, dtype=float)
    for e in events:
        cy = int(e["cy"])
        op = e["op"]
        if op == "VLD":
            vld[cy] += 1.0
        elif op == "VST":
            vst[cy] += 1.0
        else:
            comp[cy] += 1.0
    return comp, vld, vst


def moving_average(counts: np.ndarray, window: int) -> np.ndarray:
    if counts.size == 0:
        return counts
    kernel = np.ones(window, dtype=float) / float(window)
    return np.convolve(counts, kernel, mode="same")


def first_non_zero_cycle(*arrays: np.ndarray) -> int:
    for i in range(max((arr.size for arr in arrays), default=0)):
        total = 0.0
        for arr in arrays:
            if i < arr.size:
                total += float(arr[i])
        if total > 0:
            return i
    return 0


def align_curve(curve: np.ndarray, start_cycle: int) -> tuple[np.ndarray, np.ndarray]:
    if start_cycle >= curve.size:
        return np.array([], dtype=float), np.array([], dtype=float)
    xs = np.arange(curve.size - start_cycle, dtype=float)
    ys = curve[start_cycle:]
    return xs, ys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot aligned IPC comparison for dual-ports vs three-ports model logs."
    )
    parser.add_argument("--dual-log", required=True, help="Path to dual_ports done_by_cycle.json")
    parser.add_argument("--three-log", required=True, help="Path to three_ports done_by_cycle.json")
    parser.add_argument("--window", type=int, default=25, help="Moving average window size")
    parser.add_argument("--title", default="Dual Ports vs Three Ports IPC", help="Plot title")
    parser.add_argument("--out", required=True, help="Output PNG path")
    args = parser.parse_args()

    dual_events = load_events(Path(args.dual_log))
    three_events = load_events(Path(args.three_log))
    if not dual_events or not three_events:
        raise SystemExit("one of the logs is empty")

    dual_comp, dual_vld, dual_vst = build_counts(dual_events)
    three_comp, three_vld, three_vst = build_counts(three_events)

    dual_comp_ma = moving_average(dual_comp, args.window)
    three_comp_ma = moving_average(three_comp, args.window)

    dual_start = first_non_zero_cycle(dual_comp_ma)
    three_start = first_non_zero_cycle(three_comp_ma)

    dual_x_comp, dual_y_comp = align_curve(dual_comp_ma, dual_start)
    three_x_comp, three_y_comp = align_curve(three_comp_ma, three_start)

    max_len = int(max(len(dual_y_comp), len(three_y_comp)))
    x_union = np.arange(max_len, dtype=float)

    def pad_to(arr: np.ndarray, length: int) -> np.ndarray:
        if len(arr) >= length:
            return arr[:length]
        out = np.full(length, np.nan, dtype=float)
        out[: len(arr)] = arr
        return out

    plt.rcParams.update({"font.size": 12})
    fig, ax = plt.subplots(figsize=(14, 6), constrained_layout=True)

    ax.plot(
        x_union,
        pad_to(dual_y_comp, max_len),
        color="#2563eb",
        linewidth=2.4,
        label="Dual Ports Compute IPC",
    )
    ax.plot(
        x_union,
        pad_to(three_y_comp, max_len),
        color="#dc2626",
        linewidth=2.4,
        label="Three Ports Compute IPC",
    )
    ax.set_ylabel("IPC")
    ax.set_xlabel("Aligned cycle")
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.legend(loc="upper right")
    ax.set_title(
        f"{args.title}\nAligned by first non-zero compute IPC cycle, moving average window={args.window}"
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=220)
    print(f"Saved IPC comparison plot to {out_path}")


if __name__ == "__main__":
    main()
