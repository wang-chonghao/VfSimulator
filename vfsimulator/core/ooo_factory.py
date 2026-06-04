#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict

from vfsimulator.core.ooo_mainline import OoOCoreMainline
from vfsimulator.core.uarch_normalize import (
    MAINLINE_MODEL_NAME,
    apply_theoretical_limit_overrides,
    get_ooo_model_name,
    normalize_mainline_uarch,
)


def resolve_model_uarch(uarch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Public compatibility entry point used by the simulator and optimizer code.
    Even though only one backend remains, callers still go through this helper
    so the mainline defaults stay centralized in one place.
    """
    _ = get_ooo_model_name(uarch)
    return normalize_mainline_uarch(uarch)


def create_ooo_core(uarch: Dict[str, Any], pdb, dtype: str = "fp32"):
    """
    Build the single supported OoO core from a normalized mainline uarch.
    """
    resolved = dict(uarch) if bool(uarch.get("_ooo_uarch_resolved", False)) else resolve_model_uarch(uarch)
    return OoOCoreMainline(resolved, pdb, dtype=dtype)
