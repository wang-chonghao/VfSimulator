#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from collections import deque
from typing import Any, Dict

from vfsimulator.core.isa_traits import uses_lsq, uses_shared_shq_credit, uses_shq_queue


def _is_vreg_name(x: Any) -> bool:
    return isinstance(x, str) and x[:1].lower() == "v"


def _inst_reservation(inst: Dict[str, Any]) -> Dict[str, int]:
    op = str(inst.get("op", ""))
    dsts = inst.get("dst", [])
    if isinstance(dsts, str):
        dsts = [dsts]
    if not isinstance(dsts, list):
        dsts = []
    preg = sum(1 for d in dsts if _is_vreg_name(d))
    shq_queue = 1 if uses_shq_queue(op) else 0
    lsq = 1 if uses_lsq(op) else 0
    shq = 1 if uses_shared_shq_credit(op) else 0
    return {"preg": preg, "shq_queue": shq_queue, "lsq": lsq, "shq": shq}


class _IDUCreditProxy:
    def __init__(self, core, preg: int, shq_queue: int, lsq: int, shq: int):
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


def run_simulation(
    *,
    ifu,
    idu,
    ooo,
    uarch: Dict[str, Any],
    params: Dict[str, Any],
    results_dir: str,
) -> Dict[str, Any]:
    """
    Run the main IFU -> IDU -> OoO simulation loop and dump the standard logs.

    Returns:
      {
        "cycles_executed": int,
        "vf_end_cycle": int,
        "results_dir": str,
      }
    """
    idu_to_ooo_delay = int(uarch.get("idu_to_ooo_delay", 0))
    idu_to_ooo_pipe = deque()
    use_explicit_idu_credit_bank = bool(
        uarch.get("use_explicit_idu_credit_bank", False)
    )
    idu_preg_credit = int(ooo.get_free_preg())
    idu_shq_credit = int(ooo.get_free_shq())
    idu_pending_shq_queue = 0
    idu_pending_lsq = 0

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
                idu_pending_shq_queue = max(
                    0, int(idu_pending_shq_queue) - int(r["shq_queue"])
                )
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

        if use_explicit_idu_credit_bank:
            idu_credit_proxy = _IDUCreditProxy(ooo, 0, 0, 0, 0)
            idu_credit_proxy.get_free_preg = lambda: max(0, int(idu_preg_credit))
            idu_credit_proxy.get_free_shq = lambda: max(0, int(idu_shq_credit))
            idu_credit_proxy.get_free_shq_queue = lambda: max(
                0, int(ooo.get_free_shq_queue()) - int(idu_pending_shq_queue)
            )
            idu_credit_proxy.get_free_lsq = lambda: max(
                0, int(ooo.get_free_lsq()) - int(idu_pending_lsq)
            )
        else:
            idu_credit_proxy = _IDUCreditProxy(
                ooo, pending_preg, pending_shq_queue, pending_lsq, pending_shq
            )

        to_send = idu.dispatch(cycle, idu_credit_proxy)
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
            "Simulation did not complete before max_cycles. "
            f"cycle={cycle}, vf_end={ooo.vf_end_cycle()}, "
            f"ifu_done={ifu.done()}, idu_empty={idu.empty()}, "
            f"shq={len(ooo.SHQ)}, lsq={len(ooo.LSQ)}, rob={len(ooo.ROB)}, pipe={len(idu_to_ooo_pipe)}"
        )

    if not os.path.exists(results_dir):
        os.makedirs(results_dir)

    ooo.dump_history(os.path.join(results_dir, "sim_history.json"))
    ooo.dump_simple_logs(
        os.path.join(results_dir, "start_by_cycle.json"),
        os.path.join(results_dir, "done_by_cycle.json"),
    )
    idu.dump_dispatch_log(os.path.join(results_dir, "idu_to_ooo.json"))
    idu.dump_vloop_trace(os.path.join(results_dir, "vloop_trace.json"))

    return {
        "cycles_executed": int(cycle),
        "vf_end_cycle": int(ooo.vf_end_cycle()),
        "results_dir": str(results_dir),
    }
