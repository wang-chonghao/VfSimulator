#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def _dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)


def _iter_insts(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _iter_insts(item)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "inst":
        yield node
    body = node.get("body")
    if isinstance(body, list):
        for item in body:
            yield from _iter_insts(item)


def _apply_case_transform(trace_obj: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(trace_obj)

    param_overrides = case.get("params", {}) or {}
    if param_overrides:
        out.setdefault("params", {})
        for key, value in param_overrides.items():
            out["params"][key] = value

    transform = case.get("transform", {}) or {}
    replace_op = transform.get("replace_op")
    if isinstance(replace_op, dict):
        src_op = replace_op.get("from")
        dst_op = replace_op.get("to")
        if src_op and dst_op:
            for inst in _iter_insts(out.get("program", [])):
                if inst.get("op") == src_op:
                    inst["op"] = dst_op
    return out


def _lower_trace_to_vfinfo_payload(trace_obj: Dict[str, Any]) -> Dict[str, Any]:
    from api.json_adapter import JsonVfInfoAdapter
    from api.vf_lowering import VFInfoLowerer

    vf_info = JsonVfInfoAdapter.from_payload(trace_obj)
    return VFInfoLowerer().lower(vf_info)


def _run_cmd(cmd: List[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{text}")
    return text


def _parse_native_vf_end(stdout_text: str) -> int:
    match = re.search(r"vfEndCycle\s*=\s*(\d+)", stdout_text)
    if not match:
        raise RuntimeError("Cannot parse 'vfEndCycle=' from native runner output")
    return int(match.group(1))


def _run_native_on_trace(
    trace_obj: Dict[str, Any],
    runner: Path,
    run_dir: Path,
    max_cycles: int,
) -> Dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = _lower_trace_to_vfinfo_payload(trace_obj)
    trace_path = run_dir / "vfinfo_input.json"
    _dump_json(trace_path, payload)

    stdout = _run_cmd(
        [
            str(runner),
            "--trace",
            str(trace_path),
            "--out-dir",
            str(run_dir / "native"),
            "--max-cycles",
            str(max_cycles),
        ],
        ROOT,
    )
    (run_dir / "native_stdout.log").write_text(stdout, encoding="utf-8")
    return {"vf_end": _parse_native_vf_end(stdout)}


def _case_primary_metric(case: Dict[str, Any]) -> str:
    return str(case.get("primary_metric") or "vf_end")


def _resolve_case_tolerance(case: Dict[str, Any], defaults: Dict[str, Any]) -> Tuple[int, float]:
    return int(case.get("abs_tol", defaults.get("abs_tol", 0))), float(
        case.get("rel_tol", defaults.get("rel_tol", 0.0))
    )


def _run_suite(
    suite: Dict[str, Any],
    runner: Path,
    out_root: Path,
    tier: str,
    max_cycles: int,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {"cases": {}}
    for case in suite.get("cases", []) or []:
        if tier == "smoke" and case.get("tier", "full") != "smoke":
            continue
        if case.get("kind", "simulate") != "simulate":
            continue

        trace_path = ROOT / case["trace"]
        if not trace_path.exists():
            raise FileNotFoundError(f"Trace not found: {trace_path}")
        trace_obj = _apply_case_transform(_load_json(trace_path), case)
        metrics = _run_native_on_trace(
            trace_obj,
            runner=runner,
            run_dir=out_root / case["id"],
            max_cycles=max_cycles,
        )

        cce_vf_end = case.get("cce_vf_end")
        primary = _case_primary_metric(case)
        if cce_vf_end is not None and primary in metrics:
            cur_val = float(metrics[primary])
            cce_val = float(cce_vf_end)
            metrics["cce_vf_end"] = int(cce_vf_end)
            metrics["error_to_cce_abs"] = abs(cur_val - cce_val)
            metrics["error_to_cce_rel"] = abs(cur_val - cce_val) / max(1.0, abs(cce_val))
        results["cases"][case["id"]] = metrics
    return results


def _compare_with_baseline(
    suite: Dict[str, Any],
    current: Dict[str, Any],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    defaults = suite.get("defaults", {}) or {}
    cce_abs_worse_tol = float(defaults.get("cce_error_abs_worse_tol", 0.0))
    cce_rel_worse_tol = float(defaults.get("cce_error_rel_worse_tol", 0.0))
    summary: Dict[str, Any] = {"passed": True, "rows": []}
    base_cases = baseline.get("cases") or {}
    cur_cases = current.get("cases") or {}
    case_map = {case["id"]: case for case in (suite.get("cases") or [])}

    for case_id, cur_metrics in cur_cases.items():
        case = case_map.get(case_id, {})
        primary = _case_primary_metric(case)
        abs_tol, rel_tol = _resolve_case_tolerance(case, defaults)
        cur_val = cur_metrics.get(primary)
        base_val = (base_cases.get(case_id) or {}).get(primary)
        row: Dict[str, Any] = {
            "id": case_id,
            "metric": primary,
            "current": cur_val,
            "baseline": base_val,
            "abs_tol": abs_tol,
            "rel_tol": rel_tol,
            "status": "PASS",
        }
        if cur_val is None or base_val is None:
            row["status"] = "MISSING"
            summary["passed"] = False
        else:
            abs_diff = abs(int(cur_val) - int(base_val))
            rel_diff = abs_diff / max(1, abs(int(base_val)))
            row["abs_diff"] = abs_diff
            row["rel_diff"] = rel_diff
            if abs_diff > abs_tol and rel_diff > rel_tol:
                row["status"] = "FAIL"
                summary["passed"] = False
        summary["rows"].append(row)

        cur_cce_abs = cur_metrics.get("error_to_cce_abs")
        cur_cce_rel = cur_metrics.get("error_to_cce_rel")
        base_metrics = base_cases.get(case_id) or {}
        base_cce_abs = base_metrics.get("error_to_cce_abs")
        base_cce_rel = base_metrics.get("error_to_cce_rel")
        if (
            cur_cce_abs is not None
            and cur_cce_rel is not None
            and base_cce_abs is not None
            and base_cce_rel is not None
        ):
            abs_worse = float(cur_cce_abs) - float(base_cce_abs)
            rel_worse = float(cur_cce_rel) - float(base_cce_rel)
            cce_row = {
                "id": case_id,
                "metric": "error_to_cce",
                "current_abs": cur_cce_abs,
                "baseline_abs": base_cce_abs,
                "current_rel": cur_cce_rel,
                "baseline_rel": base_cce_rel,
                "abs_worse": abs_worse,
                "rel_worse": rel_worse,
                "abs_tol": cce_abs_worse_tol,
                "rel_tol": cce_rel_worse_tol,
                "status": "PASS",
            }
            if abs_worse > cce_abs_worse_tol and rel_worse > cce_rel_worse_tol:
                cce_row["status"] = "FAIL"
                summary["passed"] = False
            summary["rows"].append(cce_row)
    return summary


def _print_summary(summary: Dict[str, Any]) -> None:
    print("")
    print("=== Native Regression Summary ===")
    for row in summary.get("rows", []):
        status = row["status"]
        case_id = row["id"]
        metric = row["metric"]
        if metric == "error_to_cce":
            print(
                f"[{status}] {case_id:<40} error_to_cce: "
                f"cur_abs={row.get('current_abs')}, base_abs={row.get('baseline_abs')}, "
                f"cur_rel={row.get('current_rel'):.4f}, base_rel={row.get('baseline_rel'):.4f}, "
                f"abs_worse={row.get('abs_worse'):.4f}, rel_worse={row.get('rel_worse'):.4f}"
            )
            continue
        if "abs_diff" in row:
            print(
                f"[{status}] {case_id:<40} {metric}: "
                f"cur={row.get('current')}, base={row.get('baseline')}, "
                f"abs_diff={row['abs_diff']}, rel_diff={row['rel_diff']:.4f}"
            )
        else:
            print(f"[{status}] {case_id:<40} {metric}: cur={row.get('current')}, base={row.get('baseline')}")
    print(f"Result: {'PASS' if summary.get('passed') else 'FAIL'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run native C++ VF cost model regression suite")
    parser.add_argument("--suite", default="regression_suite/cases/cost_model_regression_cases.json")
    parser.add_argument("--baseline", default="regression_suite/cases/baseline_queue_level4_ooo_transfer_delay.json")
    parser.add_argument("--out-dir", default="results/native_regression_suite/latest")
    parser.add_argument("--tier", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--runner", default="build-native/vfsim_native_json_runner")
    parser.add_argument("--max-cycles", type=int, default=1000000)
    args = parser.parse_args()

    runner = ROOT / args.runner
    if not runner.exists():
        raise FileNotFoundError(
            f"Native runner not found: {runner}. "
            "Build it first with: cmake -S native -B build-native -DVFSIM_BUILD_TESTS=ON && "
            "cmake --build build-native -j2"
        )

    suite_path = ROOT / args.suite
    baseline_path = ROOT / args.baseline
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    suite = _load_json(suite_path)
    current = _run_suite(
        suite,
        runner=runner,
        out_root=out_dir,
        tier=args.tier,
        max_cycles=args.max_cycles,
    )
    current["meta"] = {
        "suite": suite.get("name", "unnamed_suite"),
        "tier": args.tier,
        "runner": str(runner),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    current_path = out_dir / "current_metrics.json"
    _dump_json(current_path, current)
    print(f"[info] wrote current metrics: {current_path}")

    baseline = _load_json(baseline_path)
    print(f"[info] comparing against baseline: {baseline_path}")
    summary = _compare_with_baseline(suite, current, baseline)
    summary["baseline"] = {
        "path": str(baseline_path),
        "meta": baseline.get("meta", {}),
    }
    summary_path = out_dir / "compare_summary.json"
    _dump_json(summary_path, summary)
    print(f"[info] wrote summary: {summary_path}")
    _print_summary(summary)
    if not summary.get("passed", False):
        sys.exit(1)


if __name__ == "__main__":
    main()
