from __future__ import annotations

from pathlib import Path
from typing import Dict

from vfsimulator.api.cce_adapter import parse_cce_vf_info
from vfsimulator.api.json_adapter import JsonVfInfoAdapter
from vfsimulator.api.vf_info import VFInfo


class InputAPI:
    """
    Repository-level input boundary for simulator frontends.

    All frontends return the same canonical ``VFInfo`` structure.
    """

    @staticmethod
    def load_json_trace(path: str | Path) -> VFInfo:
        return JsonVfInfoAdapter.load(path)

    @staticmethod
    def load_cce_file(
        path: str | Path,
        kernel_name: str | None = None,
        loop_params: Dict[str, int] | None = None,
    ) -> VFInfo:
        return parse_cce_vf_info(
            path,
            kernel_name=kernel_name,
            loop_params=loop_params,
        )
