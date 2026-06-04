from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from vfsimulator.api.cce_adapter import parse_cce_vf_info
from vfsimulator.api.vf_costmodel import VFInfo, VfCostModel
from vfsimulator.api.vf_lowering import VFInfoLowerer
from vfsimulator.core.flatten import Flattener
from vfsimulator.core.idu import IDU
from vfsimulator.core.ifu import IFUUnroll
from vfsimulator.core.model_config import MAINLINE, apply_vfsim_model, normalize_model_name
from vfsimulator.core.ooo_factory import create_ooo_core
from vfsimulator.core.param_db import ParamDB
from vfsimulator.core.program_ir import VfSimProgram, coerce_trace_program
from vfsimulator.core.program_analysis import ProgramAnalyzer
from vfsimulator.core.simulator_runner import run_simulation
from vfsimulator.core.vreg_live_range_normalization import normalize_program_vreg_live_ranges


@dataclass
class CoreVfCostModel(VfCostModel):
    """Concrete VF cost model backed by the current core simulator."""

    base_dir: str | Path = Path(__file__).resolve().parents[1]
    out_dir: str | Path = "results/api_costmodel"
    dtype: str = "fp32"
    model: str = MAINLINE

    def predict_vf_cycles(self, vf_info: VFInfo) -> int:
        return int(self.run_vf_info(vf_info)["vf_end_cycle"])

    def run_vf_info(self, vf_info: VFInfo) -> Dict[str, Any]:
        payload = VFInfoLowerer().lower(vf_info, dtype=self.dtype)
        return self.run_payload(payload)

    def run_payload(self, payload: Dict[str, Any], *, model: str | None = None) -> Dict[str, Any]:
        base_dir = Path(self.base_dir)
        dtype = str(payload.get("dtype", self.dtype))
        model_name = normalize_model_name(model or self.model)
        params = payload.get("params", {}) or {}
        if not isinstance(params, dict):
            raise RuntimeError("payload key 'params' must be a dict when provided")

        program = payload.get("program")
        if program is None:
            raise RuntimeError("payload missing key 'program'")
        program = coerce_trace_program(program)

        program, norm_stats = normalize_program_vreg_live_ranges(program)
        analyzer = ProgramAnalyzer(params)
        top_block_loop_bounds = analyzer.infer_top_block_loop_bounds(program)
        loop_bounds = top_block_loop_bounds.get(0, [])
        linear = Flattener(params).flatten(program)

        db = ParamDB(base_dir=str(base_dir))
        ifu = IFUUnroll(linear, params, pdb=db, dtype=dtype)
        uarch = dict(db.get_uarch())
        trace_uarch = payload.get("uarch", {}) or {}
        if not isinstance(trace_uarch, dict):
            raise RuntimeError("payload key 'uarch' must be a dict when provided")
        uarch.update(trace_uarch)
        uarch = apply_vfsim_model(uarch, model_name)

        idu = IDU(
            uarch,
            db,
            params=params,
            loop_bounds=loop_bounds,
            total_top_blocks=len(top_block_loop_bounds),
            top_block_loop_bounds=top_block_loop_bounds,
            dtype=dtype,
        )
        ooo = create_ooo_core(uarch, db, dtype=dtype)

        results_dir = Path(self.out_dir)
        if not results_dir.is_absolute():
            results_dir = base_dir / results_dir

        result = run_simulation(
            ifu=ifu,
            idu=idu,
            ooo=ooo,
            uarch=uarch,
            params=params,
            results_dir=str(results_dir),
        )
        result["linear_inst_count"] = len(linear)
        result["normalization_stats"] = norm_stats
        result["model"] = model_name
        return result

    def run_program(self, program: VfSimProgram, *, model: str | None = None) -> Dict[str, Any]:
        return self.run_payload(program.to_payload(), model=model)


def predict_cce_file_cycles(
    path: str | Path,
    *,
    kernel_name: str | None = None,
    loop_params: Dict[str, int] | None = None,
    out_dir: str | Path = "results/api_costmodel",
) -> int:
    vf_info = parse_cce_vf_info(path, kernel_name=kernel_name, loop_params=loop_params)
    return CoreVfCostModel(out_dir=out_dir).predict_vf_cycles(vf_info)
