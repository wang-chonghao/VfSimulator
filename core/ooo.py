
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Deque
from collections import deque
import json

from core.isa_traits import is_compute_op, is_load_op, is_store_op
from core.instruction_profile import InstructionProfile
from core.value_storage import ValueStorageLookup


def is_vreg(name: Any) -> bool:
    return ValueStorageLookup().is_register(name)


@dataclass
class Uop:
    inst_id: int
    op: str
    form: str
    src: List[Any]
    dst: List[Any]
    preg_src: List[Optional[str]]
    preg_dst: List[str]
    preg_old: List[Optional[str]]
    profile: Optional[InstructionProfile] = None

    state: str = "blocked"  # blocked/ready/running/done
    ready_cycle: int = 0
    start_cycle: Optional[int] = None
    done_cycle: Optional[int] = None
    blocked_reason: Optional[str] = None

    producer_op_for_store: Optional[str] = None
    producer_form_for_store: Optional[str] = None
    producer_start_for_store: Optional[int] = None
    top_block_id: int = 0
    iter_stack: List[Any] = field(default_factory=list)
    is_last_in_top_block: bool = False
    stream_seq: int = -1
    static_instruction_id: Optional[str] = None
    iteration_path: List[Dict[str, Any]] = field(default_factory=list)
    src_value_instances: List[Dict[str, Any]] = field(default_factory=list)
    dst_value_instances: List[Dict[str, Any]] = field(default_factory=list)
    exu_port: Optional[int] = None
    shq_ready_cycle: int = 0
    lsq_ready_cycle: int = 0


class OoOCore:
    def __init__(self, uarch: Dict[str, Any], pdb, dtype: str = "fp32", values: Dict[str, Any] | None = None):
        self.dtype = dtype
        self.db = pdb
        self.value_storage = ValueStorageLookup(values)
        self.theoretical_limit_mode = bool(uarch.get("theoretical_limit_mode", False))
        self.three_ports_mode = bool(uarch.get("three_ports_mode", False))

        self.load_ports = int(uarch.get("load_ports", 2))
        self.issue_ports = int(uarch.get("issue_ports", 2))  # total EXU count
        self.store_ports = int(uarch.get("store_ports", 1))
        self.ub_slots = int(uarch.get("ub_slots", 2))
        if self.load_ports <= 0 or self.store_ports <= 0 or self.ub_slots <= 0:
            raise ValueError("load_ports, store_ports, and ub_slots must be positive")
        if "lsu_issue_policy" in uarch:
            raise ValueError(
                "lsu_issue_policy has been removed; configure "
                "lsu_store_priority_preg_threshold instead"
            )
        self.lsu_store_priority_preg_threshold = int(
            uarch.get("lsu_store_priority_preg_threshold", 1)
        )
        if self.lsu_store_priority_preg_threshold < 0:
            raise ValueError("lsu_store_priority_preg_threshold must be non-negative")
        self.shq_depth = int(uarch.get("shq_depth", 58))
        self.lsq_depth = int(uarch.get("LDQ_width", 24))
        self.preg_num = int(uarch.get("vreg", uarch.get("vreg_num", 68)))

        defaults = self.db.get_defaults()
        self.vf_startup_cost = int(defaults.get("vf_startup_cost", 0))
        self.vf_drain_cost = int(defaults.get("vf_drain_cost", 0))

        # rename
        self.freelist: Deque[str] = deque([f"p{i}" for i in range(self.preg_num)])
        self.RAT: Dict[Any, str] = {}
        self.next_dynamic_preg_id: int = self.preg_num

        # queues
        self.SHQ: List[Uop] = []   # compute queue (SHQ)
        self.LSQ: List[Uop] = []   # VLD/VST only
        self.ROB: Deque[Uop] = deque()

        # dependency tracking
        self.preg_producer: Dict[str, Tuple[str, str, int, str]] = {}
        self.preg_producer_uop: Dict[str, Uop] = {}
        self.preg_producer_profile: Dict[str, InstructionProfile] = {}

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
        self.last_form = {
            "ALU": [None] * self.issue_ports,
            "SFU": [None] * self.issue_ports,
        }
        self.last_issue_cycle_exu = [-10**9] * self.issue_ports
        self.last_op_exu = [None] * self.issue_ports
        self.last_form_exu = [None] * self.issue_ports
        self.last_profile = {
            "ALU": [None] * self.issue_ports,
            "SFU": [None] * self.issue_ports,
        }
        self.last_profile_exu = [None] * self.issue_ports

        self.cycle: int = 0
        self.last_done_cycle: int = 0
        self.history: List[Dict[str, Any]] = []
        self.debug = bool(uarch.get("debug", False))

        self.preg_pending = set()
        self.ooo_to_shq_delay = int(uarch.get("ooo_to_shq_delay", 1))
        self.ooo_to_lsq_delay = int(uarch.get("ooo_to_lsq_delay", 1))
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
        self.theoretical_limit_legacy_forwarding = bool(
            uarch.get("theoretical_limit_legacy_forwarding", False)
        )

        self.cyc_start_log: List[Dict[str, Any]] = []
        self.cyc_done_log: List[Dict[str, Any]] = []

    def is_vreg(self, name: Any) -> bool:
        return self.value_storage.is_register(name)

    def is_mem(self, name: Any) -> bool:
        return self.value_storage.is_ub(name)

    # -------- logging --------
    def _log(self, event: str, u: Uop) -> None:
        self.history.append({
            "cy": self.cycle,
            "event": event,
            "id": u.inst_id,
            "static_instruction_id": u.static_instruction_id,
            "iteration_path": u.iteration_path,
            "stream_seq": u.stream_seq,
            "src_value_instances": u.src_value_instances,
            "dst_value_instances": u.dst_value_instances,
            "op": u.op,
            "form": u.form,
            "state": u.state,
            "blocked_reason": u.blocked_reason,
            "ready": u.ready_cycle,
            "start": u.start_cycle,
            "done": u.done_cycle,
            "src": u.src,
            "dst": u.dst,
            "preg_src": u.preg_src,
            "preg_dst": u.preg_dst,
            "preg_old": u.preg_old,
            "producer_op_for_store": u.producer_op_for_store,
            "producer_form_for_store": u.producer_form_for_store,
            "producer_start_for_store": u.producer_start_for_store,
        })

    def _log_start_simple(self, u: Uop) -> None:
        profile = u.profile
        op_class = profile.op_class if profile is not None else None
        self.cyc_start_log.append({
            "cy": self.cycle,
            "inst_id": u.inst_id,
            "static_instruction_id": u.static_instruction_id,
            "iteration_path": u.iteration_path,
            "stream_seq": u.stream_seq,
            "src_value_instances": u.src_value_instances,
            "dst_value_instances": u.dst_value_instances,
            "op": u.op,
            "form": u.form,
            "op_class": op_class,
            "fu_type": (
                profile.fu_type
                if profile is not None and op_class == "COMPUTE"
                else None
            ),
            "exu_port": u.exu_port,
            "ready_cycle": u.ready_cycle,
            "dst": u.dst,
            "src": u.src,
            "preg_dst": u.preg_dst,
            "preg_src": u.preg_src,
        })

    def _log_done_simple(self, u: Uop) -> None:
        profile = u.profile
        op_class = profile.op_class if profile is not None else None
        self.cyc_done_log.append({
            "cy": u.done_cycle if u.done_cycle is not None else self.cycle,
            "inst_id": u.inst_id,
            "static_instruction_id": u.static_instruction_id,
            "iteration_path": u.iteration_path,
            "stream_seq": u.stream_seq,
            "src_value_instances": u.src_value_instances,
            "dst_value_instances": u.dst_value_instances,
            "op": u.op,
            "form": u.form,
            "op_class": op_class,
            "fu_type": (
                profile.fu_type
                if profile is not None and op_class == "COMPUTE"
                else None
            ),
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
    def _inst_params(self, op: str, form: Optional[str] = None) -> Dict[str, Any]:
        if hasattr(self.db, "get_inst_form"):
            return self.db.get_inst_form(op, form=form, dtype=self.dtype)
        return self.db.get_inst(op, dtype=form or self.dtype)

    def _profile(self, op: str, form: Optional[str] = None) -> InstructionProfile:
        return self.db.resolve_inst(op, form=form, dtype=self.dtype)

    def _latency(
        self,
        op: str,
        form: Optional[str] = None,
        profile: Optional[InstructionProfile] = None,
    ) -> int:
        return int((profile or self._profile(op, form)).latency)

    def _get_ii(
        self,
        prev_op: Optional[str],
        cur_op: str,
        prev_form: Optional[str] = None,
        cur_form: Optional[str] = None,
        prev_profile: Optional[InstructionProfile] = None,
        cur_profile: Optional[InstructionProfile] = None,
    ) -> int:
        if prev_op is None:
            return 1
        if prev_profile is not None and cur_profile is not None:
            return int(self.db.get_ii_for_profiles(prev_profile, cur_profile))
        return int(
            self.db.get_ii(
                prev_op,
                cur_op,
                dtype=self.dtype,
                prev_form=prev_form,
                cur_form=cur_form,
            )
        )

    def _get_fu_type(
        self,
        op: str,
        form: Optional[str] = None,
        profile: Optional[InstructionProfile] = None,
    ) -> str:
        if profile is not None:
            return profile.fu_type
        try:
            fu = str(self._inst_params(op, form=form).get("EXU", "ALU")).upper()
        except Exception:
            fu = "ALU"
        if fu not in ("ALU", "SFU"):
            fu = "ALU"
        return fu

    def _eligible_exu_ports(
        self,
        op: str,
        form: Optional[str] = None,
        profile: Optional[InstructionProfile] = None,
    ) -> List[int]:
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
            if profile is not None:
                dispatch_exu = profile.dispatch_exu
            else:
                dispatch_exu = str(self._inst_params(op, form=form).get("dispatch_exu", "")).upper()
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
    def _ready_time_for_src(
        self,
        producer_info: Tuple[str, str, int, str],
        consumer_op: str,
        consumer_form: Optional[str] = None,
        producer_profile: Optional[InstructionProfile] = None,
        consumer_profile: Optional[InstructionProfile] = None,
    ) -> int:
        prod_op, prod_form, prod_start, _kind = producer_info
        if producer_profile is not None and consumer_profile is not None:
            fwd = int(self.db.get_forwarding_for_profiles(producer_profile, consumer_profile))
        else:
            fwd = int(self.db.get_forwarding_cycles(
                prod_op,
                consumer_op,
                dtype=self.dtype,
                producer_form=prod_form,
                consumer_form=consumer_form,
            ))
        # Queue-level timing alignment:
        # In SHQ wakeup modeling, consumer wakeup-ready follows
        #   producer_EXQ_ISSUE - 1 + forwarding
        # where prod_start is producer_EXQ_ISSUE/start_cycle.
        if (
            ((consumer_profile is not None and consumer_profile.op_class == "COMPUTE")
             or (consumer_profile is None and is_compute_op(consumer_op, self.db, consumer_form or self.dtype)))
            and bool(getattr(self, "enable_isu_queue_model", False))
            and not self.theoretical_limit_legacy_forwarding
        ):
            return prod_start + max(0, fwd - 1)
        return prod_start + fwd

    def _compute_ready_cycle(self, u: Uop) -> int:
        # dependency-only ready time
        t = max(self.vf_startup_cost, int(getattr(u, "shq_ready_cycle", 0)))
        for ps in u.preg_src:
            if ps is None:
                continue
            info = self.preg_producer.get(ps)
            if info is None:
                if ps in self.preg_pending:
                    return 10 ** 9
                continue
            t = max(
                t,
                self._ready_time_for_src(
                    info,
                    u.op,
                    u.form,
                    self.preg_producer_profile.get(ps),
                    u.profile,
                ),
            )
        return t

    def _load_ready_cycle(self, u: Uop) -> int:
        return max(self.vf_startup_cost, int(getattr(u, "lsq_ready_cycle", 0)))

    def _blocked_by_control_unit(self, u: Uop) -> bool:
        control_unit = getattr(self, "control_unit", None)
        if control_unit is None:
            return False
        return bool(
            control_unit.blocks(
                {
                    "type": "inst",
                    "op": u.op,
                    "form": u.form,
                    "stream_seq": int(getattr(u, "stream_seq", -1)),
                }
            )
        )

    def _log_membar_blocked(self, u: Uop) -> None:
        old_reason = u.blocked_reason
        u.blocked_reason = "membar"
        self._log("blocked", u)
        u.blocked_reason = old_reason

    def _store_ready_cycle(self, u: Uop) -> Tuple[int, Optional[str], Optional[str], Optional[int]]:
        for ps in u.preg_src:
            if ps is None:
                continue
            if ps in self.preg_pending and ps not in self.preg_producer:
                return 10 ** 9, None, None, None

        best_t = -1
        pop = None
        pform = None
        pst = None
        for ps in u.preg_src:
            if ps is None:
                continue
            info = self.preg_producer.get(ps)
            if info is None:
                continue
            prod_op, prod_form, prod_start, kind = info
            producer_profile = self.preg_producer_profile.get(ps)
            if kind not in ("COMPUTE", "LOAD") and not (
                producer_profile is not None
                and producer_profile.op_class in ("COMPUTE", "LOAD")
            ):
                continue
            cand = self._ready_time_for_src(
                info, u.op, u.form, producer_profile, u.profile
            )
            if cand > best_t:
                best_t = cand
                pop = prod_op
                pform = prod_form
                pst = prod_start

        if best_t < 0:
            return 10 ** 9, None, None, None
        best_t = max(best_t, int(getattr(u, "lsq_ready_cycle", 0)))
        return best_t, pop, pform, pst

    def has_pending_lsu_before(self, stream_seq: int, op_class: str) -> bool:
        target = str(op_class).upper()
        for u in self.ROB:
            if int(getattr(u, "stream_seq", -1)) >= int(stream_seq):
                continue
            if u.state == "done":
                continue
            cls = u.profile.op_class if u.profile is not None else None
            if cls == target:
                return True
        return False

    # -------- retire helper --------
    def _free_old_pregs(self, u: Uop) -> None:
        raise NotImplementedError(
            "OoOCore._free_old_pregs() must be implemented by a concrete OOO model"
        )

    def _pick_exu_port(
        self,
        fu_type: str,
        cur_op: str,
        c: int,
        exu_used_this_cycle: List[bool],
        cur_form: Optional[str] = None,
        cur_profile: Optional[InstructionProfile] = None,
    ) -> Optional[int]:
        legal_ports = set(self._eligible_exu_ports(cur_op, cur_form, cur_profile))
        if not self.enable_exq_greedy_balance:
            for port in range(self.issue_ports):
                if port not in legal_ports:
                    continue
                if exu_used_this_cycle[port]:
                    continue
                if self.enable_cross_fu_ii:
                    prev_op = self.last_op_exu[port]
                    prev_form = self.last_form_exu[port]
                    prev_issue = self.last_issue_cycle_exu[port]
                else:
                    prev_op = self.last_op[fu_type][port]
                    prev_form = self.last_form[fu_type][port]
                    prev_issue = self.last_issue_cycle[fu_type][port]
                ii = self._get_ii(
                    prev_op,
                    cur_op,
                    prev_form=prev_form,
                    cur_form=cur_form,
                    prev_profile=(self.last_profile_exu[port] if self.enable_cross_fu_ii else self.last_profile[fu_type][port]),
                    cur_profile=cur_profile,
                )
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
                prev_form = self.last_form_exu[port]
                prev_issue = self.last_issue_cycle_exu[port]
            else:
                prev_op = self.last_op[fu_type][port]
                prev_form = self.last_form[fu_type][port]
                prev_issue = self.last_issue_cycle[fu_type][port]
            ii = self._get_ii(
                prev_op,
                cur_op,
                prev_form=prev_form,
                cur_form=cur_form,
                prev_profile=(self.last_profile_exu[port] if self.enable_cross_fu_ii else self.last_profile[fu_type][port]),
                cur_profile=cur_profile,
            )
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
