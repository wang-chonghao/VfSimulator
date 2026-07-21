from __future__ import annotations

from abc import ABC, abstractmethod
from vfsimulator.api.vf_info import (
    Membar,
    MemInfo,
    ValueInfo,
    ValueStorageKind,
    VFInfo,
    VFInst,
    VFLoop,
    VFNode,
    canonicalize_vf_info,
)


class VfCostModel(ABC):
    @abstractmethod
    def predict_vf_cycles(self, vf_info: VFInfo) -> int:
        pass


__all__ = [
    "Membar",
    "MemInfo",
    "ValueInfo",
    "ValueStorageKind",
    "VFInfo",
    "VFInst",
    "VFLoop",
    "VFNode",
    "VfCostModel",
    "canonicalize_vf_info",
]
