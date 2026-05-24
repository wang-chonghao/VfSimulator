"""
Unified CLI for Stage-C optimization family.

This keeps internal implementation split into:
  - stage_c_optimizer.py (foundational split/reorder/constraint engine)
  - stage_c2_segment_unroll_optimizer.py (joint cuts + per-segment unroll)

but exposes one user-facing entrypoint.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from stage_c_optimizer import StageCOptimizer  # noqa: E402
from stage_c2_segment_unroll_optimizer import (  # noqa: E402
    StageC2SegmentUnrollOptimizer,
    _parse_cuts,
    _parse_u_candidates,
)


def _dump_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _abs_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(ROOT_DIR, path))


def _run_stage_c(args: argparse.Namespace) -> Dict[str, Any]:
    opt = StageCOptimizer(
        trace_path=args.trace,
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
    return result


def _run_stage_c2(args: argparse.Namespace) -> Dict[str, Any]:
    cuts_override: Optional[List[int]] = _parse_cuts(args.cuts) if str(args.cuts).strip() else None
    opt = StageC2SegmentUnrollOptimizer(
        trace_path=args.trace,
        trip_count=args.trip_count,
        mode=args.loop_opt_mode,
        dtype=args.dtype,
        ooo_model=args.ooo_model,
        lambda_u_change=args.lambda_u_change,
        stagec_lambda_small_chain=args.stagec_lambda_small_chain,
        stagec_min_chain_len=args.stagec_min_chain_len,
    )
    return opt.optimize(
        u_candidates=_parse_u_candidates(args.u_candidates),
        max_coord_iters=args.max_coord_iters,
        cuts_override=cuts_override,
        beam_width=max(1, int(args.beam_width)),
        beam_rounds=max(1, int(args.beam_rounds)),
        near_margin=max(0.0, float(args.near_margin)),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Unified VF Stage-C optimization CLI")
    ap.add_argument("trace", help="Input trace path")
    ap.add_argument("--trip-count", type=int, required=True, help="Loop trip count I")
    ap.add_argument(
        "--algo",
        choices=["stage_c", "stage_c2"],
        default="stage_c2",
        help="Optimization algorithm. stage_c2 is the default/recommended mainline.",
    )
    ap.add_argument(
        "--loop-opt-mode",
        choices=["loop_uncut", "loop_cut_strict", "loop_cut_loose", "all"],
        default="loop_cut_loose",
    )
    ap.add_argument("--dtype", default="fp32")
    ap.add_argument(
        "--ooo-model",
        choices=["classical-cpu-type", "consumer-done"],
        default="consumer-done",
    )
    ap.add_argument("--beam-width", type=int, default=4)
    ap.add_argument("--beam-rounds", type=int, default=2)
    ap.add_argument("--near-margin", type=float, default=0.03)
    ap.add_argument("--output-dir", required=True)

    # Stage-C specific knobs
    ap.add_argument("--lambda-cut", type=float, default=2.0)
    ap.add_argument("--lambda-small-chain", type=float, default=24.0)
    ap.add_argument("--min-chain-len", type=int, default=3)

    # Stage-C2 specific knobs
    ap.add_argument("--u-candidates", default="1,2,3,4")
    ap.add_argument("--max-coord-iters", type=int, default=3)
    ap.add_argument("--cuts", default="", help="Optional fixed cuts, e.g. 9,16")
    ap.add_argument("--lambda-u-change", type=float, default=0.0)
    ap.add_argument("--stagec-lambda-small-chain", type=float, default=24.0)
    ap.add_argument("--stagec-min-chain-len", type=int, default=3)

    args = ap.parse_args()
    args.trace = _abs_path(args.trace)
    args.output_dir = _abs_path(args.output_dir)
    os.makedirs(args.output_dir, exist_ok=True)

    if args.algo == "stage_c":
        if args.loop_opt_mode == "all":
            # stage_c supports all mode natively
            pass
        result = _run_stage_c(args)
        best_trace = result.pop("best_trace_obj")
        result["entry_algo"] = "stage_c"
    else:
        if args.loop_opt_mode == "all":
            raise ValueError("stage_c2 does not support loop_opt_mode=all. Use a single mode.")
        result = _run_stage_c2(args)
        best_trace = result.pop("best_trace_obj")
        result["entry_algo"] = "stage_c2"

    result["entrypoint"] = "optimizer/run_vf_stage_c_optimization.py"
    result["internal_modules"] = [
        "optimizer/stage_c_optimizer.py",
        "optimizer/stage_c2_segment_unroll_optimizer.py",
    ]

    summary_path = os.path.join(args.output_dir, "summary.json")
    trace_out = os.path.join(args.output_dir, "best_trace.json")
    _dump_json(summary_path, result)
    _dump_json(trace_out, best_trace)

    print(f"[vf-opt] algo: {result['entry_algo']}")
    print(f"[vf-opt] summary: {summary_path}")
    print(f"[vf-opt] trace:   {trace_out}")


if __name__ == "__main__":
    main()
