#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "unroll_test" / "json"
OUT_ROOT = ROOT / "results" / "three_ports_test"
ITERS = [16, 48, 64, 96]


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def factors_le8(n: int) -> List[int]:
    return [u for u in range(1, 9) if n % u == 0]


def run_cmd(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True)
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{text}")
    return text


def parse_vf_end(stdout_text: str) -> int:
    m = re.search(r"VF end cycle \(with drain\)\s*=\s*(\d+)", stdout_text)
    if not m:
        raise RuntimeError("Cannot parse 'VF end cycle (with drain)' from main.py output")
    return int(m.group(1))


def make_trace(src_json: Path, I: int, U: int) -> Dict[str, Any]:
    trace = load_json(src_json)
    trace.setdefault("params", {})
    trace["params"]["I"] = I
    trace["params"]["U"] = U
    return trace


def run_model(src_json: Path, case_id: str, I: int, U: int, three_ports: bool) -> int:
    mode = "three_ports" if three_ports else "dual_ports"
    run_dir = OUT_ROOT / "unroll_runs" / case_id / f"I{I}" / f"U{U}" / mode
    run_dir.mkdir(parents=True, exist_ok=True)

    trace = make_trace(src_json, I, U)
    trace_path = run_dir / "trace_input.json"
    dump_json(trace_path, trace)

    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--trace",
        str(trace_path),
        "--out_dir",
        str(run_dir / "model"),
    ]
    if three_ports:
        cmd.append("--three-ports")

    stdout = run_cmd(cmd)
    (run_dir / "model_stdout.log").write_text(stdout, encoding="utf-8")
    return parse_vf_end(stdout)


def best_result(results: List[Dict[str, int]]) -> Dict[str, int]:
    # If several unroll factors have the same model time, prefer the larger U.
    # This matches the unroll reports where plateaued minima are usually treated
    # as the largest equally-good unroll choice.
    return min(results, key=lambda r: (r["vf_end"], -r["U"]))


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def build_report(rows: List[Dict[str, Any]]) -> str:
    mean_improvement = sum(float(r["improvement_pct"]) for r in rows) / len(rows) if rows else 0.0
    total_dual = sum(int(r["dual_best_vf_end"]) for r in rows)
    total_three = sum(int(r["three_best_vf_end"]) for r in rows)
    weighted = ((total_dual - total_three) / total_dual * 100.0) if total_dual else 0.0

    lines: List[str] = []
    lines.append("# Three Ports Unroll Best Compare")
    lines.append("")
    lines.append("Scope: `unroll_test/json/*.json`, model-only, no CCE rerun.")
    lines.append("")
    lines.append("For each `(operator, I)` case, the script sweeps all factors `U <= 8` of `I` and compares the best dual-port model result with the best `--three-ports` model result.")
    lines.append("")
    lines.append("Performance improvement = `(dual best time - three-ports best time) / dual best time * 100%`.")
    lines.append("")
    lines.append("| Operator | I | Dual Best U | Dual Best Time | Three-Ports Best U | Three-Ports Best Time | Performance Improvement |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| `{r['operator']}` | {r['I']} | {r['dual_best_U']} | {r['dual_best_vf_end']} | "
            f"{r['three_best_U']} | {r['three_best_vf_end']} | {fmt_pct(float(r['improvement_pct']))} |"
        )
    lines.append("")
    lines.append(f"Average performance improvement: {fmt_pct(mean_improvement)}")
    lines.append(f"Weighted performance improvement by total cycles: {fmt_pct(weighted)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    json_cases = sorted(JSON_DIR.glob("*.json"))
    for src_json in json_cases:
        op_name = src_json.stem
        for I in ITERS:
            dual_results: List[Dict[str, int]] = []
            three_results: List[Dict[str, int]] = []
            for U in factors_le8(I):
                dual_vf = run_model(src_json, op_name, I, U, three_ports=False)
                three_vf = run_model(src_json, op_name, I, U, three_ports=True)
                dual_results.append({"U": U, "vf_end": dual_vf})
                three_results.append({"U": U, "vf_end": three_vf})
                print(f"{op_name} I={I} U={U}: dual={dual_vf}, three={three_vf}")

            dual_best = best_result(dual_results)
            three_best = best_result(three_results)
            improvement = (
                (dual_best["vf_end"] - three_best["vf_end"]) / dual_best["vf_end"] * 100.0
                if dual_best["vf_end"]
                else 0.0
            )
            row = {
                "operator": op_name,
                "I": I,
                "unrolls": factors_le8(I),
                "dual_results": dual_results,
                "three_results": three_results,
                "dual_best_U": dual_best["U"],
                "dual_best_vf_end": dual_best["vf_end"],
                "three_best_U": three_best["U"],
                "three_best_vf_end": three_best["vf_end"],
                "improvement_pct": improvement,
            }
            rows.append(row)
            print(
                f"BEST {op_name} I={I}: dual U={dual_best['U']} {dual_best['vf_end']}, "
                f"three U={three_best['U']} {three_best['vf_end']}, improvement={improvement:.2f}%"
            )

    dump_json(OUT_ROOT / "three_ports_unroll_best_compare.json", {"rows": rows})
    report = build_report(rows)
    (OUT_ROOT / "three_ports_unroll_best_compare.md").write_text(report, encoding="utf-8")
    print(f"Wrote {OUT_ROOT / 'three_ports_unroll_best_compare.md'}")


if __name__ == "__main__":
    main()
