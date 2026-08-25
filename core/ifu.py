#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""ifu_unroll_v7_block_sibling.py

Based on your current ifu_unroll_v6_block.py, with extra metadata for sibling top-level loops.

New emitted fields:
- top_block_id:
    0, 1, 2, ... for top-level sibling loop blocks in the VF body
- is_last_in_top_block:
    whether this instruction is the last instruction of that top-level block
- block_key_by_level:
    e.g. [
      ("loop0", ()),
      ("loop1", (i0,)),
      ("loop2", (i0, i1)),
    ]
- block_end_levels:
    e.g. [2], [2,1], [2,1,0]

These are intended for IDU-side dynamic VLOOP scheduling for:
- nested loops
- sibling top-level loops
"""

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

def _is_pow2(u: int) -> bool:
    return u > 0 and (u & (u - 1)) == 0


def _resolve_int(x: Any, params: Dict[str, Any], default: int, minv: int) -> int:
    if x is None or isinstance(x, bool):
        return default
    if isinstance(x, int):
        return max(minv, x)
    if isinstance(x, float):
        return max(minv, int(x))
    if isinstance(x, str):
        s = x.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return max(minv, int(s))
        if s in params:
            try:
                return max(minv, int(params[s]))
            except Exception:
                return default
    return default


def _resolve_iters(iters: Any, params: Dict[str, Any]) -> int:
    return _resolve_int(iters, params, default=1, minv=0)


def _resolve_unroll(unroll: Any, params: Dict[str, Any]) -> int:
    return _resolve_int(unroll, params, default=1, minv=1)


def _resolve_signed_int(x: Any, params: Dict[str, Any], default: int) -> int:
    if x is None or isinstance(x, bool):
        return default
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, str):
        value = params.get(x, x)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        text = str(value).strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return default


@dataclass
class LoopCarriedBinding:
    entry_value_id: str
    back_edge_value_id: str
    exit_value_id: str
    current_value_id: str
    current_value_instance: Dict[str, Any]


@dataclass
class LoopFrame:
    begin_idx: int
    end_idx: int
    loop_id: int
    iters_total: int
    iter_now: int
    is_innermost: bool
    unroll: int
    top_block_id: int
    static_loop_id: str
    induction_variable: str
    induction_start: int
    induction_step: int
    carried_bindings: List[LoopCarriedBinding]


class IFUUnroll:
    def __init__(
        self,
        linear_nodes: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
        pdb=None,
        dtype: str = "fp32",
        structured_value_identity: bool = False,
        structured_dynamic_instruction_limit: int | None = None,
    ):
        self.nodes = [dict(x) for x in (linear_nodes or [])]
        self.params = dict(params or {})
        self.db = pdb
        self.dtype = str(dtype)
        self.structured_value_identity = bool(structured_value_identity)
        uarch = self.db.get_uarch() if self.db is not None else {}
        configured_limit = (
            structured_dynamic_instruction_limit
            if structured_dynamic_instruction_limit is not None
            else uarch.get("canonical_dynamic_instruction_limit", 20_000)
        )
        parsed_limit = int(configured_limit)
        self.structured_dynamic_instruction_limit = (
            None if parsed_limit <= 0 else parsed_limit
        )

        # loop matching
        self.begin_to_end: Dict[int, int] = {}
        st: List[int] = []
        for i, n in enumerate(self.nodes):
            t = n.get("type")
            if t == "loop_begin":
                st.append(i)
            elif t == "loop_end":
                if not st:
                    raise ValueError(f"Unmatched loop_end at index {i}")
                b = st.pop()
                self.begin_to_end[b] = i
        if st:
            raise ValueError(f"Unmatched loop_begin at indices {st}")

        # loop_id assignment
        self.begin_loop_id: Dict[int, int] = {}
        lid = 0
        for i, n in enumerate(self.nodes):
            if n.get("type") == "loop_begin":
                self.begin_loop_id[i] = lid
                lid += 1

        # innermost detection
        begins = sorted(self.begin_to_end.keys())
        self.is_innermost_begin: Dict[int, bool] = {}
        for b in begins:
            e = self.begin_to_end[b]
            nested = any((b < b2 < e) for b2 in begins if b2 != b)
            self.is_innermost_begin[b] = not nested

        # -------- top-level sibling block mapping --------
        # Each top-level loop_begin gets a top_block_id by order of appearance.
        self.begin_top_block_id: Dict[int, int] = {}
        top_bid = 0
        for i, n in enumerate(self.nodes):
            if n.get("type") == "loop_begin":
                # top-level means not enclosed by another loop_begin..loop_end
                enclosed = False
                for b in begins:
                    if b == i:
                        continue
                    e = self.begin_to_end[b]
                    if b < i < e:
                        enclosed = True
                        break
                if not enclosed:
                    self.begin_top_block_id[i] = top_bid
                    top_bid += 1

        self.total_top_blocks = top_bid

        # A top-level loop starts a scheduling block. Straight-line epilogue
        # nodes remain in that block until the next top-level loop begins.
        self.pc_top_block_id: Dict[int, int] = {}
        top_starts = sorted(self.begin_top_block_id)
        if top_starts:
            for index, begin in enumerate(top_starts):
                block_id = self.begin_top_block_id[begin]
                end = top_starts[index + 1] if index + 1 < len(top_starts) else len(self.nodes)
                for pc in range(begin, end):
                    self.pc_top_block_id[pc] = block_id
            for pc in range(0, top_starts[0]):
                self.pc_top_block_id[pc] = 0

        # cache innermost bodies
        self.loop_body_cache: Dict[int, List[Dict[str, Any]]] = {}
        for b in begins:
            if not self.is_innermost_begin[b]:
                continue
            e = self.begin_to_end[b]
            body = self.nodes[b + 1 : e]
            self.loop_body_cache[b] = [
                dict(x) for x in body if x.get("type") in ("inst", "membar")
            ]

        # cache last static inst index inside each loop body
        self.loop_last_inst_idx: Dict[int, Optional[int]] = {}
        for b in begins:
            e = self.begin_to_end[b]
            last_idx = None
            for i in range(b + 1, e):
                if self.nodes[i].get("type") == "inst":
                    last_idx = i
            self.loop_last_inst_idx[b] = last_idx

        # cache last static inst index inside each top-level block
        self.top_block_last_inst_idx: Dict[int, Optional[int]] = {}
        for index, b in enumerate(top_starts):
            tbid = self.begin_top_block_id[b]
            e = top_starts[index + 1] if index + 1 < len(top_starts) else len(self.nodes)
            last_idx = None
            for i in range(b + 1, e):
                if self.nodes[i].get("type") == "inst":
                    last_idx = i
            self.top_block_last_inst_idx[tbid] = last_idx

        # runtime state
        self.pc = 0
        self.frames: List[LoopFrame] = []
        self.inst_id = 0
        self.stream_seq = 0
        self.value_aliases: Dict[str, str] = {}
        self.dynamic_value_bindings: Dict[str, Dict[str, Any]] = {}

        self._pending: List[Dict[str, Any]] = []
        self._unroll_group = 0
        self._structured_stream: deque[Dict[str, Any]] = deque()
        self._structured_stream_built = False
        self._empty_top_block_ids: set[int] = set()

    def _first_membar_in_loop_body(self, begin_idx: int) -> Optional[Dict[str, Any]]:
        for node in self.loop_body_cache.get(begin_idx, []):
            if node.get("type") == "membar":
                return node
        return None

    def _record_membar_unroll_disabled(
        self,
        loop_node: Dict[str, Any],
        membar_node: Dict[str, Any],
        loop_id: int,
        requested_unroll: int,
    ) -> None:
        if not hasattr(self.db, "record_warning"):
            return
        barrier = membar_node.get("barrier", membar_node.get("scope", ""))
        self.db.record_warning(
            "membar_unroll_disabled",
            pc=int(membar_node.get("pc", loop_node.get("pc", -1))),
            barrier=str(barrier),
            loop_id=int(loop_node.get("loop_id", loop_id)),
            requested_unroll=int(requested_unroll),
            used_unroll=1,
            reason="membar_in_unrolled_innermost_loop",
        )

    def done(self) -> bool:
        if self.structured_value_identity and self._structured_stream_built:
            return not self._structured_stream
        return self.pc >= len(self.nodes) and not self._pending

    def _snapshot(self) -> Tuple[List[int], List[int]]:
        return ([fr.loop_id for fr in self.frames], [fr.iter_now for fr in self.frames])

    def _iteration_path(self) -> List[Dict[str, Any]]:
        return [
            {
                "loop_id": frame.static_loop_id,
                "iteration": frame.iter_now,
                "induction_variable": frame.induction_variable,
                "induction_value": (
                    frame.induction_start + frame.iter_now * frame.induction_step
                ),
            }
            for frame in self.frames
        ]

    def _iteration_path_for(self, frame: LoopFrame, iteration: int) -> List[Dict[str, Any]]:
        path = self._iteration_path()
        if path and self.frames and self.frames[-1] is frame:
            path[-1] = {
                "loop_id": frame.static_loop_id,
                "iteration": iteration,
                "induction_variable": frame.induction_variable,
                "induction_value": (
                    frame.induction_start + iteration * frame.induction_step
                ),
            }
        return path

    @staticmethod
    def _value_instance(definition_id: Any, iteration_path: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "definition_id": definition_id,
            "iteration_path": [dict(item) for item in iteration_path],
        }

    def _attach_value_instances(
        self,
        inst: Dict[str, Any],
        iteration_path: List[Dict[str, Any]],
        bindings: Dict[str, Dict[str, Any]] | None = None,
        source_definition_ids: List[Any] | None = None,
    ) -> None:
        if not self.structured_value_identity:
            return
        active = self.dynamic_value_bindings if bindings is None else bindings
        resolved_sources = list(inst.get("src", []))
        raw_sources = (
            resolved_sources
            if source_definition_ids is None
            else list(source_definition_ids)
        )
        src_instances = [
            self._dynamic_instance_for_reference(raw, resolved, active)
            for raw, resolved in zip(raw_sources, resolved_sources)
        ]
        dst_instances = []
        for definition_id in inst.get("dst", []):
            identity = self._value_instance(definition_id, iteration_path)
            dst_instances.append(identity)
            active[definition_id] = identity
        inst["src_value_instances"] = src_instances
        inst["dst_value_instances"] = dst_instances

    def _dynamic_instance_for_reference(
        self,
        raw_value: Any,
        resolved_value: Any,
        bindings: Dict[str, Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        active = self.dynamic_value_bindings if bindings is None else bindings
        if isinstance(raw_value, str):
            for frame in reversed(self.frames):
                binding = next(
                    (
                        item
                        for item in frame.carried_bindings
                        if item.entry_value_id == raw_value
                    ),
                    None,
                )
                if binding is not None:
                    return dict(binding.current_value_instance)
            if raw_value in active:
                return dict(active[raw_value])
        if resolved_value in active:
            return dict(active[resolved_value])
        return self._value_instance(resolved_value, [])

    def _resolve_dynamic_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        seen: set[str] = set()
        current = value
        while current not in seen:
            seen.add(current)
            frame_value = None
            for frame in reversed(self.frames):
                binding = next(
                    (
                        item
                        for item in frame.carried_bindings
                        if item.entry_value_id == current
                    ),
                    None,
                )
                if binding is not None:
                    frame_value = binding.current_value_id
                    break
            if frame_value is not None:
                if frame_value == current:
                    return current
                current = frame_value
                continue
            alias = self.value_aliases.get(current)
            if alias is None or alias == current:
                return current
            current = alias
        return current

    def _rewrite_dynamic_sources(self, values: Any) -> List[Any]:
        if not isinstance(values, list):
            values = [] if values is None else [values]
        rewritten: List[Any] = []
        for raw_value in values:
            rewritten.append(self._resolve_dynamic_value(raw_value))
        return rewritten

    def _create_loop_carried_bindings(
        self,
        carried_values: List[Dict[str, Any]],
        *,
        zero_iterations: bool,
    ) -> List[LoopCarriedBinding]:
        bindings: List[LoopCarriedBinding] = []
        for carried in carried_values:
            exit_value = carried.get("exit_value_id")
            entry_value = carried.get("entry_value_id")
            back_edge_value = carried.get("back_edge_value_id")
            if not all(
                isinstance(item, str)
                for item in (entry_value, back_edge_value, exit_value)
            ):
                continue
            self.value_aliases.pop(exit_value, None)
            resolved_entry = self._resolve_dynamic_value(entry_value)
            entry_instance = self._dynamic_instance_for_reference(
                entry_value, resolved_entry
            )
            if zero_iterations:
                self.value_aliases[exit_value] = resolved_entry
                self.dynamic_value_bindings[exit_value] = entry_instance
                continue
            bindings.append(
                LoopCarriedBinding(
                    entry_value_id=entry_value,
                    back_edge_value_id=back_edge_value,
                    exit_value_id=exit_value,
                    current_value_id=resolved_entry,
                    current_value_instance=entry_instance,
                )
            )
        return bindings

    def _advance_loop_carried_bindings(
        self,
        frame: LoopFrame,
        bindings: Dict[str, Dict[str, Any]] | None = None,
    ) -> None:
        next_values = [
            self._resolve_dynamic_value(binding.back_edge_value_id)
            for binding in frame.carried_bindings
        ]
        next_instances = [
            self._dynamic_instance_for_reference(
                binding.back_edge_value_id,
                next_value,
                bindings,
            )
            for binding, next_value in zip(frame.carried_bindings, next_values)
        ]
        for binding, next_value, next_instance in zip(
            frame.carried_bindings, next_values, next_instances
        ):
            binding.current_value_id = next_value
            binding.current_value_instance = next_instance

    def _complete_loop_value_aliases(self, frame: LoopFrame) -> None:
        for binding in frame.carried_bindings:
            self.value_aliases[binding.exit_value_id] = binding.current_value_id
            self.dynamic_value_bindings[binding.exit_value_id] = dict(
                binding.current_value_instance
            )

    def _current_top_block_id(self) -> int:
        """
        Current instruction belongs to the top-most active loop frame's top_block_id.
        If no frame, use the current static node's top-level block.
        """
        if self.frames:
            return int(self.frames[0].top_block_id)
        return int(self.pc_top_block_id.get(self.pc, 0))

    def _build_block_key_by_level(self, loop_stack: List[int], iter_stack: List[int]) -> List[Tuple[str, Tuple[int, ...]]]:
        """
        level 0: ("loop0", ())
        level 1: ("loop1", (iter0,))
        level 2: ("loop2", (iter0, iter1))
        ...
        """
        out: List[Tuple[str, Tuple[int, ...]]] = []
        for lv in range(len(loop_stack)):
            prefix = tuple(iter_stack[:lv])
            out.append((f"loop{lv}", prefix))
        return out

    def _calc_block_end_levels_normal(self) -> List[int]:
        """
        对纯嵌套循环：
        level lv 的 block 结束条件 =
          当前 pc 是 innermost body 最后一条静态指令
          且从 lv 到 deepest 的所有 frame 都处于最后一次迭代
        """
        if not self.frames:
            return []

        deepest = len(self.frames) - 1
        deepest_fr = self.frames[deepest]
        last_idx = self.loop_last_inst_idx.get(deepest_fr.begin_idx, None)

        if last_idx is None or self.pc != last_idx:
            return []

        end_levels: List[int] = []

        for lv in range(deepest, -1, -1):
            all_final = True
            for kk in range(lv, deepest + 1):
                fr = self.frames[kk]
                if fr.iter_now != fr.iters_total - 1:
                    all_final = False
                    break

            if all_final:
                end_levels.append(lv)
            else:
                break

        return end_levels

    def _is_last_in_top_block_normal(self) -> bool:
        """
        True iff current static inst is:
          - the last static inst in current top-level block
          - and all active frames are at their last iteration
        """
        tbid = self._current_top_block_id()
        last_idx = self.top_block_last_inst_idx.get(tbid, None)
        if last_idx is None or self.pc != last_idx:
            return False

        if not self.frames:
            return True

        for fr in self.frames:
            if fr.iter_now != fr.iters_total - 1:
                return False
        return True

    def _emit_normal_inst(self, n: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(n)
        loop_stack, iter_stack = self._snapshot()

        raw_sources = list(out.get("src", []))
        out["src"] = self._rewrite_dynamic_sources(raw_sources)
        iteration_path = self._iteration_path()
        self._attach_value_instances(
            out,
            iteration_path,
            source_definition_ids=raw_sources,
        )

        out["inst_id"] = self.inst_id
        self.inst_id += 1
        out["stream_seq"] = self.stream_seq
        self.stream_seq += 1
        out["loop_stack"] = list(loop_stack)
        out["iter_stack"] = list(iter_stack)
        out["iteration_path"] = iteration_path
        out["loop_depth"] = len(loop_stack)
        out["in_loop"] = bool(loop_stack)
        out["unroll_factor"] = 1
        out["lane"] = None

        # top-level sibling info
        out["top_block_id"] = self._current_top_block_id()
        out["is_last_in_top_block"] = self._is_last_in_top_block_normal()

        # nested-loop metadata
        out["block_key_by_level"] = self._build_block_key_by_level(loop_stack, iter_stack)
        out["block_end_levels"] = self._calc_block_end_levels_normal()

        return out

    def _emit_normal_membar(self, n: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(n)
        loop_stack, iter_stack = self._snapshot()

        out["type"] = "membar"
        out["stream_seq"] = self.stream_seq
        self.stream_seq += 1
        out["loop_stack"] = list(loop_stack)
        out["iter_stack"] = list(iter_stack)
        out["iteration_path"] = self._iteration_path()
        out["loop_depth"] = len(loop_stack)
        out["in_loop"] = bool(loop_stack)
        out["unroll_factor"] = 1
        out["lane"] = None
        out["top_block_id"] = self._current_top_block_id()
        out["block_key_by_level"] = self._build_block_key_by_level(loop_stack, iter_stack)
        out["block_end_levels"] = []

        return out

    def _build_pending_unrolled(self, frame: LoopFrame) -> None:
        if self.structured_value_identity:
            self._build_pending_unrolled_structured(frame)
            return

        body = self.loop_body_cache.get(frame.begin_idx, [])

        loop_stack, iter_stack = self._snapshot()
        U = frame.unroll
        orig_base = frame.iter_now
        super_iter = orig_base // U if U > 0 else orig_base

        pending: List[Dict[str, Any]] = []

        # For unrolled innermost loop, this super-iteration block ends
        # only at the very last emitted inst of the pending batch.
        is_last_super_iter = (orig_base + U >= frame.iters_total)

        for ins in body:
            for lane in range(U):
                if ins.get("type") == "membar":
                    membar = dict(ins)
                    membar["type"] = "membar"
                    membar["stream_seq"] = self.stream_seq
                    self.stream_seq += 1
                    membar["loop_stack"] = list(loop_stack)
                    if iter_stack:
                        membar["iter_stack"] = list(iter_stack[:-1] + [super_iter])
                    else:
                        membar["iter_stack"] = []
                    membar["loop_depth"] = len(loop_stack)
                    membar["in_loop"] = True
                    membar["unroll_factor"] = U
                    membar["unroll_group"] = self._unroll_group
                    membar["unroll_lane"] = lane
                    membar["orig_iter_base"] = orig_base
                    membar["lane"] = lane
                    membar["top_block_id"] = int(frame.top_block_id)
                    bs = list(membar["iter_stack"])
                    membar["block_key_by_level"] = self._build_block_key_by_level(loop_stack, bs)
                    membar["block_end_levels"] = []
                    pending.append(membar)
                    continue

                inst = dict(ins)
                inst["inst_id"] = self.inst_id
                self.inst_id += 1
                inst["stream_seq"] = self.stream_seq
                self.stream_seq += 1

                inst["loop_stack"] = list(loop_stack)
                if iter_stack:
                    inst["iter_stack"] = list(iter_stack[:-1] + [super_iter])
                else:
                    inst["iter_stack"] = []
                inst["loop_depth"] = len(loop_stack)
                inst["in_loop"] = True

                inst["unroll_factor"] = U
                inst["unroll_group"] = self._unroll_group
                inst["unroll_lane"] = lane
                inst["orig_iter_base"] = orig_base
                inst["lane"] = lane

                inst["src"] = [(x + "_lane" + str(lane)) for x in inst["src"]]
                inst["dst"] = [(x + "_lane" + str(lane)) for x in inst["dst"]]

                # top-level sibling info
                inst["top_block_id"] = int(frame.top_block_id)
                inst["is_last_in_top_block"] = False

                # nested-loop metadata
                bs = list(inst["iter_stack"])
                inst["block_key_by_level"] = self._build_block_key_by_level(loop_stack, bs)
                inst["block_end_levels"] = []

                pending.append(inst)

        # mark only the last emitted inst as block-end candidate
        if pending:
            deepest = len(loop_stack) - 1
            end_levels: List[int] = []

            if is_last_super_iter:
                for lv in range(deepest, -1, -1):
                    all_final = True
                    for kk in range(lv, deepest + 1):
                        fr = self.frames[kk]

                        if kk == deepest:
                            final_now = is_last_super_iter
                        else:
                            final_now = (fr.iter_now == fr.iters_total - 1)

                        if not final_now:
                            all_final = False
                            break

                    if all_final:
                        end_levels.append(lv)
                    else:
                        break

            pending[-1]["block_end_levels"] = end_levels

            # top-level block end for unrolled pending batch
            if is_last_super_iter:
                top_all_final = True
                for fr in self.frames:
                    if fr is frame:
                        continue
                    if fr.iter_now != fr.iters_total - 1:
                        top_all_final = False
                        break
                pending[-1]["is_last_in_top_block"] = top_all_final

        self._unroll_group += 1
        self._pending = pending
        frame.iter_now += U

    def _build_pending_unrolled_structured(self, frame: LoopFrame) -> None:
        body = self.loop_body_cache.get(frame.begin_idx, [])
        loop_stack, iter_stack = self._snapshot()
        unroll = frame.unroll
        orig_base = frame.iter_now
        super_iter = orig_base // unroll
        is_last_super_iter = orig_base + unroll >= frame.iters_total
        bindings = dict(self.dynamic_value_bindings)
        by_lane: List[List[Dict[str, Any]]] = []

        for lane in range(unroll):
            actual_iteration = orig_base + lane
            iteration_path = self._iteration_path_for(frame, actual_iteration)
            lane_items: List[Dict[str, Any]] = []
            for ins in body:
                if ins.get("type") == "membar":
                    raise RuntimeError(
                        "Membar must disable innermost unroll before structured expansion"
                    )
                inst = dict(ins)
                raw_sources = list(inst.get("src", []))
                inst["src"] = self._rewrite_dynamic_sources(raw_sources)
                self._attach_value_instances(
                    inst,
                    iteration_path,
                    bindings,
                    source_definition_ids=raw_sources,
                )
                inst["iteration_path"] = [dict(item) for item in iteration_path]
                inst["loop_stack"] = list(loop_stack)
                inst["iter_stack"] = (
                    list(iter_stack[:-1] + [super_iter]) if iter_stack else []
                )
                inst["loop_depth"] = len(loop_stack)
                inst["in_loop"] = True
                inst["unroll_factor"] = unroll
                inst["unroll_group"] = self._unroll_group
                inst["unroll_lane"] = lane
                inst["orig_iter_base"] = orig_base
                inst["lane"] = lane
                inst["top_block_id"] = int(frame.top_block_id)
                inst["is_last_in_top_block"] = False
                inst["block_key_by_level"] = self._build_block_key_by_level(
                    loop_stack, inst["iter_stack"]
                )
                inst["block_end_levels"] = []
                lane_items.append(inst)
            by_lane.append(lane_items)
            self._advance_loop_carried_bindings(frame, bindings)

        pending: List[Dict[str, Any]] = []
        for instruction_index in range(len(body)):
            for lane in range(unroll):
                inst = by_lane[lane][instruction_index]
                inst["inst_id"] = self.inst_id
                self.inst_id += 1
                inst["stream_seq"] = self.stream_seq
                self.stream_seq += 1
                pending.append(inst)

        if pending:
            deepest = len(loop_stack) - 1
            end_levels: List[int] = []
            if is_last_super_iter:
                for level in range(deepest, -1, -1):
                    if all(
                        item is frame or item.iter_now == item.iters_total - 1
                        for item in self.frames[level:]
                    ):
                        end_levels.append(level)
                    else:
                        break
            pending[-1]["block_end_levels"] = end_levels
            if is_last_super_iter and all(
                item is frame or item.iter_now == item.iters_total - 1
                for item in self.frames
            ):
                pending[-1]["is_last_in_top_block"] = True

        self.dynamic_value_bindings = bindings
        self._unroll_group += 1
        self._pending = pending
        frame.iter_now += unroll

    @staticmethod
    def _value_instance_key(
        identity: Dict[str, Any],
    ) -> tuple[Any, tuple[tuple[str, int], ...]]:
        return (
            identity.get("definition_id"),
            tuple(
                (str(item.get("loop_id", "")), int(item.get("iteration", 0)))
                for item in identity.get("iteration_path", [])
                if isinstance(item, dict)
            ),
        )

    def _annotate_structured_value_lifetimes(
        self, stream: List[Dict[str, Any]]
    ) -> None:
        remaining_uses: Dict[tuple[Any, tuple[tuple[str, int], ...]], int] = {}
        for inst in stream:
            for identity in inst.get("src_value_instances", []):
                key = self._value_instance_key(identity)
                remaining_uses[key] = remaining_uses.get(key, 0) + 1

        for inst in stream:
            inst["dst_value_instance_keep"] = [
                remaining_uses.get(self._value_instance_key(identity), 0) > 0
                for identity in inst.get("dst_value_instances", [])
            ]

            release_sources = []
            for identity in inst.get("src_value_instances", []):
                key = self._value_instance_key(identity)
                remaining_uses[key] = remaining_uses.get(key, 0) - 1
                release_sources.append(remaining_uses[key] == 0)
            inst["src_value_instance_release"] = release_sources

    def _next_inst_raw(self) -> Optional[Dict[str, Any]]:
        if self._pending:
            return self._pending.pop(0)

        while self.pc < len(self.nodes):
            n = self.nodes[self.pc]
            t = n.get("type")

            if t == "loop_begin":
                iters = _resolve_iters(n.get("iters", 1), self.params)
                end = self.begin_to_end[self.pc]
                loop_id = self.begin_loop_id[self.pc]
                is_innermost = bool(self.is_innermost_begin.get(self.pc, False))
                unroll = _resolve_unroll(n.get("unroll", 1), self.params)
                carried_values = [
                    dict(item) for item in n.get("carried_values", [])
                ]
                carried_bindings = self._create_loop_carried_bindings(
                    carried_values,
                    zero_iterations=iters <= 0,
                )
                induction = n.get("induction", {})
                if not isinstance(induction, dict):
                    induction = {}
                induction_variable = str(
                    induction.get("variable_id", f"iter_{loop_id}")
                )
                induction_start = _resolve_signed_int(
                    induction.get("start", 0), self.params, 0
                )
                induction_step = _resolve_signed_int(
                    induction.get("step", 1), self.params, 1
                )

                if iters <= 0:
                    self.pc = end + 1
                    continue

                # find top_block_id:
                if self.frames:
                    top_block_id = self.frames[0].top_block_id
                else:
                    top_block_id = self.begin_top_block_id.get(self.pc, 0)

                # validate unroll constraints for innermost loops
                if is_innermost and unroll > 1:
                    if iters % unroll != 0:
                        raise ValueError(f"Invalid unroll={unroll}: iters={iters} not divisible by unroll")
                    membar = self._first_membar_in_loop_body(self.pc)
                    if membar is not None:
                        self._record_membar_unroll_disabled(n, membar, loop_id, unroll)
                        unroll = 1

                frame = LoopFrame(
                    begin_idx=self.pc,
                    end_idx=end,
                    loop_id=loop_id,
                    iters_total=iters,
                    iter_now=0,
                    is_innermost=is_innermost,
                    unroll=(unroll if (is_innermost and unroll > 1) else 1),
                    top_block_id=int(top_block_id),
                    static_loop_id=str(n.get("name", f"loop_{loop_id}")),
                    induction_variable=induction_variable,
                    induction_start=induction_start,
                    induction_step=induction_step,
                    carried_bindings=carried_bindings,
                )
                self.frames.append(frame)

                if frame.is_innermost and frame.unroll > 1:
                    self.pc = frame.end_idx  # skip static body
                else:
                    self.pc += 1
                continue

            if t == "loop_end":
                if not self.frames:
                    raise RuntimeError("loop_end encountered with empty runtime stack")
                top = self.frames[-1]
                if top.end_idx != self.pc:
                    raise RuntimeError("loop_end mismatch with runtime top frame")

                if top.is_innermost and top.unroll > 1:
                    if top.iter_now < top.iters_total:
                        self._build_pending_unrolled(top)
                        return self._pending.pop(0) if self._pending else None
                    else:
                        if not self.structured_value_identity:
                            self._advance_loop_carried_bindings(top)
                        self._complete_loop_value_aliases(top)
                        self.frames.pop()
                        self.pc += 1
                        continue
                else:
                    if top.iter_now + 1 < top.iters_total:
                        self._advance_loop_carried_bindings(top)
                        top.iter_now += 1
                        self.pc = top.begin_idx + 1
                        continue
                    else:
                        self._advance_loop_carried_bindings(top)
                        self._complete_loop_value_aliases(top)
                        self.frames.pop()
                        self.pc += 1
                        continue

            if t == "membar":
                out = self._emit_normal_membar(n)
                self.pc += 1
                return out

            if t != "inst":
                self.pc += 1
                continue

            out = self._emit_normal_inst(n)
            self.pc += 1
            return out

        return None

    def next_inst(self) -> Optional[Dict[str, Any]]:
        if not self.structured_value_identity:
            return self._next_inst_raw()

        self.prepare_structured_stream()

        if not self._structured_stream:
            return None
        return self._structured_stream.popleft()

    def prepare_structured_stream(self) -> None:
        """Materialize the canonical dynamic stream and its block metadata."""
        if not self.structured_value_identity or self._structured_stream_built:
            return

        expanded: List[Dict[str, Any]] = []
        while True:
            inst = self._next_inst_raw()
            if inst is None:
                break
            expanded.append(inst)
            if (
                self.structured_dynamic_instruction_limit is not None
                and len(expanded) > self.structured_dynamic_instruction_limit
            ):
                raise RuntimeError(
                    "Canonical dynamic instruction count exceeds "
                    f"canonical_dynamic_instruction_limit="
                    f"{self.structured_dynamic_instruction_limit}. "
                    "Reduce the loop count/unroll or raise the explicit limit."
                )
        self._annotate_structured_value_lifetimes(expanded)
        self._structured_stream.extend(expanded)
        nonempty_top_blocks = {
            int(inst.get("top_block_id", 0))
            for inst in expanded
            if inst.get("type") == "inst"
        }
        self._empty_top_block_ids = (
            set(range(self.total_top_blocks)) - nonempty_top_blocks
        )
        self._structured_stream_built = True

    def empty_top_block_ids(self) -> set[int]:
        self.prepare_structured_stream()
        return set(self._empty_top_block_ids)

    def take(self, n: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for _ in range(max(0, int(n))):
            inst = self.next_inst()
            if inst is None:
                break
            out.append(inst)
        return out
