"""
Merge-aware optimizer for VF loop partitioning.

Objective:
  1. Partition the DAG into multiple loop blocks as an intermediate scaffold.
  2. Merge those loop bodies back into a single loop by removing mem_inter VST/VLD.
  3. Evaluate the merged single-loop trace under the simulator.

This targets "best after merge", not "best as multi-loop program".
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import time
from typing import Any, Dict, List, Tuple

import sys

sys.path.insert(0, os.path.dirname(__file__))

from dag_lib import OperatorDAG
from partitioner import JsonGenerator, Partitioner

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from main import infer_top_block_loop_bounds
from core.flatten import Flattener
from core.idu import IDU
from core.ifu import IFUUnroll
from core.ooo_factory import create_ooo_core
from core.param_db import ParamDB


def is_intermediate_mem(name: Any) -> bool:
    return isinstance(name, str) and str(name).lower().startswith("mem_inter")


def inst_regs(inst: Dict[str, Any]) -> Tuple[set[str], set[str]]:
    srcs = {x for x in inst.get("src", []) if isinstance(x, str) and x[:1].lower() == "v"}
    dsts = {x for x in inst.get("dst", []) if isinstance(x, str) and x[:1].lower() == "v"}
    return srcs, dsts


def has_direct_dependency(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    a_src, a_dst = inst_regs(a)
    b_src, b_dst = inst_regs(b)
    if a_dst & b_src:
        return True
    if a_src & b_dst:
        return True
    if a_dst & b_dst:
        return True
    return False


def merge_partitioned_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a partitioned multi-loop trace back into one loop.

    The partitioned trace already encodes a useful instruction order.
    We simply concatenate loop bodies and remove boundary mem_inter VST/VLD.
    """
    program = trace.get("program", [])
    merged_body: List[Dict[str, Any]] = []

    for loop in program:
        body = loop.get("body", [])
        for inst in body:
            if inst.get("type") != "inst":
                continue
            op = str(inst.get("op", ""))
            srcs = inst.get("src", [])
            dsts = inst.get("dst", [])
            if op == "VLD" and any(is_intermediate_mem(s) for s in srcs):
                continue
            if op == "VST" and any(is_intermediate_mem(d) for d in dsts):
                continue
            merged_body.append(copy.deepcopy(inst))

    params = copy.deepcopy(trace.get("params", {}))
    return {
        "dtype": trace.get("dtype", "fp32"),
        "params": params,
        "program": [
            {
                "type": "loop",
                "iters": params.get("I", "I") if isinstance(params.get("I", None), int) else "I",
                "unroll": 1,
                "body": merged_body,
            }
        ],
    }


def unroll_merged_trace(trace: Dict[str, Any], factor: int) -> Dict[str, Any]:
    if factor <= 1:
        return copy.deepcopy(trace)

    params = copy.deepcopy(trace.get("params", {}))
    iters = params.get("I", None)
    if isinstance(iters, int):
        if iters % factor != 0:
            raise ValueError(f"Loop bound I={iters} is not divisible by unroll factor {factor}")
        params["I"] = iters // factor

    body = trace["program"][0]["body"]
    new_body: List[Dict[str, Any]] = []

    for inst in body:
        for lane in range(factor):
            dup = copy.deepcopy(inst)
            dup["src"] = [
                f"{x}_lane{lane}" if isinstance(x, str) and x[:1].lower() == "v" else x
                for x in dup.get("src", [])
            ]
            dup["dst"] = [
                f"{x}_lane{lane}" if isinstance(x, str) and x[:1].lower() == "v" else x
                for x in dup.get("dst", [])
            ]
            new_body.append(dup)

    return {
        "dtype": trace.get("dtype", "fp32"),
        "params": params,
        "program": [
            {
                "type": "loop",
                "iters": params.get("I", "I") if isinstance(params.get("I", None), int) else "I",
                "unroll": 1,
                "body": new_body,
            }
        ],
    }


class MergeAwareOptimizer:
    def __init__(
        self,
        trace_path: str,
        initial_temp: float = 30.0,
        min_temp: float = 1.0,
        alpha: float = 0.85,
        iters_per_temp: int = 8,
        target_chain_base: int = 8,
        parallel_chain_base: int = 5,
        unroll_factor: int = 1,
        hillclimb_passes: int = 2,
    ):
        self.trace_path = trace_path
        self.dag, self.meta = OperatorDAG.from_json_trace(trace_path)
        self.max_depth = self.dag.critical_path_length()
        self.t_init = initial_temp
        self.t_min = min_temp
        self.alpha = alpha
        self.iters_per_temp = iters_per_temp
        self.target_chain_base = target_chain_base
        self.parallel_chain_base = parallel_chain_base
        self.unroll_factor = unroll_factor
        self.hillclimb_passes = hillclimb_passes

        self.db = ParamDB(base_dir=ROOT_DIR)
        self.uarch = dict(self.db.get_uarch())
        self.history: List[Dict[str, Any]] = []

    def get_initial_state(self) -> List[int]:
        cuts = Partitioner(self.dag).suggest_cut_depths(
            single_chain_base=self.target_chain_base,
            parallel_chain_base=self.parallel_chain_base,
        )
        return sorted(list(set(cuts)))

    def perturb(self, cuts: List[int]) -> List[int]:
        new_cuts = list(cuts)
        dice = random.random()

        if not new_cuts:
            new_cuts.append(random.randint(1, self.max_depth - 1))
        elif dice < 0.2:
            d = random.randint(1, self.max_depth - 1)
            if d not in new_cuts:
                new_cuts.append(d)
        elif dice < 0.4:
            idx = random.randint(0, len(new_cuts) - 1)
            new_cuts.pop(idx)
        else:
            idx = random.randint(0, len(new_cuts) - 1)
            delta = random.choice([-2, -1, 1, 2])
            new_d = new_cuts[idx] + delta
            if 1 <= new_d <= self.max_depth - 1 and new_d not in new_cuts:
                new_cuts[idx] = new_d

        return sorted(list(set(new_cuts)))

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
        ooo = create_ooo_core(self.uarch, self.db, dtype=trace_obj.get("dtype", "fp32"))

        cycle = 0
        max_cycles = int(params.get("max_cycles", 1_000_000))
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

            to_send = idu.dispatch(cycle, ooo)
            for inst in to_send:
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

    def build_partitioned_and_merged(self, cuts: List[int]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        plan = Partitioner(self.dag).partition_by_cut_points(cuts)
        generator = JsonGenerator(
            plan,
            params=self.meta.get("params", {"I": 16}),
            dtype=self.meta.get("dtype", "fp32"),
        )
        tmp_path = os.path.join(ROOT_DIR, "results", "_merge_optimizer_tmp.json")
        generator.save(tmp_path)
        with open(tmp_path, "r", encoding="utf-8") as f:
            partitioned = json.load(f)
        merged = merge_partitioned_trace(partitioned)
        merged = unroll_merged_trace(merged, self.unroll_factor)
        return partitioned, merged

    def evaluate(self, cuts: List[int]) -> int:
        _, merged = self.build_partitioned_and_merged(cuts)
        return self._simulate_trace(merged)

    def refine_merged_order(self, merged: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        body = copy.deepcopy(merged["program"][0]["body"])
        best_trace = copy.deepcopy(merged)
        best_cycles = self._simulate_trace(best_trace)

        for _ in range(self.hillclimb_passes):
            improved = False
            idx = 0
            while idx < len(body) - 1:
                a = body[idx]
                b = body[idx + 1]
                if has_direct_dependency(a, b):
                    idx += 1
                    continue

                candidate_body = list(body)
                candidate_body[idx], candidate_body[idx + 1] = candidate_body[idx + 1], candidate_body[idx]
                candidate_trace = copy.deepcopy(merged)
                candidate_trace["program"][0]["body"] = candidate_body
                candidate_cycles = self._simulate_trace(candidate_trace)
                if candidate_cycles < best_cycles:
                    body = candidate_body
                    best_trace = candidate_trace
                    best_cycles = candidate_cycles
                    improved = True
                else:
                    idx += 1

            if not improved:
                break

        return best_trace, best_cycles

    def optimize(self) -> Tuple[List[int], int, Dict[str, Any], Dict[str, Any]]:
        current_cuts = self.get_initial_state()
        current_partitioned, current_merged = self.build_partitioned_and_merged(current_cuts)
        current_merged, current_cycles = self.refine_merged_order(current_merged)
        best_cuts = list(current_cuts)
        best_cycles = current_cycles
        best_partitioned, best_merged = current_partitioned, current_merged

        print(f"[merge-aware] initial cuts={current_cuts}, cycles={current_cycles}")

        t = self.t_init
        step = 0
        while t > self.t_min:
            for _ in range(self.iters_per_temp):
                next_cuts = self.perturb(current_cuts)
                next_partitioned, next_merged = self.build_partitioned_and_merged(next_cuts)
                next_merged, next_cycles = self.refine_merged_order(next_merged)
                delta = next_cycles - current_cycles

                if delta < 0:
                    accepted = True
                else:
                    accepted = random.random() < math.exp(-delta / t)

                if accepted:
                    current_cuts = next_cuts
                    current_cycles = next_cycles

                    if current_cycles < best_cycles:
                        best_cycles = current_cycles
                        best_cuts = list(current_cuts)
                        best_partitioned, best_merged = next_partitioned, next_merged
                        print(f"[*] step={step} temp={t:.2f} new_best={best_cycles} cuts={best_cuts}")

                self.history.append(
                    {
                        "step": step,
                        "temp": t,
                        "cycles": current_cycles,
                        "best": best_cycles,
                        "cuts": list(current_cuts),
                    }
                )
                step += 1
            t *= self.alpha

        return best_cuts, best_cycles, best_partitioned, best_merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge-aware optimizer for VF loop partitioning")
    parser.add_argument("trace", help="Input JSON trace")
    parser.add_argument("--base", type=int, default=8, help="Initial heuristic base for serial regions")
    parser.add_argument("--parallel-base", type=int, default=5, help="Initial heuristic base for parallel regions")
    parser.add_argument("--temp", type=float, default=30.0, help="Initial temperature")
    parser.add_argument("--iters", type=int, default=8, help="Iterations per temperature")
    parser.add_argument("--alpha", type=float, default=0.85, help="Cooling rate")
    parser.add_argument("--unroll", type=int, default=1, help="Optional post-merge unroll factor")
    parser.add_argument("--hillclimb-passes", type=int, default=2, help="Local adjacent-swap refinement passes on final merged body")
    args = parser.parse_args()

    opt = MergeAwareOptimizer(
        trace_path=args.trace,
        initial_temp=args.temp,
        alpha=args.alpha,
        iters_per_temp=args.iters,
        target_chain_base=args.base,
        parallel_chain_base=args.parallel_base,
        unroll_factor=args.unroll,
        hillclimb_passes=args.hillclimb_passes,
    )
    start = time.time()
    best_cuts, best_cycles, best_partitioned, best_merged = opt.optimize()
    elapsed = time.time() - start

    name = os.path.splitext(os.path.basename(args.trace))[0]
    suffix = f"_unroll{args.unroll}" if args.unroll > 1 else ""
    part_path = os.path.join(ROOT_DIR, "results", f"{name}_merge_scaffold{suffix}.json")
    merged_path = os.path.join(ROOT_DIR, "results", f"{name}_merge_optimized{suffix}.json")
    hist_path = os.path.join(ROOT_DIR, "results", f"{name}_merge_history{suffix}.json")

    with open(part_path, "w", encoding="utf-8") as f:
        json.dump(best_partitioned, f, indent=2)
    with open(merged_path, "w", encoding="utf-8") as f:
        json.dump(best_merged, f, indent=2)
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(opt.history, f, indent=2)

    print(f"\n[merge-aware] best cycles: {best_cycles}")
    print(f"[merge-aware] best cuts:   {best_cuts}")
    print(f"[merge-aware] scaffold:    {part_path}")
    print(f"[merge-aware] merged:      {merged_path}")
    print(f"[merge-aware] history:     {hist_path}")
    print(f"[merge-aware] search time: {elapsed:.2f}s")


if __name__ == "__main__":
    main()
