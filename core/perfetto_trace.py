from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


_PROCESS_BY_CLASS = {
    "LOAD": (100, "Load Unit", 10),
    "COMPUTE": (200, "EXU Unit", 20),
    "STORE": (300, "Store Unit", 30),
}


def _metadata_event(name: str, pid: int, tid: int, args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "ph": "M",
        "pid": pid,
        "tid": tid,
        "args": args,
    }


def _track_for_event(event: Mapping[str, Any]) -> Tuple[int, int, str, str] | None:
    op_class = str(event.get("op_class", "")).upper()
    process = _PROCESS_BY_CLASS.get(op_class)
    if process is None:
        return None
    pid, process_name, _sort_index = process
    if op_class == "COMPUTE":
        port = event.get("exu_port")
        tid = int(port) if port is not None else 0
        return pid, tid, process_name, f"EXU{tid}"
    if op_class == "LOAD":
        return pid, 0, process_name, "Load Pipeline"
    return pid, 0, process_name, "Store Pipeline"


def build_perfetto_trace(
    start_events: Iterable[Mapping[str, Any]],
    done_events: Iterable[Mapping[str, Any]],
    *,
    issue_ports: int,
) -> Dict[str, Any]:
    """Build a Perfetto-compatible Chrome Trace Event payload.

    Chrome JSON timestamps are expressed in microseconds. The simulator maps
    one cycle to one microsecond so cycle values remain directly readable.
    """

    done_by_id = {
        int(event["inst_id"]): event
        for event in done_events
        if event.get("inst_id") is not None
    }
    trace_events: List[Dict[str, Any]] = []

    for op_class, (pid, process_name, sort_index) in _PROCESS_BY_CLASS.items():
        trace_events.append(
            _metadata_event("process_name", pid, 0, {"name": process_name})
        )
        trace_events.append(
            _metadata_event("process_sort_index", pid, 0, {"sort_index": sort_index})
        )
        if op_class != "COMPUTE":
            track_name = "Load Pipeline" if op_class == "LOAD" else "Store Pipeline"
            trace_events.append(
                _metadata_event("thread_name", pid, 0, {"name": track_name})
            )
            trace_events.append(
                _metadata_event("thread_sort_index", pid, 0, {"sort_index": 0})
            )

    for port in range(max(1, int(issue_ports))):
        trace_events.append(
            _metadata_event("thread_name", 200, port, {"name": f"EXU{port}"})
        )
        trace_events.append(
            _metadata_event("thread_sort_index", 200, port, {"sort_index": port})
        )

    slices: List[Dict[str, Any]] = []
    for start in start_events:
        inst_id_value = start.get("inst_id")
        if inst_id_value is None:
            continue
        inst_id = int(inst_id_value)
        done = done_by_id.get(inst_id)
        track = _track_for_event(start)
        if done is None or track is None:
            continue

        pid, tid, _process_name, track_name = track
        start_cycle = int(start.get("cy", 0))
        done_cycle = int(done.get("cy", start_cycle))
        duration = max(1, done_cycle - start_cycle)
        op = str(start.get("op", "UNKNOWN"))
        form = str(start.get("form", ""))
        name = f"{op}.{form}" if form else op
        args = {
            "inst_id": inst_id,
            "static_instruction_id": start.get("static_instruction_id"),
            "stream_seq": start.get("stream_seq"),
            "start_cycle": start_cycle,
            "done_cycle": done_cycle,
            "ready_cycle": start.get("ready_cycle"),
            "op_class": start.get("op_class"),
            "fu_type": start.get("fu_type"),
            "track": track_name,
            "src": start.get("src", []),
            "dst": start.get("dst", []),
            "preg_src": start.get("preg_src", []),
            "preg_dst": start.get("preg_dst", []),
            "iteration_path": start.get("iteration_path", []),
        }
        slices.append(
            {
                "name": name,
                "cat": str(start.get("op_class", "instruction")).lower(),
                "ph": "X",
                "ts": start_cycle,
                "dur": duration,
                "pid": pid,
                "tid": tid,
                "args": args,
            }
        )

    slices.sort(
        key=lambda event: (
            int(event["ts"]),
            int(event["pid"]),
            int(event["tid"]),
            int(event["args"]["inst_id"]),
        )
    )
    trace_events.extend(slices)
    return {
        "traceEvents": trace_events,
        "displayTimeUnit": "us",
        "metadata": {
            "simulator": "VfSim",
            "timestamp_mapping": "1 simulator cycle = 1 microsecond",
        },
    }


def dump_perfetto_trace(
    path: str | Path,
    start_events: Iterable[Mapping[str, Any]],
    done_events: Iterable[Mapping[str, Any]],
    *,
    issue_ports: int,
) -> None:
    payload = build_perfetto_trace(
        start_events,
        done_events,
        issue_ports=issue_ports,
    )
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


__all__ = ["build_perfetto_trace", "dump_perfetto_trace"]
