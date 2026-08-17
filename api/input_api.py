from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping

from api.cce_adapter import parse_cce_vf_info
from api.frontend import (
    CanonicalVfInfo,
    ScalarValue,
    ValidationResult,
    VfInfoBuilder,
    validate_canonical_vf_info,
)
from api.json_adapter import JsonVfInfoAdapter
from api.vf_info import VFInfo


class InputAPI:
    """
    Repository-level input boundary for simulator frontends.

    CCE and legacy JSON loaders currently return migration-period ``VFInfo``.
    Versioned ``CanonicalVfInfo`` is exposed through an explicit validation
    boundary until the adapters and core lowering pass migrate to schema v1.
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

    @staticmethod
    def validate_canonical_vf_info(vf_info: CanonicalVfInfo) -> ValidationResult:
        """Validate the versioned frontend contract without mutating it."""

        return validate_canonical_vf_info(vf_info)

    @staticmethod
    def new_vf_info_builder(
        *,
        params: Mapping[str, int] | None = None,
        uarch: Mapping[str, ScalarValue] | None = None,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> VfInfoBuilder:
        """Create an explicit builder for the versioned canonical contract."""

        return VfInfoBuilder(params=params, uarch=uarch, source=source)
