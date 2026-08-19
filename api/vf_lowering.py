from __future__ import annotations

from typing import Any, Dict

from api.frontend.core_lowering import CoreLoweringPass
from api.frontend.legacy_vf_info_adapter import LegacyVfInfoAdapter
from api.vf_info import VFInfo


class VFInfoLowerer:
    """Compatibility wrapper that routes migration-period VFInfo via canonical IR."""

    def lower(self, vf_info: VFInfo, dtype: str | None = None) -> Dict[str, Any]:
        canonical = LegacyVfInfoAdapter().to_canonical(
            vf_info,
            source={"adapter": "legacy_vf_info"},
        )
        payload = CoreLoweringPass().lower(canonical)
        if dtype is not None:
            payload["dtype"] = str(dtype)
        return payload
