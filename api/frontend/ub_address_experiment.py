from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from .builder import VfInfoValidationError
from .core_lowering import CoreLoweringPass
from .schema import (
    AffineTerm,
    AffineExpression,
    CanonicalInstruction,
    CanonicalLoop,
    CanonicalMembar,
    CanonicalNode,
    CanonicalVfInfo,
)
from .validator import validate_canonical_vf_info


@dataclass(frozen=True)
class UbStaticAccess:
    base_object_id: str
    pointer_state_id: str
    pointer_initial_offset_bytes: AffineExpression
    access_offset_bytes: AffineExpression
    post_update_delta_bytes: AffineExpression
    access_kind: str
    span_bytes: int | None
    access_mode: str | None = None


@dataclass(frozen=True)
class PythonUbAddressExperimentMetadata:
    accesses_by_instruction: Mapping[str, tuple[UbStaticAccess, ...]]

    def validate(self, vf_info: CanonicalVfInfo) -> None:
        instruction_ids: set[str] = set()
        canonical_accesses: dict[str, set[tuple[str, str]]] = {}

        def visit(nodes: Iterable[CanonicalNode]) -> None:
            for node in nodes:
                if isinstance(node, CanonicalInstruction):
                    instruction_ids.add(node.instruction_id)
                    canonical_accesses[node.instruction_id] = {
                        (
                            operand.memory_access.base_object_id,
                            operand.memory_access.access_kind.value,
                        )
                        for operand in (*node.inputs, *node.outputs)
                        if operand.memory_access is not None
                    }
                elif isinstance(node, CanonicalLoop):
                    visit(node.body)

        visit(vf_info.context)
        for instruction_id, accesses in self.accesses_by_instruction.items():
            if instruction_id not in instruction_ids:
                raise ValueError(
                    f"UB experiment metadata references unknown instruction "
                    f"{instruction_id!r}"
                )
            for access in accesses:
                if access.base_object_id not in vf_info.storage_objects:
                    raise ValueError(
                        f"UB experiment metadata for {instruction_id!r} references "
                        f"unknown storage object {access.base_object_id!r}"
                    )
                if access.access_kind not in {"read", "write"}:
                    raise ValueError(
                        f"Unsupported UB access kind {access.access_kind!r}"
                    )
                if not access.pointer_state_id:
                    raise ValueError(
                        "UB experiment pointer_state_id must be non-empty"
                    )
                if (
                    access.base_object_id,
                    access.access_kind,
                ) not in canonical_accesses[instruction_id]:
                    raise ValueError(
                        f"UB experiment metadata for {instruction_id!r} does not "
                        "match a canonical memory operand"
                    )
                if access.span_bytes is not None and access.span_bytes <= 0:
                    raise ValueError("UB access span_bytes must be positive")


def _dtype_size_bytes(dtype: str | None) -> int | None:
    normalized = str(dtype or "").lower()
    if normalized in {"fp16", "bf16", "b16", "int16", "uint16"}:
        return 2
    if normalized in {"fp32", "b32", "int32", "uint32"}:
        return 4
    if normalized in {"int8", "uint8"}:
        return 1
    if normalized in {"fp64", "int64", "uint64"}:
        return 8
    return None


def _scale_affine(expression: AffineExpression, factor: int) -> AffineExpression:
    return AffineExpression(
        constant=expression.constant * factor,
        terms=tuple(
            AffineTerm(term.variable_id, term.coefficient * factor)
            for term in expression.terms
        ),
    )


def metadata_from_canonical(
    vf_info: CanonicalVfInfo,
) -> PythonUbAddressExperimentMetadata:
    """Derive no-update pointer metadata from canonical memory operands."""

    accesses_by_instruction: dict[str, tuple[UbStaticAccess, ...]] = {}

    def visit(nodes: Iterable[CanonicalNode]) -> None:
        for node in nodes:
            if isinstance(node, CanonicalLoop):
                visit(node.body)
                continue
            if not isinstance(node, CanonicalInstruction):
                continue
            static_accesses: list[UbStaticAccess] = []
            for operand in (*node.inputs, *node.outputs):
                memory = operand.memory_access
                if memory is None:
                    continue
                element_size = _dtype_size_bytes(operand.dtype)
                access_offset = (
                    _scale_affine(memory.offset, element_size)
                    if element_size is not None
                    else memory.offset
                )
                span_bytes = (
                    int(memory.span) * element_size
                    if memory.span is not None and element_size is not None
                    else None
                )
                static_accesses.append(
                    UbStaticAccess(
                        base_object_id=memory.base_object_id,
                        pointer_state_id=memory.base_object_id,
                        pointer_initial_offset_bytes=AffineExpression(),
                        access_offset_bytes=access_offset,
                        post_update_delta_bytes=AffineExpression(),
                        access_kind=memory.access_kind.value,
                        span_bytes=span_bytes,
                    )
                )
            if static_accesses:
                accesses_by_instruction[node.instruction_id] = tuple(
                    static_accesses
                )

    visit(vf_info.context)
    metadata = PythonUbAddressExperimentMetadata(accesses_by_instruction)
    metadata.validate(vf_info)
    return metadata


def _expression_payload(expression: AffineExpression) -> dict:
    return {
        "constant": expression.constant,
        "terms": [
            {
                "variable_id": term.variable_id,
                "coefficient": term.coefficient,
            }
            for term in expression.terms
        ],
    }


class ExperimentalCanonicalCoreLowering:
    """Attach Python-only UB address metadata after normal canonical lowering."""

    def lower(
        self,
        vf_info: CanonicalVfInfo,
        metadata: PythonUbAddressExperimentMetadata,
    ) -> dict:
        validation = validate_canonical_vf_info(vf_info)
        if not validation.ok:
            raise VfInfoValidationError(validation.errors)
        metadata.validate(vf_info)
        payload = CoreLoweringPass().lower(vf_info)
        accesses_by_instruction = metadata.accesses_by_instruction

        def visit(nodes: list[dict]) -> None:
            for node in nodes:
                if node.get("type") == "loop":
                    visit(node.get("body", []))
                    continue
                instruction_id = node.get("static_instruction_id")
                accesses = accesses_by_instruction.get(str(instruction_id), ())
                if not accesses:
                    continue
                node["ub_address_accesses"] = [
                    {
                        "base_object_id": access.base_object_id,
                        "pointer_state_id": access.pointer_state_id,
                        "pointer_initial_offset_bytes": _expression_payload(
                            access.pointer_initial_offset_bytes
                        ),
                        "access_offset_bytes": _expression_payload(
                            access.access_offset_bytes
                        ),
                        "post_update_delta_bytes": _expression_payload(
                            access.post_update_delta_bytes
                        ),
                        "access_kind": access.access_kind,
                        "span_bytes": access.span_bytes,
                        "access_mode": access.access_mode,
                    }
                    for access in accesses
                ]

        visit(payload["program"])
        payload["uarch"] = dict(payload.get("uarch", {}))
        payload["uarch"]["ub_dependency_mode"] = "range_overlap"
        payload["ub_address_experiment"] = True
        return payload


def remove_directional_membars(vf_info: CanonicalVfInfo) -> tuple[CanonicalVfInfo, int]:
    removed = 0

    def transform(nodes: Iterable[CanonicalNode]) -> tuple[CanonicalNode, ...]:
        nonlocal removed
        output: list[CanonicalNode] = []
        for node in nodes:
            if isinstance(node, CanonicalMembar) and node.barrier in {
                "VST_VLD",
                "VLD_VST",
            }:
                removed += 1
                continue
            if isinstance(node, CanonicalLoop):
                disables_unroll = any(
                    isinstance(child, CanonicalMembar)
                    and child.barrier in {"VST_VLD", "VLD_VST"}
                    for child in node.body
                )
                output.append(
                    replace(
                        node,
                        body=transform(node.body),
                        unroll=1 if disables_unroll and node.unroll > 1 else node.unroll,
                    )
                )
            else:
                output.append(node)
        return tuple(output)

    transformed = replace(vf_info, context=transform(vf_info.context))
    validation = validate_canonical_vf_info(transformed)
    if not validation.ok:
        raise VfInfoValidationError(validation.errors)
    return transformed, removed


__all__ = [
    "ExperimentalCanonicalCoreLowering",
    "PythonUbAddressExperimentMetadata",
    "UbStaticAccess",
    "metadata_from_canonical",
    "remove_directional_membars",
]
