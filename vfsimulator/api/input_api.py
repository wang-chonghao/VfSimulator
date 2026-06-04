from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from vfsimulator.api.cce_adapter import parse_cce_vf_info
from vfsimulator.api.vf_lowering import VFInfoLowerer


class InputAPI:
    """
    Repository-level input boundary for simulator frontends.

    Planned modes:
    - CCE source file -> normalized VF payload
    - legacy JSON file -> normalized VF payload
    """

    @staticmethod
    def load_json_trace(path: str | Path) -> Dict[str, Any]:
        trace_path = Path(path)
        with trace_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise RuntimeError("JSON trace root must be an object")
        if "program" not in payload:
            raise RuntimeError("trace.json missing key 'program'")
        return payload

    @staticmethod
    def load_cce_file(
        path: str | Path,
        kernel_name: str | None = None,
        loop_params: Dict[str, int] | None = None,
    ) -> Dict[str, Any]:
        vf_info = parse_cce_vf_info(
            path,
            kernel_name=kernel_name,
            loop_params=loop_params,
        )
        return VFInfoLowerer().lower(vf_info)
