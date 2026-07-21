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


class IFUUnroll:
    def __init__(
        self,
        linear_nodes: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
        pdb=None,
        dtype: str = "fp32",
    ):
        self.nodes = [dict(x) for x in (linear_nodes or [])]
        self.params = dict(params or {})
        self.db = pdb
        self.dtype = str(dtype)

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

        # cache innermost bodies
        self.loop_body_cache: Dict[int, List[Dict[str, Any]]] = {}
        for b in begins:
            if not self.is_innermost_begin[b]:
                continue
            e = self.begin_to_end[b]
            body = self.nodes[b + 1 : e]
            self.loop_body_cache[b] = [dict(x) for x in body if x.get("type") == "inst"]

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
        for b, tbid in self.begin_top_block_id.items():
            e = self.begin_to_end[b]
            last_idx = None
            for i in range(b + 1, e):
                if self.nodes[i].get("type") == "inst":
                    last_idx = i
            self.top_block_last_inst_idx[tbid] = last_idx

        # runtime state
        self.pc = 0
        self.frames: List[LoopFrame] = []
        self.inst_id = 0

        self._pending: List[Dict[str, Any]] = []
        self._unroll_group = 0

    def done(self) -> bool:
        return self.pc >= len(self.nodes) and not self._pending

    def _snapshot(self) -> Tuple[List[int], List[int]]:
        return ([fr.loop_id for fr in self.frames], [fr.iter_now for fr in self.frames])

    def _current_top_block_id(self) -> int:
        """
        Current instruction belongs to the top-most active loop frame's top_block_id.
        If no frame, return 0.
        """
        if self.frames:
            return int(self.frames[0].top_block_id)
        return 0

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
        if not self.frames:
            return False

        tbid = self._current_top_block_id()
        last_idx = self.top_block_last_inst_idx.get(tbid, None)
        if last_idx is None or self.pc != last_idx:
            return False

        for fr in self.frames:
            if fr.iter_now != fr.iters_total - 1:
                return False
        return True

    def _emit_normal_inst(self, n: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(n)
        loop_stack, iter_stack = self._snapshot()

        out["inst_id"] = self.inst_id
        self.inst_id += 1
        out["loop_stack"] = list(loop_stack)
        out["iter_stack"] = list(iter_stack)
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

    def _build_pending_unrolled(self, frame: LoopFrame) -> None:
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
                inst = dict(ins)
                inst["inst_id"] = self.inst_id
                self.inst_id += 1

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

    def next_inst(self) -> Optional[Dict[str, Any]]:
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

                frame = LoopFrame(
                    begin_idx=self.pc,
                    end_idx=end,
                    loop_id=loop_id,
                    iters_total=iters,
                    iter_now=0,
                    is_innermost=is_innermost,
                    unroll=(unroll if (is_innermost and unroll > 1) else 1),
                    top_block_id=int(top_block_id),
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
                        self.frames.pop()
                        self.pc += 1
                        continue
                else:
                    if top.iter_now + 1 < top.iters_total:
                        top.iter_now += 1
                        self.pc = top.begin_idx + 1
                        continue
                    else:
                        self.frames.pop()
                        self.pc += 1
                        continue

            if t != "inst":
                self.pc += 1
                continue

            out = self._emit_normal_inst(n)
            self.pc += 1
            return out

        return None

    def take(self, n: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for _ in range(max(0, int(n))):
            inst = self.next_inst()
            if inst is None:
                break
            out.append(inst)
        return out
