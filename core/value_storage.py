#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, Mapping


REGISTER = "Register"
UB = "UB"
SCALAR = "Scalar"
_LANE_SUFFIX_RE = re.compile(r"^(?P<base>.+)_lane\d+$")


def infer_legacy_storage(name: Any) -> str:
    if not isinstance(name, str):
        return SCALAR
    lower = name.lower()
    if lower.startswith("mem"):
        return UB
    if lower.startswith("v"):
        return REGISTER
    return SCALAR


def normalize_value_storage(values: Any | None) -> Dict[str, str]:
    if not values:
        return {}
    if not isinstance(values, Mapping):
        return {}

    out: Dict[str, str] = {}
    for key, raw in values.items():
        value_id = str(key)
        storage = None
        if isinstance(raw, Mapping):
            storage = raw.get("storage") or raw.get("location")
            value_id = str(raw.get("value_id") or raw.get("name") or value_id)
        else:
            storage = getattr(raw, "storage", None) or getattr(raw, "location", None)
            value_id = str(getattr(raw, "value_id", None) or getattr(raw, "name", None) or value_id)
        if storage in (REGISTER, UB, SCALAR):
            out[value_id] = str(storage)
    return out


class ValueStorageLookup:
    def __init__(self, values: Any | None = None):
        self.storage_by_id = normalize_value_storage(values)

    def storage_of(self, name: Any) -> str:
        if isinstance(name, str):
            storage = self.storage_by_id.get(name)
            if storage:
                return storage
            match = _LANE_SUFFIX_RE.match(name)
            if match:
                storage = self.storage_by_id.get(match.group("base"))
                if storage:
                    return storage
        return infer_legacy_storage(name)

    def is_register(self, name: Any) -> bool:
        return self.storage_of(name) == REGISTER

    def is_ub(self, name: Any) -> bool:
        return self.storage_of(name) == UB

    def is_scalar(self, name: Any) -> bool:
        return self.storage_of(name) == SCALAR
