from __future__ import annotations

import math
from typing import Any, Mapping

from api.frontend.diagnostics import (
    Diagnostic,
    DiagnosticSeverity,
    ValidationResult,
)


DEPRECATED_UARCH_FIELDS = frozenset({"load_done_latency"})
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


__all__ = ["DEPRECATED_UARCH_FIELDS", "validate_uarch_overrides"]
