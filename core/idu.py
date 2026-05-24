#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from collections import deque
import json


class IDU:
    def __init__(
        self,
        uarch,
        pdb,
        params=None,
        loop_bounds=None,
        total_top_blocks=1,
        top_block_loop_bounds=None,
    ):
        self.window_width = uarch["IDU_window_width"]
        self.issue_width = uarch["IDU_issue_width"]
        self.theoretical_limit_mode = bool(uarch.get("theoretical_limit_mode", False))
        self.theoretical_limit_vloop_only = bool(
            uarch.get("theoretical_limit_vloop_only", False)
        )
        self.db = pdb

        defaults = self.db.get_defaults()
        self.vf_startup_cost = int(defaults.get("vf_startup_cost", 0))
        self.idu_dispatch_start_advance = int(uarch.get("idu_dispatch_start_advance", 0))
        self.vloop_to_dispatch_delay = int(uarch.get("vloop_to_dispatch_delay", 4))
        self.global_shq_preg_gate = bool(
            uarch.get("global_shq_preg_gate", False)
        )
        # Empirical loop1 feedback calibration:
        # initial VLOOP seeds keep their original timing, but for feedback-
        # triggered next loop1 starts we enforce a minimum start interval of
        # 7 cycles between consecutive loop1 instances. Physically this stands
        # in for a coarse IDU/IFU control-path feedback delay observed in
        # hardware-vs-simulator comparison.
        self.loop1_min_feedback_gap = 7

        self.window = deque()

        # global/basic loop info
        self.params = params or {}
        self.loop_bounds = list(loop_bounds or [])
        self.loop_depth = len(self.loop_bounds)

        # top-level sibling blocks
        self.total_top_blocks = int(total_top_blocks)

        # {top_block_id: [bounds...]}
        if top_block_loop_bounds is None:
            # backward compatible
            self.top_block_loop_bounds = {0: list(loop_bounds or [])}
        else:
            self.top_block_loop_bounds = {
                int(k): list(v or []) for k, v in top_block_loop_bounds.items()
            }

        # ---------- top-level sibling block VLOOP ----------
        self.top_block_vloop_start = {}
        self.top_block_body_open_time = {}

        # ---------- nested dynamic VLOOP ----------
        # key:
        #   (tbid, "loop0", ())
        #   (tbid, "loop1", (i,))
        #   (tbid, "loop2", (i,j))
        self.vloop_start = {}
        self.body_open_time = {}
        self.last_dispatch_time = {}

        # per-inner-block dispatch base:
        #   dispatch >= block_base_cycle[inner_key] + iter_inner
        self.block_base_cycle = {}

        self.vloop_trace = []
        self._init_vloop_starts()

        # IDU -> OoO dispatch log
        self.dispatch_log = []

    # ---------------- basic ----------------

    def empty(self) -> bool:
        return len(self.window) == 0

    def can_accept(self):
        if self.theoretical_limit_mode:
            return True
        return len(self.window) < self.window_width

    def accept(self, inst):
        if self.theoretical_limit_mode or len(self.window) < self.window_width:
            self.window.append(inst)

    # ---------------- top-block helpers ----------------

    def _get_block_bounds(self, top_block_id: int):
        return list(self.top_block_loop_bounds.get(int(top_block_id), []))

    def _get_block_depth(self, top_block_id: int) -> int:
        return len(self._get_block_bounds(top_block_id))

    def _set_top_block_vloop(self, top_block_id: int, start_cycle: int):
        top_block_id = int(top_block_id)
        start_cycle = int(start_cycle)

        if top_block_id in self.top_block_vloop_start:
            return

        self.top_block_vloop_start[top_block_id] = start_cycle
        self.top_block_body_open_time[top_block_id] = start_cycle + self.vloop_to_dispatch_delay

    # ---------------- nested VLOOP helpers ----------------

    def _record_vloop(self, top_block_id: int, loop_id: str, it, start_cycle: int):
        self.vloop_trace.append({
            "top_block_id": int(top_block_id),
            "loop_id": loop_id,
            "iter": list(it),
            "start_cycle": int(start_cycle),
        })

    def _make_key(self, top_block_id: int, loop_id: str, it) -> tuple:
        return (int(top_block_id), str(loop_id), tuple(it))

    def _set_vloop_start(self, key, start_cycle: int):
        """
        key:
          (tbid, "loop0", ())
          (tbid, "loop1", (i,))
          (tbid, "loop2", (i,j))
        """
        if key in self.vloop_start:
            return

        start_cycle = int(start_cycle)
        self.vloop_start[key] = start_cycle
        self.body_open_time[key] = start_cycle + self.vloop_to_dispatch_delay

        tbid, loop_id, it = key
        self._record_vloop(tbid, loop_id, it, start_cycle)

    def _init_top_block_nested_starts(self, top_block_id: int, top_vloop_start: int):
        """
        When a top-level sibling block is triggered, initialize its first nested VLOOPs.
        Supports depth 1/2/3 independently for each top block.
        """
        tbid = int(top_block_id)
        T = int(top_vloop_start)
        bounds = self._get_block_bounds(tbid)
        depth = len(bounds)

        if depth <= 0:
            return

        # depth=1: loop0() = T
        self._set_vloop_start(self._make_key(tbid, "loop0", ()), T)

        # depth=2: loop1(0) = T + 1
        if depth >= 2 and bounds[0] > 0:
            self._set_vloop_start(self._make_key(tbid, "loop1", (0,)), T + 1)

        # depth=3: loop2(0,0) = T + 2
        if depth >= 3 and bounds[0] > 0 and bounds[1] > 0:
            self._set_vloop_start(self._make_key(tbid, "loop2", (0, 0)), T + 2)

    def _init_vloop_starts(self):
        """
        Initialize top_block 0 only:
          top_block 0 VLOOP = 19
        then initialize its nested starts.
        """
        if self.total_top_blocks <= 0:
            return
        self._set_top_block_vloop(0, 19)
        self._init_top_block_nested_starts(0, 19)

    def _normalize_block_key(self, raw_key, top_block_id: int):
        """
        IFU raw_key:
          ("loop0", ())
          ("loop1", (i,))
          ("loop2", (i,j))
        Here we prepend top_block_id namespace.
        """
        if isinstance(raw_key, (list, tuple)) and len(raw_key) == 2:
            loop_id = raw_key[0]
            it = tuple(raw_key[1]) if isinstance(raw_key[1], (list, tuple)) else ()
            return self._make_key(top_block_id, loop_id, it)
        return None

    def _current_inner_block_key(self, inst):
        """
        Pick the deepest loop block that this instruction actually belongs to.

        A top-level loop body can contain instructions before/after sibling
        inner loops. Those instructions only carry a loop0 key; gating them on
        the top block's maximum depth would incorrectly make them wait for an
        inner-loop VLOOP that has not started yet.
        """
        top_block_id = int(inst.get("top_block_id", 0))
        depth = self._get_block_depth(top_block_id)

        bk = inst.get("block_key_by_level", None)
        if isinstance(bk, list) and len(bk) >= 1:
            key = self._normalize_block_key(bk[-1], top_block_id)
            if key is not None:
                return key

        # fallback（兼容旧 IFU）
        iter_stack = inst.get("iter_stack", [])
        inst_depth = int(inst.get("loop_depth", len(iter_stack) if isinstance(iter_stack, list) else 0))
        active_depth = min(depth, inst_depth)
        if active_depth <= 0:
            return None
        if active_depth == 1:
            return self._make_key(top_block_id, "loop0", ())
        if active_depth == 2 and len(iter_stack) >= 1:
            return self._make_key(top_block_id, "loop1", (int(iter_stack[0]),))
        if active_depth == 3 and len(iter_stack) >= 2:
            return self._make_key(top_block_id, "loop2", (int(iter_stack[0]), int(iter_stack[1])))

        return None

    def _update_last_dispatch(self, inst, cycle: int):
        """
        Record last dispatch time for all nested blocks this inst belongs to.
        """
        top_block_id = int(inst.get("top_block_id", 0))
        depth = self._get_block_depth(top_block_id)

        bk = inst.get("block_key_by_level", None)
        if isinstance(bk, list):
            for raw_key in bk:
                key = self._normalize_block_key(raw_key, top_block_id)
                if key is not None:
                    self.last_dispatch_time[key] = int(cycle)
            return

        # fallback（兼容旧 IFU）
        iter_stack = inst.get("iter_stack", [])
        if depth == 1:
            self.last_dispatch_time[self._make_key(top_block_id, "loop0", ())] = int(cycle)
        elif depth == 2 and len(iter_stack) >= 1:
            self.last_dispatch_time[self._make_key(top_block_id, "loop1", (int(iter_stack[0]),))] = int(cycle)
        elif depth == 3 and len(iter_stack) >= 2:
            self.last_dispatch_time[self._make_key(top_block_id, "loop2", (int(iter_stack[0]), int(iter_stack[1])))] = int(cycle)
            self.last_dispatch_time[self._make_key(top_block_id, "loop1", (int(iter_stack[0]),))] = int(cycle)
            self.last_dispatch_time[self._make_key(top_block_id, "loop0", ())] = int(cycle)

    def _trigger_next_vloops(self, inst, cycle: int):
        """
        Dynamically trigger:
        1) next sibling top-level block
        2) next nested block inside current top-level block
        """
        top_block_id = int(inst.get("top_block_id", 0))
        depth = self._get_block_depth(top_block_id)
        bounds = self._get_block_bounds(top_block_id)

        # ---------- sibling top-level block ----------
        if bool(inst.get("is_last_in_top_block", False)):
            next_tbid = top_block_id + 1
            if next_tbid < self.total_top_blocks:
                if next_tbid not in self.top_block_vloop_start:
                    self._set_top_block_vloop(next_tbid, cycle)
                    self._init_top_block_nested_starts(next_tbid, cycle)

        # ---------- nested loop ----------
        if depth <= 0:
            return

        end_levels = inst.get("block_end_levels", [])
        if not isinstance(end_levels, list) or not end_levels:
            return

        iter_stack = inst.get("iter_stack", [])
        if not isinstance(iter_stack, list):
            return

        # depth = 1: only loop0, nothing else to trigger
        if depth == 1:
            return

        # depth = 2
        if depth == 2:
            # level 1 ends => next loop1(i+1)
            if 1 in end_levels and len(iter_stack) >= 1:
                i = int(iter_stack[0])
                I = int(bounds[0])

                cur_key = self._make_key(top_block_id, "loop1", (i,))
                end_cy = self.last_dispatch_time.get(cur_key, int(cycle))

                if i + 1 < I:
                    prev_start = self.vloop_start.get(cur_key, end_cy)
                    next_start = max(int(end_cy), int(prev_start) + self.loop1_min_feedback_gap)
                    self._set_vloop_start(
                        self._make_key(top_block_id, "loop1", (i + 1,)),
                        next_start,
                    )
            return

        # depth = 3
        if depth == 3:
            # level 2 ends => next loop2(i, j+1)
            if 2 in end_levels and len(iter_stack) >= 2:
                i = int(iter_stack[0])
                j = int(iter_stack[1])
                M = int(bounds[1])

                cur_key = self._make_key(top_block_id, "loop2", (i, j))
                end_cy = self.last_dispatch_time.get(cur_key, int(cycle))

                if j + 1 < M:
                    self._set_vloop_start(
                        self._make_key(top_block_id, "loop2", (i, j + 1)),
                        end_cy,
                    )

            # level 1 ends => next loop1(i+1), and first child loop2(i+1,0)
            if 1 in end_levels and len(iter_stack) >= 1:
                i = int(iter_stack[0])
                K = int(bounds[0])
                M = int(bounds[1])

                cur_key = self._make_key(top_block_id, "loop1", (i,))
                end_cy = self.last_dispatch_time.get(cur_key, int(cycle))

                if i + 1 < K:
                    prev_start = self.vloop_start.get(cur_key, end_cy)
                    next_loop1_start = max(int(end_cy), int(prev_start) + self.loop1_min_feedback_gap)
                    self._set_vloop_start(
                        self._make_key(top_block_id, "loop1", (i + 1,)),
                        next_loop1_start,
                    )
                    if M > 0:
                        self._set_vloop_start(
                            self._make_key(top_block_id, "loop2", (i + 1, 0)),
                            next_loop1_start + 1,
                        )
            return

    # ---------------- dispatch ----------------

    def dispatch(self, cycle, ooo):
        if not self.window:
            return []

        # VF startup gate
        dispatch_start_gate = max(0, int(self.vf_startup_cost) - int(self.idu_dispatch_start_advance))
        if cycle < dispatch_start_gate:
            return []

        dispatched = []

        if self.theoretical_limit_mode:
            credits = 10 ** 18
            shq_queue_free = 10 ** 18
            lsq_free = 10 ** 18
            shq_free = 10 ** 18
            issue_budget = len(self.window)
        else:
            credits = ooo.get_free_preg()
            shq_queue_free = ooo.get_free_shq_queue()  # compute queue only
            lsq_free = ooo.get_free_lsq()    # VLD/VST only
            shq_free = ooo.get_free_shq()    # shared SHQ credit for compute + VST
            issue_budget = self.issue_width

        this_cycle_credits = credits
        this_cycle_shq_queue = shq_queue_free
        this_cycle_lsq = lsq_free
        this_cycle_shq = shq_free

        if shq_queue_free <= 0 and lsq_free <= 0:
            return []
        if self.global_shq_preg_gate and (
            int(credits) <= 0 or int(shq_free) <= 0
        ):
            return []

        for inst in list(self.window):
            if len(dispatched) >= issue_budget:
                break

            op = inst.get("op", "")
            iter_stack = inst.get("iter_stack", [])
            top_block_id = int(inst.get("top_block_id", 0))

            # -------------------------------------------------
            # -1) sibling top-block gate
            # dispatch >= top_block_body_open_time[top_block_id]
            # -------------------------------------------------
            top_open = self.top_block_body_open_time.get(top_block_id, None)
            if top_open is None:
                break
            if cycle < top_open:
                break

            # -------------------------------------------------
            # 0) nested body-open gate
            # dispatch >= body_open_time(inner_block)
            # -------------------------------------------------
            inner_key = self._current_inner_block_key(inst)
            if (not self.theoretical_limit_vloop_only) and inner_key is not None:
                open_t = self.body_open_time.get(inner_key, None)
                if open_t is None:
                    break
                if cycle < open_t:
                    break

            # -------------------------------------------------
            # 1) block-scoped innermost iter gate
            # dispatch >= block_base_cycle[inner_key] + iter_inner
            # -------------------------------------------------
            iter_id = iter_stack[-1] if iter_stack else 0
            if (not self.theoretical_limit_mode) and (not self.theoretical_limit_vloop_only) and inner_key is not None:
                if iter_id == 0 and inner_key not in self.block_base_cycle:
                    self.block_base_cycle[inner_key] = cycle

                base_cy = self.block_base_cycle.get(inner_key, None)
                if base_cy is None:
                    break

                if cycle < base_cy + iter_id:
                    break

            # -------------------------------------------------
            # 2) SHQ / LSQ space gate
            # -------------------------------------------------
            if op == "VLD":
                if lsq_free <= 0:
                    break
            elif op == "VST":
                if lsq_free <= 0:
                    break
                if shq_free <= 0:
                    break
            else:
                if shq_queue_free <= 0:
                    break
                if shq_free <= 0:
                    break

            # -------------------------------------------------
            # 3) preg credit gate
            # -------------------------------------------------
            dst_count = 0
            for d in inst.get("dst", []):
                if isinstance(d, str) and d[:1].lower() == "v":
                    dst_count += 1

            if credits < dst_count:
                break

            # -------------------------------------------------
            # 4) dispatch accepted
            # -------------------------------------------------
            dispatched.append(inst)

            credits -= dst_count
            if op in ("VLD", "VST"):
                lsq_free -= 1
                if op == "VST":
                    shq_free -= 1
            else:
                shq_queue_free -= 1
                shq_free -= 1

        # commit dispatch
        for inst in dispatched:
            self.window.popleft()

            self.dispatch_log.append({
                "cy": cycle,
                "inst_id": inst.get("inst_id", inst.get("id")),
                "op": inst.get("op"),
                "dst": inst.get("dst", []),
                "src": inst.get("src", []),
                "top_block_id": int(inst.get("top_block_id", 0)),
                "vreg": this_cycle_credits,
                "SHQ_QUEUE": this_cycle_shq_queue,
                "LSQ": this_cycle_lsq,
                "SHQ": this_cycle_shq,
            })

            self._update_last_dispatch(inst, cycle)
            self._trigger_next_vloops(inst, cycle)

        return dispatched

    # ---------------- dump ----------------

    def dump_dispatch_log(self, path="idu_to_ooo.json"):
        with open(path, "w", encoding="utf-8") as f:
            for item in self.dispatch_log:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def dump_vloop_trace(self, path="vloop_trace.json"):
        data = sorted(
            self.vloop_trace,
            key=lambda x: (x["top_block_id"], x["start_cycle"], x["loop_id"], tuple(x["iter"]))
        )
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
