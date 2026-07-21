#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _iter_insts(node: Any):
    if isinstance(node, list):
        for x in node:
            yield from _iter_insts(x)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "inst":
        yield node
    body = node.get("body")
    if isinstance(body, list):
        for x in body:
            yield from _iter_insts(x)


def _apply_case_transform(trace_obj: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(trace_obj)

    param_overrides = case.get("params", {}) or {}
    if param_overrides:
        out.setdefault("params", {})
        for k, v in param_overrides.items():
            out["params"][k] = v

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


def _run_cmd(cmd: List[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{text}")
    return text


def _parse_vf_end(stdout_text: str) -> int:
    m = re.search(r"VF end cycle \(with drain\)\s*=\s*(\d+)", stdout_text)
    if not m:
        raise RuntimeError("Cannot parse 'VF end cycle (with drain)' from main.py output")
    return int(m.group(1))


def _run_main_on_trace(
    trace_obj: Dict[str, Any],
    run_dir: Path,
    ooo_model: str,
    run_theoretical: bool,
    extra_main_args: List[str] | None = None,
) -> Dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace_input.json"
    _dump_json(trace_path, trace_obj)

    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--trace",
        str(trace_path),
        "--out_dir",
        str(run_dir / "model"),
    ]
    # `ooo_model` is kept in old manifests for history, but current main.py
    # has a single queue_level4 mainline and no longer exposes --ooo-model.
    if extra_main_args:
        cmd.extend(extra_main_args)
    stdout = _run_cmd(cmd, ROOT)
    metrics = {
        "vf_end": _parse_vf_end(stdout),
    }

    if run_theoretical:
        cmd_theo = cmd + ["--theoretical-limit-vloop-only"]
        stdout_theo = _run_cmd(cmd_theo, ROOT)
        metrics["vf_end_theoretical"] = _parse_vf_end(stdout_theo)

    return metrics


def _run_optimizer_case(case: Dict[str, Any], run_dir: Path, ooo_model: str) -> Dict[str, Any]:
    trace_path = ROOT / case["trace"]
    if not trace_path.exists():
        raise FileNotFoundError(f"Trace not found: {trace_path}")

    trip_count = int(case["trip_count"])
    optimizer_py = ROOT / case.get("optimizer", "optimizer/generic_trip_aware_split_optimizer.py")
    if not optimizer_py.exists():
        raise FileNotFoundError(f"Optimizer not found: {optimizer_py}")

    baseline_dir = run_dir / "baseline"
    baseline_trace = _apply_case_transform(_load_json(trace_path), case)
    baseline_trace.setdefault("params", {})
    baseline_trace["params"]["I"] = trip_count
    baseline_metrics = _run_main_on_trace(
        trace_obj=baseline_trace,
        run_dir=baseline_dir,
        ooo_model=ooo_model,
        run_theoretical=bool(case.get("run_theoretical", False)),
    )

    opt_out = run_dir / "optimized.json"
    opt_meta = run_dir / "optimized_meta.json"

    opt_args = [
        sys.executable,
        str(optimizer_py),
        str(trace_path),
        "--trip-count",
        str(trip_count),
        "--output",
        str(opt_out),
        "--meta-out",
        str(opt_meta),
        "--ooo-model",
        ooo_model,
    ]

    optimizer_args = case.get("optimizer_args", {}) or {}
    for k, v in optimizer_args.items():
        flag = f"--{k.replace('_', '-')}"
        opt_args += [flag, str(v)]

    _run_cmd(opt_args, ROOT)
    opt_trace = _load_json(opt_out)
    opt_metrics = _run_main_on_trace(
        trace_obj=opt_trace,
        run_dir=run_dir / "optimized",
        ooo_model=ooo_model,
        run_theoretical=False,
    )

    out: Dict[str, Any] = {}
    out["baseline_vf_end"] = int(baseline_metrics["vf_end"])
    out["optimized_vf_end"] = int(opt_metrics["vf_end"])
    out["improvement"] = int(out["baseline_vf_end"] - out["optimized_vf_end"])
    out["improvement_pct"] = (
        (100.0 * out["improvement"] / out["baseline_vf_end"]) if out["baseline_vf_end"] else 0.0
    )

    if opt_meta.exists():
        meta = _load_json(opt_meta)
        if "cycles" in meta:
            out["optimizer_meta_cycles"] = int(meta["cycles"])
        if "best_cuts" in meta:
            out["optimizer_best_cuts"] = meta["best_cuts"]

    return out


def _case_primary_metric(case: Dict[str, Any]) -> str:
    pm = case.get("primary_metric")
    if pm:
        return pm
    return "optimized_vf_end" if case.get("kind") == "optimize" else "vf_end"


def _resolve_case_tolerance(case: Dict[str, Any], defaults: Dict[str, Any]) -> Tuple[int, float]:
    abs_tol = int(case.get("abs_tol", defaults.get("abs_tol", 0)))
    rel_tol = float(case.get("rel_tol", defaults.get("rel_tol", 0.0)))
    return abs_tol, rel_tol


def _run_suite(suite: Dict[str, Any], out_root: Path, tier: str) -> Dict[str, Any]:
    defaults = suite.get("defaults", {}) or {}
    ooo_model = defaults.get("ooo_model", "queue_level4")
    run_theoretical_default = bool(defaults.get("run_theoretical", False))
    extra_main_args_default = list(defaults.get("extra_main_args", []) or [])

    cases = suite.get("cases", []) or []
    selected: List[Dict[str, Any]] = []
    for c in cases:
        case_tier = c.get("tier", "full")
        if tier == "smoke" and case_tier != "smoke":
            continue
        selected.append(c)

    results: Dict[str, Any] = {"cases": {}}
    for case in selected:
        cid = case["id"]
        cdir = out_root / cid
        cdir.mkdir(parents=True, exist_ok=True)

        kind = case.get("kind", "simulate")
        if kind == "simulate":
            trace_path = ROOT / case["trace"]
            if not trace_path.exists():
                raise FileNotFoundError(f"Trace not found: {trace_path}")
            raw = _load_json(trace_path)
            cooked = _apply_case_transform(raw, case)
            metrics = _run_main_on_trace(
                trace_obj=cooked,
                run_dir=cdir,
                ooo_model=case.get("ooo_model", ooo_model),
                run_theoretical=bool(case.get("run_theoretical", run_theoretical_default)),
                extra_main_args=list(case.get("extra_main_args", extra_main_args_default) or []),
            )
        elif kind == "optimize":
            metrics = _run_optimizer_case(case, cdir, case.get("ooo_model", ooo_model))
        else:
            raise ValueError(f"Unsupported case kind: {kind}")

        # Optional CCE ground-truth for accuracy tracking.
        cce_vf_end = case.get("cce_vf_end")
        primary_metric = _case_primary_metric(case)
        if cce_vf_end is not None and primary_metric in metrics:
            cur_val = float(metrics[primary_metric])
            cce_val = float(cce_vf_end)
            metrics["cce_vf_end"] = int(cce_vf_end)
            metrics["error_to_cce_abs"] = abs(cur_val - cce_val)
            metrics["error_to_cce_rel"] = abs(cur_val - cce_val) / max(1.0, abs(cce_val))

        results["cases"][cid] = metrics

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
    base_cases = (baseline.get("cases") or {})
    cur_cases = (current.get("cases") or {})
    case_map = {c["id"]: c for c in (suite.get("cases") or [])}

    for cid, cur_metrics in cur_cases.items():
        case = case_map.get(cid, {})
        primary = _case_primary_metric(case)
        abs_tol, rel_tol = _resolve_case_tolerance(case, defaults)
        cur_val = cur_metrics.get(primary)
        base_val = (base_cases.get(cid) or {}).get(primary)

        row: Dict[str, Any] = {
            "id": cid,
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
            rel_diff = (abs_diff / max(1, abs(int(base_val)))) if base_val is not None else 1.0
            row["abs_diff"] = abs_diff
            row["rel_diff"] = rel_diff
            if abs_diff > abs_tol and rel_diff > rel_tol:
                row["status"] = "FAIL"
                summary["passed"] = False

        summary["rows"].append(row)

        # Accuracy guard vs CCE ground-truth:
        # do not allow current error-to-CCE to become significantly worse than baseline error-to-CCE.
        cur_cce_abs = cur_metrics.get("error_to_cce_abs")
        cur_cce_rel = cur_metrics.get("error_to_cce_rel")
        base_case_metrics = (base_cases.get(cid) or {})
        base_cce_abs = base_case_metrics.get("error_to_cce_abs")
        base_cce_rel = base_case_metrics.get("error_to_cce_rel")
        if (
            cur_cce_abs is not None
            and cur_cce_rel is not None
            and base_cce_abs is not None
            and base_cce_rel is not None
        ):
            abs_worse = float(cur_cce_abs) - float(base_cce_abs)
            rel_worse = float(cur_cce_rel) - float(base_cce_rel)
            cce_row: Dict[str, Any] = {
                "id": cid,
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
    rows = summary.get("rows", [])
    print("")
    print("=== Regression Summary ===")
    for r in rows:
        status = r["status"]
        cid = r["id"]
        metric = r["metric"]
        if metric == "error_to_cce":
            print(
                f"[{status}] {cid:<40} error_to_cce: "
                f"cur_abs={r.get('current_abs')}, base_abs={r.get('baseline_abs')}, "
                f"cur_rel={r.get('current_rel'):.4f}, base_rel={r.get('baseline_rel'):.4f}, "
                f"abs_worse={r.get('abs_worse'):.4f}, rel_worse={r.get('rel_worse'):.4f}"
            )
            continue

        cur = r.get("current")
        base = r.get("baseline")
        if "abs_diff" in r:
            print(
                f"[{status}] {cid:<40} {metric}: cur={cur}, base={base}, "
                f"abs_diff={r['abs_diff']}, rel_diff={r['rel_diff']:.4f}"
            )
        else:
            print(f"[{status}] {cid:<40} {metric}: cur={cur}, base={base}")
    print(f"Result: {'PASS' if summary.get('passed') else 'FAIL'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VF cost model regression suite")
    parser.add_argument(
        "--suite",
        type=str,
        default="regression_suite/cases/cost_model_regression_cases.json",
        help="Path to regression suite JSON",
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default="regression_suite/cases/baseline_queue_level4_ooo_transfer_delay.json",
        help="Path to queue_level4+ooo-transfer-delay baseline JSON",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="results/regression_suite/latest",
        help="Output directory for this regression run",
    )
    parser.add_argument(
        "--tier",
        type=str,
        choices=["smoke", "full"],
        default="smoke",
        help="Run smoke subset or full suite",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite baseline JSON with current run metrics",
    )
    parser.add_argument(
        "--ooo-model",
        type=str,
        default=None,
        help="Override suite default ooo_model for all simulate cases",
    )
    parser.add_argument(
        "--extra-main-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Extra arguments forwarded verbatim to main.py for all simulate cases",
    )
    args = parser.parse_args()

    suite_path = ROOT / args.suite
    baseline_path = ROOT / args.baseline
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    suite = _load_json(suite_path)
    suite = copy.deepcopy(suite)
    suite.setdefault("defaults", {})
    if args.ooo_model:
        suite["defaults"]["ooo_model"] = str(args.ooo_model)
    if args.extra_main_args:
        suite["defaults"]["extra_main_args"] = list(args.extra_main_args)
    current = _run_suite(suite, out_dir, tier=args.tier)
    current["meta"] = {
        "suite": suite.get("name", "unnamed_suite"),
        "tier": args.tier,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    current_path = out_dir / "current_metrics.json"
    _dump_json(current_path, current)
    print(f"[info] wrote current metrics: {current_path}")

    if args.update_baseline:
        baseline_payload = {
            "meta": current["meta"],
            "cases": current.get("cases", {}),
        }
        _dump_json(baseline_path, baseline_payload)
        print(f"[info] baseline updated: {baseline_path}")
        print("[info] done.")
        return

    if not baseline_path.exists():
        raise FileNotFoundError(
            f"Baseline not found: {baseline_path}. Run with --update-baseline first."
        )

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
