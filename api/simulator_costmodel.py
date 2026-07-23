from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from api.cce_adapter import parse_cce_vf_info
from api.json_adapter import JsonVfInfoAdapter
from api.vf_costmodel import VFInfo, VfCostModel, canonicalize_vf_info
from api.vf_lowering import VFInfoLowerer
from core.flatten import Flattener
from core.idu import IDU
from core.ifu import IFUUnroll
from core.ooo_factory import create_ooo_core, resolve_model_uarch
from core.param_db import ParamDB
from core.program_analysis import ProgramAnalyzer
from core.program_canonicalization import canonicalize_single_super_iteration_loops
from core.simulator_runner import run_simulation
from core.vreg_live_range_normalization import normalize_program_vreg_live_ranges


@dataclass
class CoreVfCostModel(VfCostModel):
    """Concrete VF cost model backed by the current core simulator."""

    base_dir: str | Path = Path(__file__).resolve().parents[1]
    out_dir: str | Path = "results/api_costmodel"
    dtype: str = "fp32"

    def predict_vf_cycles(self, vf_info: VFInfo) -> int:
        return int(self.run_vf_info(vf_info)["vf_end_cycle"])

    def run_vf_info(self, vf_info: VFInfo) -> Dict[str, Any]:
        canonical = canonicalize_vf_info(vf_info)
        payload = VFInfoLowerer().lower(canonical)
        return self._run_lowered_payload(payload)

    def run_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Compatibility entry: adapt a JSON-shaped payload to VfInfo first."""
        return self.run_vf_info(JsonVfInfoAdapter.from_payload(payload))

    def _run_lowered_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        base_dir = Path(self.base_dir)
        dtype = str(payload.get("dtype", self.dtype))
        params = payload.get("params", {}) or {}
        if not isinstance(params, dict):
            raise RuntimeError("payload key 'params' must be a dict when provided")

        program = payload.get("program")
        if program is None:
            raise RuntimeError("payload missing key 'program'")
        values = payload.get("values", {}) or {}

        db = ParamDB(base_dir=str(base_dir))
        program, norm_stats = normalize_program_vreg_live_ranges(program, values=values)
        program, canonicalization_stats = canonicalize_single_super_iteration_loops(
            program,
            params,
            pdb=db,
            dtype=dtype,
        )
        analyzer = ProgramAnalyzer(params, values=values)
        top_block_loop_bounds = analyzer.infer_top_block_loop_bounds(program)
        loop_bounds = top_block_loop_bounds.get(0, [])
        linear = Flattener(params).flatten(program)

        ifu = IFUUnroll(linear, params, pdb=db, dtype=dtype)
        uarch = dict(db.get_uarch())
        trace_uarch = payload.get("uarch", {}) or {}
        if not isinstance(trace_uarch, dict):
            raise RuntimeError("payload key 'uarch' must be a dict when provided")
        uarch.update(trace_uarch)
        uarch = self._mainline_uarch(uarch)

        idu = IDU(
            uarch,
            db,
            params=params,
            loop_bounds=loop_bounds,
            total_top_blocks=len(top_block_loop_bounds),
            top_block_loop_bounds=top_block_loop_bounds,
            dtype=dtype,
        )
        ooo = create_ooo_core(uarch, db, dtype=dtype, values=values)

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
            values=values,
        )
        result["linear_inst_count"] = len(linear)
        result["normalization_stats"] = norm_stats
        result["canonicalization_stats"] = canonicalization_stats
        return result

    @staticmethod
    def _mainline_uarch(uarch: Dict[str, Any]) -> Dict[str, Any]:
        uarch["ooo_model"] = "queue_level4"
        return resolve_model_uarch(uarch)


def predict_cce_file_cycles(
    path: str | Path,
    *,
    kernel_name: str | None = None,
    loop_params: Dict[str, int] | None = None,
    out_dir: str | Path = "results/api_costmodel",
) -> int:
    vf_info = parse_cce_vf_info(path, kernel_name=kernel_name, loop_params=loop_params)
    return CoreVfCostModel(out_dir=out_dir).predict_vf_cycles(vf_info)
