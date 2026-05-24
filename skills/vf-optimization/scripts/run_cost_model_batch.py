#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def parse_vf_end(text: str) -> int:
    match = re.search(r"VF end cycle \(with drain\)\s*=\s*(\d+)", text)
    if not match:
        raise RuntimeError("Cannot parse VF end cycle from main.py output")
    return int(match.group(1))


def resolve_repo_path(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return p.resolve()


def theoretical_flag(value: str) -> list[str]:
    if not value:
        return []
    if value == "vloop-only":
        return ["--theoretical-limit-vloop-only"]
    if value == "vloop-only-legacy-forwarding-direct-issue":
        return ["--theoretical-limit-vloop-only-legacy-forwarding-direct-issue"]
    raise ValueError(f"unsupported theoretical limit variant: {value}")


def run_main(
    *,
    input_kind: str,
    input_path: Path,
    out_dir: Path,
    cce_kernel: str,
    theoretical_limit: str,
    three_ports: bool,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    model_dir = out_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--out_dir",
        str(model_dir),
    ]
    if input_kind == "trace":
        cmd.extend(["--trace", str(input_path)])
    elif input_kind == "cce":
        cmd.extend(["--cce", str(input_path)])
        if cce_kernel:
            cmd.extend(["--cce-kernel", cce_kernel])
    else:
        raise ValueError(f"unsupported input kind: {input_kind}")

    cmd.extend(theoretical_flag(theoretical_limit))
    if three_ports:
        cmd.append("--three-ports")

    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True)
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    (out_dir / "model_stdout.log").write_text(text, encoding="utf-8")
    if proc.returncode != 0:
        return {"ok": False, "error": f"main.py failed ({proc.returncode})"}
    return {"ok": True, "vf_end": parse_vf_end(text)}


def prepare_trace_case(case: Dict[str, Any], out_dir: Path) -> Path:
    trace_path = resolve_repo_path(case["trace"])
    if not trace_path.exists():
        raise FileNotFoundError(trace_path)

    trace_obj = load_json(trace_path)
    params = case.get("params", {}) or {}
    if params:
        trace_obj.setdefault("params", {})
        trace_obj["params"].update(params)

    cooked = out_dir / "trace_input.json"
    dump_json(cooked, trace_obj)
    return cooked


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Batch run the current VF cost model. Manifest format: "
            "{defaults:{...}, cases:[{id, trace|cce, params?, cce_kernel?, theoretical_limit?, three_ports?}]}"
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    manifest_path = resolve_repo_path(args.manifest)
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)

    out_root = resolve_repo_path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    suite = load_json(manifest_path)
    defaults = suite.get("defaults", {}) or {}
    cases = suite.get("cases", []) or []

    rows: List[Dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        case_id = case.get("id", f"case_{idx}")
        run_dir = out_root / case_id
        row: Dict[str, Any] = {"id": case_id}
        try:
            has_trace = bool(case.get("trace"))
            has_cce = bool(case.get("cce"))
            if has_trace == has_cce:
                raise ValueError("case must provide exactly one of 'trace' or 'cce'")

            input_kind = "trace" if has_trace else "cce"
            if input_kind == "trace":
                input_path = prepare_trace_case(case, run_dir)
                source_path = resolve_repo_path(case["trace"])
            else:
                source_path = resolve_repo_path(case["cce"])
                if not source_path.exists():
                    raise FileNotFoundError(source_path)
                input_path = source_path

            theoretical_limit = str(case.get("theoretical_limit", defaults.get("theoretical_limit", "")) or "")
            three_ports = bool(case.get("three_ports", defaults.get("three_ports", False)))
            cce_kernel = str(case.get("cce_kernel", defaults.get("cce_kernel", "")) or "")

            ret = run_main(
                input_kind=input_kind,
                input_path=input_path,
                out_dir=run_dir,
                cce_kernel=cce_kernel,
                theoretical_limit=theoretical_limit,
                three_ports=three_ports,
            )
            row.update(
                {
                    "input_kind": input_kind,
                    "input": str(source_path),
                    "cce_kernel": cce_kernel,
                    "theoretical_limit": theoretical_limit,
                    "three_ports": three_ports,
                    "ok": bool(ret.get("ok")),
                }
            )
            if ret.get("ok"):
                row["vf_end"] = int(ret["vf_end"])
            else:
                row["error"] = ret.get("error", "unknown")
        except Exception as exc:
            row.update({"ok": False, "error": str(exc)})
        rows.append(row)

    summary = {"manifest": str(manifest_path), "rows": rows}
    dump_json(out_root / "summary.json", summary)

    csv_path = out_root / "summary.csv"
    keys = [
        "id",
        "ok",
        "vf_end",
        "input_kind",
        "input",
        "cce_kernel",
        "theoretical_limit",
        "three_ports",
        "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in keys})

    print(f"[OK] wrote: {out_root / 'summary.json'}")
    print(f"[OK] wrote: {csv_path}")


if __name__ == "__main__":
    main()
