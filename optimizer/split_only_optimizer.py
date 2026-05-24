"""
Split-only optimizer for VF operators.

This optimizer targets step-1 partitioning only:
  original single loop -> multiple top-level loops

Key features:
  - evaluates under the current simulator with strong mem_bar
  - allocates reusable mem_inter slots by lifetime
  - rejects or penalizes solutions that exceed UB capacity
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from dag_lib import OperatorDAG, DagNode
from partitioner import Partitioner, PartitionPlan
from main import infer_top_block_loop_bounds
from core.flatten import Flattener
from core.ifu import IFUUnroll
from core.idu import IDU
from core.ooo_factory import create_ooo_core
from core.param_db import ParamDB


VECTOR_BYTES_FP32 = 64 * 4
UB_LIMIT_BYTES = 256 * 1024


def _canonical_gelu_poly_body() -> List[Tuple[str, Tuple[str, ...], Tuple[str, ...]]]:
    # Canonicalized from cce_code/GeLU_poly.dsl.
    # We intentionally validate a normalized semantic skeleton here because
    # GeLU_poly.json is a long-lived hand-maintained trace used as an optimizer
    # source of truth. If it drifts, optimization can silently target the wrong
    # kernel semantics and still "look" plausible in cycle results.
    return [
        ("VLD", ("V0",), ("memA",)),
        ("VMULS", ("V1",), ("V0",)),
        ("VMULS", ("V2",), ("V0",)),
        ("VMINS", ("V2",), ("V2",)),
        ("VMAXS", ("V2",), ("V2",)),
        ("VMUL", ("V3",), ("V2", "V2")),
        ("VMULS", ("V4",), ("V3",)),
        ("VADDS", ("V4",), ("V4",)),
        ("VMUL", ("V4",), ("V4", "V3")),
        ("VADDS", ("V4",), ("V4",)),
        ("VMUL", ("V4",), ("V4", "V3")),
        ("VADDS", ("V4",), ("V4",)),
        ("VMUL", ("V4",), ("V4", "V3")),
        ("VADDS", ("V4",), ("V4",)),
        ("VMUL", ("V4",), ("V4", "V3")),
        ("VADDS", ("V4",), ("V4",)),
        ("VMUL", ("V4",), ("V4", "V2")),
        ("VADDS", ("V5",), ("V3",)),
        ("VMUL", ("V5",), ("V5", "V3")),
        ("VADDS", ("V5",), ("V5",)),
        ("VMUL", ("V5",), ("V5", "V3")),
        ("VADDS", ("V5",), ("V5",)),
        ("VMUL", ("V5",), ("V5", "V3")),
        ("VADDS", ("V5",), ("V5",)),
        ("VMUL", ("V5",), ("V5", "V3")),
        ("VADDS", ("V5",), ("V5",)),
        ("VDIV", ("V5",), ("V4", "V5")),
        ("VADDS", ("V5",), ("V5",)),
        ("VMUL", ("V6",), ("V5", "V1")),
        ("VST", ("memB",), ("V6",)),
    ]


def _validate_known_trace_semantics(trace_path: str) -> None:
    path = Path(trace_path)
    # Validate every GeLU_poly-like source, not just the canonical VFtest filename.
    # Historical snapshots (e.g. GeLU_poly_I96_baseline_trace.json) are also used
    # as optimizer inputs and can silently drift from the DSL ground truth.
    if "gelu_poly" not in path.stem.lower():
        return

    trace_obj = json.loads(path.read_text(encoding="utf-8-sig"))
    program = trace_obj.get("program", [])
    if len(program) != 1 or program[0].get("type") != "loop":
        raise ValueError(
            f"{path.name} failed GeLU semantic validation: expected a single top-level loop trace."
        )

    body = program[0].get("body", [])
    expected = _canonical_gelu_poly_body()
    if len(body) != len(expected):
        raise ValueError(
            f"{path.name} failed GeLU semantic validation: expected {len(expected)} body insts, got {len(body)}."
        )

    for idx, (inst, canonical) in enumerate(zip(body, expected)):
        actual = (
            str(inst.get("op")),
            tuple(str(x) for x in inst.get("dst", [])),
            tuple(str(x) for x in inst.get("src", [])),
        )
        if actual != canonical:
            raise ValueError(
                f"{path.name} failed GeLU semantic validation at body index {idx}: "
                f"expected {canonical}, got {actual}. "
                "Please refresh VFtest/GeLU_poly.json so it matches cce_code/GeLU_poly.dsl before optimizing."
            )


@dataclass(frozen=True)
class BoundaryValue:
    producer_id: int
    reg: str
    start_part: int
    end_part: int


def _load_remat_source(node: DagNode, reg: str) -> str | None:
    """
    Return the original memory source if this value can be cheaply re-materialized
    by reissuing a VLD in a later partition.

    We only allow this for direct VLD values coming from original input memory,
    not for intermediate mem_inter buffers.
    """
    if not node.is_load():
        return None
    if reg not in node.dst:
        return None
    for s in node.src:
        src_name = str(s)
        if src_name.startswith("mem") and not src_name.startswith("mem_inter"):
            return src_name
    return None


def build_boundary_values(
    plan: PartitionPlan,
) -> Tuple[List[BoundaryValue], Dict[Tuple[int, str], str], Dict[Tuple[int, str], str]]:
    dag = plan.dag
    node_to_part: Dict[int, int] = {}
    for p in plan.partitions:
        for nid in p.node_ids:
            node_to_part[nid] = p.id

    values: List[BoundaryValue] = []
    key_to_mem: Dict[Tuple[int, str], str] = {}
    key_to_reload_src: Dict[Tuple[int, str], str] = {}

    intervals: List[BoundaryValue] = []
    for p in plan.partitions:
        for nid in p.node_ids:
            node = dag.nodes[nid]
            for d in node.dst:
                consumer_parts = sorted(
                    {
                        node_to_part[succ]
                        for succ in node.successors
                        if node_to_part.get(succ, p.id) != p.id and d in dag.nodes[succ].src
                    }
                )
                if not consumer_parts:
                    continue
                remat_src = _load_remat_source(node, d)
                if remat_src is not None:
                    key_to_reload_src[(nid, d)] = remat_src
                    continue
                intervals.append(
                    BoundaryValue(
                        producer_id=nid,
                        reg=d,
                        start_part=p.id,
                        end_part=max(consumer_parts),
                    )
                )

    # Greedy interval coloring by earliest free slot.
    intervals.sort(key=lambda x: (x.start_part, x.end_part, x.producer_id, x.reg))
    slot_end: List[int] = []
    for item in intervals:
        slot_idx = None
        for idx, end_part in enumerate(slot_end):
            if end_part < item.start_part:
                slot_idx = idx
                break
        if slot_idx is None:
            slot_idx = len(slot_end)
            slot_end.append(item.end_part)
        else:
            slot_end[slot_idx] = item.end_part
        mem_name = f"mem_inter_slot{slot_idx}"
        values.append(item)
        key_to_mem[(item.producer_id, item.reg)] = mem_name

    return values, key_to_mem, key_to_reload_src


def generate_partitioned_trace_with_slot_reuse(
    plan: PartitionPlan,
    params: Dict[str, Any],
    dtype: str = "fp32",
) -> Tuple[Dict[str, Any], int, int]:
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
    global_rank = {nid: idx for idx, nid in enumerate(dag.topological_sort())}

    def op_class(node: DagNode) -> int:
        if node.is_load():
            return 0
        if node.is_store():
            return 2
        return 1

    def reorder(partition_nodes: List[int]) -> List[int]:
        node_set = set(partition_nodes)
        indegree = {nid: 0 for nid in partition_nodes}
        local_depth = {nid: 0 for nid in partition_nodes}
        for nid in partition_nodes:
            indegree[nid] = sum(1 for p in dag.nodes[nid].predecessors if p in node_set)
        for nid in partition_nodes:
            node = dag.nodes[nid]
            for succ in node.successors:
                if succ in node_set:
                    local_depth[succ] = max(local_depth[succ], local_depth[nid] + (1 if node.is_compute() else 0))
        ready = [nid for nid in partition_nodes if indegree[nid] == 0]
        ordered: List[int] = []
        while ready:
            ready.sort(
                key=lambda nid: (
                    op_class(dag.nodes[nid]),
                    local_depth[nid],
                    -len([s for s in dag.nodes[nid].successors if s in node_set]),
                    global_rank[nid],
                )
            )
            nid = ready.pop(0)
            ordered.append(nid)
            for succ in dag.nodes[nid].successors:
                if succ not in node_set:
                    continue
                indegree[succ] -= 1
                if indegree[succ] == 0:
                    ready.append(succ)
        return ordered if len(ordered) == len(partition_nodes) else list(partition_nodes)

    for partition in plan.partitions:
        ordered_node_ids = reorder(partition.node_ids)
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
            body.append({"type": "inst", "op": "VLD", "dst": [reg], "src": [mem_name]})
        for reg, (_pred_id, mem_name) in sorted(incoming.items()):
            body.append({"type": "inst", "op": "VLD", "dst": [reg], "src": [mem_name]})

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
                body.append({"type": "inst", "op": "VST", "dst": [mem_name], "src": [d]})
                outgoing_added.add((nid, d))

        program.append({"type": "loop", "iters": "I", "unroll": 1, "body": body})

    return {"dtype": dtype, "params": dict(params), "program": program}, slot_count, ub_bytes


class SplitOnlyOptimizer:
    def __init__(
        self,
        trace_path: str,
        trip_count: int,
        dtype: str = "fp32",
        cut_penalty_mode: str = "off",
        cut_penalty_scale: float = 1.0,
        ooo_model: str = "consumer-done",
    ):
        _validate_known_trace_semantics(trace_path)
        self.trace_path = trace_path
        self.trip_count = trip_count
        self.dag, self.meta = OperatorDAG.from_json_trace(trace_path)
        self.dtype = dtype or self.meta.get("dtype", "fp32")
        self.max_depth = self.dag.critical_path_length()
        self.db = ParamDB(base_dir=ROOT_DIR)
        self.uarch = dict(self.db.get_uarch())
        self.uarch["ooo_model"] = ooo_model
        self.cache: Dict[Tuple[int, ...], Tuple[float, int, int, int, Dict[str, Any]]] = {}
        self.cut_penalty_mode = cut_penalty_mode.strip().lower()
        self.cut_penalty_scale = float(cut_penalty_scale)

    def params_for_trip(self) -> Dict[str, Any]:
        params = copy.deepcopy(self.meta.get("params", {}))
        params["I"] = self.trip_count
        params.setdefault("U", 1)
        return params

    def suggest_seeds(self) -> List[List[int]]:
        if self.trip_count <= 16:
            base_pairs = [(6, 4), (5, 4), (8, 5), (4, 3)]
        elif self.trip_count <= 64:
            base_pairs = [(4, 3), (5, 3), (4, 2), (6, 4)]
        else:
            base_pairs = [(4, 2), (3, 2), (4, 3), (5, 3)]

        seeds = [[]]
        for serial_base, parallel_base in base_pairs:
            cuts = Partitioner(self.dag).suggest_cut_depths(
                single_chain_base=serial_base,
                parallel_chain_base=parallel_base,
            )
            seeds.append(cuts)

        # region-aware hand seeds around GeLU_poly structure
        for cuts in ([4], [4, 9], [4, 9, 14], [4, 8, 13], [5, 10, 15], [4, 7, 11, 15]):
            seeds.append([d for d in cuts if 0 < d < self.max_depth])

        uniq = []
        seen = set()
        for seed in seeds:
            t = tuple(sorted(set(seed)))
            if t not in seen:
                seen.add(t)
                uniq.append(list(t))
        return uniq

    def _simulate_trace(self, trace_obj: Dict[str, Any]) -> int:
        params = trace_obj.get("params", {}) or {}
        program = trace_obj.get("program", [])
        linear = Flattener(params).flatten(program)
        top_block_loop_bounds = infer_top_block_loop_bounds(program, params)
        ifu = IFUUnroll(linear, params)
        idu = IDU(
            self.uarch,
            self.db,
            params=params,
            loop_bounds=top_block_loop_bounds.get(0, []),
            total_top_blocks=len(top_block_loop_bounds),
            top_block_loop_bounds=top_block_loop_bounds,
        )
        ooo = create_ooo_core(self.uarch, self.db, dtype=trace_obj.get("dtype", self.dtype))
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

    def _compute_partition_penalty(self, plan: PartitionPlan) -> float:
        if self.cut_penalty_mode == "off":
            return 0.0

        compute_sizes: List[int] = []
        for p in plan.partitions:
            count = sum(1 for nid in p.node_ids if self.dag.nodes[nid].is_compute())
            compute_sizes.append(count)

        if not compute_sizes:
            return 0.0

        penalty = 0.0
        avg_size = sum(compute_sizes) / len(compute_sizes)

        # Old stronger penalty:
        # 1. every extra cut has a base cost
        # 2. tiny partitions are penalized heavily
        # 3. tiny tail fragments are penalized even more
        # 4. clearly unbalanced partitions get an extra penalty
        penalty += max(0, len(plan.partitions) - 1) * 12.0

        for idx, size in enumerate(compute_sizes):
            if size <= 2:
                penalty += 90.0
            elif size <= 4:
                penalty += 36.0
            elif size <= 6:
                penalty += 12.0

            if idx == len(compute_sizes) - 1:
                if size <= 3:
                    penalty += 120.0
                elif size <= 5:
                    penalty += 36.0

        for size in compute_sizes:
            if avg_size > 0 and size < 0.35 * avg_size:
                penalty += 8.0

        return penalty * self.cut_penalty_scale

    def evaluate(self, cuts: List[int]) -> Tuple[float, int, int, int, Dict[str, Any]]:
        key = tuple(sorted(set(cuts)))
        if key in self.cache:
            return self.cache[key]

        plan = Partitioner(self.dag).partition_by_cut_points(list(key))
        trace_obj, slot_count, ub_bytes = generate_partitioned_trace_with_slot_reuse(
            plan,
            params=self.params_for_trip(),
            dtype=self.dtype,
        )
        cut_penalty = self._compute_partition_penalty(plan)
        overflow = max(0, ub_bytes - UB_LIMIT_BYTES)
        if overflow > 0:
            cycles = 10**9
            score = cycles + overflow * 1000
        else:
            cycles = self._simulate_trace(trace_obj)
            # soft penalties only regularize cut structure; UB capacity itself remains a hard limit
            score = float(cycles + len(key) * 2 + cut_penalty)

        result = (score, cycles, slot_count, ub_bytes, trace_obj)
        self.cache[key] = result
        return result

    def neighbors(self, cuts: List[int]) -> List[List[int]]:
        current = sorted(set(cuts))
        cand = []
        existing = set(current)
        for d in range(1, self.max_depth):
            if d not in existing:
                cand.append(sorted(current + [d]))
        for i, d in enumerate(current):
            reduced = current[:i] + current[i + 1 :]
            cand.append(sorted(reduced))
            for delta in (-2, -1, 1, 2):
                nd = d + delta
                if 1 <= nd < self.max_depth and nd not in existing:
                    moved = list(current)
                    moved[i] = nd
                    cand.append(sorted(set(moved)))

        uniq = []
        seen = set()
        for item in cand:
            t = tuple(item)
            if t not in seen:
                seen.add(t)
                uniq.append(item)
        return uniq

    def hillclimb(self, seed: List[int]) -> Tuple[List[int], float, int, int, int, Dict[str, Any]]:
        best_cuts = sorted(set(seed))
        best_score, best_cycles, best_slots, best_ub, best_trace = self.evaluate(best_cuts)
        improved = True
        while improved:
            improved = False
            for nxt in self.neighbors(best_cuts):
                score, cycles, slots, ub, trace = self.evaluate(nxt)
                if score < best_score:
                    best_cuts, best_score, best_cycles, best_slots, best_ub, best_trace = (
                        nxt,
                        score,
                        cycles,
                        slots,
                        ub,
                        trace,
                    )
                    improved = True
                    break
        return best_cuts, best_score, best_cycles, best_slots, best_ub, best_trace

    def optimize(self) -> Tuple[List[int], int, int, int, Dict[str, Any]]:
        best = None
        for seed in self.suggest_seeds():
            result = self.hillclimb(seed)
            if best is None or result[1] < best[1]:
                best = result
                print(
                    f"[split-only][I={self.trip_count}] seed={seed} -> "
                    f"cycles={result[2]} cuts={result[0]} slots={result[3]} ub={result[4]}"
                )
        assert best is not None
        return best[0], best[2], best[3], best[4], best[5]


def main() -> None:
    parser = argparse.ArgumentParser(description="Split-only optimizer with UB-aware mem slot reuse")
    parser.add_argument("trace", help="Input trace path")
    parser.add_argument("--trip-count", type=int, required=True, help="Loop trip count I")
    parser.add_argument("--output", type=str, required=True, help="Output JSON path")
    parser.add_argument("--meta-out", type=str, default=None, help="Optional metadata JSON path")
    parser.add_argument(
        "--cut-penalty",
        choices=["off", "on"],
        default="off",
        help="Whether to apply an extra penalty to overly fine / tiny partitions",
    )
    parser.add_argument(
        "--cut-penalty-scale",
        type=float,
        choices=[0.25, 0.5, 1.0],
        default=1.0,
        help="Scale factor for the cut penalty when --cut-penalty=on",
    )
    parser.add_argument(
        "--ooo-model",
        choices=["classical-cpu-type", "consumer-done"],
        default="consumer-done",
        help="Choose which OoO register lifetime model to use during optimization",
    )
    args = parser.parse_args()

    opt = SplitOnlyOptimizer(
        args.trace,
        trip_count=args.trip_count,
        cut_penalty_mode=args.cut_penalty,
        cut_penalty_scale=args.cut_penalty_scale,
        ooo_model=args.ooo_model,
    )
    cuts, cycles, slots, ub_bytes, trace_obj = opt.optimize()

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(trace_obj, f, indent=2)

    if args.meta_out:
        meta = {
            "trace": args.trace,
            "trip_count": args.trip_count,
            "cut_penalty": args.cut_penalty,
            "cut_penalty_scale": args.cut_penalty_scale,
            "ooo_model": args.ooo_model,
            "best_cuts": cuts,
            "cycles": cycles,
            "slot_count": slots,
            "ub_bytes": ub_bytes,
            "ub_limit_bytes": UB_LIMIT_BYTES,
        }
        with open(args.meta_out, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    print(f"[split-only] best cycles: {cycles}")
    print(f"[split-only] best cuts:   {cuts}")
    print(f"[split-only] slots:       {slots}")
    print(f"[split-only] ub bytes:    {ub_bytes}")
    print(f"[split-only] output:      {args.output}")


if __name__ == "__main__":
    main()
