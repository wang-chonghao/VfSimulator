#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

"""
ISU-side helpers for the mainline simulator.

Boundary:
- OOO owns rename, preg lifecycle, SHQ, LSQ, ready computation, and store/load paths.
- ISU owns what happens after ready compute instructions leave SHQ:
  - direct issue to EXU in direct-issue mode
  - SHQ -> EXQ enqueue
  - EXQ arbitration
  - EXQ -> EXU launch

The current implementation intentionally keeps state on the core object and
only extracts the issue-path logic, so behavior remains unchanged while the
OOO/ISU structure becomes easier to read.
"""


class ISUController:
    def __init__(self, core) -> None:
        self.core = core

    def remove_issued(self, queue_name: str, issued: List[Any]) -> None:
        if not issued:
            return
        rm_ids = {id(item) for item in issued}
        if queue_name == "SHQ":
            self.core.SHQ[:] = [item for item in self.core.SHQ if id(item) not in rm_ids]
        elif queue_name == "LSQ":
            self.core.LSQ = [item for item in self.core.LSQ if id(item) not in rm_ids]

    def exq_occ(self, port: int) -> int:
        q = self.core.exq_wait[port]
        occ = len(q["ALU"]) + len(q["SFU"])
        if self.core.exq_capacity_counts_inflight:
            occ += self.core.exq_inflight[port]
        return occ

    def total_compute_inflight(self) -> int:
        total = sum(int(x) for x in self.core.exq_inflight)
        for q in self.core.exq_wait:
            total += len(q["ALU"]) + len(q["SFU"])
        return int(total)

    def predict_exq_issue_cycle(
        self,
        port: int,
        fu_type: str,
        op: str,
        recv_cycle: int,
    ) -> int:
        q_fu = self.core.exq_wait[port][fu_type]
        pred = int(recv_cycle)
        if q_fu:
            prev = q_fu[-1]
            prev_pred = int(getattr(prev, "exq_pred_issue", recv_cycle))
            ii = self.core._get_ii(prev.op, op)
            pred = max(pred, prev_pred + ii)
        else:
            prev_op = self.core.last_op[fu_type][port]
            prev_issue = self.core.last_issue_cycle[fu_type][port]
            ii = self.core._get_ii(prev_op, op)
            pred = max(pred, prev_issue + ii)

        if self.core.predict_exq_issue_with_cross_fu:
            prev_op_exu = self.core.last_op_exu[port]
            prev_issue_exu = self.core.last_issue_cycle_exu[port]
            ii_exu = self.core._get_ii(prev_op_exu, op)
            pred = max(pred, prev_issue_exu + ii_exu)
        return pred

    def issue_direct_from_shq(
        self,
        cycle: int,
        issued_srcs_this_cycle: Set[str],
        exu_used_this_cycle: List[bool],
    ) -> int:
        issued_ex: List[Any] = []
        ex_count = 0
        for u in self.core.SHQ:
            if u.state != "ready":
                continue
            if ex_count >= self.core.issue_ports:
                break

            cur_srcs = {ps for ps in u.preg_src if ps is not None}
            if (
                self.core.enforce_same_cycle_src_hazard
                and (not self.core.theoretical_limit_mode)
                and (issued_srcs_this_cycle & cur_srcs)
            ):
                continue

            fu_type = self.core._get_fu_type(u.op)
            chosen_port = self.core._pick_exu_port(
                fu_type, u.op, cycle, exu_used_this_cycle
            )
            if chosen_port is None:
                continue

            u.start_cycle = cycle
            u.done_cycle = cycle + self.core._latency(u.op)
            u.state = "running"
            self.core._schedule_src_release_from_start(u)
            self.core._log("start", u)
            self.core._log_start_simple(u)

            ex_count += 1
            issued_ex.append(u)

            if self.core.enable_cross_fu_ii:
                self.core.last_issue_cycle_exu[chosen_port] = cycle
                self.core.last_op_exu[chosen_port] = u.op
            else:
                self.core.last_issue_cycle[fu_type][chosen_port] = cycle
                self.core.last_op[fu_type][chosen_port] = u.op
            exu_used_this_cycle[chosen_port] = True
            u.exu_port = chosen_port
            self.core.exq_inflight[chosen_port] += 1

            for pd in u.preg_dst:
                self.core.preg_producer[pd] = (u.op, u.start_cycle, "COMPUTE")
                self.core.preg_producer_uop[pd] = u
                self.core.preg_pending.discard(pd)

            issued_srcs_this_cycle |= cur_srcs

        self.remove_issued("SHQ", issued_ex)
        return ex_count

    def enqueue_shq_to_exq(
        self,
        cycle: int,
        issued_srcs_this_cycle: Set[str],
    ) -> int:
        issued_shq: List[Any] = []
        shq_to_exq_cnt = [0] * self.core.issue_ports
        ex_count = 0

        for u in self.core.SHQ:
            if u.state != "ready":
                continue
            if ex_count >= self.core.issue_ports:
                break

            cur_srcs = {ps for ps in u.preg_src if ps is not None}
            if (
                self.core.enforce_same_cycle_src_hazard
                and (not self.core.theoretical_limit_mode)
                and (issued_srcs_this_cycle & cur_srcs)
            ):
                continue

            fu_type = self.core._get_fu_type(u.op)
            legal_ports = set(self.core._eligible_exu_ports(u.op))
            candidates: List[int] = []
            for port in range(self.core.issue_ports):
                if port not in legal_ports:
                    continue
                if shq_to_exq_cnt[port] >= self.core.shq_to_exq_port_per_cycle:
                    continue
                occ = self.exq_occ(port)
                if occ >= self.core.exq_depth:
                    continue
                candidates.append(port)
            if not candidates:
                continue

            recv_cycle = cycle + self.core.exq_recv_delay
            if self.core.shq_exq_static_rr:
                ordered = sorted(
                    candidates,
                    key=lambda p: ((p - self.core.shq_exq_rr_ptr) % self.core.issue_ports, p),
                )
                chosen_port = ordered[0]
                predicted_issue = self.predict_exq_issue_cycle(
                    chosen_port, fu_type, u.op, recv_cycle
                )
                self.core.shq_exq_rr_ptr = (chosen_port + 1) % self.core.issue_ports
            else:
                best = None
                for port in candidates:
                    predicted_issue = self.predict_exq_issue_cycle(
                        port, fu_type, u.op, recv_cycle
                    )
                    occ = self.exq_occ(port)
                    key = (predicted_issue, occ, port)
                    if best is None or key < best[0]:
                        best = (key, port)
                chosen_port = best[1]
                predicted_issue = best[0][0]

            u.exu_port = chosen_port
            u.exq_recv_cycle = recv_cycle  # type: ignore[attr-defined]
            u.exq_pred_issue = predicted_issue  # type: ignore[attr-defined]
            u.state = "exq_wait"
            issued_shq.append(u)
            self.core.exq_wait[chosen_port][fu_type].append(u)

            if bool(getattr(u, "shq_tracked", False)):
                self.core._schedule_shq_release(cycle, 1)
                setattr(u, "shq_tracked", False)
            shq_to_exq_cnt[chosen_port] += 1
            ex_count += 1
            issued_srcs_this_cycle |= cur_srcs

        self.remove_issued("SHQ", issued_shq)
        return ex_count

    def issue_exq_to_exu(self, cycle: int, exu_used_this_cycle: List[bool]) -> None:
        for port in range(self.core.issue_ports):
            if exu_used_this_cycle[port]:
                continue
            q_port = self.core.exq_wait[port]
            if not q_port["ALU"] and not q_port["SFU"]:
                continue

            prev_op_exu = self.core.last_op_exu[port]
            prev_issue_exu = self.core.last_issue_cycle_exu[port]
            best: Optional[Tuple[Tuple[int, int, int], str, Any]] = None
            for fu_type in ("ALU", "SFU"):
                if not q_port[fu_type]:
                    continue
                cand = q_port[fu_type][0]
                if (
                    self.core.exq_issue_inflight_cap_per_port > 0
                    and int(self.core.exq_inflight[port])
                    >= self.core.exq_issue_inflight_cap_per_port
                ):
                    continue
                recv_cy = int(getattr(cand, "exq_recv_cycle", cycle))
                if recv_cy > cycle:
                    continue
                ready_cy = int(self.core._compute_ready_cycle(cand))
                if ready_cy > cycle:
                    continue

                ii_exu = self.core._get_ii(prev_op_exu, cand.op)
                if cycle < prev_issue_exu + ii_exu:
                    continue

                key = (ready_cy, recv_cy, cand.inst_id)
                if best is None or key < best[0]:
                    best = (key, fu_type, cand)

            if best is None:
                continue

            _, fu_type, u = best
            q_port[fu_type].popleft()
            u.start_cycle = cycle
            u.done_cycle = cycle + self.core._latency(u.op)
            u.state = "running"
            self.core._schedule_src_release_from_start(u)
            self.core._log("start", u)
            self.core._log_start_simple(u)

            self.core.last_issue_cycle_exu[port] = cycle
            self.core.last_op_exu[port] = u.op
            self.core.last_issue_cycle[fu_type][port] = cycle
            self.core.last_op[fu_type][port] = u.op
            exu_used_this_cycle[port] = True
            self.core.exq_inflight[port] += 1

            for pd in u.preg_dst:
                self.core.preg_producer[pd] = (u.op, u.start_cycle, "COMPUTE")
                self.core.preg_producer_uop[pd] = u
                self.core.preg_pending.discard(pd)
