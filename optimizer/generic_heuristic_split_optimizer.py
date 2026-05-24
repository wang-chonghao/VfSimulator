"""
Generic heuristic split-only optimizer for VF operators.

This optimizer reuses the split-only simulator / UB-aware evaluation flow,
but removes GeLU_poly-specific warm-start cut seeds. Instead, it derives
candidate cut depths directly from DAG structure:

  - compute-depth width transitions
  - reverse-depth / tail pressure
  - high-latency op locations
  - estimated cross-boundary live-out pressure

The goal is to provide a more operator-agnostic heuristic entry point while
keeping the current strong mem_bar + UB-capacity model intact.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from dag_lib import OperatorDAG
from partitioner import Partitioner
from split_only_optimizer import (
    SplitOnlyOptimizer,
    UB_LIMIT_BYTES,
)


HIGH_LATENCY_OPS = {"VDIV", "VEXP"}


class GenericHeuristicSplitOptimizer(SplitOnlyOptimizer):
    """
    A generic heuristic variant of the split-only optimizer.

    Differences vs. SplitOnlyOptimizer:
      - no GeLU_poly hand-written cut seeds
      - seed generation is based on DAG-derived structural features
      - still uses the same simulator-facing evaluation and penalties
    """

    def __init__(
        self,
        trace_path: str,
        trip_count: int,
        dtype: str = "fp32",
        cut_penalty_mode: str = "off",
        cut_penalty_scale: float = 1.0,
        ooo_model: str = "consumer-done",
    ):
        super().__init__(
            trace_path=trace_path,
            trip_count=trip_count,
            dtype=dtype,
            cut_penalty_mode=cut_penalty_mode,
            cut_penalty_scale=cut_penalty_scale,
            ooo_model=ooo_model,
        )
        self.partitioner = Partitioner(self.dag)
        self.topo = self.dag.topological_sort()
        self.compute_depth = self.partitioner._get_compute_depths(self.topo)
        self.width_by_depth = self.partitioner._get_compute_widths(self.compute_depth)
        self.reverse_compute_depth = self._compute_reverse_compute_depth()

    def _compute_reverse_compute_depth(self) -> Dict[int, int]:
        reverse_depth = {nid: 0 for nid in self.dag.nodes}
        for nid in reversed(self.topo):
            node = self.dag.nodes[nid]
            best = 0
            for succ in node.successors:
                succ_node = self.dag.nodes[succ]
                contrib = reverse_depth[succ] + (1 if succ_node.is_compute() else 0)
                if contrib > best:
                    best = contrib
            reverse_depth[nid] = best
        return reverse_depth

    def _compute_depth_op_stats(self) -> Dict[int, Dict[str, int]]:
        stats: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for nid, node in self.dag.nodes.items():
            if not node.is_compute():
                continue
            d = self.compute_depth[nid]
            stats[d]["compute"] += 1
            if node.op in HIGH_LATENCY_OPS:
                stats[d]["high_latency"] += 1
        return stats

    def _estimate_cut_liveout(self, cut_depth: int) -> int:
        regs: Set[str] = set()
        for nid, node in self.dag.nodes.items():
            if not node.is_compute():
                continue
            src_side = self.compute_depth[nid] < cut_depth
            if not src_side:
                continue
            for succ in node.successors:
                succ_node = self.dag.nodes[succ]
                if not succ_node.is_compute():
                    succ_depth = self.compute_depth.get(succ, self.compute_depth[nid])
                else:
                    succ_depth = self.compute_depth[succ]
                if succ_depth >= cut_depth:
                    for d in node.dst:
                        if d in succ_node.src:
                            regs.add(d)
        return len(regs)

    def _score_candidate_depths(self) -> List[int]:
        if not self.width_by_depth:
            return []

        depth_stats = self._compute_depth_op_stats()
        max_depth = max(self.width_by_depth.keys())
        scored: List[Tuple[float, int]] = []

        for d in range(1, max_depth):
            width_prev = self.width_by_depth.get(d - 1, 0)
            width_cur = self.width_by_depth.get(d, 0)
            width_next = self.width_by_depth.get(d + 1, 0)

            prev_parallel = width_prev >= 2
            cur_parallel = width_cur >= 2
            next_parallel = width_next >= 2
            transition = int(prev_parallel != cur_parallel) + int(cur_parallel != next_parallel)
            width_jump = abs(width_cur - width_prev) + abs(width_next - width_cur)

            high_latency = (
                depth_stats[d - 1].get("high_latency", 0)
                + depth_stats[d].get("high_latency", 0)
                + depth_stats[d + 1].get("high_latency", 0)
            )
            liveout = self._estimate_cut_liveout(d)

            # Favor cuts that:
            #   - sit near serial/parallel region transitions
            #   - align with width changes
            #   - help isolate high-latency tail regions
            #   - are not too close to input/output extremes
            #   - do not introduce too many cross-boundary values
            center_balance = min(d, max_depth - d) / max(1, max_depth)
            score = (
                transition * 8.0
                + width_jump * 1.5
                + high_latency * 3.0
                + center_balance * 2.0
                - liveout * 1.5
            )
            scored.append((score, d))

        scored.sort(key=lambda x: (-x[0], x[1]))
        return [d for _score, d in scored]

    def _region_periodic_seeds(self, candidate_depths: List[int]) -> List[List[int]]:
        if not self.width_by_depth:
            return []

        max_depth = max(self.width_by_depth.keys())
        transition_depths = sorted(
            d
            for d in candidate_depths[: min(6, len(candidate_depths))]
            if 0 < d < max_depth
        )

        boundaries = [0] + transition_depths + [max_depth + 1]
        seeds: List[List[int]] = []

        for shrink in (0, 1):
            cuts: Set[int] = set()
            for start, end in zip(boundaries[:-1], boundaries[1:]):
                # classify region by average width
                region_widths = [self.width_by_depth.get(dep, 0) for dep in range(start, end)]
                avg_width = sum(region_widths) / max(1, len(region_widths))
                if self.trip_count <= 16:
                    serial_base = 6
                    parallel_base = 4
                elif self.trip_count <= 64:
                    serial_base = 4
                    parallel_base = 3
                else:
                    serial_base = 4
                    parallel_base = 2

                base = parallel_base if avg_width >= 2.0 else serial_base
                base = max(2, base - shrink)
                self.partitioner._add_periodic_cuts(cuts, start, end, base)
            if cuts:
                seeds.append(sorted(c for c in cuts if 0 < c <= max_depth))
        return seeds

    def suggest_seeds(self) -> List[List[int]]:
        base_pairs: List[Tuple[int, int]]
        if self.trip_count <= 16:
            base_pairs = [(6, 4), (5, 4), (8, 5), (4, 3)]
        elif self.trip_count <= 64:
            base_pairs = [(4, 3), (5, 3), (4, 2), (6, 4)]
        else:
            base_pairs = [(4, 2), (3, 2), (4, 3), (5, 3)]

        seeds: List[List[int]] = [[]]
        for serial_base, parallel_base in base_pairs:
            cuts = self.partitioner.suggest_cut_depths(
                single_chain_base=serial_base,
                parallel_chain_base=parallel_base,
            )
            seeds.append(cuts)

        candidate_depths = self._score_candidate_depths()

        # Single-depth and short multi-depth seeds from top structural candidates.
        top = candidate_depths[: min(8, len(candidate_depths))]
        for d in top[:5]:
            seeds.append([d])
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                if top[j] - top[i] >= 2:
                    seeds.append([top[i], top[j]])
                if len(seeds) >= 18:
                    break
            if len(seeds) >= 18:
                break

        if len(top) >= 3:
            seeds.append(sorted(top[:3]))
        if len(top) >= 4:
            seeds.append(sorted(top[:4:2]))

        # Region-aware periodic seeds inferred from automatic transitions.
        seeds.extend(self._region_periodic_seeds(candidate_depths))

        uniq: List[List[int]] = []
        seen = set()
        for seed in seeds:
            t = tuple(sorted(set(d for d in seed if 0 < d < self.max_depth)))
            if t not in seen:
                seen.add(t)
                uniq.append(list(t))
        return uniq


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic heuristic split-only optimizer")
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

    opt = GenericHeuristicSplitOptimizer(
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
            "heuristic": "generic_dag_features",
        }
        with open(args.meta_out, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    print(f"[generic-split] best cycles: {cycles}")
    print(f"[generic-split] best cuts:   {cuts}")
    print(f"[generic-split] slots:       {slots}")
    print(f"[generic-split] ub bytes:    {ub_bytes}")
    print(f"[generic-split] output:      {args.output}")


if __name__ == "__main__":
    main()
