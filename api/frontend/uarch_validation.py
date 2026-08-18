from __future__ import annotations

import math
import json
from pathlib import Path
from typing import Any, Mapping

from api.frontend.diagnostics import (
    Diagnostic,
    DiagnosticSeverity,
    ValidationResult,
)


_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "configs/uarch_override_schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
UARCH_FIELD_SPECS = dict(_SCHEMA["fields"])
UARCH_FIELD_TYPES = {
    name: spec["type"] for name, spec in UARCH_FIELD_SPECS.items()
}
DEPRECATED_UARCH_FIELDS = frozenset(_SCHEMA.get("deprecated_fields", ()))
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def validate_uarch_overrides(uarch: Any) -> ValidationResult:
    """Validate rules that apply to every public uarch override entry."""

    if not isinstance(uarch, Mapping):
        return ValidationResult(
            (
                Diagnostic(
                    "invalid_scalar_map",
                    DiagnosticSeverity.ERROR,
                    "uarch must be a mapping",
                    context={"path": "uarch"},
                ),
            )
        )

    diagnostics: list[Diagnostic] = []
    for key, value in uarch.items():
        if not isinstance(key, str):
            diagnostics.append(
                Diagnostic(
                    "invalid_scalar_attribute",
                    DiagnosticSeverity.ERROR,
                    "uarch only supports string keys",
                    context={"path": "uarch", "key_type": type(key).__name__},
                )
            )
            continue
        path = f"uarch.{key}"
        valid_scalar = (
            value is None
            or isinstance(value, (bool, str))
            or (
                isinstance(value, int)
                and not isinstance(value, bool)
                and _INT64_MIN <= value <= _INT64_MAX
            )
            or (isinstance(value, float) and math.isfinite(value))
        )
        if not valid_scalar:
            diagnostics.append(
                Diagnostic(
                    "invalid_scalar_attribute",
                    DiagnosticSeverity.ERROR,
                    f"{path} must be a finite JSON scalar",
                    context={"path": path, "value_type": type(value).__name__},
                )
            )
            continue

        expected_type = UARCH_FIELD_TYPES.get(key)
        type_matches = (
            expected_type is None
            or (expected_type == "integer" and type(value) is int)
            or (expected_type == "boolean" and type(value) is bool)
            or (expected_type == "string" and type(value) is str)
        )
        if not type_matches:
            diagnostics.append(
                Diagnostic(
                    "uarch_field_type_mismatch",
                    DiagnosticSeverity.ERROR,
                    f"{path} must use {expected_type} type",
                    context={
                        "path": path,
                        "field": key,
                        "expected_type": expected_type,
                        "actual_type": type(value).__name__,
                    },
                )
            )

    diagnostics.extend(
        Diagnostic(
            "deprecated_uarch_field",
            DiagnosticSeverity.ERROR,
            "Deprecated uarch field is no longer accepted",
            context={"path": f"uarch.{name}", "field": name},
        )
        for name in sorted(DEPRECATED_UARCH_FIELDS & uarch.keys())
    )
    return ValidationResult(tuple(diagnostics))


__all__ = [
    "DEPRECATED_UARCH_FIELDS",
    "UARCH_FIELD_SPECS",
    "UARCH_FIELD_TYPES",
    "validate_uarch_overrides",
]
