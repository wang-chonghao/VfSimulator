from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping
import warnings

from api.cce_adapter import parse_cce_canonical_vf_info, parse_cce_vf_info
from api.frontend import (
    CanonicalVfInfo,
    CanonicalJsonVfInfoAdapter,
    ScalarValue,
    ValidationResult,
    VfInfoBuilder,
    validate_canonical_vf_info,
)
from api.frontend.value_versioning import ValueVersioningPass
from api.frontend.legacy_vf_info_adapter import LegacyVfInfoAdapter
from api.json_adapter import JsonVfInfoAdapter, LegacyCanonicalJsonAdapter
from api.vf_info import VFInfo


class InputAPI:
    """
    Repository-level input boundary for simulator frontends.

    Migration-period ``VFInfo`` and versioned ``CanonicalVfInfo`` have explicit
    entry points. No loader silently falls back between legacy and canonical
    contracts.
    """

    @staticmethod
    def load_legacy_json_vf_info(path: str | Path) -> VFInfo:
        return JsonVfInfoAdapter.load(path)

    @staticmethod
    def load_json_trace(path: str | Path) -> VFInfo:
        warnings.warn(
            "load_json_trace() is deprecated; use load_legacy_json_vf_info() "
            "or load_legacy_json_canonical()",
            DeprecationWarning,
            stacklevel=2,
        )
        return InputAPI.load_legacy_json_vf_info(path)

    @staticmethod
    def load_legacy_json_canonical(path: str | Path) -> CanonicalVfInfo:
        return LegacyCanonicalJsonAdapter.load(path)

    @staticmethod
    def load_canonical_json(path: str | Path) -> CanonicalVfInfo:
        """Load and validate a versioned CanonicalVfInfo JSON document."""

        return CanonicalJsonVfInfoAdapter.load(path)

    @staticmethod
    def load_cce_vf_info(
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
    def load_cce_file(
        path: str | Path,
        kernel_name: str | None = None,
        loop_params: Dict[str, int] | None = None,
    ) -> VFInfo:
        warnings.warn(
            "load_cce_file() is deprecated; use load_cce_canonical() or "
            "load_cce_vf_info()",
            DeprecationWarning,
            stacklevel=2,
        )
        return InputAPI.load_cce_vf_info(path, kernel_name, loop_params)

    @staticmethod
    def load_cce_canonical(
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
    def to_canonical(
        vf_info: VFInfo,
        *,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> CanonicalVfInfo:
        """Version logical register names into canonical value definitions."""

        return ValueVersioningPass().run(vf_info, source=source)

    @staticmethod
    def adapt_legacy_vf_info(
        vf_info: VFInfo,
        *,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> CanonicalVfInfo:
        """Repair legacy omissions and convert migration-period input."""

        return LegacyVfInfoAdapter().to_canonical(vf_info, source=source)

    @staticmethod
    def new_vf_info_builder(
        *,
        params: Mapping[str, int] | None = None,
        uarch: Mapping[str, ScalarValue] | None = None,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> VfInfoBuilder:
        """Create an explicit builder for the versioned canonical contract."""

        return VfInfoBuilder(params=params, uarch=uarch, source=source)
