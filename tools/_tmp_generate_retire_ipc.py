from pathlib import Path
from collections import Counter
from PIL import Image, ImageDraw, ImageFont
import re
import csv
import json

LINE_RE = re.compile(
    r"^\[info\]\s+\[PERF\]\s+\[(?P<issue>\d+)\]\s+EXU\s+instr_name\s+(?P<instr>\S+)"
    r".*?\sinstr_id\s+(?P<instr_id>\d+)\s+PC\s+(?P<pc>[0-9a-fA-F]+)"
    r"\s+retire\s+(?P<retire>\d+)\s+.*?exu_id:(?P<exu_id>\d+)"
)

OUT = Path(r"d:\VfSimulator\results\cce_IPC")
OUT.mkdir(parents=True, exist_ok=True)
FONT = ImageFont.load_default()
WINDOW = 32
LOG = OUT / "_tmp_generate_retire_ipc.log"


def log(msg: str):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run(case: str):
    log(f"{case}: start")
    dump = Path(r"d:\VfSimulator\cce_dump") / case / "core0.veccore0.rvec.EXU.dump"
    rows = []
    for line in dump.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = LINE_RE.search(line)
        if m:
            rows.append(
                {
                    "retire": int(m.group("retire")),
                    "instr": m.group("instr"),
                    "exu_id": int(m.group("exu_id")),
                }
            )
    log(f"{case}: parsed_rows={len(rows)}")

    retire_counts = Counter(r["retire"] for r in rows)
    first_cycle = min(retire_counts)
    last_cycle = max(retire_counts)
    xs = list(range(first_cycle, last_cycle + 1))
    ipc = []
    current_sum = 0
    for t in xs:
        current_sum += retire_counts.get(t, 0)
        old_t = t - WINDOW
        if old_t >= first_cycle:
            current_sum -= retire_counts.get(old_t, 0)
        ipc.append(current_sum / WINDOW)
    log(f"{case}: built_series={len(xs)}")

    stem = f"{case}_core0_veccore0_rvec_EXU_retire_win{WINDOW}"
    csv_path = OUT / f"{stem}.csv"
    png_path = OUT / f"{stem}.png"
    json_path = OUT / f"{stem}_summary.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["cycle", "retire_count_at_cycle", f"retire_ipc_window_{WINDOW}"])
        for x, y in zip(xs, ipc):
            writer.writerow([x, retire_counts.get(x, 0), f"{y:.6f}"])
    log(f"{case}: wrote_csv")

    width, height, pad = 1400, 700, 60
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.line((pad, height - pad, width - pad, height - pad), fill="#111827", width=2)
    draw.line((pad, pad, pad, height - pad), fill="#111827", width=2)

    y_max = max(max(ipc, default=0.0), 1.0)
    for i in range(6):
        y = pad + i * (height - 2 * pad) / 5
        draw.line((pad, y, width - pad, y), fill="#e5e7eb", width=1)

    tick_positions = [xs[0]] if len(xs) <= 1 else [xs[round(i * (len(xs) - 1) / 7)] for i in range(8)]
    seen = set()
    tick_positions = [x for x in tick_positions if not (x in seen or seen.add(x))]
    for x in tick_positions:
        px = pad + (x - xs[0]) / max(1, xs[-1] - xs[0]) * (width - 2 * pad)
        draw.line((px, pad, px, height - pad), fill="#f3f4f6", width=1)

    x_span = max(1, xs[-1] - xs[0])
    y_span = max(1e-9, y_max)
    prev = None
    for x, y in zip(xs, ipc):
        px = int(round(pad + (x - xs[0]) / x_span * (width - 2 * pad)))
        py = int(round(height - pad - (y / y_span) * (height - 2 * pad)))
        if prev is not None:
            draw.line((prev[0], prev[1], px, py), fill="#dc2626", width=2)
        prev = (px, py)
    log(f"{case}: before_save_png")
    image.save(png_path)
    log(f"{case}: after_save_png")

    summary = {
        "instruction_count": len(rows),
        "first_retire_cycle": first_cycle,
        "last_retire_cycle": last_cycle,
        "window": WINDOW,
        "max_retire_ipc": max(ipc) if ipc else 0.0,
        "avg_retire_ipc": sum(ipc) / len(ipc) if ipc else 0.0,
    }
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(f"{case}: wrote_json")
    print(png_path)


if __name__ == "__main__":
    run("src_fanout_probe")
    run("GeLU_poly")
