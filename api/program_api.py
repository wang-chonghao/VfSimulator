from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from api.simulator_costmodel import CoreVfCostModel
from core.model_config import normalize_model_name
from core.program_ir import VfSimProgram


def predict_from_program(
    program: VfSimProgram,
    *,
    config_root: str | Path | None = None,
    out_dir: str | Path = "results/program_api",
    model: str = "mainline",
    dump_trace_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Run VfSimulator from the stable in-memory program API."""
    if not isinstance(program, VfSimProgram):
        raise TypeError("program must be a VfSimProgram")
    model_name = normalize_model_name(model)

    payload = program.to_payload()
    if dump_trace_path is not None:
        _dump_payload(payload, dump_trace_path, model=model_name)

    base_dir = Path(config_root) if config_root is not None else Path(__file__).resolve().parents[1]
    result = CoreVfCostModel(
        base_dir=base_dir,
        out_dir=out_dir,
        dtype=program.dtype,
        model=model_name,
    ).run_program(program)
    return {
        "cycles": int(result["vf_end_cycle"]),
        "model": model_name,
        "payload": payload,
        "raw": result,
        "trace_path": str(dump_trace_path) if dump_trace_path is not None else None,
    }


def _dump_payload(payload: Dict[str, Any], path: str | Path, *, model: str) -> None:
    dump_path = Path(path)
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    with dump_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "model": model,
                "payload": payload,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
