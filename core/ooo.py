
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Deque
from collections import deque
import json


def is_vreg(name: Any) -> bool:
    return isinstance(name, str) and name[:1].lower() == "v"


def is_mem(name: Any) -> bool:
    return isinstance(name, str) and name[:3].lower() == "mem"


def is_intermediate_mem(name: Any) -> bool:
    return isinstance(name, str) and name.lower().startswith("mem_inter")


def make_mem_key(name: str, iter_stack: List[Any]) -> Tuple[str, Tuple[int, ...]]:
    norm_iter = tuple(int(x) for x in (iter_stack or []))
    return (name, norm_iter)


@dataclass
class Uop:
    inst_id: int
    op: str
    src: List[Any]
    dst: List[Any]
    preg_src: List[Optional[str]]
    preg_dst: List[str]
    preg_old: List[Optional[str]]

    state: str = "blocked"  # blocked/ready/running/done
    ready_cycle: int = 0
    start_cycle: Optional[int] = None
    done_cycle: Optional[int] = None

    producer_op_for_store: Optional[str] = None
    producer_start_for_store: Optional[int] = None
    mem_dep_uops: List["Uop"] = field(default_factory=list)
    top_block_id: int = 0
    iter_stack: List[Any] = field(default_factory=list)
    is_last_in_top_block: bool = False
    exu_port: Optional[int] = None


class OoOCore:
    def __init__(self, uarch: Dict[str, Any], pdb, dtype: str = "fp32"):
        self.dtype = dtype
        self.db = pdb
        self.theoretical_limit_mode = bool(uarch.get("theoretical_limit_mode", False))
        self.three_ports_mode = bool(uarch.get("three_ports_mode", False))

        self.load_ports = int(uarch.get("load_ports", 2))
        self.issue_ports = int(uarch.get("issue_ports", 2))  # total EXU count
        self.store_ports = int(uarch.get("store_ports", 1))
        self.shq_depth = int(uarch.get("shq_depth", 58))
        self.lsq_depth = int(uarch.get("LDQ_width", 24))
        self.preg_num = int(uarch.get("vreg", uarch.get("vreg_num", 68)))

        defaults = self.db.get_defaults()
        self.vf_startup_cost = int(defaults.get("vf_startup_cost", 0))
        self.vf_drain_cost = int(defaults.get("vf_drain_cost", 0))

        # rename
        self.freelist: Deque[str] = deque([f"p{i}" for i in range(self.preg_num)])
        self.RAT: Dict[str, str] = {}
        self.next_dynamic_preg_id: int = self.preg_num

        # queues
        self.SHQ: List[Uop] = []   # compute queue (SHQ)
        self.LSQ: List[Uop] = []   # VLD/VST only
        self.ROB: Deque[Uop] = deque()

        # dependency tracking
        self.preg_producer: Dict[str, Tuple[str, int, str]] = {}
        self.preg_producer_uop: Dict[str, Uop] = {}

        # EXU issue history.
        # By default, II is enforced at EXU level (cross-FU), because each EXU
        # contains ALU+SFU resources but dispatch is still serialized per EXU.
        self.enable_cross_fu_ii = bool(uarch.get("enable_cross_fu_ii", True))
        self.last_issue_cycle = {
            "ALU": [-10**9] * self.issue_ports,
            "SFU": [-10**9] * self.issue_ports,
        }
        self.last_op = {
            "ALU": [None] * self.issue_ports,
            "SFU": [None] * self.issue_ports,
        }
        self.last_issue_cycle_exu = [-10**9] * self.issue_ports
        self.last_op_exu = [None] * self.issue_ports

        self.cycle: int = 0
        self.last_done_cycle: int = 0
        self.history: List[Dict[str, Any]] = []
        self.debug = bool(uarch.get("debug", False))

        self.preg_pending = set()
        self.VLD_COST = int(uarch.get("VLD_COST", 9))
        self.mem_last_store_uop: Dict[Tuple[str, Tuple[int, ...]], Uop] = {}
        self.mem_bar_mode = str(uarch.get("mem_bar_mode", "weak")).strip().lower()
        self.enforce_same_cycle_src_hazard = bool(uarch.get("enforce_same_cycle_src_hazard", True))
        # Optional EXQ-aware port selection policy (disabled by default to preserve old behavior)
        self.enable_exq_greedy_balance = bool(uarch.get("enable_exq_greedy_balance", False))
        self.exq_inflight = [0] * self.issue_ports
        self.enable_isu_queue_model = bool(uarch.get("enable_isu_queue_model", False))
        self.exq_depth = int(uarch.get("exq_depth", 26))
        self.exq_recv_delay = int(uarch.get("exq_recv_delay", 1))
        self.shq_to_exq_port_per_cycle = int(uarch.get("shq_to_exq_port_per_cycle", 1))
        self.exq_capacity_counts_inflight = bool(uarch.get("exq_capacity_counts_inflight", False))
        self.exq_rr_ptr = 0
        self.block_outstanding_stores: Dict[int, int] = {}
        self.block_last_inst_done: Dict[int, bool] = {}
        self.block_release_cycle: Dict[int, int] = {}
        self.theoretical_limit_legacy_forwarding = bool(
            uarch.get("theoretical_limit_legacy_forwarding", False)
        )

        self.cyc_start_log: List[Dict[str, Any]] = []
        self.cyc_done_log: List[Dict[str, Any]] = []

    # -------- logging --------
    def _log(self, event: str, u: Uop) -> None:
        self.history.append({
            "cy": self.cycle,
            "event": event,
            "id": u.inst_id,
            "op": u.op,
            "state": u.state,
            "ready": u.ready_cycle,
            "start": u.start_cycle,
            "done": u.done_cycle,
            "src": u.src,
            "dst": u.dst,
            "preg_src": u.preg_src,
            "preg_dst": u.preg_dst,
            "preg_old": u.preg_old,
            "producer_op_for_store": u.producer_op_for_store,
            "producer_start_for_store": u.producer_start_for_store,
        })

    def _log_start_simple(self, u: Uop) -> None:
        self.cyc_start_log.append({
            "cy": self.cycle,
            "inst_id": u.inst_id,
            "op": u.op,
            "dst": u.dst,
            "src": u.src,
        })

    def _log_done_simple(self, u: Uop) -> None:
        self.cyc_done_log.append({
            "cy": u.done_cycle if u.done_cycle is not None else self.cycle,
            "inst_id": u.inst_id,
            "op": u.op,
            "dst": u.dst,
            "src": u.src,
        })

    def dump_history(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def dump_simple_logs(self, start_path="start_log.jsonl", done_path="done_log.jsonl") -> None:
        with open(start_path, "w", encoding="utf-8") as f:
            for item in self.cyc_start_log:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        with open(done_path, "w", encoding="utf-8") as f:
            for item in self.cyc_done_log:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # -------- IDU interface --------
    def rename_credits(self) -> int:
        if self.theoretical_limit_mode:
            return 10 ** 18
        return len(self.freelist)

    def get_free_preg(self) -> int:
        return self.rename_credits()

    def get_free_shq_queue(self) -> int:
        if self.theoretical_limit_mode:
            return 10 ** 18
        return self.shq_depth - len(self.SHQ)

    def get_free_lsq(self) -> int:
        if self.theoretical_limit_mode:
            return 10 ** 18
        return self.lsq_depth - len(self.LSQ)

    def get_free_shq(self) -> int:
        # Base model: no separate SHQ credit model.
        return 10 ** 18

    def update_idu_visibility(self, cycle: int) -> Dict[str, int]:
        """
        Hook for models that need time-delayed credit visibility back to IDU.
        Base model has no delayed-visibility state.
        """
        return {"preg_free": 0, "shq_release": 0}

    # -------- ISA --------
    def _inst_params(self, op: str) -> Dict[str, Any]:
        return self.db.get_inst(op, dtype=self.dtype)

    def _latency(self, op: str) -> int:
        return int(self._inst_params(op).get("latency", 1))

    def _get_ii(self, prev_op: Optional[str], cur_op: str) -> int:
        if prev_op is None:
            return 1
        return int(self.db.get_ii(prev_op, cur_op, dtype=self.dtype))

    def _startup_cost(self, op: str) -> int:
        return int(self._inst_params(op).get("pipeline_startup_cost", 0))

    def _drain_cost(self, op: str) -> int:
        return int(self._inst_params(op).get("pipeline_drain_cost", 0))

    def _data_store_cost(self, producer_op: str) -> int:
        return int(self._inst_params(producer_op).get("data_store_cost", 1))

    def _get_fu_type(self, op: str) -> str:
        try:
            fu = str(self._inst_params(op).get("EXU", "ALU")).upper()
        except Exception:
            fu = "ALU"
        if fu not in ("ALU", "SFU"):
            fu = "ALU"
        return fu

    def _eligible_exu_ports(self, op: str) -> List[int]:
        """
        Restrict which EXU/EXQ ports an op may use according to isa.json.

        Supported tags:
        - EXU0_ONLY : only port 0
        - EXU01     : port 0 / port 1 (or port 0 / port 1 / port 2 in three_ports_mode)
        - EXU012    : port 0 / port 1 / port 2

        Fallback:
        - missing / unknown tag => all available ports
        """
        try:
            dispatch_exu = str(self._inst_params(op).get("dispatch_exu", "")).upper()
        except Exception:
            dispatch_exu = ""

        if dispatch_exu == "EXU0_ONLY":
            return [0] if self.issue_ports > 0 else []
        if dispatch_exu == "EXU01":
            if self.three_ports_mode:
                return [p for p in range(min(self.issue_ports, 3))]
            return [p for p in range(min(self.issue_ports, 2))]
        if dispatch_exu == "EXU012":
            return [p for p in range(min(self.issue_ports, 3))]
        return list(range(self.issue_ports))

    # -------- accept / rename --------
    def accept(self, inst: Dict[str, Any]) -> None:
        raise NotImplementedError("OoOCore.accept() must be implemented by a concrete OOO model")

    # -------- readiness --------
    def _ready_time_for_src(self, producer_info: Tuple[str, int, str], consumer_op: str) -> int:
        prod_op, prod_start, kind = producer_info
        if kind == "VLD":
            if bool(getattr(self, "enable_isu_queue_model", False)) and not self.theoretical_limit_legacy_forwarding:
                # Queue model aligns SHQ wakeup to VLD start + (startup_cost - 1).
                return prod_start + max(0, self._startup_cost(consumer_op) - 1)
            return prod_start + self._startup_cost(consumer_op)
        fwd = int(self.db.get_forwarding_cycles(prod_op, consumer_op, dtype=self.dtype))
        # Queue-level timing alignment:
        # In SHQ wakeup modeling, consumer wakeup-ready follows
        #   producer_EXQ_ISSUE - 1 + forwarding
        # where prod_start is producer_EXQ_ISSUE/start_cycle.
        if (
            kind == "COMPUTE"
            and bool(getattr(self, "enable_isu_queue_model", False))
            and not self.theoretical_limit_legacy_forwarding
        ):
            return prod_start + max(0, fwd - 1)
        return prod_start + fwd

    def _compute_ready_cycle(self, u: Uop) -> int:
        # dependency-only ready time
        t = self.vf_startup_cost
        for ps in u.preg_src:
            if ps is None:
                continue
            info = self.preg_producer.get(ps)
            if info is None:
                if ps in self.preg_pending:
                    return 10 ** 9
                continue
            t = max(t, self._ready_time_for_src(info, u.op))
        return t

    def _load_ready_cycle(self, u: Uop) -> int:
        t = self.vf_startup_cost
        for pred_u in u.mem_dep_uops:
            if pred_u.done_cycle is None:
                return 10 ** 9
            t = max(t, pred_u.done_cycle)
        if self.mem_bar_mode == "strong":
            for s in u.src:
                if not is_intermediate_mem(s):
                    continue
                if u.top_block_id <= 0:
                    continue
                prev_block_id = u.top_block_id - 1
                release_cycle = self.block_release_cycle.get(prev_block_id)
                if release_cycle is None:
                    return 10 ** 9
                t = max(t, release_cycle)
        return t

    def _store_ready_cycle(self, u: Uop) -> Tuple[int, Optional[str], Optional[int]]:
        for ps in u.preg_src:
            if ps is None:
                continue
            if ps in self.preg_pending and ps not in self.preg_producer:
                return 10 ** 9, None, None

        best_t = -1
        pop = None
        pst = None
        for ps in u.preg_src:
            if ps is None:
                continue
            info = self.preg_producer.get(ps)
            if info is None:
                continue
            prod_op, prod_start, kind = info
            if kind != "COMPUTE":
                continue
            cand = prod_start + self._drain_cost(prod_op)
            if cand > best_t:
                best_t = cand
                pop = prod_op
                pst = prod_start

        if best_t < 0:
            return 10 ** 9, None, None
        return best_t, pop, pst

    # -------- retire helper --------
    def _free_old_pregs(self, u: Uop) -> None:
        raise NotImplementedError(
            "OoOCore._free_old_pregs() must be implemented by a concrete OOO model"
        )

    def _pick_exu_port(self, fu_type: str, cur_op: str, c: int, exu_used_this_cycle: List[bool]) -> Optional[int]:
        legal_ports = set(self._eligible_exu_ports(cur_op))
        if not self.enable_exq_greedy_balance:
            for port in range(self.issue_ports):
                if port not in legal_ports:
                    continue
                if exu_used_this_cycle[port]:
                    continue
                if self.enable_cross_fu_ii:
                    prev_op = self.last_op_exu[port]
                    prev_issue = self.last_issue_cycle_exu[port]
                else:
                    prev_op = self.last_op[fu_type][port]
                    prev_issue = self.last_issue_cycle[fu_type][port]
                ii = self._get_ii(prev_op, cur_op)
                if c >= prev_issue + ii:
                    return port
            return None

        candidates = []
        for port in range(self.issue_ports):
            if port not in legal_ports:
                continue
            if exu_used_this_cycle[port]:
                continue
            if self.enable_cross_fu_ii:
                prev_op = self.last_op_exu[port]
                prev_issue = self.last_issue_cycle_exu[port]
            else:
                prev_op = self.last_op[fu_type][port]
                prev_issue = self.last_issue_cycle[fu_type][port]
            ii = self._get_ii(prev_op, cur_op)
            avail = max(c, prev_issue + ii)
            candidates.append((port, avail))

        if not candidates:
            return None
        min_avail = min(av for _, av in candidates)
        if min_avail > c:
            return None

        fast_ports = [p for p, av in candidates if av == min_avail]
        if len(fast_ports) == 1:
            return fast_ports[0]

        min_load = min(self.exq_inflight[p] for p in fast_ports)
        light_ports = [p for p in fast_ports if self.exq_inflight[p] == min_load]
        if len(light_ports) == 1:
            return light_ports[0]

        for off in range(self.issue_ports):
            cand = (self.exq_rr_ptr + off) % self.issue_ports
            if cand in light_ports:
                self.exq_rr_ptr = (cand + 1) % self.issue_ports
                return cand
        return light_ports[0]

    # -------- step --------
    def step(self) -> None:
        raise NotImplementedError("OoOCore.step() must be implemented by a concrete OOO model")

    def vf_end_cycle(self) -> int:
        return self.last_done_cycle + self.vf_drain_cost
