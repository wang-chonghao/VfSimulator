from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any

from api.frontend.builder import VfInfoValidationError
from api.frontend.diagnostics import Diagnostic, DiagnosticSeverity
from api.frontend.schema import CanonicalVfInfo, SourceLocation
from api.frontend.serialization import canonical_vf_info_from_dict
from api.frontend.validator import validate_canonical_vf_info


_SCHEMA_PATH = Path(__file__).with_name("canonical_vf_info_v1.schema.json")


@lru_cache(maxsize=1)
def _canonical_schema_validator():
    try:
        validator_class = import_module("jsonschema").Draft202012Validator
    except ModuleNotFoundError as error:
        if error.name != "jsonschema":
            raise
        raise RuntimeError(
            "Canonical JSON validation requires the optional 'jsonschema' "
            "package; install it with 'python3 -m pip install jsonschema'"
        ) from error
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_class.check_schema(schema)
    return validator_class(schema)


def _json_path(parts) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


class CanonicalJsonVfInfoAdapter:
    """Load the versioned canonical JSON contract without legacy inference."""

    @staticmethod
    def load(path: str | Path) -> CanonicalVfInfo:
        source_path = Path(path)
        try:
            with source_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except json.JSONDecodeError as error:
            raise VfInfoValidationError(
                (
                    Diagnostic(
                        code="canonical_json_syntax_error",
                        severity=DiagnosticSeverity.ERROR,
                        message=error.msg,
                        location=SourceLocation(
                            source=str(source_path),
                            line=error.lineno,
                            column=error.colno,
                            path="$",
                        ),
                    ),
                )
            ) from error
        return CanonicalJsonVfInfoAdapter.from_payload(
            payload,
            source=str(source_path),
        )

    @staticmethod
    def from_payload(
        payload: Mapping[str, Any],
        *,
        source: str | None = None,
    ) -> CanonicalVfInfo:
        if not isinstance(payload, Mapping):
            raise CanonicalJsonVfInfoAdapter._decode_error(
                "CanonicalVfInfo JSON root must be an object",
                source=source,
            )
        schema_errors = sorted(
            _canonical_schema_validator().iter_errors(payload),
            key=lambda error: (
                tuple(str(part) for part in error.absolute_path),
                error.message,
            ),
        )
        if schema_errors:
            raise VfInfoValidationError(
                Diagnostic(
                    code="canonical_json_schema_error",
                    severity=DiagnosticSeverity.ERROR,
                    message=error.message,
                    location=SourceLocation(
                        source=source,
                        path=_json_path(error.absolute_path),
                    ),
                    context={
                        "validator": error.validator,
                        "schema_path": _json_path(error.absolute_schema_path),
                    },
                )
                for error in schema_errors
            )
        try:
            vf_info = canonical_vf_info_from_dict(payload)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise CanonicalJsonVfInfoAdapter._decode_error(
                f"Cannot decode CanonicalVfInfo v1 payload: {error}",
                source=source,
            ) from error

        result = validate_canonical_vf_info(vf_info)
        if not result.ok:
            raise VfInfoValidationError(result.errors)
        return vf_info

    @staticmethod
    def _decode_error(message: str, *, source: str | None) -> VfInfoValidationError:
        return VfInfoValidationError(
            (
                Diagnostic(
                    code="canonical_payload_decode_error",
                    severity=DiagnosticSeverity.ERROR,
                    message=message,
                    location=SourceLocation(source=source, path="$"),
                ),
            )
        )


__all__ = ["CanonicalJsonVfInfoAdapter"]
