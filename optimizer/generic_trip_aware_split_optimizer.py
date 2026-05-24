"""
Trip-count-aware generic split-only optimizer for VF operators.

This variant keeps the existing generic DAG-feature heuristic and hill-climb
flow, but adjusts seed generation with a weak trip-count-aware granularity
prior:

  - trip count only biases the initial seed distribution
  - no operator-name-specific hard-coded cuts are used
  - final selection still relies on simulator evaluation + hill-climb

The goal is to improve robustness on operators whose sweet spot drifts with
trip count, while keeping the strategy generic and explainable.
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

from partitioner import Partitioner
from split_only_optimizer import SplitOnlyOptimizer, UB_LIMIT_BYTES


HIGH_LATENCY_OPS = {"VDIV", "VEXP"}


class GenericTripAwareSplitOptimizer(SplitOnlyOptimizer):
    """
    A generic heuristic split optimizer with trip-count-aware seed generation.

    Compared with GenericHeuristicSplitOptimizer:
      - still generic, no operator-specific cut tables
      - uses trip count only as a weak prior on coarse/medium/fine granularity
      - keeps the same simulator-backed evaluation and hill-climb
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
            if self.compute_depth[nid] >= cut_depth:
                continue
            for succ in node.successors:
                succ_node = self.dag.nodes[succ]
                succ_depth = self.compute_depth.get(succ, self.compute_depth[nid])
                if succ_node.is_compute():
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

    def _trip_grain_pairs(self) -> List[Tuple[int, int]]:
        """
        Return coarse/medium/fine (serial_base, parallel_base) priors.

        This is intentionally weak and broad:
          - small trip counts bias toward coarser seeds
          - larger trip counts bias toward finer seeds
          - all regimes keep coarse/medium/fine alternatives alive
        """
        if self.trip_count <= 8:
            return [(8, 5), (6, 4), (5, 4)]
        if self.trip_count <= 16:
            return [(7, 5), (6, 4), (5, 4)]
        if self.trip_count <= 32:
            return [(6, 4), (5, 4), (4, 3)]
        if self.trip_count <= 64:
            return [(5, 4), (4, 3), (4, 2)]
        return [(5, 3), (4, 3), (4, 2)]

    def _trip_grain_targets(self) -> List[int]:
        if not self.width_by_depth:
            return []
        max_depth = max(self.width_by_depth.keys()) + 1

        if self.trip_count <= 8:
            ratios = (0.55, 0.35, 0.22)
        elif self.trip_count <= 16:
            ratios = (0.45, 0.30, 0.20)
        elif self.trip_count <= 32:
            ratios = (0.35, 0.24, 0.16)
        elif self.trip_count <= 64:
            ratios = (0.28, 0.20, 0.14)
        else:
            ratios = (0.24, 0.18, 0.125)

        grains = []
        for ratio in ratios:
            g = max(2, int(round(max_depth * ratio)))
            grains.append(g)
        return sorted(set(grains), reverse=True)

    def _grain_periodic_seed(self, grain: int, offset: int = 0) -> List[int]:
        if not self.width_by_depth:
            return []
        max_depth = max(self.width_by_depth.keys())
        if grain <= 0:
            return []

        cuts: List[int] = []
        d = max(1, grain + offset)
        while d <= max_depth:
            cuts.append(d)
            d += grain
        return [c for c in cuts if 0 < c < self.max_depth]

    def _trip_grain_seeds(self) -> List[List[int]]:
        seeds: List[List[int]] = []
        for grain in self._trip_grain_targets():
            seeds.append(self._grain_periodic_seed(grain, 0))
            if grain >= 4:
                seeds.append(self._grain_periodic_seed(grain, -(grain // 3)))
                seeds.append(self._grain_periodic_seed(grain, grain // 3))
        return [s for s in seeds if s]

    def _region_periodic_seeds(self, candidate_depths: List[int]) -> List[List[int]]:
        if not self.width_by_depth:
            return []

        max_depth = max(self.width_by_depth.keys())
        transition_depths = sorted(
            d for d in candidate_depths[: min(6, len(candidate_depths))] if 0 < d < max_depth
        )
        boundaries = [0] + transition_depths + [max_depth + 1]

        pair_presets = self._trip_grain_pairs()
        seeds: List[List[int]] = []

        for serial_base, parallel_base in pair_presets[:2]:
            cuts: Set[int] = set()
            for start, end in zip(boundaries[:-1], boundaries[1:]):
                region_widths = [self.width_by_depth.get(dep, 0) for dep in range(start, end)]
                avg_width = sum(region_widths) / max(1, len(region_widths))
                base = parallel_base if avg_width >= 2.0 else serial_base
                self.partitioner._add_periodic_cuts(cuts, start, end, base)
            if cuts:
                seeds.append(sorted(c for c in cuts if 0 < c <= max_depth))
        return seeds

    def suggest_seeds(self) -> List[List[int]]:
        seeds: List[List[int]] = [[]]

        # 1) Generic coarse/medium/fine periodic seeds from trip-count prior.
        seeds.extend(self._trip_grain_seeds())

        # 2) DAG-width-aware periodic seeds, still generic.
        for serial_base, parallel_base in self._trip_grain_pairs():
            cuts = self.partitioner.suggest_cut_depths(
                single_chain_base=serial_base,
                parallel_chain_base=parallel_base,
            )
            seeds.append(cuts)

        # 3) Structural candidate cuts from DAG features.
        candidate_depths = self._score_candidate_depths()
        top = candidate_depths[: min(8, len(candidate_depths))]

        for d in top[:5]:
            seeds.append([d])

        # 4) Combine structural cuts with grain seeds, but keep combinations short.
        for base in self._trip_grain_seeds()[:3]:
            if not base:
                continue
            combined = sorted(set(base[:2] + top[:2]))
            if combined:
                seeds.append(combined)

        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                if top[j] - top[i] >= 2:
                    seeds.append([top[i], top[j]])
                if len(seeds) >= 24:
                    break
            if len(seeds) >= 24:
                break

        if len(top) >= 3:
            seeds.append(sorted(top[:3]))

        # 5) Region-aware periodic seeds around detected transitions.
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
    parser = argparse.ArgumentParser(description="Trip-aware generic heuristic split-only optimizer")
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

    opt = GenericTripAwareSplitOptimizer(
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
            "heuristic": "generic_trip_aware_dag_features",
        }
        with open(args.meta_out, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    print(f"[generic-trip-aware-split] best cycles: {cycles}")
    print(f"[generic-trip-aware-split] best cuts:   {cuts}")
    print(f"[generic-trip-aware-split] slots:       {slots}")
    print(f"[generic-trip-aware-split] ub bytes:    {ub_bytes}")
    print(f"[generic-trip-aware-split] output:      {args.output}")


if __name__ == "__main__":
    main()
