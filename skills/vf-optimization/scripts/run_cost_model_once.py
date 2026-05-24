#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


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


def parse_param_kv(items: list[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid --set-param item: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"invalid --set-param item: {item}")
        if re.fullmatch(r"-?\d+", value):
            out[key] = int(value)
        elif re.fullmatch(r"-?\d+\.\d+", value):
            out[key] = float(value)
        elif value.lower() in {"true", "false"}:
            out[key] = value.lower() == "true"
        else:
            out[key] = value
    return out


def resolve_repo_path(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return p.resolve()


def run_main(args: argparse.Namespace, model_dir: Path, input_path: Path) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--out_dir",
        str(model_dir),
    ]
    if args.trace:
        cmd.extend(["--trace", str(input_path)])
    else:
        cmd.extend(["--cce", str(input_path)])
        if args.cce_kernel:
            cmd.extend(["--cce-kernel", args.cce_kernel])
    if args.theoretical_limit_vloop_only:
        cmd.append("--theoretical-limit-vloop-only")
    if args.theoretical_limit_vloop_only_legacy_forwarding_direct_issue:
        cmd.append("--theoretical-limit-vloop-only-legacy-forwarding-direct-issue")
    if args.three_ports:
        cmd.append("--three-ports")

    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True)
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise RuntimeError(f"main.py failed ({proc.returncode})\n{text}")
    return {"vf_end": parse_vf_end(text), "stdout": text}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the current VF cost model for one JSON trace or CCE file.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trace", help="JSON trace path, repo-relative or absolute")
    group.add_argument("--cce", help="CCE/DSL path, repo-relative or absolute")
    parser.add_argument("--cce-kernel", default=None, help="Select one __VEC_SCOPE__ kernel for --cce input")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument("--theoretical-limit-vloop-only", action="store_true")
    parser.add_argument("--theoretical-limit-vloop-only-legacy-forwarding-direct-issue", action="store_true")
    parser.add_argument("--three-ports", action="store_true", help="Enable experimental 3-port model")
    parser.add_argument(
        "--set-param",
        action="append",
        default=[],
        help="Override JSON trace params, e.g. I=64. Only valid with --trace.",
    )
    args = parser.parse_args()

    if args.theoretical_limit_vloop_only and args.theoretical_limit_vloop_only_legacy_forwarding_direct_issue:
        raise RuntimeError("Select only one theoretical-limit variant")
    if args.set_param and not args.trace:
        raise RuntimeError("--set-param currently applies only to --trace input")

    out_dir = resolve_repo_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_dir = out_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)

    source_path = resolve_repo_path(args.trace or args.cce)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    input_path = source_path
    if args.trace:
        trace_obj = load_json(source_path)
        overrides = parse_param_kv(args.set_param)
        if overrides:
            trace_obj.setdefault("params", {})
            trace_obj["params"].update(overrides)
        input_path = out_dir / "trace_input.json"
        dump_json(input_path, trace_obj)

    run = run_main(args, model_dir, input_path)
    (out_dir / "model_stdout.log").write_text(run["stdout"], encoding="utf-8")

    result = {
        "input": str(source_path),
        "input_kind": "trace" if args.trace else "cce",
        "trace_input": str(input_path) if args.trace else "",
        "cce_kernel": args.cce_kernel or "",
        "theoretical_limit_vloop_only": bool(args.theoretical_limit_vloop_only),
        "theoretical_limit_vloop_only_legacy_forwarding_direct_issue": bool(
            args.theoretical_limit_vloop_only_legacy_forwarding_direct_issue
        ),
        "three_ports": bool(args.three_ports),
        "vf_end": int(run["vf_end"]),
    }
    dump_json(out_dir / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
