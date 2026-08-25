from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import (
    CanonicalVfInfo,
    CanonicalJsonVfInfoAdapter,
    ScalarValue,
    ValidationResult,
    VfInfoBuilder,
    validate_canonical_vf_info,
)


class InputAPI:
    """
    Repository-level input boundary for simulator frontends.

    All formal loaders produce the versioned ``CanonicalVfInfo`` contract.
    """

    @staticmethod
    def load_json(path: str | Path) -> CanonicalVfInfo:
        """Load and validate a versioned CanonicalVfInfo JSON document."""

        return CanonicalJsonVfInfoAdapter.load(path)

    @staticmethod
    def load_cce(
        path: str | Path,
        kernel_name: str | None = None,
        loop_params: Dict[str, int] | None = None,
    ) -> CanonicalVfInfo:
        return parse_cce_canonical_vf_info(
            path,
            kernel_name=kernel_name,
            loop_params=loop_params,
        )

    @staticmethod
    def validate_canonical_vf_info(vf_info: CanonicalVfInfo) -> ValidationResult:
        """Validate the versioned frontend contract without mutating it."""

        return validate_canonical_vf_info(vf_info)

    @staticmethod
    def new_builder(
        *,
        params: Mapping[str, int] | None = None,
        uarch: Mapping[str, ScalarValue] | None = None,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> VfInfoBuilder:
        """Create an explicit builder for the versioned canonical contract."""

        return VfInfoBuilder(params=params, uarch=uarch, source=source)
