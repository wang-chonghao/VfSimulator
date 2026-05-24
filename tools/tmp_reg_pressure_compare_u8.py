import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


CCE_IDU = Path(
    r"D:\VfSimulator\results\unroll_test\sweep_u1248_misched0_unrollfix\SwiGLU_I96_U8\cce_dump\core0.veccore0.rvec.IDU.dump"
)
MODEL_Q1 = Path(r"D:\VfSimulator\results\tmp_queue_level1_swiglu_i96_u8_v5\idu_to_ooo.json")
MODEL_CD = Path(r"D:\VfSimulator\results\tmp_consumer_done_swiglu_i96_u8\idu_to_ooo.json")
OUT_DIR = Path(r"D:\VfSimulator\results\tmp_reg_pressure_compare_u8")
OUT_CSV = OUT_DIR / "cce_vs_model_vreg_pressure.csv"
OUT_MD = OUT_DIR / "summary.md"


@dataclass
class Event:
    cycle: int
    vreg: int
    line_no: int
    src_path: Path
    raw: str
    kind: str


def percentile_linear(values: List[int], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * (p / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(s[lo])
    frac = pos - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def parse_cce_idu(path: Path) -> List[Event]:
    out: List[Event] = []
    re_block = re.compile(r"\[PERF\]\s+\[(\d+)\].*IDU_BLOCK.*\bvreg:(\d+)\b")
    re_send = re.compile(r"@(\d+)\s+instr send to OOO:.*\bvreg:(\d+)\)")
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            m = re_block.search(line)
            if m:
                out.append(
                    Event(
                        cycle=int(m.group(1)),
                        vreg=int(m.group(2)),
                        line_no=i,
                        src_path=path,
                        raw=line.rstrip(),
                        kind="IDU_BLOCK",
                    )
                )
                continue
            m = re_send.search(line)
            if m:
                out.append(
                    Event(
                        cycle=int(m.group(1)),
                        vreg=int(m.group(2)),
                        line_no=i,
                        src_path=path,
                        raw=line.rstrip(),
                        kind="SEND_TO_OOO",
                    )
                )
    return out


def parse_model_idu_to_ooo(path: Path) -> List[Event]:
    out: List[Event] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out.append(
                Event(
                    cycle=int(obj["cy"]),
                    vreg=int(obj["vreg"]),
                    line_no=i,
                    src_path=path,
                    raw=line,
                    kind="IDU_TO_OOO",
                )
            )
    return out


def aggregate_cycle_min(events: List[Event]) -> Tuple[Dict[int, int], Dict[int, Event]]:
    by_cycle: Dict[int, int] = {}
    ev_by_cycle: Dict[int, Event] = {}
    for ev in events:
        if ev.cycle not in by_cycle or ev.vreg < by_cycle[ev.cycle]:
            by_cycle[ev.cycle] = ev.vreg
            ev_by_cycle[ev.cycle] = ev
    return by_cycle, ev_by_cycle


def zero_intervals(series: Dict[int, int]) -> List[Tuple[int, int, int]]:
    cycles = sorted(c for c, v in series.items() if v == 0)
    if not cycles:
        return []
    intervals: List[Tuple[int, int, int]] = []
    start = cycles[0]
    prev = cycles[0]
    for c in cycles[1:]:
        if c == prev + 1:
            prev = c
            continue
        intervals.append((start, prev, prev - start + 1))
        start = c
        prev = c
    intervals.append((start, prev, prev - start + 1))
    return intervals


def summarize_series(name: str, series: Dict[int, int], cycle_event: Dict[int, Event]) -> Dict[str, object]:
    values = [series[c] for c in sorted(series)]
    min_v = min(values)
    min_cycles = [c for c in sorted(series) if series[c] == min_v]
    z_intv = zero_intervals(series)
    min_evidence = cycle_event[min_cycles[0]]
    return {
        "name": name,
        "n_cycles": len(values),
        "min": min_v,
        "avg": sum(values) / len(values),
        "p10": percentile_linear(values, 10),
        "p50": percentile_linear(values, 50),
        "p90": percentile_linear(values, 90),
        "vreg0_count": sum(1 for v in values if v == 0),
        "vreg0_intervals": z_intv,
        "first_bottom_cycle": min_cycles[0],
        "last_bottom_cycle": min_cycles[-1],
        "bottom_value": min_v,
        "min_evidence": min_evidence,
    }


def fmt_f(v: float) -> str:
    return f"{v:.3f}"


def write_csv(
    out_csv: Path,
    cce: Dict[int, int],
    q1: Dict[int, int],
    cd: Dict[int, int],
) -> None:
    all_cycles = sorted(set(cce) | set(q1) | set(cd))
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "cycle",
                "cce_vreg_free",
                "model_vreg_free_queue_level1",
                "model_vreg_free_consumer_done",
            ]
        )
        for c in all_cycles:
            w.writerow(
                [
                    c,
                    cce.get(c, ""),
                    q1.get(c, ""),
                    cd.get(c, ""),
                ]
            )


def fmt_intervals(intervals: List[Tuple[int, int, int]]) -> str:
    if not intervals:
        return "[]"
    parts = [f"[{s},{e}]len={ln}" for s, e, ln in intervals]
    return "[" + ", ".join(parts) + "]"


def interval_len_stats(intervals: List[Tuple[int, int, int]]) -> str:
    if not intervals:
        return "count=0"
    lens = sorted(i[2] for i in intervals)
    p50 = percentile_linear(lens, 50)
    return "count={c}, min={mn}, p50={p50}, max={mx}".format(
        c=len(lens), mn=lens[0], p50=fmt_f(p50), mx=lens[-1]
    )


def build_summary_md(
    out_md: Path,
    cce_s: Dict[str, object],
    q1_s: Dict[str, object],
    cd_s: Dict[str, object],
) -> None:
    def line_ref(ev: Event) -> str:
        return f"{ev.src_path}:{ev.line_no}"

    # Pressure higher means lower free vreg.
    # Use median and p10 as primary decision metrics.
    model_median_lower = (q1_s["p50"] < cce_s["p50"]) and (cd_s["p50"] < cce_s["p50"])
    model_p10_lower = (q1_s["p10"] < cce_s["p10"]) and (cd_s["p10"] < cce_s["p10"])
    clear_gap = (cce_s["p50"] - q1_s["p50"] >= 2) or (cce_s["p50"] - cd_s["p50"] >= 2)
    clearly_higher = model_median_lower and model_p10_lower and clear_gap

    if clearly_higher:
        conclusion = "模型是否明显更高压力：是。结论：模型寄存器压力明显更高（可用 vreg 明显更低）。"
    else:
        conclusion = "模型是否明显更高压力：否。结论：模型寄存器压力不明显高于 CCE（中位数/低分位差距不显著或不一致）。"

    with out_md.open("w", encoding="utf-8-sig") as f:
        f.write("# U=8 CCE vs 模型 寄存器压力对比\n\n")
        f.write("口径统一：`vreg` 均表示“可用寄存器数（free vreg）”。\n")
        f.write("- CCE：来自 `core0.veccore0.rvec.IDU.dump` 中 `instr send to OOO ... vreg:xx`\n")
        f.write("- 模型：来自 `idu_to_ooo.json` 的 `vreg` 字段\n")
        f.write("- 聚合方式：按 cycle 取该 cycle 内最小 `vreg` 作为该 cycle 压力值\n\n")

        f.write("## 结论\n\n")
        f.write(f"{conclusion}\n\n")

        f.write("## 统计结果（按 cycle）\n\n")
        f.write("| 数据源 | min | avg | p10 | p50 | p90 | vreg=0 次数 | 首次触底 cycle | 最后触底 cycle |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for s in [cce_s, q1_s, cd_s]:
            f.write(
                "| {name} | {min} | {avg} | {p10} | {p50} | {p90} | {z} | {fb} | {lb} |\n".format(
                    name=s["name"],
                    min=s["min"],
                    avg=fmt_f(float(s["avg"])),
                    p10=fmt_f(float(s["p10"])),
                    p50=fmt_f(float(s["p50"])),
                    p90=fmt_f(float(s["p90"])),
                    z=s["vreg0_count"],
                    fb=s["first_bottom_cycle"],
                    lb=s["last_bottom_cycle"],
                )
            )
        f.write("\n")

        f.write("## vreg=0 连续区间\n\n")
        for s in [cce_s, q1_s, cd_s]:
            f.write(
                f"- {s['name']}: {interval_len_stats(s['vreg0_intervals'])}; intervals={fmt_intervals(s['vreg0_intervals'])}\n"
            )
        f.write("\n")

        f.write("## 证据（可直接定位到原始行）\n\n")
        for s in [cce_s, q1_s, cd_s]:
            ev = s["min_evidence"]
            assert isinstance(ev, Event)
            f.write(f"- {s['name']} 触底值={s['bottom_value']} 的首个证据：`{line_ref(ev)}`\n")
            f.write(f"  - 行内容：`{ev.raw[:220]}`\n")
        f.write("\n")

        f.write("## 输出文件\n\n")
        f.write(f"- CSV: `{OUT_CSV}`\n")
        f.write(f"- Markdown: `{OUT_MD}`\n")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cce_events = parse_cce_idu(CCE_IDU)
    q1_events = parse_model_idu_to_ooo(MODEL_Q1)
    cd_events = parse_model_idu_to_ooo(MODEL_CD)

    # Fairness/alignment: model source is IDU->OOO dispatch stream,
    # so CCE uses IDU "instr send to OOO" only.
    cce_send_events = [e for e in cce_events if e.kind == "SEND_TO_OOO"]
    cce_cycle, cce_ev = aggregate_cycle_min(cce_send_events)
    q1_cycle, q1_ev = aggregate_cycle_min(q1_events)
    cd_cycle, cd_ev = aggregate_cycle_min(cd_events)

    write_csv(OUT_CSV, cce_cycle, q1_cycle, cd_cycle)

    cce_s = summarize_series("CCE(IDU)", cce_cycle, cce_ev)
    q1_s = summarize_series("MODEL(queue_level1)", q1_cycle, q1_ev)
    cd_s = summarize_series("MODEL(consumer_done)", cd_cycle, cd_ev)

    build_summary_md(OUT_MD, cce_s, q1_s, cd_s)

    print(f"Wrote: {OUT_CSV}")
    print(f"Wrote: {OUT_MD}")
    print(
        "Quick stats p50: CCE={:.3f}, Q1={:.3f}, CD={:.3f}".format(
            float(cce_s["p50"]), float(q1_s["p50"]), float(cd_s["p50"])
        )
    )


if __name__ == "__main__":
    main()
