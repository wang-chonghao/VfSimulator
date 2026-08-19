from __future__ import annotations

from abc import ABC, abstractmethod
import warnings

from api.frontend.schema import CanonicalVfInfo
from api.frontend.legacy_vf_info_adapter import LegacyVfInfoAdapter
from api.vf_info import (
    Membar,
    MemInfo,
    ValueInfo,
    ValueStorageKind,
    VFInfo,
    VFAlias,
    VFInst,
    VFLoop,
    VFNode,
    canonicalize_vf_info,
)


class VfCostModel(ABC):
    @abstractmethod
    def predict_canonical_vf_cycles(self, vf_info: CanonicalVfInfo) -> int:
        pass

    def predict_legacy_vf_cycles(self, vf_info: VFInfo) -> int:
        canonical = LegacyVfInfoAdapter().to_canonical(vf_info)
        return self.predict_canonical_vf_cycles(canonical)

    def predict_vf_cycles(self, vf_info: VFInfo) -> int:
        """Deprecated compatibility wrapper for migration-period ``VFInfo``."""

        warnings.warn(
            "predict_vf_cycles(VFInfo) is deprecated; use "
            "predict_canonical_vf_cycles(CanonicalVfInfo) or "
            "predict_legacy_vf_cycles(VFInfo)",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.predict_legacy_vf_cycles(vf_info)


__all__ = [
    "Membar",
    "MemInfo",
    "ValueInfo",
    "ValueStorageKind",
    "VFInfo",
    "VFAlias",
    "VFInst",
    "VFLoop",
    "VFNode",
    "VfCostModel",
    "canonicalize_vf_info",
]
