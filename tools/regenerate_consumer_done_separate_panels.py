#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT / "results" / "fusion_sweep_singlev1_consumer_done_multii_20260401"
PANEL_DIR = BASE_DIR / "separate_panels"
TRIP_COUNTS = [2, 4, 8, 16, 32, 64, 128]
LOOPS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512]
VADDS_PER_LOOP = sorted([512 // l for l in LOOPS])


def format_tick(v: float) -> str:
    return f"{v:.4f}"


def x_for_index(idx: int, count: int) -> float:
    x0, x1 = 80.0, 670.0
    if count == 1:
        return x0
    return x0 + (x1 - x0) * idx / (count - 1)


def choose_y_range(values):
    y_min_data = min(values)
    y_max_data = max(values)
    span = y_max_data - y_min_data
    pad = max(0.03, span * 0.12)
    if span < 1e-9:
        y_lo = max(0.0, y_min_data - 0.5)
        y_hi = y_max_data + 0.5
    else:
        y_lo = max(0.0, y_min_data - pad)
        y_hi = y_max_data + pad
        if y_hi - y_lo < 0.2:
            mid = (y_hi + y_lo) * 0.5
            y_lo = max(0.0, mid - 0.1)
            y_hi = mid + 0.1
    return y_lo, y_hi


def y_to_px(y: float, y_lo: float, y_hi: float) -> float:
    top, bottom = 50.0, 450.0
    ratio = (y - y_lo) / (y_hi - y_lo)
    return bottom - ratio * (bottom - top)


def build_svg(trip_count: int, rows):
    rows_by_loop = {int(r["loops"]): float(r["vf_end_per_vadd"]) for r in rows}
    rows_by_vadds = {512 // loop: value for loop, value in rows_by_loop.items()}
    ys = [rows_by_vadds[v] for v in VADDS_PER_LOOP]
    y_lo, y_hi = choose_y_range(ys)

    best_loop = min(LOOPS, key=lambda l: rows_by_loop[l])
    best_idx = LOOPS.index(best_loop)
    best_vadds = 512 // best_loop
    best_y_val = rows_by_loop[best_loop]

    lines = []
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" width="700" height="520" viewBox="0 0 700 520">')
    lines.append('<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#222}.small{font-size:14px}.axis{font-size:16px}.title{font-size:24px;font-weight:700}</style>')
    lines.append('<rect x="0" y="0" width="700" height="520" fill="white"/>')
    lines.append(f'<text x="350.0" y="30" text-anchor="middle" class="title">singleV1 VADDS sweep, I={trip_count}</text>')
    lines.append('<rect x="80" y="50" width="590" height="400" fill="none" stroke="#111827" stroke-width="2.2"/>')

    tick_count = 6
    for i in range(tick_count):
        y_val = y_lo + (y_hi - y_lo) * i / (tick_count - 1)
        y_px = y_to_px(y_val, y_lo, y_hi)
        lines.append(f'<line x1="75" y1="{y_px:.1f}" x2="670" y2="{y_px:.1f}" stroke="#e5e7eb"/>')
        lines.append(f'<text x="72" y="{(y_px + 4):.1f}" text-anchor="end" class="small">{format_tick(y_val)}</text>')

    for idx, vadds in enumerate(VADDS_PER_LOOP):
        x = x_for_index(idx, len(VADDS_PER_LOOP))
        lines.append(f'<line x1="{x:.1f}" y1="50" x2="{x:.1f}" y2="455" stroke="#e5e7eb"/>')
        lines.append(f'<text x="{x:.1f}" y="472" text-anchor="middle" class="small">{vadds}</text>')

    lines.append('<text x="350.0" y="502" text-anchor="middle" class="axis">VADDS per loop (log2 scale)</text>')
    lines.append('<text x="20" y="260.0" transform="rotate(-90 20 260.0)" text-anchor="middle" class="axis">VF end / total VADDS</text>')

    poly_points = []
    for idx, vadds in enumerate(VADDS_PER_LOOP):
        x = x_for_index(idx, len(VADDS_PER_LOOP))
        y_px = y_to_px(rows_by_vadds[vadds], y_lo, y_hi)
        poly_points.append(f"{x:.1f},{y_px:.1f}")
    lines.append(f'<polyline fill="none" stroke="#2563eb" stroke-width="3" points="{" ".join(poly_points)}"/>')

    for idx, vadds in enumerate(VADDS_PER_LOOP):
        x = x_for_index(idx, len(VADDS_PER_LOOP))
        y_px = y_to_px(rows_by_vadds[vadds], y_lo, y_hi)
        if vadds == best_vadds:
            lines.append(f'<circle cx="{x:.1f}" cy="{y_px:.1f}" r="5" fill="#d94841"/>')
            text_y = max(62.0, y_px - 10.0)
            lines.append(f'<text x="{x:.1f}" y="{text_y:.1f}" text-anchor="middle" class="small">best: {best_loop}L/{best_vadds}</text>')
        else:
            lines.append(f'<circle cx="{x:.1f}" cy="{y_px:.1f}" r="4" fill="#2563eb"/>')

    lines.append('</svg>')
    return "\n".join(lines) + "\n", best_loop, best_vadds, best_y_val


def main():
    PANEL_DIR.mkdir(parents=True, exist_ok=True)
    readme_lines = []

    for trip_count in TRIP_COUNTS:
        json_path = BASE_DIR / f"I{trip_count}" / "sweep_results_normalized.json"
        if not json_path.exists():
            raise FileNotFoundError(f"Missing data file: {json_path}")
        rows = json.loads(json_path.read_text(encoding="utf-8"))

        svg_text, best_loop, best_vadds, best_val = build_svg(trip_count, rows)
        out_name = f"fusion_sweep_singlev1_I{trip_count}_normalized_consumer_done_20260401.svg"
        out_path = PANEL_DIR / out_name
        out_path.write_text(svg_text, encoding="utf-8")

        readme_lines.append(
            f"I={trip_count}: {out_name}  best={best_loop} loops / {best_vadds} vadds, vf_end_per_vadd={best_val:.6f}"
        )

    (PANEL_DIR / "README.txt").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
