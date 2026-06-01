import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

LINE_RE = re.compile(
    r"^\[info\]\s+\[PERF\]\s+\[(?P<issue>\d+)\]\s+EXU\s+instr_name\s+(?P<instr>\S+)"
    r".*?\sinstr_id\s+(?P<instr_id>\d+)\s+PC\s+(?P<pc>[0-9a-fA-F]+)"
    r"\s+retire\s+(?P<retire>\d+)\s+.*?exu_id:(?P<exu_id>\d+)"
)

PNG_WIDTH = 1400
PNG_HEIGHT = 700
DEFAULT_WINDOW = 32


def parse_args():
    p = argparse.ArgumentParser(description="Plot retire sliding-window IPC from CCE simulator EXU dump")
    p.add_argument("dump", help="Path to core0.veccore0.rvec.EXU.dump")
    p.add_argument("--out-dir", help="Output directory, default: results/cce_IPC")
    p.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="Sliding window size in cycles, default 32")
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


def build_retire_sliding_window_series(rows, window):
    retire_counts = Counter(r["retire"] for r in rows)
    first_cycle = min(retire_counts)
    last_cycle = max(retire_counts)
    xs = list(range(first_cycle, last_cycle + 1))

    current_sum = 0
    ipc = []
    for t in xs:
        current_sum += retire_counts.get(t, 0)
        old_t = t - window
        if old_t >= first_cycle:
            current_sum -= retire_counts.get(old_t, 0)
        ipc.append(current_sum / window)
    return xs, ipc, retire_counts


def write_csv(path: Path, xs, retire_ipc, retire_counts, window):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["cycle", "retire_count_at_cycle", f"retire_ipc_window_{window}"])
        for x, ipc in zip(xs, retire_ipc):
            writer.writerow([x, retire_counts.get(x, 0), f"{ipc:.6f}"])


def polyline_points(xs, ys, width, height, pad, x_min, x_max, y_max):
    pts = []
    x_span = max(1, x_max - x_min)
    y_span = max(1e-9, y_max)
    for x, y in zip(xs, ys):
        px = pad + (x - x_min) / x_span * (width - 2 * pad)
        py = height - pad - (y / y_span) * (height - 2 * pad)
        pts.append(f"{px:.2f},{py:.2f}")
    return " ".join(pts)


def write_svg(path: Path, xs, retire_ipc, title, window):
    width, height, pad = PNG_WIDTH, PNG_HEIGHT, 60
    x_min, x_max = xs[0], xs[-1]
    y_max = max(max(retire_ipc, default=0.0), 1.0)
    retire_pts = polyline_points(xs, retire_ipc, width, height, pad, x_min, x_max, y_max)
    grid = []
    for i in range(6):
        y = pad + i * (height - 2 * pad) / 5
        value = y_max * (1 - i / 5)
        grid.append(f'<line x1="{pad}" y1="{y:.2f}" x2="{width-pad}" y2="{y:.2f}" stroke="#e5e7eb" stroke-width="1" />')
        grid.append(f'<text x="10" y="{y+4:.2f}" font-size="12" fill="#374151">{value:.2f}</text>')
    x_ticks = []
    tick_count = min(8, len(xs))
    tick_positions = [x_min] if tick_count <= 1 else [xs[round(i * (len(xs) - 1) / (tick_count - 1))] for i in range(tick_count)]
    seen = set()
    tick_positions = [x for x in tick_positions if not (x in seen or seen.add(x))]
    for x in tick_positions:
        px = pad + (x - x_min) / max(1, x_max - x_min) * (width - 2 * pad)
        x_ticks.append(f'<line x1="{px:.2f}" y1="{pad}" x2="{px:.2f}" y2="{height-pad}" stroke="#f3f4f6" stroke-width="1" />')
        x_ticks.append(f'<text x="{px:.2f}" y="{height-20}" text-anchor="middle" font-size="12" fill="#374151">{x}</text>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="white" />
  <text x="{pad}" y="30" font-size="24" font-family="Arial, sans-serif" fill="#111827">{title}</text>
  <text x="{pad}" y="52" font-size="14" font-family="Arial, sans-serif" fill="#4b5563">retire-based sliding window IPC, window={window} cycles</text>
  <line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#111827" stroke-width="1.5" />
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#111827" stroke-width="1.5" />
  {''.join(grid)}
  {''.join(x_ticks)}
  <polyline fill="none" stroke="#dc2626" stroke-width="2" points="{retire_pts}" />
  <rect x="{width-220}" y="26" width="14" height="14" fill="#dc2626" />
  <text x="{width-200}" y="38" font-size="13" fill="#111827">retire sliding-window IPC</text>
  <text x="{width/2:.2f}" y="{height-4}" text-anchor="middle" font-size="14" fill="#111827">cycle</text>
  <text x="18" y="{height/2:.2f}" transform="rotate(-90 18,{height/2:.2f})" text-anchor="middle" font-size="14" fill="#111827">IPC (instr / cycle)</text>
</svg>
'''
    path.write_text(svg, encoding="utf-8")


def render_png(path: Path, xs, retire_ipc, title, window):
    width, height, pad = PNG_WIDTH, PNG_HEIGHT, 60
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    x_min, x_max = xs[0], xs[-1]
    y_max = max(max(retire_ipc, default=0.0), 1.0)

    draw.text((pad, 18), title, fill="#111827", font=font)
    draw.text(
        (pad, 45),
        f"retire-based sliding window IPC, window={window} cycles",
        fill="#4b5563",
        font=font,
    )

    draw.line((pad, height - pad, width - pad, height - pad), fill="#111827", width=2)
    draw.line((pad, pad, pad, height - pad), fill="#111827", width=2)

    for i in range(6):
        y = pad + i * (height - 2 * pad) / 5
        value = y_max * (1 - i / 5)
        draw.line((pad, y, width - pad, y), fill="#e5e7eb", width=1)
        draw.text((10, y - 8), f"{value:.2f}", fill="#374151", font=font)

    tick_count = min(8, len(xs))
    tick_positions = [x_min] if tick_count <= 1 else [xs[round(i * (len(xs) - 1) / (tick_count - 1))] for i in range(tick_count)]
    seen = set()
    tick_positions = [x for x in tick_positions if not (x in seen or seen.add(x))]
    for x in tick_positions:
        px = pad + (x - x_min) / max(1, x_max - x_min) * (width - 2 * pad)
        draw.line((px, pad, px, height - pad), fill="#f3f4f6", width=1)
        draw.text((px - 12, height - 20), str(x), fill="#374151", font=font)

    x_span = max(1, x_max - x_min)
    y_span = max(1e-9, y_max)
    points = []
    for x, y in zip(xs, retire_ipc):
        px = pad + (x - x_min) / x_span * (width - 2 * pad)
        py = height - pad - (y / y_span) * (height - 2 * pad)
        points.append((px, py))
    if len(points) >= 2:
        draw.line(points, fill="#dc2626", width=2)

    draw.rectangle((width - 220, 26, width - 206, 40), fill="#dc2626")
    draw.text((width - 200, 25), "retire sliding-window IPC", fill="#111827", font=font)
    draw.text((width / 2 - 18, height - 22), "cycle", fill="#111827", font=font)
    draw.text((8, pad - 18), "IPC", fill="#111827", font=font)

    image.save(path)


def write_summary(path: Path, rows, retire_ipc, window):
    summary = {
        "instruction_count": len(rows),
        "first_retire_cycle": min(r["retire"] for r in rows),
        "last_retire_cycle": max(r["retire"] for r in rows),
        "window": window,
        "max_retire_ipc": max(retire_ipc) if retire_ipc else 0.0,
        "avg_retire_ipc": (sum(retire_ipc) / len(retire_ipc)) if retire_ipc else 0.0,
        "instr_by_name": dict(sorted(Counter(r["instr"] for r in rows).items())),
        "instr_by_exu": dict(sorted(Counter(str(r["exu_id"]) for r in rows).items())),
    }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def derive_case_name(dump_path: Path):
    parts = list(dump_path.parts)
    if "cce_dump" in parts:
        idx = parts.index("cce_dump")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return dump_path.parent.name


def main():
    args = parse_args()
    dump_path = Path(args.dump)
    if not dump_path.is_file():
        raise SystemExit(f"Dump file not found: {dump_path}")
    if args.window <= 0:
        raise SystemExit("window must be > 0")

    repo_root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir) if args.out_dir else repo_root / "results" / "cce_IPC"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = parse_exu_dump(dump_path)
    if not rows:
        raise SystemExit(f"No EXU PERF rows parsed from: {dump_path}")

    xs, retire_ipc, retire_counts = build_retire_sliding_window_series(rows, args.window)
    case_name = derive_case_name(dump_path)
    title = args.title or f"{case_name} core0 retire IPC"
    stem = f"{case_name}_core0_veccore0_rvec_EXU_retire_win{args.window}"

    csv_path = out_dir / f"{stem}.csv"
    svg_path = out_dir / f"{stem}.svg"
    png_path = out_dir / f"{stem}.png"
    json_path = out_dir / f"{stem}_summary.json"

    write_csv(csv_path, xs, retire_ipc, retire_counts, args.window)
    write_svg(svg_path, xs, retire_ipc, title, args.window)
    render_png(png_path, xs, retire_ipc, title, args.window)
    write_summary(json_path, rows, retire_ipc, args.window)

    print(f"[DONE] parsed rows      : {len(rows)}")
    print(f"[DONE] csv             : {csv_path}")
    print(f"[DONE] svg             : {svg_path}")
    print(f"[DONE] png             : {png_path}")
    print(f"[DONE] summary         : {json_path}")


if __name__ == "__main__":
    main()
