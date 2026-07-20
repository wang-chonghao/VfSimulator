"""
Stage-C optimizer (unroll=1) for VF operators.

This optimizer focuses only on Stage C in optimize_plan_v1:
  - loop_uncut
  - loop_cut_strict
  - loop_cut_loose

Key points:
  1. unroll is fixed to 1 in all generated traces
  2. instruction reorder uses a dual-issue-aware list scheduler
  3. split modes reuse existing partitioning + boundary materialization flow
  4. strict/loose differ by UB policy
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from dag_lib import DagNode, OperatorDAG
from partitioner import Partitioner, PartitionPlan
from split_only_optimizer import (
    UB_LIMIT_BYTES,
    VECTOR_BYTES_FP32,
    build_boundary_values,
)
from main import infer_top_block_loop_bounds
from core.flatten import Flattener
from core.ifu import IFUUnroll
from core.idu import IDU
from core.ooo_factory import create_ooo_core
from core.param_db import ParamDB


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _dump_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _dependency_delay(pdb: ParamDB, pred: DagNode, succ: DagNode, dtype: str) -> int:
    if pred.is_compute() and succ.is_compute():
        return max(1, int(pdb.get_forwarding_cycles(pred.op, succ.op, dtype)))
    if pred.is_load() and succ.is_compute():
        # startup from first VLD to compute issue
        startup = pdb.get_inst_param(succ.op, "pipeline_startup_cost", dtype=dtype, default=1)
        try:
            return max(1, int(startup))
        except Exception:
            return 1
    if pred.is_compute() and succ.is_store():
        drain = pdb.get_inst_param(pred.op, "pipeline_drain_cost", dtype=dtype, default=1)
        try:
            return max(1, int(drain))
        except Exception:
            return 1
    return 1


class DualIssueReorderer:
    """
    Topology-preserving list scheduler with coarse dual-issue awareness.

    It is not a cycle-accurate replacement of OoO simulation; instead, it
    generates a better static instruction order by approximating:
      - issue width
      - load/store port limits
      - EXU dispatch constraints
      - same-EXU initiation interval (II)
      - forwarding/startup/drain style dependency delays
    """

    def __init__(self, dag: OperatorDAG, pdb: ParamDB, uarch: Dict[str, Any], dtype: str = "fp32"):
        self.dag = dag
        self.pdb = pdb
        self.uarch = dict(uarch)
        self.dtype = dtype
        self.issue_ports = int(self.uarch.get("issue_ports", 2))
        self.load_ports = int(self.uarch.get("load_ports", 2))
        self.store_ports = int(self.uarch.get("store_ports", 1))
        self.topo_rank = {nid: i for i, nid in enumerate(self.dag.topological_sort())}
        self.tail_len = self._compute_tail_len()
        self.latency = self._compute_latency_cache()

    def _compute_latency_cache(self) -> Dict[int, int]:
        out: Dict[int, int] = {}
        for nid, node in self.dag.nodes.items():
            if not node.is_compute():
                out[nid] = 1
                continue
            lat = self.pdb.get_inst_param(node.op, "latency", dtype=self.dtype, default=1)
            try:
                out[nid] = max(1, int(lat))
            except Exception:
                out[nid] = 1
        return out

    def _compute_tail_len(self) -> Dict[int, int]:
        topo = self.dag.topological_sort()
        tail = {nid: 0 for nid in self.dag.nodes}
        for nid in reversed(topo):
            best = 0
            for succ in self.dag.nodes[nid].successors:
                w = 1 if self.dag.nodes[succ].is_compute() else 0
                best = max(best, w + tail[succ])
            tail[nid] = best
        return tail

    def _dispatch_mask(self, node: DagNode) -> str:
        if not node.is_compute():
            return "NA"
        mask = self.pdb.get_inst_param(node.op, "dispatch_exu", dtype=self.dtype, default="EXU01")
        return str(mask).strip().upper()

    def _can_place_compute(
        self,
        node: DagNode,
        cycle: int,
        last_issue_by_exu: Dict[int, Tuple[int, str]],
        used_exu: Set[int],
    ) -> Optional[int]:
        mask = self._dispatch_mask(node)
        candidates: List[int]
        if mask in ("EXU0_ONLY", "EXU0"):
            candidates = [0]
        elif mask in ("EXU1_ONLY", "EXU1"):
            candidates = [1]
        else:
            candidates = [0, 1]

        for exu in candidates:
            if exu in used_exu:
                continue
            prev = last_issue_by_exu.get(exu)
            if prev is None:
                return exu
            prev_cycle, prev_op = prev
            ii = max(1, int(self.pdb.get_ii(prev_op, node.op, self.dtype)))
            if cycle - prev_cycle >= ii:
                return exu
        return None

    def reorder_nodes(self, node_ids: List[int]) -> List[int]:
        node_set = set(node_ids)
        indegree: Dict[int, int] = {}
        for nid in node_ids:
            indegree[nid] = sum(1 for p in self.dag.nodes[nid].predecessors if p in node_set)

        earliest: Dict[int, int] = {nid: 0 for nid in node_ids}
        ready: Set[int] = set(nid for nid in node_ids if indegree[nid] == 0)
        scheduled_cycle: Dict[int, int] = {}
        last_issue_by_exu: Dict[int, Tuple[int, str]] = {}
        cycle = 0

        def node_score(nid: int, chosen: List[int]) -> Tuple[float, int]:
            node = self.dag.nodes[nid]
            critical = float(self.tail_len.get(nid, 0))
            op_bias = 2.0 if node.is_compute() else (1.0 if node.is_load() else 0.2)
            hi_lat = 0.15 * float(self.latency.get(nid, 1))
            fanout = 0.2 * float(len(node.successors))
            live_pressure = 0.1 * float(len(node.dst))
            coissue = 0.0
            if chosen:
                first = self.dag.nodes[chosen[0]]
                if first.is_compute() and node.is_compute():
                    if self._dispatch_mask(first) != "EXU0_ONLY" and self._dispatch_mask(node) != "EXU0_ONLY":
                        coissue += 0.8
                if first.is_load() and node.is_compute():
                    coissue += 0.5
                if first.is_compute() and node.is_load():
                    coissue += 0.3
            return (critical + op_bias + hi_lat + fanout + coissue - live_pressure, -self.topo_rank.get(nid, nid))

        while len(scheduled_cycle) < len(node_ids):
            issuable = [nid for nid in ready if earliest[nid] <= cycle]
            issued: List[int] = []
            used_exu: Set[int] = set()
            load_cnt = 0
            store_cnt = 0

            for _slot in range(max(1, self.issue_ports)):
                if not issuable:
                    break
                issuable.sort(key=lambda nid: node_score(nid, issued), reverse=True)
                picked = None
                picked_exu: Optional[int] = None
                for nid in issuable:
                    node = self.dag.nodes[nid]
                    if node.is_load():
                        if load_cnt >= self.load_ports:
                            continue
                        picked = nid
                        break
                    if node.is_store():
                        if store_cnt >= self.store_ports:
                            continue
                        picked = nid
                        break
                    exu = self._can_place_compute(node, cycle, last_issue_by_exu, used_exu)
                    if exu is None:
                        continue
                    picked = nid
                    picked_exu = exu
                    break

                if picked is None:
                    break

                node = self.dag.nodes[picked]
                issued.append(picked)
                issuable.remove(picked)
                if node.is_load():
                    load_cnt += 1
                elif node.is_store():
                    store_cnt += 1
                else:
                    assert picked_exu is not None
                    used_exu.add(picked_exu)
                    last_issue_by_exu[picked_exu] = (cycle, node.op)

            if not issued:
                cycle += 1
                continue

            for nid in issued:
                scheduled_cycle[nid] = cycle
                ready.remove(nid)
                for succ in self.dag.nodes[nid].successors:
                    if succ not in node_set:
                        continue
                    indegree[succ] -= 1
                    delay = _dependency_delay(self.pdb, self.dag.nodes[nid], self.dag.nodes[succ], self.dtype)
                    earliest[succ] = max(earliest[succ], cycle + delay)
                    if indegree[succ] == 0:
                        ready.add(succ)
            cycle += 1

        ordered = sorted(node_ids, key=lambda nid: (scheduled_cycle.get(nid, 10**9), self.topo_rank.get(nid, nid)))
        return ordered if len(ordered) == len(node_ids) else list(node_ids)


def _generate_trace_with_reorder(
    plan: PartitionPlan,
    params: Dict[str, Any],
    dtype: str,
    reorderer: DualIssueReorderer,
) -> Tuple[Dict[str, Any], int, int, List[Any]]:
    dag = plan.dag
    node_to_part: Dict[int, int] = {}
    for p in plan.partitions:
        for nid in p.node_ids:
            node_to_part[nid] = p.id

    boundary_values, mem_map, reload_map = build_boundary_values(plan)
    slot_count = len(set(mem_map.values()))
    iters = int(params.get("I", 1))
    ub_bytes = slot_count * iters * VECTOR_BYTES_FP32

    program: List[Dict[str, Any]] = []

    for partition in plan.partitions:
        ordered_node_ids = reorderer.reorder_nodes(partition.node_ids)
        body: List[Dict[str, Any]] = []

        incoming: Dict[str, Tuple[int, str]] = {}
        incoming_reload: Dict[str, Tuple[int, str]] = {}
        for nid in ordered_node_ids:
            node = dag.nodes[nid]
            for pred_id in node.predecessors:
                if node_to_part[pred_id] == partition.id:
                    continue
                pred_node = dag.nodes[pred_id]
                for d in pred_node.dst:
                    if d not in node.src:
                        continue
                    if (pred_id, d) in reload_map:
                        incoming_reload[d] = (pred_id, reload_map[(pred_id, d)])
                    elif (pred_id, d) in mem_map:
                        incoming[d] = (pred_id, mem_map[(pred_id, d)])

        for reg, (_pred_id, mem_name) in sorted(incoming_reload.items()):
            body.append({"type": "inst", "op": "VLDS", "dst": [reg], "src": [mem_name]})
        for reg, (_pred_id, mem_name) in sorted(incoming.items()):
            body.append({"type": "inst", "op": "VLDS", "dst": [reg], "src": [mem_name]})

        for nid in ordered_node_ids:
            node = dag.nodes[nid]
            if node.is_load():
                if any(str(s).startswith("mem") and not str(s).startswith("mem_inter") for s in node.src):
                    body.append({"type": "inst", "op": node.op, "dst": list(node.dst), "src": list(node.src)})
            elif node.is_store():
                if any(str(d).startswith("mem") and not str(d).startswith("mem_inter") for d in node.dst):
                    body.append({"type": "inst", "op": node.op, "dst": list(node.dst), "src": list(node.src)})
            else:
                body.append({"type": "inst", "op": node.op, "dst": list(node.dst), "src": list(node.src)})

        outgoing_added = set()
        for nid in ordered_node_ids:
            node = dag.nodes[nid]
            for d in node.dst:
                mem_name = mem_map.get((nid, d))
                if mem_name is None or (nid, d) in outgoing_added:
                    continue
                body.append({"type": "inst", "op": "VSTS", "dst": [mem_name], "src": [d]})
                outgoing_added.add((nid, d))

        program.append({"type": "loop", "iters": "I", "unroll": 1, "body": body})

    trace = {"dtype": dtype, "params": dict(params), "program": program}
    return trace, slot_count, ub_bytes, boundary_values


def _simulate_trace(trace_obj: Dict[str, Any], pdb: ParamDB, ooo_model: str, dtype: str) -> int:
    params = trace_obj.get("params", {}) or {}
    program = trace_obj.get("program", [])
    linear = Flattener(params).flatten(program)
    top_block_loop_bounds = infer_top_block_loop_bounds(program, params)
    ifu = IFUUnroll(linear, params, pdb=pdb, dtype=dtype)
    uarch = dict(pdb.get_uarch())
    uarch["ooo_model"] = ooo_model

    idu = IDU(
        uarch,
        pdb,
        params=params,
        loop_bounds=top_block_loop_bounds.get(0, []),
        total_top_blocks=len(top_block_loop_bounds),
        top_block_loop_bounds=top_block_loop_bounds,
        dtype=dtype,
    )
    ooo = create_ooo_core(uarch, pdb, dtype=dtype)
    cycle = 0
    max_cycles = int(params.get("max_cycles", 2_000_000))
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
        for inst in idu.dispatch(cycle, ooo):
            ooo.accept(inst)
        ooo.step()
        if (
            ifu.done()
            and idu.empty()
            and len(ooo.SHQ) == 0
            and len(ooo.LSQ) == 0
            and len(ooo.ROB) == 0
        ):
            break
        cycle += 1
    return cycle


@dataclass
class EvalResult:
    mode: str
    cuts: List[int]
    score: float
    vf_end: int
    slot_count: int
    ub_bytes: int
    trace_obj: Dict[str, Any]
    feasible: bool
    reason: str = ""


class StageCOptimizer:
    def __init__(
        self,
        trace_path: str,
        trip_count: int,
        dtype: str = "fp32",
        ooo_model: str = "consumer-done",
        lambda_cut: float = 2.0,
        lambda_small_chain: float = 24.0,
        min_chain_len: int = 3,
    ):
        self.trace_path = trace_path
        self.trace_obj = _load_json(trace_path)
        self.trace_obj.setdefault("params", {})
        self.trace_obj["params"]["I"] = int(trip_count)
        self.trace_obj["params"]["U"] = 1
        self.trip_count = int(trip_count)
        self.dtype = dtype
        self.ooo_model = ooo_model
        self.lambda_cut = float(lambda_cut)
        self.lambda_small_chain = float(lambda_small_chain)
        self.min_chain_len = int(min_chain_len)

        self.dag, meta = OperatorDAG.from_json_trace(trace_path)
        self.meta = meta
        self.pdb = ParamDB(base_dir=ROOT_DIR)
        self.uarch = self.pdb.get_uarch()
        self.reorderer = DualIssueReorderer(self.dag, self.pdb, self.uarch, dtype=self.dtype)
        self.max_depth = self.dag.critical_path_length()
        self.cache: Dict[Tuple[str, Tuple[int, ...]], EvalResult] = {}

    def _params(self) -> Dict[str, Any]:
        params = copy.deepcopy(self.trace_obj.get("params", {}))
        params["I"] = self.trip_count
        params["U"] = 1
        return params

    def _partition_compute_chain_len(self, partition_nodes: List[int]) -> int:
        """
        Longest compute dependency-chain length (node count) inside one partition.
        Only compute->compute edges are considered for chain growth.
        """
        node_set = set(partition_nodes)
        topo = sorted(partition_nodes, key=lambda nid: self.reorderer.topo_rank.get(nid, nid))
        dp: Dict[int, int] = {}
        best = 0
        for nid in topo:
            node = self.dag.nodes[nid]
            if not node.is_compute():
                dp[nid] = 0
                continue
            cur = 1
            for pred in node.predecessors:
                if pred in node_set and self.dag.nodes[pred].is_compute():
                    cur = max(cur, dp.get(pred, 0) + 1)
            dp[nid] = cur
            best = max(best, cur)
        return best

    def _partition_penalty(self, plan: PartitionPlan, cut_count: int) -> float:
        penalty = self.lambda_cut * cut_count
        if self.min_chain_len <= 0:
            return penalty
        for p in plan.partitions:
            chain_len = self._partition_compute_chain_len(p.node_ids)
            if chain_len <= self.min_chain_len:
                # Example: threshold=3 => chain_len=3/2 are both penalized.
                penalty += self.lambda_small_chain * float(self.min_chain_len - chain_len + 1)
        return penalty

    @staticmethod
    def _active_on_boundaries(intervals: List[Tuple[int, int]], num_parts: int) -> List[int]:
        bcnt = max(0, num_parts - 1)
        active = [0 for _ in range(bcnt)]
        for s, e in intervals:
            for b in range(bcnt):
                if s <= b < e:
                    active[b] += 1
        return active

    def _baseline_value_intervals(self, plan: PartitionPlan) -> List[Tuple[int, int]]:
        dag = plan.dag
        node_to_part: Dict[int, int] = {}
        for p in plan.partitions:
            for nid in p.node_ids:
                node_to_part[nid] = p.id

        intervals: List[Tuple[int, int]] = []
        for nid, node in dag.nodes.items():
            if not node.dst:
                continue
            start = node_to_part.get(nid, 0)
            for d in node.dst:
                ds = str(d)
                if not ds.startswith("V"):
                    continue
                end = start
                for succ in node.successors:
                    succ_node = dag.nodes[succ]
                    if ds in succ_node.src:
                        end = max(end, node_to_part.get(succ, start))
                intervals.append((start, end))
        return intervals

    def _strict_feasible(self, plan: PartitionPlan, boundary_values: List[Any]) -> Tuple[bool, str]:
        num_parts = len(plan.partitions)
        if num_parts <= 1:
            return True, ""

        baseline_intervals = self._baseline_value_intervals(plan)
        baseline_active = self._active_on_boundaries(baseline_intervals, num_parts)
        base_slots = max(baseline_active) if baseline_active else 0

        extra_intervals: List[Tuple[int, int]] = []
        for item in boundary_values:
            s = int(getattr(item, "start_part", 0))
            e = int(getattr(item, "end_part", s))
            extra_intervals.append((s, e))
        extra_active = self._active_on_boundaries(extra_intervals, num_parts)

        for b in range(len(extra_active)):
            free_slots = max(0, base_slots - baseline_active[b])
            if extra_active[b] > free_slots:
                return (
                    False,
                    f"strict_boundary_overflow:b{b}:extra={extra_active[b]},free={free_slots},base_slots={base_slots}",
                )

        return True, ""

    def _evaluate(self, mode: str, cuts: List[int]) -> EvalResult:
        key = (mode, tuple(sorted(set(cuts))))
        if key in self.cache:
            return self.cache[key]

        plan = Partitioner(self.dag).partition_by_cut_points(list(key[1]))
        trace, slot_count, ub_bytes, boundary_values = _generate_trace_with_reorder(
            plan=plan,
            params=self._params(),
            dtype=self.dtype,
            reorderer=self.reorderer,
        )

        if mode == "loop_cut_strict":
            ok, reason = self._strict_feasible(plan, boundary_values)
            if not ok:
                res = EvalResult(
                    mode,
                    list(key[1]),
                    float(10**12 + slot_count * 10**7),
                    10**9,
                    slot_count,
                    ub_bytes,
                    trace,
                    False,
                    reason,
                )
                self.cache[key] = res
                return res

        if mode == "loop_cut_loose":
            if ub_bytes > UB_LIMIT_BYTES:
                overflow = ub_bytes - UB_LIMIT_BYTES
                res = EvalResult(
                    mode,
                    list(key[1]),
                    float(10**11 + overflow),
                    10**9,
                    slot_count,
                    ub_bytes,
                    trace,
                    False,
                    f"ub_overflow:{overflow}",
                )
                self.cache[key] = res
                return res

        if mode == "loop_uncut":
            # uncut mode should not have manual cuts
            if key[1]:
                res = EvalResult(
                    mode,
                    list(key[1]),
                    float(10**12),
                    10**9,
                    slot_count,
                    ub_bytes,
                    trace,
                    False,
                    "loop_uncut_requires_empty_cuts",
                )
                self.cache[key] = res
                return res

        vf_end = _simulate_trace(trace, self.pdb, self.ooo_model, self.dtype)
        score = float(vf_end + self._partition_penalty(plan, cut_count=len(key[1])))
        res = EvalResult(mode, list(key[1]), score, vf_end, slot_count, ub_bytes, trace, True, "")
        self.cache[key] = res
        return res

    def _seed_cuts(self) -> List[List[int]]:
        seeds = [[]]
        base_pairs = [(4, 2), (4, 3), (5, 3), (6, 4)]
        part = Partitioner(self.dag)
        for s_base, p_base in base_pairs:
            seeds.append(part.suggest_cut_depths(single_chain_base=s_base, parallel_chain_base=p_base))

        if self.max_depth >= 3:
            seeds.extend([[2], [3], [4], [2, 5], [3, 6]])
        uniq = []
        seen = set()
        for s in seeds:
            t = tuple(sorted(set(d for d in s if 0 < d < self.max_depth)))
            if t not in seen:
                seen.add(t)
                uniq.append(list(t))
        return uniq

    def _neighbors(self, cuts: List[int]) -> List[List[int]]:
        cur = sorted(set(cuts))
        out: List[List[int]] = []
        ex = set(cur)
        for d in range(1, self.max_depth):
            if d not in ex:
                out.append(sorted(cur + [d]))
        for i, d in enumerate(cur):
            out.append(sorted(cur[:i] + cur[i + 1 :]))
            for delta in (-2, -1, 1, 2):
                nd = d + delta
                if 1 <= nd < self.max_depth and nd not in ex:
                    nxt = list(cur)
                    nxt[i] = nd
                    out.append(sorted(set(nxt)))
        uniq = []
        seen = set()
        for item in out:
            t = tuple(item)
            if t not in seen:
                seen.add(t)
                uniq.append(item)
        return uniq

    def _local_polish(self, mode: str, start: EvalResult) -> EvalResult:
        cur = start
        improved = True
        while improved:
            improved = False
            for nxt in self._neighbors(cur.cuts):
                cand = self._evaluate(mode, nxt)
                if cand.feasible and cand.score < cur.score:
                    cur = cand
                    improved = True
                    break
        return cur

    def _beam_optimize_mode(self, mode: str, beam_width: int, beam_rounds: int) -> EvalResult:
        if mode == "loop_uncut":
            return self._evaluate(mode, [])

        frontier: List[Tuple[int, ...]] = [tuple(seed) for seed in self._seed_cuts()]
        seen: Set[Tuple[int, ...]] = set(frontier)

        def best_k(keys: List[Tuple[int, ...]], k: int) -> List[Tuple[int, ...]]:
            scored = []
            for kk in keys:
                ev = self._evaluate(mode, list(kk))
                scored.append((ev.score, kk))
            scored.sort(key=lambda x: x[0])
            return [kk for _s, kk in scored[: max(1, k)]]

        frontier = best_k(frontier, beam_width)
        for _round in range(max(1, beam_rounds)):
            candidates: List[Tuple[int, ...]] = list(frontier)
            for key in frontier:
                for nxt in self._neighbors(list(key)):
                    nt = tuple(sorted(set(nxt)))
                    if nt in seen:
                        continue
                    seen.add(nt)
                    candidates.append(nt)
            frontier = best_k(candidates, beam_width)

        best: Optional[EvalResult] = None
        for key in frontier:
            polished = self._local_polish(mode, self._evaluate(mode, list(key)))
            if not polished.feasible:
                continue
            if best is None or polished.score < best.score:
                best = polished
        if best is None:
            # fallback to best (possibly infeasible) from frontier for transparent reporting
            best = self._evaluate(mode, list(frontier[0]))
        assert best is not None
        return best

    def run(self, loop_opt_mode: str, beam_width: int, beam_rounds: int, near_margin: float) -> Dict[str, Any]:
        modes = [loop_opt_mode]
        if loop_opt_mode == "all":
            modes = ["loop_uncut", "loop_cut_strict", "loop_cut_loose"]

        mode_results: Dict[str, Any] = {}
        best_global: Optional[EvalResult] = None
        for mode in modes:
            res = self._beam_optimize_mode(mode, beam_width=beam_width, beam_rounds=beam_rounds)
            mode_results[mode] = {
                "cuts": res.cuts,
                "score": res.score,
                "vf_end": res.vf_end,
                "slot_count": res.slot_count,
                "ub_bytes": res.ub_bytes,
                "feasible": res.feasible,
                "reason": res.reason,
            }
            if (best_global is None) or (res.feasible and res.score < best_global.score):
                best_global = res

        assert best_global is not None
        near_modes: List[str] = []
        for mode, m in mode_results.items():
            if not m.get("feasible", False):
                continue
            s = float(m["score"])
            if s <= best_global.score * (1.0 + max(0.0, near_margin)):
                near_modes.append(mode)

        return {
            "trace": self.trace_path,
            "trip_count": self.trip_count,
            "unroll_fixed": 1,
            "ooo_model": self.ooo_model,
            "loop_opt_mode": loop_opt_mode,
            "search": {
                "beam_width": beam_width,
                "beam_rounds": beam_rounds,
                "near_margin": near_margin,
            },
            "mode_results": mode_results,
            "best_mode": best_global.mode,
            "best_cuts": best_global.cuts,
            "best_score": best_global.score,
            "best_vf_end": best_global.vf_end,
            "best_slot_count": best_global.slot_count,
            "best_ub_bytes": best_global.ub_bytes,
            "near_modes": near_modes,
            "best_trace_obj": best_global.trace_obj,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage-C optimizer for unroll=1 loop optimization")
    ap.add_argument("trace", help="Input trace path")
    ap.add_argument("--trip-count", type=int, required=True, help="Loop trip count I")
    ap.add_argument(
        "--loop-opt-mode",
        choices=["loop_uncut", "loop_cut_strict", "loop_cut_loose", "all"],
        default="all",
        help="Optimization strategy/mode",
    )
    ap.add_argument(
        "--ooo-model",
        choices=["classical-cpu-type", "consumer-done"],
        default="consumer-done",
        help="OoO model used by simulator evaluation",
    )
    ap.add_argument("--dtype", default="fp32")
    ap.add_argument("--lambda-cut", type=float, default=2.0)
    ap.add_argument("--lambda-small-chain", type=float, default=24.0)
    ap.add_argument(
        "--min-chain-len",
        type=int,
        default=3,
        help="penalize partitions whose longest compute dependency-chain length <= this value",
    )
    ap.add_argument("--beam-width", type=int, default=6)
    ap.add_argument("--beam-rounds", type=int, default=3)
    ap.add_argument(
        "--near-margin",
        type=float,
        default=0.03,
        help="mark modes within (1+near_margin)*best_score as near-best",
    )
    ap.add_argument("--output-dir", required=True, help="Output directory")
    args = ap.parse_args()

    trace_path = args.trace
    if not os.path.isabs(trace_path):
        trace_path = os.path.abspath(os.path.join(ROOT_DIR, trace_path))
    out_dir = args.output_dir
    if not os.path.isabs(out_dir):
        out_dir = os.path.abspath(os.path.join(ROOT_DIR, out_dir))
    os.makedirs(out_dir, exist_ok=True)

    opt = StageCOptimizer(
        trace_path=trace_path,
        trip_count=args.trip_count,
        dtype=args.dtype,
        ooo_model=args.ooo_model,
        lambda_cut=args.lambda_cut,
        lambda_small_chain=args.lambda_small_chain,
        min_chain_len=args.min_chain_len,
    )
    result = opt.run(
        args.loop_opt_mode,
        beam_width=max(1, int(args.beam_width)),
        beam_rounds=max(1, int(args.beam_rounds)),
        near_margin=max(0.0, float(args.near_margin)),
    )
    best_trace = result.pop("best_trace_obj")

    summary_path = os.path.join(out_dir, "summary.json")
    trace_path_out = os.path.join(out_dir, "best_trace.json")
    _dump_json(summary_path, result)
    _dump_json(trace_path_out, best_trace)

    print(f"[stage-c] best mode: {result['best_mode']}")
    print(f"[stage-c] best vf_end: {result['best_vf_end']}")
    print(f"[stage-c] best cuts: {result['best_cuts']}")
    print(f"[stage-c] summary: {summary_path}")
    print(f"[stage-c] trace:   {trace_path_out}")


if __name__ == "__main__":
    main()
