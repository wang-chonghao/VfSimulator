#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstructionProfile:
    """Immutable scheduling properties for one requested instruction form."""

    profile_id: int
    op: str
    requested_form: str
    resolved_form: str
    dtype: str
    op_class: str
    fu_type: str
    dispatch_exu: str
    latency: int
