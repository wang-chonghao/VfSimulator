"""
Stage C2: split + per-segment unroll optimizer (development draft).

This script builds on Stage C:
  1) get a split plan (cuts) from Stage C mode
  2) optimize per-segment unroll vector U_k with coordinate descent
  3) use strong caching to avoid repeated simulation

Current scope:
  - mode: loop_cut_loose / loop_cut_strict / loop_uncut
  - segment-level unroll candidates (default: 1,2,3,4)
  - plan-level cache + segment-level proxy cache
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from stage_c_optimizer import StageCOptimizer, _simulate_trace  # noqa: E402


def _dump_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _parse_cuts(text: str) -> List[int]:
    if not text.strip():
        return []
    return sorted({int(x.strip()) for x in text.split(",") if x.strip()})


def _parse_u_candidates(text: str) -> List[int]:
    vals = sorted({int(x.strip()) for x in text.split(",") if x.strip()})
    return [v for v in vals if v >= 1]


def _segment_signature(loop_obj: Dict[str, Any]) -> str:
    body = loop_obj.get("body", []) or []
    norm = []
    for inst in body:
        norm.append(
            {
                "op": str(inst.get("op", "")),
                "dst_n": len(inst.get("dst", []) or []),
                "src_n": len(inst.get("src", []) or []),
            }
        )
    blob = json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


@dataclass
class PlanEval:
    score: float
    vf_end: int
    feasible: bool
    reason: str
    trace_obj: Dict[str, Any]


class StageC2SegmentUnrollOptimizer:
    def __init__(
        self,
        trace_path: str,
        trip_count: int,
        mode: str,
        dtype: str,
        ooo_model: str,
        lambda_u_change: float = 0.0,
        stagec_lambda_small_chain: float = 24.0,
        stagec_min_chain_len: int = 3,
    ):
        self.trace_path = trace_path
        self.trip_count = int(trip_count)
        self.mode = mode
        self.dtype = dtype
        self.ooo_model = ooo_model
        self.lambda_u_change = float(lambda_u_change)

        self.base = StageCOptimizer(
            trace_path=trace_path,
            trip_count=trip_count,
            dtype=dtype,
            ooo_model=ooo_model,
            lambda_small_chain=float(stagec_lambda_small_chain),
            min_chain_len=int(stagec_min_chain_len),
        )

        # strong caches
        self.plan_cache: Dict[Tuple[Any, ...], PlanEval] = {}
        self.segment_cache: Dict[Tuple[Any, ...], float] = {}
        self.cache_stats = {"plan_hit": 0, "plan_miss": 0, "seg_hit": 0, "seg_miss": 0}

    def _valid_u(self, candidates: List[int]) -> List[int]:
        out = []
        for u in candidates:
            if u <= 0:
                continue
            # keep only divisors of trip count
            if self.trip_count % u == 0:
                out.append(u)
        return sorted(set(out)) or [1]

    def _apply_unroll_vector(self, trace_obj: Dict[str, Any], u_vec: List[int]) -> Dict[str, Any]:
        t = copy.deepcopy(trace_obj)
        loops = [x for x in (t.get("program", []) or []) if isinstance(x, dict) and x.get("type") == "loop"]
        if len(loops) != len(u_vec):
            raise ValueError(f"loop count mismatch: loops={len(loops)} vs u_vec={len(u_vec)}")
        for i, loop in enumerate(loops):
            loop["unroll"] = int(u_vec[i])
        return t

    def _segment_proxy(self, trace_obj: Dict[str, Any], seg_idx: int, u: int) -> float:
        loops = [x for x in (trace_obj.get("program", []) or []) if isinstance(x, dict) and x.get("type") == "loop"]
        loop = loops[seg_idx]
        sig = _segment_signature(loop)
        key = (sig, int(u), self.mode, self.ooo_model, self.trip_count)
        if key in self.segment_cache:
            self.cache_stats["seg_hit"] += 1
            return self.segment_cache[key]
        self.cache_stats["seg_miss"] += 1

        # Lightweight proxy (not final scoring):
        # smaller compute_count/u and shorter chain get smaller proxy.
        compute_cnt = 0
        for inst in loop.get("body", []) or []:
            op = str(inst.get("op", "")).upper()
            if op not in ("VLD", "VST"):
                compute_cnt += 1
        proxy = float(compute_cnt) / max(1, int(u))
        self.segment_cache[key] = proxy
        return proxy

    def _evaluate_plan(self, cuts: List[int], u_vec: List[int], base_trace_obj: Dict[str, Any]) -> PlanEval:
        key = (
            tuple(sorted(set(cuts))),
            tuple(int(x) for x in u_vec),
            self.mode,
            self.ooo_model,
            self.trip_count,
            self.dtype,
        )
        if key in self.plan_cache:
            self.cache_stats["plan_hit"] += 1
            return self.plan_cache[key]
        self.cache_stats["plan_miss"] += 1

        try:
            trace_obj = self._apply_unroll_vector(base_trace_obj, u_vec)
        except Exception as e:
            out = PlanEval(10**12, 10**9, False, f"apply_unroll_failed:{e}", base_trace_obj)
            self.plan_cache[key] = out
            return out

        vf_end = _simulate_trace(trace_obj, self.base.pdb, self.ooo_model, self.dtype)
        # keep final score close to raw vf_end; optional mild smoothness penalty on large U shifts
        change_pen = self.lambda_u_change * sum(max(0, u - 1) for u in u_vec)
        score = float(vf_end + change_pen)
        out = PlanEval(score, int(vf_end), True, "", trace_obj)
        self.plan_cache[key] = out
        return out

    def _candidate_cuts(
        self,
        cuts_override: Optional[List[int]],
        beam_width: int,
        beam_rounds: int,
        near_margin: float,
    ) -> List[List[int]]:
        if cuts_override is not None:
            return [list(sorted(set(cuts_override)))]

        if self.mode == "loop_uncut":
            return [[]]

        # Reuse Stage-C style beam on cuts to get strong candidates,
        # then C2 compares them under their own best unroll vectors.
        frontier: List[Tuple[int, ...]] = [tuple(seed) for seed in self.base._seed_cuts()]
        seen = set(frontier)

        def best_k(keys: List[Tuple[int, ...]], k: int) -> List[Tuple[int, ...]]:
            scored = []
            for kk in keys:
                ev = self.base._evaluate(self.mode, list(kk))
                scored.append((ev.score, kk))
            scored.sort(key=lambda x: x[0])
            return [kk for _s, kk in scored[: max(1, k)]]

        frontier = best_k(frontier, beam_width)
        for _ in range(max(1, beam_rounds)):
            candidates: List[Tuple[int, ...]] = list(frontier)
            for key in frontier:
                for nxt in self.base._neighbors(list(key)):
                    nt = tuple(sorted(set(nxt)))
                    if nt in seen:
                        continue
                    seen.add(nt)
                    candidates.append(nt)
            frontier = best_k(candidates, beam_width)

        # Expand with near-best cuts from Stage-C result summary
        r = self.base.run(self.mode, beam_width=beam_width, beam_rounds=beam_rounds, near_margin=near_margin)
        best_cuts = tuple(sorted(set(r["best_cuts"])))
        cut_set = {tuple(c) for c in frontier}
        cut_set.add(best_cuts)
        return [list(c) for c in sorted(cut_set)]

    def _optimize_u_for_fixed_cuts(
        self,
        cuts: List[int],
        valid_u: List[int],
        max_coord_iters: int,
    ) -> Tuple[List[int], PlanEval]:
        ev = self.base._evaluate(self.mode, cuts)
        if not ev.feasible:
            return [], PlanEval(10**12, 10**9, False, f"infeasible_cuts:{ev.reason}", ev.trace_obj)
        base_trace = ev.trace_obj

        loops = [x for x in (base_trace.get("program", []) or []) if isinstance(x, dict) and x.get("type") == "loop"]
        seg_n = len(loops)
        if seg_n == 0:
            return [], PlanEval(10**12, 10**9, False, "no_top_loops", base_trace)

        u_vec = [1] * seg_n
        cur = self._evaluate_plan(cuts, u_vec, base_trace)

        for _ in range(max(1, int(max_coord_iters))):
            changed = False
            for i in range(seg_n):
                # proxy-ranked candidates first; still evaluate all for correctness
                cand_rank = sorted(valid_u, key=lambda u: self._segment_proxy(base_trace, i, u))
                best_local = cur
                best_u = u_vec[i]
                for u in cand_rank:
                    test = list(u_vec)
                    test[i] = int(u)
                    ev = self._evaluate_plan(cuts, test, base_trace)
                    if ev.feasible and ev.score < best_local.score:
                        best_local = ev
                        best_u = int(u)
                if best_u != u_vec[i]:
                    u_vec[i] = best_u
                    cur = best_local
                    changed = True
            if not changed:
                break
        return u_vec, cur

    def optimize(
        self,
        u_candidates: List[int],
        max_coord_iters: int,
        cuts_override: Optional[List[int]],
        beam_width: int,
        beam_rounds: int,
        near_margin: float,
    ) -> Dict[str, Any]:
        valid_u = self._valid_u(u_candidates)
        cut_plans = self._candidate_cuts(
            cuts_override=cuts_override,
            beam_width=beam_width,
            beam_rounds=beam_rounds,
            near_margin=near_margin,
        )

        best_cuts: List[int] = []
        best_u_vec: List[int] = []
        best_eval: Optional[PlanEval] = None
        candidate_rows: List[Dict[str, Any]] = []
        for cuts in cut_plans:
            u_vec, ev = self._optimize_u_for_fixed_cuts(
                cuts=cuts,
                valid_u=valid_u,
                max_coord_iters=max_coord_iters,
            )
            candidate_rows.append(
                {
                    "cuts": cuts,
                    "best_u_vector": u_vec,
                    "score": ev.score,
                    "vf_end": ev.vf_end,
                    "feasible": ev.feasible,
                    "reason": ev.reason,
                }
            )
            if ev.feasible and (best_eval is None or ev.score < best_eval.score):
                best_eval = ev
                best_cuts = cuts
                best_u_vec = u_vec

        if best_eval is None:
            # transparent fallback: choose lowest-score row even if infeasible
            candidate_rows.sort(key=lambda x: x["score"])
            row0 = candidate_rows[0]
            best_cuts = list(row0["cuts"])
            best_u_vec = list(row0.get("best_u_vector", []))
            ev0 = self.base._evaluate(self.mode, best_cuts)
            best_eval = PlanEval(row0["score"], row0["vf_end"], False, row0.get("reason", "no_feasible"), ev0.trace_obj)

        return {
            "trace": self.trace_path,
            "trip_count": self.trip_count,
            "mode": self.mode,
            "ooo_model": self.ooo_model,
            "search": {
                "beam_width": beam_width,
                "beam_rounds": beam_rounds,
                "near_margin": near_margin,
                "candidate_cut_count": len(cut_plans),
            },
            "cuts": best_cuts,
            "u_candidates": valid_u,
            "best_u_vector": best_u_vec,
            "best_score": best_eval.score,
            "best_vf_end": best_eval.vf_end,
            "cache_stats": dict(self.cache_stats),
            "candidates": candidate_rows,
            "best_trace_obj": best_eval.trace_obj,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage C2 split + per-segment unroll optimizer")
    ap.add_argument("trace", help="Input trace path")
    ap.add_argument("--trip-count", type=int, required=True, help="Loop trip count I")
    ap.add_argument(
        "--loop-opt-mode",
        choices=["loop_uncut", "loop_cut_strict", "loop_cut_loose"],
        default="loop_cut_loose",
    )
    ap.add_argument("--dtype", default="fp32")
    ap.add_argument(
        "--ooo-model",
        choices=["classical-cpu-type", "consumer-done"],
        default="consumer-done",
    )
    ap.add_argument("--u-candidates", default="1,2,3,4", help="Comma-separated candidate unroll values")
    ap.add_argument("--max-coord-iters", type=int, default=3)
    ap.add_argument("--cuts", default="", help="Optional fixed cuts, e.g. 9,16")
    ap.add_argument("--beam-width", type=int, default=4)
    ap.add_argument("--beam-rounds", type=int, default=2)
    ap.add_argument("--near-margin", type=float, default=0.03)
    ap.add_argument("--lambda-u-change", type=float, default=0.0)
    ap.add_argument(
        "--stagec-lambda-small-chain",
        type=float,
        default=24.0,
        help="small-chain penalty coefficient used when Stage C chooses base cuts",
    )
    ap.add_argument(
        "--stagec-min-chain-len",
        type=int,
        default=3,
        help="small-chain threshold used when Stage C chooses base cuts",
    )
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    trace_path = args.trace
    if not os.path.isabs(trace_path):
        trace_path = os.path.abspath(os.path.join(ROOT_DIR, trace_path))
    out_dir = args.output_dir
    if not os.path.isabs(out_dir):
        out_dir = os.path.abspath(os.path.join(ROOT_DIR, out_dir))
    os.makedirs(out_dir, exist_ok=True)

    cuts_override = _parse_cuts(args.cuts) if args.cuts.strip() else None
    opt = StageC2SegmentUnrollOptimizer(
        trace_path=trace_path,
        trip_count=args.trip_count,
        mode=args.loop_opt_mode,
        dtype=args.dtype,
        ooo_model=args.ooo_model,
        lambda_u_change=args.lambda_u_change,
        stagec_lambda_small_chain=args.stagec_lambda_small_chain,
        stagec_min_chain_len=args.stagec_min_chain_len,
    )
    result = opt.optimize(
        u_candidates=_parse_u_candidates(args.u_candidates),
        max_coord_iters=args.max_coord_iters,
        cuts_override=cuts_override,
        beam_width=max(1, int(args.beam_width)),
        beam_rounds=max(1, int(args.beam_rounds)),
        near_margin=max(0.0, float(args.near_margin)),
    )
    best_trace = result.pop("best_trace_obj")
    summary_path = os.path.join(out_dir, "summary.json")
    trace_out = os.path.join(out_dir, "best_trace.json")
    _dump_json(summary_path, result)
    _dump_json(trace_out, best_trace)

    print(f"[stage-c2] mode: {result['mode']}")
    print(f"[stage-c2] cuts: {result['cuts']}")
    print(f"[stage-c2] best U vector: {result['best_u_vector']}")
    print(f"[stage-c2] best vf_end: {result['best_vf_end']}")
    print(f"[stage-c2] summary: {summary_path}")
    print(f"[stage-c2] trace:   {trace_out}")


if __name__ == "__main__":
    main()
