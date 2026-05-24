#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from collections import deque
from typing import Any, Dict, List, Tuple

from core.flatten import Flattener
from core.idu import IDU
from core.ifu import IFUUnroll
from core.ooo_factory import create_ooo_core
from core.param_db import ParamDB
from main import collect_vreg_capacity_warnings, infer_top_block_loop_bounds, load_json


class IDUTimingProbe(IDU):
    def __init__(self, *args, body_open_offset: int = 2, **kwargs):
        self._body_open_offset = int(body_open_offset)
        super().__init__(*args, **kwargs)

    def _set_top_block_vloop(self, top_block_id: int, start_cycle: int):
        top_block_id = int(top_block_id)
        start_cycle = int(start_cycle)
        if top_block_id in self.top_block_vloop_start:
            return
        self.top_block_vloop_start[top_block_id] = start_cycle
        self.top_block_body_open_time[top_block_id] = start_cycle + self._body_open_offset

    def _set_vloop_start(self, key, start_cycle: int):
        if key in self.vloop_start:
            return
        start_cycle = int(start_cycle)
        self.vloop_start[key] = start_cycle
        self.body_open_time[key] = start_cycle + self._body_open_offset
        tbid, loop_id, it = key
        self._record_vloop(tbid, loop_id, it, start_cycle)


def _inst_dst_vreg_count(inst: Dict[str, Any]) -> int:
    c = 0
    for d in inst.get("dst", []) or []:
        if isinstance(d, str) and d[:1].lower() == "v":
            c += 1
    return c


def _pending_usage(pending: deque[Tuple[int, Dict[str, Any]]]) -> Tuple[int, int, int]:
    preg = 0
    shq_queue = 0
    lsq = 0
    for _, inst in pending:
        op = inst.get("op", "")
        preg += _inst_dst_vreg_count(inst)
        if op in ("VLD", "VST"):
            lsq += 1
        else:
            shq_queue += 1
    return preg, shq_queue, lsq


class _OOOAvailProxy:
    def __init__(self, ooo, pending_preg: int, pending_shq_queue: int, pending_lsq: int):
        self._ooo = ooo
        self._pending_preg = int(pending_preg)
        self._pending_shq_queue = int(pending_shq_queue)
        self._pending_lsq = int(pending_lsq)

        def get_free_preg(self):
            return max(0, int(self._ooo.get_free_preg()) - self._pending_preg)

    def get_free_shq_queue(self):
        return max(0, int(self._ooo.get_free_shq_queue()) - self._pending_shq_queue)

    def get_free_lsq(self):
        return max(0, int(self._ooo.get_free_lsq()) - self._pending_lsq)


def run_once(
    trace_path: str,
    out_dir: str,
    ooo_model: str,
    body_open_offset: int,
    ooo_accept_delay: int,
    post_step_accept: bool,
    vreg_num: int | None = None,
):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isabs(trace_path):
        trace_path = os.path.join(base_dir, trace_path)
    if not os.path.exists(trace_path):
        raise FileNotFoundError(f"Trace not found: {trace_path}")

    trace = load_json(trace_path)
    dtype = trace.get("dtype", "fp32")
    params = trace.get("params", {}) or {}
    program = trace.get("program")
    if program is None:
        raise RuntimeError("trace.json missing key 'program'")

    top_block_loop_bounds = infer_top_block_loop_bounds(program, params)
    total_top_blocks = len(top_block_loop_bounds)
    loop_bounds = top_block_loop_bounds.get(0, [])

    linear = Flattener(params).flatten(program)
    ifu = IFUUnroll(linear, params)

    db = ParamDB(base_dir=base_dir)
    uarch = dict(db.get_uarch())
    uarch["ooo_model"] = ooo_model
    uarch["consumer_done_release_delay"] = 0
    if vreg_num is not None and int(vreg_num) > 0:
        uarch["vreg_num"] = int(vreg_num)

    idu = IDUTimingProbe(
        uarch,
        db,
        params=params,
        loop_bounds=loop_bounds,
        total_top_blocks=total_top_blocks,
        top_block_loop_bounds=top_block_loop_bounds,
        body_open_offset=body_open_offset,
    )
    ooo = create_ooo_core(uarch, db, dtype=dtype)

    cycle = 0
    max_cycles = int(params.get("max_cycles", 1_000_000))
    pending_to_ooo: deque[Tuple[int, Dict[str, Any]]] = deque()

    while cycle < max_cycles:
        while idu.can_accept():
            if ifu.done():
                break
            inst = ifu.next_inst()
            if inst is None:
                break
            if "inst_id" not in inst and "id" in inst:
                inst["inst_id"] = inst["id"]
            idu.accept(inst)

        # IDU dispatch first, but delay visible-to-OOO acceptance.
        # Pending-not-yet-accepted instructions still consume front-end resources.
        pending_preg, pending_shq_queue, pending_lsq = _pending_usage(pending_to_ooo)
        to_send = idu.dispatch(cycle, _OOOAvailProxy(ooo, pending_preg, pending_shq_queue, pending_lsq))
        for inst in to_send:
            pending_to_ooo.append((cycle + int(ooo_accept_delay), inst))

        # Optional pre-step accept (not used in this experiment by default).
        if not post_step_accept:
            while pending_to_ooo and pending_to_ooo[0][0] <= cycle:
                _, inst = pending_to_ooo.popleft()
                ooo.accept(inst)

        ooo.step()

        # Post-step accept means accepted this cycle can only issue next cycle.
        if post_step_accept:
            while pending_to_ooo and pending_to_ooo[0][0] <= cycle:
                _, inst = pending_to_ooo.popleft()
                ooo.accept(inst)

        if (
            ifu.done()
            and idu.empty()
            and len(ooo.SHQ) == 0
            and len(ooo.LSQ) == 0
            and len(ooo.ROB) == 0
            and len(pending_to_ooo) == 0
        ):
            break

        cycle += 1

    results_dir = out_dir
    if not os.path.isabs(results_dir):
        results_dir = os.path.join(base_dir, results_dir)
    os.makedirs(results_dir, exist_ok=True)

    vreg_capacity_warnings = collect_vreg_capacity_warnings(program, params, int(ooo.preg_num))
    if vreg_capacity_warnings:
        warning_path = os.path.join(results_dir, "vreg_capacity_warnings.json")
        import json
        with open(warning_path, "w", encoding="utf-8") as f:
            json.dump(vreg_capacity_warnings, f, indent=2, ensure_ascii=False)

    ooo.dump_history(os.path.join(results_dir, "sim_history.json"))
    ooo.dump_simple_logs(
        os.path.join(results_dir, "start_by_cycle.json"),
        os.path.join(results_dir, "done_by_cycle.json"),
    )
    idu.dump_dispatch_log(os.path.join(results_dir, "idu_to_ooo.json"))
    idu.dump_vloop_trace(os.path.join(results_dir, "vloop_trace.json"))

    print(f"[RESULT] VF end cycle (with drain) = {ooo.vf_end_cycle()}")
    print(f"[RESULT] out_dir = {results_dir}")


def main():
    parser = argparse.ArgumentParser(description="Queue-level timing experiment without touching main pipeline.")
    parser.add_argument("--trace", required=True, help="Trace JSON path")
    parser.add_argument("--out_dir", required=True, help="Output directory")
    parser.add_argument("--ooo-model", default="queue_level1", choices=["classical-cpu-type", "consumer-done", "queue_level1"])
    parser.add_argument("--body-open-offset", type=int, default=2, help="IDU body-open offset after VLOOP start")
    parser.add_argument("--ooo-accept-delay", type=int, default=1, help="Delay (cycles) from IDU dispatch to OOO accept visibility")
    parser.add_argument("--post-step-accept", action="store_true", help="Accept into OOO after ooo.step of same cycle")
    parser.add_argument("--vreg-num", type=int, default=None, help="Override uarch vreg_num for experiment")
    args = parser.parse_args()

    run_once(
        trace_path=args.trace,
        out_dir=args.out_dir,
        ooo_model=args.ooo_model,
        body_open_offset=args.body_open_offset,
        ooo_accept_delay=args.ooo_accept_delay,
        post_step_accept=bool(args.post_step_accept),
        vreg_num=args.vreg_num,
    )


if __name__ == "__main__":
    main()
