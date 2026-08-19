from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DynamicMemoryRange:
    base_object_id: str | None
    byte_start: int | None
    byte_end: int | None
    access_kind: str
    unresolved_reason: str | None = None

    @property
    def resolved(self) -> bool:
        return (
            self.base_object_id is not None
            and self.byte_start is not None
            and self.byte_end is not None
            and self.byte_end > self.byte_start
            and self.unresolved_reason is None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_object_id": self.base_object_id,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "access_kind": self.access_kind,
            "resolved": self.resolved,
            "unresolved_reason": self.unresolved_reason,
        }


def ranges_overlap(lhs: DynamicMemoryRange, rhs: DynamicMemoryRange) -> bool:
    if not lhs.resolved or not rhs.resolved:
        return False
    return bool(
        lhs.base_object_id == rhs.base_object_id
        and max(int(lhs.byte_start), int(rhs.byte_start))
        < min(int(lhs.byte_end), int(rhs.byte_end))
    )


def _evaluate_affine(
    expression: Mapping[str, Any],
    symbols: Mapping[str, int],
) -> tuple[int | None, str | None]:
    try:
        value = int(expression.get("constant", 0))
        for term in expression.get("terms", []):
            variable = str(term.get("variable_id", ""))
            if variable not in symbols:
                return None, f"unknown_symbol:{variable}"
            value += int(term.get("coefficient", 0)) * int(symbols[variable])
        return value, None
    except (TypeError, ValueError):
        return None, "invalid_affine_expression"


class UbDynamicAddressGenerator:
    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = {
            str(key): int(value)
            for key, value in params.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        self.pointer_current: dict[str, int] = {}

    @staticmethod
    def _iteration_symbols(iteration_path: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        symbols: dict[str, int] = {}
        for item in iteration_path:
            variable = item.get("induction_variable")
            value = item.get("induction_value")
            if variable is not None and value is not None:
                symbols[str(variable)] = int(value)
        return symbols

    def attach(self, inst: dict[str, Any]) -> None:
        static_accesses = inst.get("ub_address_accesses", [])
        if not static_accesses:
            return
        symbols = dict(self.params)
        symbols.update(self._iteration_symbols(inst.get("iteration_path", [])))
        dynamic: list[dict[str, Any]] = []
        for access in static_accesses:
            state_id = str(access.get("pointer_state_id", ""))
            base_object_id = access.get("base_object_id")
            access_kind = str(access.get("access_kind", ""))
            initial, initial_error = _evaluate_affine(
                access.get("pointer_initial_offset_bytes", {}), symbols
            )
            offset, offset_error = _evaluate_affine(
                access.get("access_offset_bytes", {}), symbols
            )
            update, update_error = _evaluate_affine(
                access.get("post_update_delta_bytes", {}), symbols
            )
            span = access.get("span_bytes")
            reason = initial_error or offset_error or update_error
            if span is None:
                reason = reason or "unknown_span"
            else:
                try:
                    span = int(span)
                    if span <= 0:
                        reason = reason or "invalid_span"
                except (TypeError, ValueError):
                    reason = reason or "invalid_span"
                    span = None

            current = self.pointer_current.get(state_id)
            if current is None and initial is not None:
                current = int(initial)
                self.pointer_current[state_id] = current
            byte_start = (
                int(current) + int(offset)
                if current is not None and offset is not None
                else None
            )
            byte_end = (
                byte_start + int(span)
                if byte_start is not None and span is not None
                else None
            )
            item = DynamicMemoryRange(
                base_object_id=(str(base_object_id) if base_object_id else None),
                byte_start=byte_start,
                byte_end=byte_end,
                access_kind=access_kind,
                unresolved_reason=reason,
            )
            dynamic.append(item.as_dict())
            if current is not None and update is not None:
                self.pointer_current[state_id] = int(current) + int(update)
        inst["memory_ranges"] = dynamic


def dependency_conflict(
    prior: DynamicMemoryRange,
    current: DynamicMemoryRange,
) -> tuple[bool, str | None, str | None]:
    if prior.resolved and current.resolved:
        return ranges_overlap(prior, current), None, None
    if prior.base_object_id is not None and current.base_object_id is not None:
        if prior.base_object_id != current.base_object_id:
            return False, None, None
        return (
            True,
            "same_base",
            current.unresolved_reason or prior.unresolved_reason or "unresolved_range",
        )
    return True, "global", current.unresolved_reason or prior.unresolved_reason or "unknown_base"


__all__ = [
    "DynamicMemoryRange",
    "UbDynamicAddressGenerator",
    "dependency_conflict",
    "ranges_overlap",
]
