#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import math
import os
import sys
from collections import deque
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.flatten import Flattener
from core.idu import IDU
from core.ifu import IFUUnroll
from core.ooo_factory import create_ooo_core, resolve_model_uarch
from core.param_db import ParamDB
from main import infer_top_block_loop_bounds, load_json

OUT_DIR = ROOT / "results" / "dev_test_logguided_iterations"

CASES = [
    {
        "name": "SwiGLU_I96_U6",
        "trace": ROOT / "results" / "unroll_test" / "sweep_u36_i96_start5_queue_level3_modelonly_20260417" / "SwiGLU_I96_U6" / "trace_input.json",
        "cce_vf": 647,
    },
    {
        "name": "SwiGLU_I96_U8",
        "trace": ROOT / "results" / "unroll_test" / "sweep_u1248_misched0_unrollfix" / "SwiGLU_I96_U8" / "trace_input.json",
        "cce_vf": 662,
    },
    {
        "name": "SiLU_I96_U4",
        "trace": ROOT / "results" / "unroll_test" / "sweep_u1248_misched0_unrollfix" / "SiLU_I96_U4" / "trace_input.json",
        "cce_vf": 382,
    },
]


ITERATIONS = [
    {
        "id": 1,
        "title": "Baseline queue_level3 reproduction",
        "change": "Reproduce historical level3 behavior: start+5, IDU->OoO=2, preg visible=2, SHQ visible=2, EXQ depth=infinite, global preg+SHQ gate=on.",
        "why": "As a baseline before log-guided fixes, matching the old level3 assumptions that produced the large U=8 error.",
        "uarch": {
            "ooo_model": "queue_level3",
            "consumer_release_from_start": True,
            "consumer_release_start_offset": 5,
            "queue_level3_idu_visible_delay": 2,
            "queue_level3_preg_visible_delay": 2,
            "queue_level3_shq_visible_delay": 2,
            "idu_to_ooo_delay": 2,
            "queue_level3_global_shq_preg_gate": True,
            "queue_level2_exq_depth": 10**9,
            "exq_capacity_counts_inflight": False,
        },
    },
    {
        "id": 2,
        "title": "Drop unsupported global gate",
        "change": "Disable queue_level3_global_shq_preg_gate.",
        "why": "CCE IDU log does not show a hard all-stop when one of preg/SHQ hits zero; it keeps dispatching subject to per-resource limits.",
        "uarch": {
            "queue_level3_global_shq_preg_gate": False,
        },
    },
    {
        "id": 3,
        "title": "Reduce IDU->OoO delay",
        "change": "Set idu_to_ooo_delay from 2 to 1.",
        "why": "Current model was over-serializing front-end transport. CCE dispatch to downstream receive timing suggests our extra transport slack was too large.",
        "uarch": {
            "idu_to_ooo_delay": 1,
        },
    },
    {
        "id": 4,
        "title": "Soften preg visible delay to 1",
        "change": "Set queue_level3_preg_visible_delay from 2 to 1.",
        "why": "U=8 error strongly correlates with visible preg starvation bursts; first step is to reduce return latency by one cycle.",
        "uarch": {
            "queue_level3_preg_visible_delay": 1,
        },
    },
    {
        "id": 5,
        "title": "Make preg free immediately visible",
        "change": "Set queue_level3_preg_visible_delay from 1 to 0.",
        "why": "CCE frees source scoreboards quickly; delayed visible preg return was still the dominant cause of second-wave launch bubbles.",
        "uarch": {
            "queue_level3_preg_visible_delay": 0,
        },
    },
    {
        "id": 6,
        "title": "Soften SHQ visible delay to 1",
        "change": "Set queue_level3_shq_visible_delay from 2 to 1.",
        "why": "After preg return is improved, test whether SHQ credit visibility is the next material front-end bottleneck.",
        "uarch": {
            "queue_level3_shq_visible_delay": 1,
        },
    },
    {
        "id": 7,
        "title": "Make SHQ release immediately visible",
        "change": "Set queue_level3_shq_visible_delay from 1 to 0.",
        "why": "If SHQ return still causes bursty dispatch, immediate visibility should further smooth the next-wave launch.",
        "uarch": {
            "queue_level3_shq_visible_delay": 0,
        },
    },
    {
        "id": 8,
        "title": "Use log-guided start+4 release",
        "change": "Set consumer_release_start_offset from 5 to 4.",
        "why": "In CCE ISU.dump, ISU_SCB_FREE_REG is repeatedly observed at consumer issue/start + 4 cycles, not +5.",
        "uarch": {
            "consumer_release_start_offset": 4,
        },
    },
    {
        "id": 9,
        "title": "Model finite EXQ depth",
        "change": "Set EXQ depth to 26 entries per EXQ.",
        "why": "CCE hardware appears to have finite EXQ capacity; this checks whether queue occupancy realism improves or hurts alignment.",
        "uarch": {
            "queue_level2_exq_depth": 26,
        },
    },
    {
        "id": 10,
        "title": "Count in-flight ops against EXQ depth",
        "change": "Enable exq_capacity_counts_inflight.",
        "why": "Probe whether running-but-not-retired EXQ residents should still consume EXQ credit, closer to hardware occupancy accounting.",
        "uarch": {
            "exq_capacity_counts_inflight": True,
        },
    },
    {
        "id": 11,
        "title": "Use explicit IDU credit banks",
        "change": "Replace in-flight reservation proxy with explicit IDU-side preg/SHQ credit banks that decrement on dispatch send and increment only when OoO visible release messages arrive.",
        "why": "This matches the intended hardware protocol more closely and removes proxy-style double-accounting during the 1-2 cycle transport window.",
        "uarch": {
            "queue_level3_use_explicit_idu_credit_bank": True,
        },
    },
]


def _is_vreg_name(x: Any) -> bool:
    return isinstance(x, str) and x[:1].lower() == "v"


def run_case(trace_path: Path, uarch_override: Dict[str, Any]) -> int:
    base_dir = str(ROOT)
    trace = load_json(str(trace_path))
    dtype = trace.get("dtype", "fp32")
    params = trace.get("params", {}) or {}
    program = trace.get("program")
    if program is None:
        raise RuntimeError(f"{trace_path} missing key 'program'")

    top_block_loop_bounds = infer_top_block_loop_bounds(program, params)
    total_top_blocks = len(top_block_loop_bounds)
    loop_bounds = top_block_loop_bounds.get(0, [])

    linear = Flattener(params).flatten(program)
    ifu = IFUUnroll(linear, params)
    db = ParamDB(base_dir=base_dir)
    uarch = dict(db.get_uarch())
    uarch.update(uarch_override)
    uarch = resolve_model_uarch(uarch)

    idu = IDU(
        uarch,
        db,
        params=params,
        loop_bounds=loop_bounds,
        total_top_blocks=total_top_blocks,
        top_block_loop_bounds=top_block_loop_bounds,
    )
    ooo = create_ooo_core(uarch, db, dtype=dtype)
    idu_to_ooo_delay = int(uarch.get("idu_to_ooo_delay", 0))
    idu_to_ooo_pipe = deque()
    use_explicit_idu_credit_bank = bool(
        uarch.get("queue_level3_use_explicit_idu_credit_bank", False)
    )
    idu_preg_credit = int(ooo.get_free_preg())
    idu_shq_credit = int(ooo.get_free_shq())
    idu_pending_shq_queue = 0
    idu_pending_lsq = 0

    def _inst_reservation(inst: Dict[str, Any]) -> Dict[str, int]:
        op = str(inst.get("op", ""))
        dsts = inst.get("dst", [])
        if isinstance(dsts, str):
            dsts = [dsts]
        if not isinstance(dsts, list):
            dsts = []
        preg = sum(1 for d in dsts if _is_vreg_name(d))
        shq_queue = 0 if op in ("VLD", "VST") else 1
        lsq = 1 if op in ("VLD", "VST") else 0
        shq = 0 if op == "VLD" else 1
        return {"preg": preg, "shq_queue": shq_queue, "lsq": lsq, "shq": shq}

    cycle = 0
    max_cycles = int(params.get("max_cycles", 1_000_000))
    completed = False
    while cycle < max_cycles:
        visible_delta = ooo.update_idu_visibility(cycle)
        if use_explicit_idu_credit_bank:
            idu_preg_credit += int(visible_delta.get("preg_free", 0))
            idu_shq_credit += int(visible_delta.get("shq_release", 0))

        while idu_to_ooo_pipe and idu_to_ooo_pipe[0][0] <= cycle:
            _, inst = idu_to_ooo_pipe.popleft()
            if use_explicit_idu_credit_bank:
                r = _inst_reservation(inst)
                idu_pending_shq_queue = max(0, int(idu_pending_shq_queue) - int(r["shq_queue"]))
                idu_pending_lsq = max(0, int(idu_pending_lsq) - int(r["lsq"]))
            ooo.accept(inst)

        pending_preg = pending_shq_queue = pending_lsq = pending_shq = 0
        if not use_explicit_idu_credit_bank:
            for _, inst in idu_to_ooo_pipe:
                r = _inst_reservation(inst)
                pending_preg += int(r["preg"])
                pending_shq_queue += int(r["shq_queue"])
                pending_lsq += int(r["lsq"])
                pending_shq += int(r["shq"])

        while idu.can_accept():
            if ifu.done():
                break
            inst = ifu.next_inst()
            if inst is None:
                break
            if "inst_id" not in inst and "id" in inst:
                inst["inst_id"] = inst["id"]
            idu.accept(inst)

        class _IDUCreditProxy:
            def __init__(self, core, preg, shq_queue, lsq, shq):
                self.core = core
                self.preg = int(preg)
                self.shq_queue = int(shq_queue)
                self.lsq = int(lsq)
                self.shq = int(shq)

            def get_free_preg(self):
                return max(0, int(self.core.get_free_preg()) - self.preg)

            def get_free_shq_queue(self):
                return max(0, int(self.core.get_free_shq_queue()) - self.shq_queue)

            def get_free_lsq(self):
                return max(0, int(self.core.get_free_lsq()) - self.lsq)

            def get_free_shq(self):
                return max(0, int(self.core.get_free_shq()) - self.shq)

        if use_explicit_idu_credit_bank:
            proxy = _IDUCreditProxy(ooo, 0, 0, 0, 0)
            proxy.get_free_preg = lambda: max(0, int(idu_preg_credit))
            proxy.get_free_shq = lambda: max(0, int(idu_shq_credit))
            proxy.get_free_shq_queue = lambda: max(0, int(ooo.get_free_shq_queue()) - int(idu_pending_shq_queue))
            proxy.get_free_lsq = lambda: max(0, int(ooo.get_free_lsq()) - int(idu_pending_lsq))
        else:
            proxy = _IDUCreditProxy(ooo, pending_preg, pending_shq_queue, pending_lsq, pending_shq)
        to_send = idu.dispatch(cycle, proxy)
        for inst in to_send:
            if use_explicit_idu_credit_bank:
                r = _inst_reservation(inst)
                idu_preg_credit = max(0, int(idu_preg_credit) - int(r["preg"]))
                idu_shq_credit = max(0, int(idu_shq_credit) - int(r["shq"]))
                if idu_to_ooo_delay > 0:
                    idu_pending_shq_queue += int(r["shq_queue"])
                    idu_pending_lsq += int(r["lsq"])
            if idu_to_ooo_delay > 0:
                idu_to_ooo_pipe.append((cycle + idu_to_ooo_delay, inst))
            else:
                ooo.accept(inst)

        ooo.step()

        if (
            ifu.done()
            and idu.empty()
            and len(ooo.SHQ) == 0
            and len(ooo.LSQ) == 0
            and len(ooo.ROB) == 0
            and len(idu_to_ooo_pipe) == 0
        ):
            completed = True
            break
        cycle += 1

    if not completed:
        raise RuntimeError(
            "run_case did not complete before max_cycles. "
            f"trace={trace_path}, cycle={cycle}, vf_end={ooo.vf_end_cycle()}, "
            f"ifu_done={ifu.done()}, idu_empty={idu.empty()}, "
            f"shq={len(ooo.SHQ)}, lsq={len(ooo.LSQ)}, rob={len(ooo.ROB)}, pipe={len(idu_to_ooo_pipe)}"
        )

    return int(ooo.vf_end_cycle())


def _rel_err(pred: int, cce: int) -> float:
    return abs(pred - cce) / cce * 100.0


def _maybe_plot(csv_path: Path, png_path: Path) -> str:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception as exc:  # pragma: no cover
        return f"plot skipped: {exc}"

    rows: List[Dict[str, str]] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    iters = sorted({int(r["iteration"]) for r in rows})
    case_names = sorted({r["case"] for r in rows})
    plt.figure(figsize=(10, 6))
    for case in case_names:
        ys = [float(next(r["rel_err_pct"] for r in rows if int(r["iteration"]) == i and r["case"] == case)) for i in iters]
        plt.plot(iters, ys, marker="o", label=case)

    avg_ys = []
    for i in iters:
        vals = [float(r["rel_err_pct"]) for r in rows if int(r["iteration"]) == i]
        avg_ys.append(mean(vals))
    plt.plot(iters, avg_ys, marker="s", linewidth=2.5, linestyle="--", label="avg")
    plt.xlabel("Iteration")
    plt.ylabel("Relative Error (%)")
    plt.title("Log-Guided Queue Model Iterations")
    plt.xticks(iters)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(png_path, dpi=180)
    plt.close()
    return "ok"


def _write_svg_plot(csv_path: Path, svg_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path, "r", encoding="utf-8")))
    iters = sorted({int(r["iteration"]) for r in rows})
    cases = ["SwiGLU_I96_U6", "SwiGLU_I96_U8", "SiLU_I96_U4"]
    series = {
        case: [
            float(next(r["rel_err_pct"] for r in rows if int(r["iteration"]) == i and r["case"] == case))
            for i in iters
        ]
        for case in cases
    }
    series["avg"] = [
        mean(float(r["rel_err_pct"]) for r in rows if int(r["iteration"]) == i)
        for i in iters
    ]

    colors = {
        "SwiGLU_I96_U6": "#1f77b4",
        "SwiGLU_I96_U8": "#d62728",
        "SiLU_I96_U4": "#2ca02c",
        "avg": "#111111",
    }
    width, height = 980, 620
    ml, mr, mt, mb = 80, 30, 30, 80
    plot_w = width - ml - mr
    plot_h = height - mt - mb
    max_y = max(max(v) for v in series.values())
    max_y = math.ceil(max_y / 10.0) * 10.0

    def sx(i: int) -> float:
        if len(iters) == 1:
            return ml + plot_w / 2
        return ml + (i - min(iters)) * plot_w / (max(iters) - min(iters))

    def sy(v: float) -> float:
        return mt + plot_h - (v / max_y) * plot_h

    parts: List[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">')
    parts.append('<style>text{font-family:Segoe UI,Arial,sans-serif;font-size:14px} .title{font-size:20px;font-weight:600}</style>')
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>')
    parts.append(f'<text class="title" x="{width/2}" y="24" text-anchor="middle">Log-Guided Iteration Relative Error Curve</text>')
    for yv in range(0, int(max_y) + 1, 10):
        y = sy(yv)
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width-mr}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        parts.append(f'<text x="{ml-10}" y="{y+5:.1f}" text-anchor="end">{yv}</text>')
    parts.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{height-mb}" stroke="#333" stroke-width="1.5"/>')
    parts.append(f'<line x1="{ml}" y1="{height-mb}" x2="{width-mr}" y2="{height-mb}" stroke="#333" stroke-width="1.5"/>')
    for i in iters:
        x = sx(i)
        parts.append(f'<line x1="{x:.1f}" y1="{height-mb}" x2="{x:.1f}" y2="{height-mb+6}" stroke="#333"/>')
        parts.append(f'<text x="{x:.1f}" y="{height-mb+24}" text-anchor="middle">{i}</text>')
    parts.append(f'<text x="{width/2}" y="{height-24}" text-anchor="middle">Iteration</text>')
    parts.append(f'<text transform="translate(24 {height/2}) rotate(-90)" text-anchor="middle">Relative Error (%)</text>')
    for name, vals in series.items():
        pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in zip(iters, vals))
        dash = ' stroke-dasharray="8 5"' if name == "avg" else ""
        sw = "3" if name == "avg" else "2.5"
        parts.append(f'<polyline fill="none" stroke="{colors[name]}" stroke-width="{sw}"{dash} points="{pts}"/>')
        for i, v in zip(iters, vals):
            parts.append(f'<circle cx="{sx(i):.1f}" cy="{sy(v):.1f}" r="4" fill="{colors[name]}"/>')
    lx, ly = width - 220, 60
    for idx, name in enumerate(["SwiGLU_I96_U6", "SwiGLU_I96_U8", "SiLU_I96_U4", "avg"]):
        y = ly + idx * 24
        dash = ' stroke-dasharray="8 5"' if name == "avg" else ""
        parts.append(f'<line x1="{lx}" y1="{y}" x2="{lx+24}" y2="{y}" stroke="{colors[name]}" stroke-width="3"{dash}/>')
        parts.append(f'<text x="{lx+32}" y="{y+5}">{name}</text>')
    parts.append("</svg>")
    svg_path.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "iteration_metrics.csv"
    json_path = OUT_DIR / "iteration_metrics.json"
    md_path = OUT_DIR / "iteration_log.md"
    png_path = OUT_DIR / "iteration_error_curve.png"
    svg_path = OUT_DIR / "iteration_error_curve.svg"

    cumulative: Dict[str, Any] = {
        "ooo_model": "queue_level3",
        "consumer_release_from_start": True,
    }

    all_rows: List[Dict[str, Any]] = []
    iter_summaries: List[Dict[str, Any]] = []

    for it in ITERATIONS:
        cumulative.update(it["uarch"])
        case_rows: List[Dict[str, Any]] = []
        for case in CASES:
            pred = run_case(case["trace"], cumulative)
            err = _rel_err(pred, case["cce_vf"])
            row = {
                "iteration": it["id"],
                "title": it["title"],
                "case": case["name"],
                "pred_vf": pred,
                "cce_vf": case["cce_vf"],
                "rel_err_pct": round(err, 4),
            }
            all_rows.append(row)
            case_rows.append(row)

        avg_err = mean(r["rel_err_pct"] for r in case_rows)
        best_case = min(case_rows, key=lambda r: r["rel_err_pct"])
        worst_case = max(case_rows, key=lambda r: r["rel_err_pct"])
        iter_summaries.append(
            {
                "iteration": it["id"],
                "title": it["title"],
                "change": it["change"],
                "why": it["why"],
                "uarch": dict(cumulative),
                "avg_err_pct": round(avg_err, 4),
                "best_case": {"name": best_case["case"], "err_pct": best_case["rel_err_pct"]},
                "worst_case": {"name": worst_case["case"], "err_pct": worst_case["rel_err_pct"]},
                "rows": case_rows,
            }
        )

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["iteration", "title", "case", "pred_vf", "cce_vf", "rel_err_pct"])
        writer.writeheader()
        writer.writerows(all_rows)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "cases": [
                    {
                        **case,
                        "trace": str(case["trace"]),
                    }
                    for case in CASES
                ],
                "iterations": iter_summaries,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    plot_status = _maybe_plot(csv_path, png_path)
    _write_svg_plot(csv_path, svg_path)

    lines: List[str] = []
    lines.append("# Log-Guided Queue Iteration Log")
    lines.append("")
    lines.append("Tracked cases:")
    lines.append("- `SwiGLU I=96 U=6`")
    lines.append("- `SwiGLU I=96 U=8`")
    lines.append("- `SiLU I=96 U=4`")
    lines.append("")
    lines.append("CCE baselines:")
    for case in CASES:
        lines.append(f"- `{case['name']}`: `{case['cce_vf']}`")
    lines.append("")
    lines.append(f"Curve (PNG if available): `{png_path}`")
    lines.append(f"Curve (always generated SVG): `{svg_path}`")
    lines.append(f"Plot status: `{plot_status}`")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Iter | Change | Avg Err | SwiGLU_U6 | SwiGLU_U8 | SiLU_U4 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for s in iter_summaries:
        row_map = {r["case"]: r for r in s["rows"]}
        lines.append(
            "| {it} | {chg} | {avg:.2f}% | {u6:.2f}% | {u8:.2f}% | {silu:.2f}% |".format(
                it=s["iteration"],
                chg=s["title"],
                avg=s["avg_err_pct"],
                u6=float(row_map["SwiGLU_I96_U6"]["rel_err_pct"]),
                u8=float(row_map["SwiGLU_I96_U8"]["rel_err_pct"]),
                silu=float(row_map["SiLU_I96_U4"]["rel_err_pct"]),
            )
        )
    lines.append("")
    lines.append("## Iteration Notes")
    lines.append("")
    for s in iter_summaries:
        lines.append(f"### Iteration {s['iteration']}: {s['title']}")
        lines.append("")
        lines.append(f"- Change: {s['change']}")
        lines.append(f"- Logic: {s['why']}")
        lines.append(f"- Average error: `{s['avg_err_pct']:.2f}%`")
        lines.append(
            f"- Best case: `{s['best_case']['name']}` = `{s['best_case']['err_pct']:.2f}%`, "
            f"worst case: `{s['worst_case']['name']}` = `{s['worst_case']['err_pct']:.2f}%`"
        )
        lines.append("- Results:")
        for r in s["rows"]:
            lines.append(
                f"  - `{r['case']}`: model=`{r['pred_vf']}`, cce=`{r['cce_vf']}`, rel_err=`{r['rel_err_pct']:.2f}%`"
            )
        lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {md_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Plot status: {plot_status}")
    if plot_status == "ok":
        print(f"Wrote {png_path}")
    print(f"Wrote {svg_path}")


if __name__ == "__main__":
    main()
