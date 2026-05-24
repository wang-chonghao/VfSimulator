#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "regression_suite" / "cases" / "cost_model_regression_cases.json"
OUT_ROOT = ROOT / "results" / "three_ports_test"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def iter_insts(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, list):
        for x in node:
            yield from iter_insts(x)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "inst":
        yield node
    body = node.get("body")
    if isinstance(body, list):
        for x in body:
            yield from iter_insts(x)


def apply_case_transform(trace_obj: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(trace_obj)

    params = case.get("params", {}) or {}
    if params:
        out.setdefault("params", {})
        for key, value in params.items():
            out["params"][key] = value

    transform = case.get("transform", {}) or {}
    replace_op = transform.get("replace_op")
    if isinstance(replace_op, dict):
        src_op = replace_op.get("from")
        dst_op = replace_op.get("to")
        if src_op and dst_op:
            for inst in iter_insts(out.get("program", [])):
                if inst.get("op") == src_op:
                    inst["op"] = dst_op

    return out


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


def run_model(trace_obj: Dict[str, Any], out_dir: Path, ooo_model: str, three_ports: bool) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "trace_input.json"
    dump_json(trace_path, trace_obj)

    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--trace",
        str(trace_path),
        "--out_dir",
        str(out_dir / "model"),
        "--ooo-model",
        ooo_model,
    ]
    if three_ports:
        cmd.append("--three-ports")

    stdout = run_cmd(cmd)
    (out_dir / "model_stdout.log").write_text(stdout, encoding="utf-8")
    return parse_vf_end(stdout)


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def build_report(rows: List[Dict[str, Any]], suite_name: str) -> str:
    mean_improvement = sum(float(r["improvement_pct"]) for r in rows) / len(rows) if rows else 0.0
    total_dual = sum(int(r["dual_vf_end"]) for r in rows)
    total_three = sum(int(r["three_vf_end"]) for r in rows)
    weighted = ((total_dual - total_three) / total_dual * 100.0) if total_dual else 0.0

    lines: List[str] = []
    lines.append("# Three Ports Test")
    lines.append("")
    lines.append(f"Suite: `{suite_name}`")
    lines.append("")
    lines.append("对比口径：同一测试集 case 只跑模型，不重跑 CCE。")
    lines.append("")
    lines.append("- 双发射：默认配置，`issue_ports=2`，`load_ports=2`，`store_ports=1`。")
    lines.append("- 三发射：命令行加入 `--three-ports`，运行时覆盖为 `issue_ports=3`，`load_ports=3`，`store_ports=1`，并将 `EXU01` 指令解释为可走 `EXU0/1/2`。")
    lines.append("- 性能提升：`(双发射模型时间 - 三发射模型时间) / 双发射模型时间 * 100%`。")
    lines.append("")
    lines.append("| 算子名称 | 双发射模型结果 | 三发射模型结果 | 性能提升 |")
    lines.append("|---|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| `{r['case_id']}` | {r['dual_vf_end']} | {r['three_vf_end']} | {fmt_pct(float(r['improvement_pct']))} |"
        )
    lines.append("")
    lines.append(f"平均性能提升（逐 case 算术平均）：{fmt_pct(mean_improvement)}")
    lines.append(f"总体加权性能提升（按 cycle 总和）：{fmt_pct(weighted)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    suite = load_json(SUITE_PATH)
    suite_name = str(suite.get("name", SUITE_PATH.name))
    defaults = suite.get("defaults", {}) or {}
    default_ooo_model = str(defaults.get("ooo_model", "consumer-done"))

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []

    for case in suite.get("cases", []) or []:
        if case.get("kind", "simulate") != "simulate":
            continue

        case_id = str(case["id"])
        trace_path = ROOT / str(case["trace"])
        trace_obj = apply_case_transform(load_json(trace_path), case)
        ooo_model = str(case.get("ooo_model", default_ooo_model))

        case_dir = OUT_ROOT / case_id
        dual = run_model(trace_obj, case_dir / "dual_ports", ooo_model=ooo_model, three_ports=False)
        three = run_model(trace_obj, case_dir / "three_ports", ooo_model=ooo_model, three_ports=True)
        improvement_pct = ((dual - three) / dual * 100.0) if dual else 0.0

        row = {
            "case_id": case_id,
            "dual_vf_end": int(dual),
            "three_vf_end": int(three),
            "improvement_pct": improvement_pct,
        }
        rows.append(row)
        print(f"{case_id}: dual={dual}, three={three}, improvement={improvement_pct:.2f}%")

    dump_json(OUT_ROOT / "three_ports_compare.json", {"suite": suite_name, "rows": rows})
    (OUT_ROOT / "three_ports_compare.md").write_text(build_report(rows, suite_name), encoding="utf-8")
    print(f"Wrote {OUT_ROOT / 'three_ports_compare.md'}")


if __name__ == "__main__":
    main()
