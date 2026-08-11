"""
Convert VfSimulator's start_by_cycle.json / done_by_cycle.json into a Chrome
Trace Event Format ("trace.json") file, viewable at https://ui.perfetto.dev
or chrome://tracing.

Each dynamic instruction becomes one duration ("X") event, with
ts/dur converted from simulator cycles to microseconds via --freq-mhz
(default 2000 MHz, i.e. 1 cycle = 0.5 ns). Events are bucketed into three
lanes -- RVECLD (VLDS), RVEST (VSTS), RVECEX (everything else) -- and packed
into sub-lanes with a greedy interval-scheduling pass so overlapping
instructions of the same category render on separate rows instead of
stacking on top of each other. All sub-lanes of a category share the same
track name (no per-lane numeric suffix); Perfetto still keeps them on
separate rows internally via distinct tid values.

Usage:
    python tools/gen_trace_json.py \
        --start results/<out_dir>/start_by_cycle.json \
        --done  results/<out_dir>/done_by_cycle.json \
        --out   results/<out_dir>/trace.json \
        --freq-mhz 2000
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _category(op: str) -> str:
    if op == "VLDS":
        return "RVECLD"
    if op == "VSTS":
        return "RVEST"
    return "RVECEX"


def _pack_lanes(events: List[Dict[str, Any]]) -> Dict[int, int]:
    """Greedy interval-scheduling: assign each event's inst_id to the first
    lane whose previous occupant already ended, else a new lane."""
    lane_end: List[int] = []
    assignment: Dict[int, int] = {}
    for ev in sorted(events, key=lambda e: (e["start"], e["inst_id"])):
        for lane_idx, end in enumerate(lane_end):
            if end <= ev["start"]:
                lane_end[lane_idx] = ev["end"]
                assignment[ev["inst_id"]] = lane_idx
                break
        else:
            lane_end.append(ev["end"])
            assignment[ev["inst_id"]] = len(lane_end) - 1
    return assignment


def build_trace(start_path: Path, done_path: Path, freq_mhz: float = 2000.0) -> Dict[str, Any]:
    ns_per_cycle = 1000.0 / freq_mhz  # 2000 MHz -> 0.5 ns/cycle (6 cycle = 3 ns)
    us_per_cycle = ns_per_cycle / 1000.0  # Trace Event Format ts/dur are in microseconds
    starts = {row["inst_id"]: row for row in _load_jsonl(start_path)}
    dones = {row["inst_id"]: row for row in _load_jsonl(done_path)}

    joined: List[Dict[str, Any]] = []
    for inst_id, s in starts.items():
        d = dones.get(inst_id)
        if d is None:
            continue
        joined.append(
            {
                "inst_id": inst_id,
                "op": s["op"],
                "form": s.get("form"),
                "dst": s.get("dst"),
                "src": s.get("src"),
                "start": s["cy"],
                "end": max(d["cy"], s["cy"] + 1),
            }
        )

    by_category: Dict[str, List[Dict[str, Any]]] = {"RVECLD": [], "RVEST": [], "RVECEX": []}
    for ev in joined:
        by_category[_category(ev["op"])].append(ev)

    # tid layout: RVECLD -> 1..N, RVECEX -> 101..100+N, RVEST -> 201..200+N
    tid_base = {"RVECLD": 1, "RVECEX": 101, "RVEST": 201}
    trace_events: List[Dict[str, Any]] = []
    thread_names: Dict[int, str] = {}

    for category, events in by_category.items():
        lane_of = _pack_lanes(events)
        max_lane = max(lane_of.values(), default=-1)
        for lane in range(max_lane + 1):
            tid = tid_base[category] + lane
            thread_names[tid] = category  # same track name for every sub-lane, no numeric suffix
        for ev in events:
            tid = tid_base[category] + lane_of[ev["inst_id"]]
            trace_events.append(
                {
                    "name": ev["op"],
                    "cat": category,
                    "ph": "X",
                    "ts": ev["start"] * us_per_cycle,
                    "dur": (ev["end"] - ev["start"]) * us_per_cycle,
                    "pid": 1,
                    "tid": tid,
                    "args": {
                        "inst_id": ev["inst_id"],
                        "form": ev["form"],
                        "dst": ev["dst"],
                        "src": ev["src"],
                        "start_cycle": ev["start"],
                        "done_cycle": ev["end"],
                    },
                }
            )

    meta_events: List[Dict[str, Any]] = [
        {"name": "process_name", "ph": "M", "pid": 1, "args": {"name": "VF core 0"}}
    ]
    for tid, name in sorted(thread_names.items()):
        meta_events.append(
            {"name": "thread_name", "ph": "M", "pid": 1, "tid": tid, "args": {"name": name}}
        )

    return {
        "traceEvents": meta_events + trace_events,
        "displayTimeUnit": "ns",
        "otherData": {
            "note": (
                f"ts/dur are in microseconds, converted from simulator cycles at "
                f"{freq_mhz} MHz ({ns_per_cycle} ns/cycle). args.start_cycle/"
                f"done_cycle keep the original cycle numbers."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Path to start_by_cycle.json")
    parser.add_argument("--done", required=True, help="Path to done_by_cycle.json")
    parser.add_argument("--out", required=True, help="Output trace.json path")
    parser.add_argument(
        "--freq-mhz", type=float, default=2000.0, help="Clock frequency in MHz (default 2000)"
    )
    args = parser.parse_args()

    trace = build_trace(Path(args.start), Path(args.done), freq_mhz=args.freq_mhz)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(trace, f)
    print(f"Wrote {out_path} ({len(trace['traceEvents'])} events)")


if __name__ == "__main__":
    main()
