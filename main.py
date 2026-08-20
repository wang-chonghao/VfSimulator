#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from typing import Any, Dict

from api.frontend import CanonicalVfInfo, CoreLoweringPass
from api.input_api import InputAPI
from core.flatten import Flattener
from core.idu import IDU
from core.ifu import IFUUnroll
from core.ooo_factory import (
    apply_theoretical_limit_overrides,
    create_ooo_core,
    resolve_model_uarch,
)
from core.param_db import ParamDB
from core.program_analysis import ProgramAnalyzer
from core.simulator_runner import dump_model_warnings, run_simulation


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VF Simulator Main Entry")
    parser.add_argument("--trace", type=str, help="Path to the JSON trace file")
    parser.add_argument(
        "--cce",
        type=str,
        help="Path to a CCE/DSL file. When provided, parse __VEC_SCOPE__ into CanonicalVfInfo.",
    )
    parser.add_argument(
        "--cce-kernel",
        type=str,
        default=None,
        help="Select a specific __VEC_SCOPE__ kernel name when the CCE file has multiple VF kernels.",
    )
    parser.add_argument(
        "--out_dir",
        type=str,
        default="results",
        help="Directory to save simulation logs",
    )
    parser.add_argument(
        "--theoretical-limit-vloop-only",
        action="store_true",
        help=(
            "Theoretical limit variant: keep top-level VLOOPv2 start timing "
            "but remove cross-iteration exposure gates"
        ),
    )
    parser.add_argument(
        "--theoretical-limit-vloop-only-legacy-forwarding-direct-issue",
        action="store_true",
        help=(
            "Theoretical limit variant: vloop-only + legacy-forwarding + "
            "single-queue direct issue (bypass SHQ->EXQ staging)"
        ),
    )
    parser.add_argument(
        "--three-ports",
        dest="three_ports",
        action="store_true",
        help=(
            "Enable the experimental 3-port VF model: 3 EXU/EXQ issue ports, "
            "3 VLD per cycle, and EXU01 ops may use EXU0/1/2. VST remains 1 per cycle."
        ),
    )
    return parser


def resolve_trace_path(base_dir: str, trace_arg: str | None) -> str:
    trace_path = trace_arg or os.path.join(base_dir, "VFtest", "VADD_oneloop.json")
    if not os.path.isabs(trace_path):
        trace_path = os.path.join(base_dir, trace_path)
    return trace_path


def resolve_input_path(base_dir: str, path_arg: str) -> str:
    if os.path.isabs(path_arg):
        return path_arg
    return os.path.join(base_dir, path_arg)


def load_input_canonical_vf_info(
    base_dir: str,
    args: argparse.Namespace,
) -> tuple[CanonicalVfInfo, str]:
    if args.trace and args.cce:
        raise RuntimeError("Please provide only one input: --trace or --cce")

    if args.cce:
        cce_path = resolve_input_path(base_dir, args.cce)
        if not os.path.exists(cce_path):
            raise RuntimeError(f"CCE file not found: {cce_path}")
        print(f"[INFO] Loading CCE: {cce_path}")
        if args.cce_kernel:
            print(f"[INFO] CCE kernel = {args.cce_kernel}")
        return (
            InputAPI.load_cce_canonical(cce_path, kernel_name=args.cce_kernel),
            cce_path,
        )

    trace_path = resolve_trace_path(base_dir, args.trace)
    if not os.path.exists(trace_path):
        raise RuntimeError(f"Trace file not found: {trace_path}")
    print(f"[INFO] Loading trace: {trace_path}")
    return InputAPI.load_legacy_json_canonical(trace_path), trace_path


def build_uarch(
    db: ParamDB,
    trace_uarch: Dict[str, Any],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    uarch = dict(db.get_uarch())
    if trace_uarch:
        uarch.update(trace_uarch)

    uarch["ooo_model"] = "queue_level4"

    if args.three_ports:
        uarch["three_ports_mode"] = True
        uarch["issue_ports"] = 3
        uarch["load_ports"] = 3
        uarch["store_ports"] = 1
        print("[INFO] three ports mode = ON")

    theoretical_limit_enabled = bool(
        args.theoretical_limit_vloop_only
        or args.theoretical_limit_vloop_only_legacy_forwarding_direct_issue
    )
    if theoretical_limit_enabled:
        uarch["theoretical_limit_mode"] = True
        print("[INFO] theoretical limit mode = ON")
    if args.theoretical_limit_vloop_only:
        uarch["theoretical_limit_vloop_only"] = True
        print("[INFO] theoretical limit variant = vloop-only")
    if args.theoretical_limit_vloop_only_legacy_forwarding_direct_issue:
        uarch["theoretical_limit_vloop_only"] = True
        uarch["theoretical_limit_legacy_forwarding"] = True
        uarch["theoretical_limit_direct_issue"] = True
        print("[INFO] theoretical limit variant = vloop-only + legacy-forwarding + direct-issue")

    uarch = resolve_model_uarch(uarch)
    if theoretical_limit_enabled:
        uarch = apply_theoretical_limit_overrides(uarch)

    print("[INFO] mainline model = queue_level4")
    print("[INFO] consumer release rule = start +", int(uarch["consumer_release_start_offset"]))
    if theoretical_limit_enabled:
        print(
            "[INFO] theoretical limit config =",
            f"vloop_to_dispatch_delay={int(uarch.get('vloop_to_dispatch_delay', 0))},",
            f"idu_dispatch_start_advance={int(uarch.get('idu_dispatch_start_advance', 0))},",
            f"idu_to_ooo_delay={int(uarch.get('idu_to_ooo_delay', 0))},",
            f"exq_recv_delay={int(uarch.get('exq_recv_delay', 0))}",
        )
    return uarch


def scan_instruction_fallback_warnings(program, db: ParamDB, dtype: str) -> None:
    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == "inst":
            op = str(node.get("op", ""))
            form = str(node.get("form", "") or dtype)
            db.get_inst_form(op, form=form, dtype=dtype)
            return
        if ntype == "loop":
            visit(node.get("body", []))

    visit(program)


def write_warning_log(
    results_dir: str,
    vreg_warnings: list[Dict[str, Any]],
    instruction_warnings: list[Dict[str, Any]],
) -> None:
    if not vreg_warnings and not instruction_warnings:
        return
    print("[WARN] Low-confidence scenario detected:")
    for warning in vreg_warnings:
        print(
            "[WARN]",
            f"{warning['loop_path']}: expanded_vreg_namespace={warning['expanded_vreg_namespace']}",
            f"> preg_num={warning['preg_num']}",
        )
    if instruction_warnings:
        unsupported = sum(
            1
            for warning in instruction_warnings
            if str(warning.get("kind", "")).startswith("unsupported_isa")
        )
        timing = sum(
            1
            for warning in instruction_warnings
            if str(warning.get("kind", "")).startswith("missing_")
        )
        print(
            "[WARN]",
            f"instruction fallback warnings: unsupported={unsupported}, timing={timing}",
        )
    if dump_model_warnings(
        results_dir,
        instruction_warnings=instruction_warnings,
        vreg_warnings=vreg_warnings,
    ):
        print(f"Wrote {os.path.join(results_dir, 'model_warnings.json')}")


def main():
    args = build_arg_parser().parse_args()
    base_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        vf_info, _ = load_input_canonical_vf_info(base_dir, args)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return

    lowering = CoreLoweringPass()
    lowering.ensure_current_core_compatible(vf_info)
    trace = lowering.lower(vf_info)

    dtype = trace.get("dtype", "fp32")
    params = trace.get("params", {}) or {}
    values = trace.get("values", {}) or {}
    program = trace.get("program")
    if program is None:
        raise RuntimeError("trace.json missing key 'program'")
    db = ParamDB(base_dir=base_dir)
    print("[INFO] canonical input: legacy lowering and vreg normalization = OFF")
    scan_instruction_fallback_warnings(program, db, dtype)

    analyzer = ProgramAnalyzer(params, values=values)
    top_block_loop_bounds = analyzer.infer_top_block_loop_bounds(program)
    total_top_blocks = len(top_block_loop_bounds)
    print("[INFO] top block loop bounds =", top_block_loop_bounds)
    print("[INFO] total top blocks =", total_top_blocks)

    loop_bounds = top_block_loop_bounds.get(0, [])
    linear = Flattener(params).flatten(program)

    trace_uarch = trace.get("uarch", {}) or {}
    if not isinstance(trace_uarch, dict):
        raise RuntimeError("trace.json key 'uarch' must be a dict when provided")
    uarch = build_uarch(db, trace_uarch, args)
    dynamic_instruction_limit = int(
        uarch.get("canonical_dynamic_instruction_limit", 20_000)
    )
    ifu = IFUUnroll(
        linear,
        params,
        pdb=db,
        dtype=dtype,
        structured_value_identity=True,
        structured_dynamic_instruction_limit=dynamic_instruction_limit,
    )

    idu = IDU(
        uarch,
        db,
        params=params,
        loop_bounds=loop_bounds,
        total_top_blocks=total_top_blocks,
        top_block_loop_bounds=top_block_loop_bounds,
        dtype=dtype,
    )

    results_dir = args.out_dir
    if not os.path.isabs(results_dir):
        results_dir = os.path.join(base_dir, results_dir)

    ooo = create_ooo_core(uarch, db, dtype=dtype, values=values)
    sim_result = run_simulation(
        ifu=ifu,
        idu=idu,
        ooo=ooo,
        uarch=uarch,
        params=params,
        results_dir=results_dir,
        values=values,
    )

    print("Done. cycles_executed =", int(sim_result["cycles_executed"]))

    vreg_capacity_warnings = analyzer.collect_vreg_capacity_warnings(
        program,
        int(ooo.preg_num),
    )
    write_warning_log(results_dir, vreg_capacity_warnings, db.get_warnings())

    print(f"Wrote {os.path.join(results_dir, 'sim_history.json')}")
    print(f"Wrote Perfetto trace to {sim_result['trace_path']}")
    print(f"Wrote logs to {results_dir}")
    print("VF end cycle (with drain) =", int(sim_result["vf_end_cycle"]))
    print(f"Wrote idu_to_ooo.json to {results_dir}")
    print(f"Wrote vloop_trace.json to {results_dir}")


if __name__ == "__main__":
    main()
