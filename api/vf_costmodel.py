from __future__ import annotations

from abc import ABC, abstractmethod

from api.frontend.schema import CanonicalVfInfo


class VfCostModel(ABC):
    @abstractmethod
    def predict_vf_cycles(self, vf_info: CanonicalVfInfo) -> int:
        """Predict cycles for the sole formal input contract."""
        pass


__all__ = [
    "VfCostModel",
]
