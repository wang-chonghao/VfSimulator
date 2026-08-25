#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Set, Tuple
from collections import deque

from core import isu
from core.isa_traits import is_load_op, is_store_op, uses_lsq, uses_shared_shq_credit
from core.ooo import OoOCore, Uop


@dataclass
class SrcReleaseEvent:
    inst_id: int
    preg: str
    gen: int


class PregLifecycleController:
    def __init__(self, core: "OoOCoreMainline") -> None:
        self.core = core

    def is_current_mapping(self, preg: str) -> bool:
        return preg in self.core.RAT.values()

    def can_free_preg(self, preg: str) -> bool:
        if self.core.theoretical_limit_mode:
            return False
        if not preg or preg in self.core.freelist:
            return False
        if self.is_current_mapping(preg):
            return False
        if self.core.preg_consumer_count.get(preg, 0) > 0:
            return False
        if preg in self.core.preg_pending:
            return False
        eligible = self.core.preg_release_eligible_cycle.get(preg)
        if eligible is not None and self.core.cycle < eligible:
            return False
        return True

    def try_free_preg(self, preg: str) -> bool:
        if not self.can_free_preg(preg):
            return False
        self.core.preg_producer.pop(preg, None)
        self.core.preg_producer_uop.pop(preg, None)
        self.core.preg_producer_profile.pop(preg, None)
        self.core.preg_pending.discard(preg)
        self.core.preg_consumer_count.pop(preg, None)
        self.core.preg_release_eligible_cycle.pop(preg, None)
        self.core.preg_bypass_producer_done.discard(preg)
        self.core.freelist.append(preg)
        if self.core.enable_credit_visibility_delay:
            delay = int(self.core.idu_visible_preg_delay)
            if delay <= 0:
                self.core.visible_preg_free = int(self.core.visible_preg_free) + 1
                self.core.idu_mailbox_preg_release_delta = (
                    int(self.core.idu_mailbox_preg_release_delta) + 1
                )
            else:
                t = int(self.core.cycle) + delay
                self.core.visible_preg_free_events[t] = int(
                    self.core.visible_preg_free_events.get(t, 0)
                ) + 1
        return True

    def on_uop_done(self, u: Uop) -> None:
        if self.core.assert_start_release_integrity:
            exp = int(self.core.src_release_expected.get(u.inst_id, 0))
            seen = int(self.core.src_release_seen.get(u.inst_id, 0))
            if seen != exp:
                raise AssertionError(
                    f"start-release mismatch at done: inst_id={u.inst_id}, seen={seen}, expected={exp}"
                )
        self.core.src_release_expected.pop(u.inst_id, None)
        self.core.src_release_seen.pop(u.inst_id, None)

    def schedule_src_release_from_start(self, u: Uop) -> None:
        key = int(u.inst_id)
        if key in self.core.src_release_scheduled_inst_ids:
            return
        if u.start_cycle is None:
            return
        op_offset = int(
            self.core.consumer_release_start_offset_by_op.get(
                str(u.op), self.core.consumer_release_start_offset
            )
        )
        release_cycle = int(u.start_cycle) + op_offset
        src_gens: List[Optional[int]] = list(
            getattr(u, "preg_src_gen", [None] * len(u.preg_src))
        )
        srcs: List[Tuple[str, int]] = []
        for idx, preg in enumerate(u.preg_src):
            if preg is None:
                continue
            gen = src_gens[idx] if idx < len(src_gens) else None
            if gen is None:
                gen = int(self.core.preg_generation.get(preg, 0))
            srcs.append((preg, int(gen)))
        if srcs:
            evs = self.core.src_release_events.setdefault(release_cycle, [])
            for preg, gen in srcs:
                evs.append(SrcReleaseEvent(inst_id=key, preg=preg, gen=gen))
        self.core.src_release_expected[key] = len(srcs)
        self.core.src_release_seen[key] = 0
        self.core.src_release_scheduled_inst_ids.add(key)

    def is_inplace_consumer(self, u: Uop) -> bool:
        if len(u.src) != 1 or len(u.dst) != 1:
            return False
        s = u.src[0]
        d = u.dst[0]
        return (
            isinstance(s, str)
            and isinstance(d, str)
            and s == d
            and self.core.is_vreg(s)
            and self.core.is_vreg(d)
        )

    def run_src_release_events(self, cycle: int) -> None:
        evs = self.core.src_release_events.pop(cycle, None)
        if not evs:
            return
        for ev in evs:
            preg, gen = ev.preg, ev.gen
            self.core.src_release_seen[ev.inst_id] = int(
                self.core.src_release_seen.get(ev.inst_id, 0)
            ) + 1
            cur_gen = int(self.core.preg_generation.get(preg, 0))
            if cur_gen != int(gen):
                if self.core.assert_start_release_integrity:
                    raise AssertionError(
                        f"stale start-release event: inst_id={ev.inst_id}, preg={preg}, event_gen={gen}, cur_gen={cur_gen}, cycle={cycle}"
                    )
                continue
            if preg in self.core.preg_consumer_count:
                self.core.preg_consumer_count[preg] = max(
                    0, self.core.preg_consumer_count[preg] - 1
                )
                if self.core.preg_consumer_count[preg] == 0:
                    prev = self.core.preg_release_eligible_cycle.get(preg, -1)
                    self.core.preg_release_eligible_cycle[preg] = max(prev, cycle)
                    if (
                        self.core.allow_inplace_consumer_bypass_producer_done
                        and self.is_inplace_consumer(self.find_uop_by_inst_id(ev.inst_id))
                    ):
                        self.core.preg_bypass_producer_done.add(preg)
            self.try_free_preg(preg)

    def find_uop_by_inst_id(self, inst_id: int) -> Uop:
        for q in (self.core.ROB, self.core.SHQ, self.core.LSQ):
            for u in q:
                if int(u.inst_id) == int(inst_id):
                    return u
        raise KeyError(f"uop not found for inst_id={inst_id}")

    def try_free_dests(self, u: Uop) -> None:
        for preg in u.preg_dst:
            self.try_free_preg(preg)

    def try_free_eligible_pregs(self) -> None:
        if not self.core.preg_release_eligible_cycle:
            return
        for preg in list(self.core.preg_release_eligible_cycle.keys()):
            self.try_free_preg(preg)

    def run_overwrite_release_events(self, cycle: int) -> None:
        olds = self.core.overwrite_release_events.pop(int(cycle), None)
        if not olds:
            return
        for old_p in olds:
            self.core.preg_bypass_producer_done.add(old_p)
            self.try_free_preg(old_p)

    def free_old_pregs(self, u: Uop) -> None:
        for old_p in u.preg_old:
            if old_p is None:
                continue
            self.try_free_preg(old_p)


class SHQResourceController:
    def __init__(self, core: "OoOCoreMainline") -> None:
        self.core = core

    def run_shq_release_events(self, cycle: int) -> None:
        if not self.core.enable_shq_credit_model:
            return
        released = int(self.core.shq_release_events.pop(cycle, 0))
        if released <= 0:
            return
        self.core.shq_used = max(0, self.core.shq_used - released)
        if not self.core.enable_credit_visibility_delay:
            return
        delay = int(self.core.idu_visible_shq_delay)
        if delay <= 0:
            self.core.visible_shq_used = max(
                0, int(self.core.visible_shq_used) - int(released)
            )
            self.core.idu_mailbox_shq_release_delta = (
                int(self.core.idu_mailbox_shq_release_delta) + int(released)
            )
            return
        visible_cycle = int(cycle) + delay
        self.core.visible_shq_release_events[visible_cycle] = int(
            self.core.visible_shq_release_events.get(visible_cycle, 0)
        ) + int(released)

    def schedule_shq_release(self, cycle: int, count: int = 1) -> None:
        if not self.core.enable_shq_credit_model or count <= 0:
            return
        release_cycle = int(cycle) + int(self.core.shq_release_delay)
        self.core.shq_release_events[release_cycle] = int(
            self.core.shq_release_events.get(release_cycle, 0)
        ) + int(count)

    def get_free_shq(self) -> int:
        if self.core.theoretical_limit_mode or not self.core.enable_shq_credit_model:
            return 10**18
        if self.core.enable_credit_visibility_delay:
            return max(0, int(self.core.shq_depth) - int(self.core.visible_shq_used))
        return max(0, int(self.core.shq_depth) - int(self.core.shq_used))

    def get_free_preg(self) -> int:
        if self.core.theoretical_limit_mode:
            return 10**18
        if self.core.enable_credit_visibility_delay:
            return max(0, int(self.core.visible_preg_free))
        return OoOCore.get_free_preg(self.core)

    def update_idu_visibility(self, cycle: int) -> Dict[str, int]:
        if not self.core.enable_credit_visibility_delay:
            return {"preg_free": 0, "shq_release": 0}
        preg_delta = int(self.core.idu_mailbox_preg_release_delta)
        shq_delta = int(self.core.idu_mailbox_shq_release_delta)
        self.core.idu_mailbox_preg_release_delta = 0
        self.core.idu_mailbox_shq_release_delta = 0

        preg_release = int(self.core.visible_preg_free_events.pop(int(cycle), 0))
        if preg_release > 0:
            self.core.visible_preg_free = int(self.core.visible_preg_free) + preg_release
            preg_delta += preg_release

        shq_release = int(self.core.visible_shq_release_events.pop(int(cycle), 0))
        if shq_release > 0:
            self.core.visible_shq_used = max(
                0, int(self.core.visible_shq_used) - shq_release
            )
            shq_delta += shq_release

        return {"preg_free": preg_delta, "shq_release": shq_delta}


class RenameController:
    def __init__(self, core: "OoOCoreMainline") -> None:
        self.core = core

    def accept(self, inst: Dict[str, Any]) -> None:
        op = str(inst.get("op"))
        form = str(inst.get("form", "") or self.core.dtype)
        profile = self.core._profile(op, form)
        inst_id = int(inst.get("inst_id", inst.get("id", -1)))
        iter_stack = list(inst.get("iter_stack", []))
        top_block_id = int(inst.get("top_block_id", 0))
        is_last_in_top_block = bool(inst.get("is_last_in_top_block", False))
        stream_seq = int(inst.get("stream_seq", -1))
        static_instruction_id = inst.get("static_instruction_id")
        if static_instruction_id is not None:
            static_instruction_id = str(static_instruction_id)
        raw_iteration_path = inst.get("iteration_path", [])
        iteration_path = (
            [dict(item) for item in raw_iteration_path if isinstance(item, dict)]
            if isinstance(raw_iteration_path, list)
            else []
        )
        raw_src_instances = inst.get("src_value_instances", [])
        src_value_instances = (
            [dict(item) for item in raw_src_instances if isinstance(item, dict)]
            if isinstance(raw_src_instances, list)
            else []
        )
        raw_dst_instances = inst.get("dst_value_instances", [])
        dst_value_instances = (
            [dict(item) for item in raw_dst_instances if isinstance(item, dict)]
            if isinstance(raw_dst_instances, list)
            else []
        )
        raw_src_release = inst.get("src_value_instance_release", [])
        src_value_instance_release = (
            [bool(item) for item in raw_src_release]
            if isinstance(raw_src_release, list)
            else []
        )
        raw_dst_keep = inst.get("dst_value_instance_keep", [])
        dst_value_instance_keep = (
            [bool(item) for item in raw_dst_keep]
            if isinstance(raw_dst_keep, list)
            else []
        )

        def rat_key(instance: Dict[str, Any] | None, fallback: Any) -> Any:
            if not instance:
                return fallback
            path = instance.get("iteration_path", [])
            frozen_path = tuple(
                (str(item.get("loop_id", "")), int(item.get("iteration", 0)))
                for item in path
                if isinstance(item, dict)
            )
            return (str(instance.get("definition_id", fallback)), frozen_path)

        srcs = inst.get("src", [])
        dsts = inst.get("dst", [])
        if isinstance(srcs, str):
            srcs = [srcs]
        if isinstance(dsts, str):
            dsts = [dsts]
        if not isinstance(srcs, list):
            srcs = []
        if not isinstance(dsts, list):
            dsts = []

        preg_src: List[str | None] = []
        preg_src_gen: List[Optional[int]] = []
        released_rat_pregs: List[str] = []
        for index, s in enumerate(srcs):
            instance = (
                src_value_instances[index]
                if index < len(src_value_instances)
                else None
            )
            source_key = rat_key(instance, s)
            preg = None
            if self.core.is_vreg(s):
                preg = self.core.RAT.get(source_key)
                if preg is None and instance is not None:
                    preg = self.core.RAT.get(s)
                if (
                    index < len(src_value_instance_release)
                    and src_value_instance_release[index]
                ):
                    released = self.core.RAT.pop(source_key, None)
                    if released is not None:
                        released_rat_pregs.append(released)
            preg_src.append(preg)
            if preg is None:
                preg_src_gen.append(None)
            else:
                preg_src_gen.append(int(self.core.preg_generation.get(preg, 0)))
        for preg in preg_src:
            if preg is not None:
                self.core.preg_consumer_count[preg] = (
                    self.core.preg_consumer_count.get(preg, 0) + 1
                )
                self.core.preg_release_eligible_cycle.pop(preg, None)

        preg_dst: List[str] = []
        preg_old: List[str | None] = []
        preg_alloc_count = 0
        for index, d in enumerate(dsts):
            if not self.core.is_vreg(d):
                continue
            if self.core.theoretical_limit_mode:
                new_p = f"p{self.core.next_dynamic_preg_id}"
                self.core.next_dynamic_preg_id += 1
            else:
                new_p = self.core.freelist.popleft()
            instance = (
                dst_value_instances[index]
                if index < len(dst_value_instances)
                else None
            )
            destination_key = rat_key(instance, d)
            old_p = self.core.RAT.get(destination_key)
            keep_mapping = (
                dst_value_instance_keep[index]
                if index < len(dst_value_instance_keep)
                else True
            )
            if keep_mapping:
                self.core.RAT[destination_key] = new_p
            preg_dst.append(new_p)
            preg_old.append(old_p)
            preg_alloc_count += 1
            self.core.preg_generation[new_p] = (
                int(self.core.preg_generation.get(new_p, 0)) + 1
            )
            self.core.preg_consumer_count[new_p] = 0
            self.core.preg_release_eligible_cycle.pop(new_p, None)
        if self.core.enable_credit_visibility_delay and preg_alloc_count > 0:
            self.core.visible_preg_free = max(
                0, int(self.core.visible_preg_free) - int(preg_alloc_count)
            )

        u = Uop(
            inst_id=inst_id,
            op=op,
            form=form,
            src=list(srcs),
            dst=list(dsts),
            preg_src=preg_src,
            preg_dst=preg_dst,
            preg_old=preg_old,
            profile=profile,
            top_block_id=top_block_id,
            iter_stack=list(iter_stack),
            is_last_in_top_block=is_last_in_top_block,
            stream_seq=stream_seq,
            static_instruction_id=static_instruction_id,
            iteration_path=iteration_path,
            src_value_instances=src_value_instances,
            dst_value_instances=dst_value_instances,
            memory_ranges=[
                dict(item)
                for item in inst.get("memory_ranges", [])
                if isinstance(item, dict)
            ],
        )
        self.core.bind_align_state(u, inst.get("attributes"))
        setattr(u, "preg_src_gen", preg_src_gen)

        for pd in preg_dst:
            self.core.preg_pending.add(pd)

        if profile.op_class in ("LOAD", "STORE"):
            u.lsq_ready_cycle = int(self.core.cycle) + max(0, int(self.core.ooo_to_lsq_delay))
            self.core.LSQ.append(u)
            self.core._record_ub_lsu(u)
        else:
            u.shq_ready_cycle = int(self.core.cycle) + max(0, int(self.core.ooo_to_shq_delay))
            self.core.SHQ.append(u)
        if (
            self.core.enable_shq_credit_model
            and profile.op_class in ("COMPUTE", "STORE")
        ):
            self.core.shq_used += 1
            if self.core.enable_credit_visibility_delay:
                self.core.visible_shq_used += 1
            setattr(u, "shq_tracked", True)
        else:
            setattr(u, "shq_tracked", False)

        if profile.op_class == "STORE":
            for d in dsts:
                if self.core.is_mem(d):
                    iter_key = self.core._top_iter_key_from_stack(top_block_id, iter_stack)
                    self.core.iter_outstanding_stores[iter_key] = (
                        self.core.iter_outstanding_stores.get(iter_key, 0) + 1
                    )

        self.core.ROB.append(u)

        for released_preg in released_rat_pregs:
            self.core.preg_lifecycle.try_free_preg(released_preg)

        for old_p in preg_old:
            if old_p is not None:
                if self.core.allow_overwrite_predst_bypass_producer_done:
                    t = int(self.core.cycle) + max(
                        0, int(self.core.overwrite_predst_release_delay)
                    )
                    self.core.overwrite_release_events.setdefault(t, []).append(old_p)
                else:
                    self.core.preg_lifecycle.try_free_preg(old_p)

        if self.core.debug:
            print("[ACCEPT]", op, inst_id, srcs, dsts, preg_src, preg_dst, preg_old)


class OoOCoreMainline(OoOCore):
    """
    Mainline queue-level OoO core with start-based source release.

    Once a younger write overwrites architectural vreg Vn, the older preg is
    sealed and will never gain new consumers. Consumers already bound to that
    old preg are counted when accepted, and the preg is released after all
    bound consumers have reached their source-release point.

    Boundary in the current refactor:
    - This class owns OOO-side behavior: rename, preg lifecycle, SHQ/LSQ/ROB,
      SHQ credit bookkeeping, ready-cycle computation, VLD/VST path, and
      source-release bookkeeping.
    - Compute issue behavior after SHQ is delegated to `core.isu`.
    """

    def __init__(self, uarch: Dict[str, Any], pdb, dtype: str = "fp32", values: Dict[str, Any] | None = None):
        super().__init__(uarch, pdb, dtype=dtype, values=values)
        self.isu = isu.ISUController(self)
        self.preg_lifecycle = PregLifecycleController(self)
        self.shq_resources = SHQResourceController(self)
        self.rename_unit = RenameController(self)
        self.preg_consumer_count: Dict[str, int] = {}
        # Mainline source release rule:
        #   eligible = consumer.start_cycle + consumer_release_start_offset
        self.consumer_release_start_offset: int = int(
            uarch.get("consumer_release_start_offset", 0)
        )
        raw_offset_by_op = uarch.get("consumer_release_start_offset_by_op", {}) or {}
        self.consumer_release_start_offset_by_op: Dict[str, int] = {
            str(k): int(v) for k, v in dict(raw_offset_by_op).items()
        }
        self.preg_release_eligible_cycle: Dict[str, int] = {}
        # Physical-register versioning to avoid stale delayed-release events
        # touching a reused preg instance.
        self.preg_generation: Dict[str, int] = {}
        self.require_producer_done_for_preg_free: bool = bool(
            uarch.get("require_producer_done_for_preg_free", True)
        )
        self.producer_start_offset_for_preg_free: int = int(
            uarch.get("producer_start_offset_for_preg_free", -1)
        )
        self.allow_inplace_consumer_bypass_producer_done: bool = bool(
            uarch.get("allow_inplace_consumer_bypass_producer_done", False)
        )
        self.allow_overwrite_predst_bypass_producer_done: bool = bool(
            uarch.get("allow_overwrite_predst_bypass_producer_done", False)
        )
        self.overwrite_predst_release_delay: int = int(
            uarch.get("overwrite_predst_release_delay", 0)
        )
        self.src_release_events: Dict[int, List[SrcReleaseEvent]] = {}
        self.preg_bypass_producer_done: Set[str] = set()
        self.overwrite_release_events: Dict[int, List[str]] = {}
        self.src_release_scheduled_inst_ids: Set[int] = set()
        # Start-release accounting for assertion checks in pure start-based mode.
        self.src_release_expected: Dict[int, int] = {}
        self.src_release_seen: Dict[int, int] = {}
        self.assert_start_release_integrity: bool = bool(
            uarch.get("assert_start_release_integrity", True)
        )
        # Optional ISU queue model (disabled by default).
        self.enable_isu_queue_model: bool = bool(uarch.get("enable_isu_queue_model", False))
        self.shq_depth: int = int(uarch.get("shq_depth", self.shq_depth))
        self.exq_depth: int = int(uarch.get("exq_depth", 26))
        self.exq_recv_delay: int = int(uarch.get("exq_recv_delay", 1))
        self.shq_to_exq_port_per_cycle: int = int(uarch.get("shq_to_exq_port_per_cycle", 1))
        self.exq_capacity_counts_inflight: bool = bool(uarch.get("exq_capacity_counts_inflight", False))
        self.compute_inflight_cap: int = int(uarch.get("compute_inflight_cap", 0))
        self.exq_issue_inflight_cap_per_port: int = int(
            uarch.get("exq_issue_inflight_cap_per_port", 0)
        )
        self.predict_exq_issue_with_cross_fu: bool = bool(
            uarch.get("predict_exq_issue_with_cross_fu", False)
        )
        self.shq_exq_static_rr: bool = bool(
            uarch.get("shq_exq_static_rr", False)
        )
        self.shq_exq_rr_ptr: int = 0
        self.shq_exq_dispatch_policy: str = str(
            uarch.get("shq_exq_dispatch_policy", "fu_round_robin_fifo")
        ).lower()
        self.exu0_reserve_lookahead: int = int(
            uarch.get("exu0_reserve_lookahead", 0)
        )
        self.exu0_reserve_min_count: int = int(
            uarch.get("exu0_reserve_min_count", 1)
        )
        self.shq_exq_rr_ptr_by_fu: Dict[str, int] = {
            "ALU": 0,
            "SFU": 0,
        }
        self.admit_blocked_to_exq: bool = bool(
            uarch.get("admit_blocked_to_exq", False)
        )
        # finite SHQ credit model
        self.enable_shq_credit_model: bool = bool(
            uarch.get("enable_shq_credit_model", False)
        )
        self.shq_release_delay: int = int(
            uarch.get("shq_release_delay", 1)
        )
        self.shq_used: int = 0
        self.shq_release_events: Dict[int, int] = {}
        # delayed credit visibility back to IDU
        self.enable_credit_visibility_delay: bool = bool(
            uarch.get("enable_credit_visibility_delay", False)
        )
        self.idu_visible_preg_delay: int = int(
            uarch.get("idu_visible_preg_delay", 0)
        )
        self.idu_visible_shq_delay: int = int(
            uarch.get("idu_visible_shq_delay", 0)
        )
        self.visible_preg_free: int = len(self.freelist)
        self.visible_shq_used: int = 0
        self.visible_preg_free_events: Dict[int, int] = {}
        self.visible_shq_release_events: Dict[int, int] = {}
        # For explicit IDU-side credit-bank experiments:
        # when visibility delay is 0, releases become visible "by next IDU cycle"
        # and therefore need an explicit mailbox delta instead of relying on
        # absolute visible counters.
        self.idu_mailbox_preg_release_delta: int = 0
        self.idu_mailbox_shq_release_delta: int = 0
        # Per-EXQ per-FU queues:
        # - FIFO within each FU queue
        # - ALU/SFU can be interleaved at issue stage
        self.exq_wait: List[Dict[str, Deque[Uop]]] = [
            {"ALU": deque(), "SFU": deque()} for _ in range(self.issue_ports)
        ]
        # Experimental iter-boundary sealing state.
        # Keep the data structures for compatibility, but the current
        # reproduction path leaves this mechanism disabled.
        self.iter_outstanding_stores: Dict[Tuple[int, int], int] = {}
        self.iter_last_inst_done: Dict[Tuple[int, int], bool] = {}
        self.iter_release_cycle: Dict[Tuple[int, int], int] = {}
        self.iter_tail_vst_candidates: Dict[Tuple[int, int], List[Tuple[str, str]]] = {}
        self.iter_boundary_sealed_vregs: Set[str] = set()

    def _top_iter_key_from_stack(self, top_block_id: int, iter_stack: List[Any]) -> Tuple[int, int]:
        top_iter = int(iter_stack[0]) if iter_stack else -1
        return (int(top_block_id), top_iter)

    def _seal_current_mapping(self, vreg: str, preg: str) -> bool:
        return True

    def _run_iter_tail_vst_seal(self, iter_key: Tuple[int, int], cycle: int) -> None:
        self.iter_tail_vst_candidates.pop(iter_key, None)

    def _is_current_mapping(self, preg: str) -> bool:
        return self.preg_lifecycle.is_current_mapping(preg)

    def _can_free_preg(self, preg: str) -> bool:
        return self.preg_lifecycle.can_free_preg(preg)

    def _try_free_preg(self, preg: str) -> bool:
        return self.preg_lifecycle.try_free_preg(preg)

    def _on_uop_done(self, u: Uop) -> None:
        self.preg_lifecycle.on_uop_done(u)

    def _schedule_src_release_from_start(self, u: Uop) -> None:
        self.preg_lifecycle.schedule_src_release_from_start(u)

    def _is_inplace_consumer(self, u: Uop) -> bool:
        return self.preg_lifecycle.is_inplace_consumer(u)

    def _run_src_release_events(self, cycle: int) -> None:
        self.preg_lifecycle.run_src_release_events(cycle)

    def _find_uop_by_inst_id(self, inst_id: int) -> Uop:
        return self.preg_lifecycle.find_uop_by_inst_id(inst_id)

    def _try_free_dests(self, u: Uop) -> None:
        self.preg_lifecycle.try_free_dests(u)

    def _overwrite_store_barrier_cycle(self, u: Uop) -> int:
        return 0

    def _predict_store_start_cycle(self, target: Uop) -> int:
        """
        Predict the actual VST start cycle under the current single-/multi-port
        store issue rule:
        - stores issue in ROB/LSQ order
        - at most `store_ports` stores can start per cycle

        For already-started stores, use the real start_cycle.
        For pending stores, build a conservative schedule from current ROB state.
        """
        if target.start_cycle is not None:
            return int(target.start_cycle)

        not_done_stores: List[Uop] = []
        for u in self.ROB:
            if not (u.profile and u.profile.op_class == "STORE") or u.state == "done":
                continue
            not_done_stores.append(u)

        port_next_free = [self.vf_startup_cost] * max(1, int(self.store_ports))
        for st_u in not_done_stores:
            if st_u.start_cycle is not None:
                started = int(st_u.start_cycle)
                port = min(range(len(port_next_free)), key=lambda i: port_next_free[i])
                port_next_free[port] = max(int(port_next_free[port]), started + 1)
                if st_u is target:
                    return started
                continue

            ready, _, _, _ = self._store_ready_cycle(st_u)
            port = min(range(len(port_next_free)), key=lambda i: port_next_free[i])
            pred = max(int(ready), int(port_next_free[port]))
            port_next_free[port] = pred + 1
            if st_u is target:
                return pred

        return 10 ** 9

    def _try_free_eligible_pregs(self) -> None:
        self.preg_lifecycle.try_free_eligible_pregs()

    def _run_overwrite_release_events(self, cycle: int) -> None:
        self.preg_lifecycle.run_overwrite_release_events(cycle)

    @staticmethod
    def _lsu_age_key(u: Uop) -> Tuple[int, int]:
        stream_seq = int(u.stream_seq) if int(u.stream_seq) >= 0 else int(u.inst_id)
        return (stream_seq, int(u.inst_id))

    def _lsu_issue_key(self, u: Uop) -> Tuple[int, int, int]:
        age = self._lsu_age_key(u)
        preg_pressure = (
            len(self.freelist) < self.lsu_store_priority_preg_threshold
        )
        preferred_class = "STORE" if preg_pressure else "LOAD"
        class_priority = 0 if u.profile.op_class == preferred_class else 1
        return (class_priority, *age)

    def _issue_ready_lsu(
        self,
        cycle: int,
        issued_loads: int,
        issued_stores: int,
        issued_total: int,
        membar_blocked_logged_ids: Optional[Set[int]] = None,
        ub_dependency_blocked_logged_ids: Optional[Set[int]] = None,
    ) -> Tuple[int, int, int]:
        """Issue pressure-ranked ready LSU operations within shared UB limits."""
        if cycle < self.vf_startup_cost or issued_total >= self.ub_slots:
            return issued_loads, issued_stores, issued_total

        candidates = sorted(
            (
                u
                for u in self.LSQ
                if u.state == "ready"
                and u.profile
                and u.profile.op_class in ("LOAD", "STORE")
            ),
            key=self._lsu_issue_key,
        )
        issued: List[Uop] = []
        for u in candidates:
            if issued_total >= self.ub_slots:
                break

            op_class = u.profile.op_class
            if op_class == "LOAD" and issued_loads >= self.load_ports:
                continue
            if op_class == "STORE" and issued_stores >= self.store_ports:
                continue
            if self._blocked_by_control_unit(u):
                if (
                    membar_blocked_logged_ids is None
                    or u.inst_id not in membar_blocked_logged_ids
                ):
                    self._log_membar_blocked(u)
                    if membar_blocked_logged_ids is not None:
                        membar_blocked_logged_ids.add(u.inst_id)
                continue
            if self._blocked_by_ub_dependency(
                u,
                ub_dependency_blocked_logged_ids,
            ):
                continue
            if (
                op_class == "STORE"
                and not u.store_dependencies_resolved
                and u.producer_op_for_store is None
            ):
                continue

            u.start_cycle = cycle
            u.blocked_reason = None
            u.done_cycle = cycle + self._latency(u.op, u.form, u.profile)
            u.state = "running"
            if u.align_producer_record is not None:
                u.align_producer_record.start_cycle = cycle
            self._schedule_src_release_from_start(u)

            if op_class == "LOAD":
                issued_loads += 1
                for pd in u.preg_dst:
                    self.preg_producer[pd] = (u.op, u.form, u.start_cycle, "LOAD")
                    self.preg_producer_profile[pd] = u.profile
                    self.preg_producer_uop[pd] = u
                    self.preg_pending.discard(pd)
            else:
                issued_stores += 1
                if self.enable_shq_credit_model and bool(getattr(u, "shq_tracked", False)):
                    self._schedule_shq_release(cycle, 1)
                    setattr(u, "shq_tracked", False)

            issued_total += 1
            self._log("start", u)
            self._log_start_simple(u)
            issued.append(u)

        self.isu.remove_issued("LSQ", issued)
        return issued_loads, issued_stores, issued_total

    def accept(self, inst: Dict[str, Any]) -> None:
        self.rename_unit.accept(inst)

    def _free_old_pregs(self, u: Uop) -> None:
        self.preg_lifecycle.free_old_pregs(u)

    def _run_shq_release_events(self, cycle: int) -> None:
        self.shq_resources.run_shq_release_events(cycle)

    def _schedule_shq_release(self, cycle: int, count: int = 1) -> None:
        self.shq_resources.schedule_shq_release(cycle, count)

    def get_free_shq(self) -> int:
        return self.shq_resources.get_free_shq()

    def get_free_preg(self) -> int:
        return self.shq_resources.get_free_preg()

    def update_idu_visibility(self, cycle: int) -> Dict[str, int]:
        return self.shq_resources.update_idu_visibility(cycle)

    def step(self) -> None:
        c = self.cycle
        self._run_shq_release_events(c)
        self._run_src_release_events(c)
        self._run_overwrite_release_events(c)

        for u in self.ROB:
            if u.state == "running" and u.done_cycle is not None and c >= u.done_cycle:
                u.state = "done"
                if u.exu_port is not None and 0 <= u.exu_port < self.issue_ports:
                    self.exq_inflight[u.exu_port] = max(0, self.exq_inflight[u.exu_port] - 1)
                    u.exu_port = None
                if u.profile and u.profile.op_class == "STORE":
                    if u.align_producer_record is not None:
                        u.align_producer_record.done_cycle = u.done_cycle
                    iter_key = self._top_iter_key_from_stack(u.top_block_id, list(u.iter_stack))
                    iter_remain = self.iter_outstanding_stores.get(iter_key, 0) - 1
                    self.iter_outstanding_stores[iter_key] = max(0, iter_remain)
                if u.is_last_in_top_block:
                    iter_key = self._top_iter_key_from_stack(u.top_block_id, list(u.iter_stack))
                    self.iter_last_inst_done[iter_key] = True
                iter_key = self._top_iter_key_from_stack(u.top_block_id, list(u.iter_stack))
                if self.iter_last_inst_done.get(iter_key, False) and self.iter_outstanding_stores.get(iter_key, 0) == 0:
                    prev = self.iter_release_cycle.get(iter_key, -1)
                    self.iter_release_cycle[iter_key] = max(prev, u.done_cycle)
                    self._run_iter_tail_vst_seal(iter_key, u.done_cycle)
                self._log("done", u)
                self._log_done_simple(u)
                self.last_done_cycle = max(self.last_done_cycle, u.done_cycle)
                self._on_uop_done(u)
                self._try_free_dests(u)

        while self.ROB and self.ROB[0].state == "done":
            u = self.ROB.popleft()
            self._free_old_pregs(u)
            self._log("retire", u)

        # Retry delayed recycle points every cycle, otherwise a preg that
        # became free exactly at (done + k) may never be revisited.
        self._try_free_eligible_pregs()

        for u in self.LSQ:
            if u.state in ("running", "done"):
                continue
            if u.profile and u.profile.op_class == "LOAD":
                u.ready_cycle = self._load_ready_cycle(u)
            else:
                (
                    u.ready_cycle,
                    u.producer_op_for_store,
                    u.producer_form_for_store,
                    u.producer_start_for_store,
                ) = self._store_ready_cycle(u)
            u.state = "ready" if c >= u.ready_cycle else "blocked"

        for u in self.SHQ:
            if u.state in ("running", "done"):
                continue
            u.ready_cycle = self._compute_ready_cycle(u)
            u.state = "ready" if c >= u.ready_cycle else "blocked"

        ex = 0
        issued_loads = 0
        issued_stores = 0
        issued_lsu_total = 0
        membar_blocked_logged_ids: Set[int] = set()
        ub_dependency_blocked_logged_ids: Set[int] = set()
        issued_loads, issued_stores, issued_lsu_total = self._issue_ready_lsu(
            c,
            issued_loads,
            issued_stores,
            issued_lsu_total,
            membar_blocked_logged_ids,
            ub_dependency_blocked_logged_ids,
        )

        for u in self.SHQ:
            if u.state in ("running", "done"):
                continue
            u.ready_cycle = self._compute_ready_cycle(u)
            u.state = "ready" if c >= u.ready_cycle else "blocked"

        for u in self.LSQ:
            if u.state in ("running", "done"):
                continue
            if not (u.profile and u.profile.op_class == "STORE"):
                continue
            (
                u.ready_cycle,
                u.producer_op_for_store,
                u.producer_form_for_store,
                u.producer_start_for_store,
            ) = self._store_ready_cycle(u)
            u.state = "ready" if c >= u.ready_cycle else "blocked"

        issued_srcs_this_cycle = set()
        exu_used_this_cycle = [False] * self.issue_ports

        if not self.enable_isu_queue_model:
            ex += self.isu.issue_direct_from_shq(
                c,
                issued_srcs_this_cycle,
                exu_used_this_cycle,
            )
        else:
            ex += self.isu.enqueue_shq_to_exq(
                c,
                issued_srcs_this_cycle,
            )
            self.isu.issue_exq_to_exu(
                c,
                exu_used_this_cycle,
            )

        for u in self.LSQ:
            if u.state in ("running", "done"):
                continue
            if not (u.profile and u.profile.op_class == "STORE"):
                continue
            (
                u.ready_cycle,
                u.producer_op_for_store,
                u.producer_form_for_store,
                u.producer_start_for_store,
            ) = self._store_ready_cycle(u)
            u.state = "ready" if c >= u.ready_cycle else "blocked"

        self._issue_ready_lsu(
            c,
            issued_loads,
            issued_stores,
            issued_lsu_total,
            membar_blocked_logged_ids,
            ub_dependency_blocked_logged_ids,
        )

        self.cycle += 1
