from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api.frontend.schema import (
    AccessKind,
    AffineExpression,
    AffineTerm,
    CanonicalInstruction,
    CanonicalLoop,
    CanonicalMembar,
    CanonicalOperand,
    CanonicalValue,
    CanonicalVfInfo,
    DependencyKind,
    DependencyRef,
    InductionVariable,
    InstructionClass,
    LoopCarriedValue,
    MemoryAccess,
    OperandRole,
    SourceLocation,
    StorageKind,
)


def _source_location(value: Any) -> SourceLocation | None:
    if value is None:
        return None
    return SourceLocation(
        source=value.get("source"),
        line=value.get("line"),
        column=value.get("column"),
        path=value.get("path"),
    )


def _dependency(value: Mapping[str, Any]) -> DependencyRef:
    return DependencyRef(
        producer_instruction_id=value["producer_instruction_id"],
        kind=DependencyKind(value["kind"]),
        operand_index=value.get("operand_index"),
    )


def _operand(value: Mapping[str, Any]) -> CanonicalOperand:
    memory_value = value.get("memory_access")
    memory = None
    if memory_value is not None:
        offset = memory_value["offset"]
        memory = MemoryAccess(
            base_value_id=memory_value["base_value_id"],
            offset=AffineExpression(
                constant=offset["constant"],
                terms=tuple(
                    AffineTerm(term["variable_id"], term["coefficient"])
                    for term in offset["terms"]
                ),
            ),
            access_kind=AccessKind(memory_value["access_kind"]),
            span=memory_value.get("span"),
            alias_group=memory_value.get("alias_group"),
        )
    return CanonicalOperand(
        value_id=value["value_id"],
        role=OperandRole(value["role"]),
        dtype=value.get("dtype"),
        memory_access=memory,
    )


def _node(value: Mapping[str, Any]):
    kind = value["kind"]
    if kind == "instruction":
        return CanonicalInstruction(
            instruction_id=value["instruction_id"],
            opcode=value["opcode"],
            instruction_class=InstructionClass(value["instruction_class"]),
            form=value["form"],
            inputs=tuple(_operand(item) for item in value.get("inputs", ())),
            outputs=tuple(_operand(item) for item in value.get("outputs", ())),
            dependencies=tuple(
                _dependency(item) for item in value.get("dependencies", ())
            ),
            attributes=dict(value.get("attributes", {})),
            source_location=_source_location(value.get("source_location")),
        )
    if kind == "loop":
        induction = value["induction"]
        return CanonicalLoop(
            loop_id=value["loop_id"],
            induction=InductionVariable(
                induction["variable_id"], induction["start"], induction["step"]
            ),
            count=value["count"],
            unroll=value["unroll"],
            carried_values=tuple(
                LoopCarriedValue(
                    item["logical_id"],
                    item["entry_value_id"],
                    item["back_edge_value_id"],
                    item["exit_value_id"],
                )
                for item in value.get("carried_values", ())
            ),
            body=tuple(_node(item) for item in value.get("body", ())),
            source_location=_source_location(value.get("source_location")),
        )
    if kind == "membar":
        return CanonicalMembar(
            instruction_id=value["instruction_id"],
            barrier=value["barrier"],
            dependencies=tuple(
                _dependency(item) for item in value.get("dependencies", ())
            ),
            source_location=_source_location(value.get("source_location")),
        )
    raise ValueError(f"unsupported canonical node kind: {kind!r}")


def canonical_vf_info_from_dict(payload: Mapping[str, Any]) -> CanonicalVfInfo:
    """Decode the language-neutral CanonicalVfInfo v1 serialization shape."""

    values = {
        definition_id: CanonicalValue(
            definition_id=value["definition_id"],
            logical_id=value["logical_id"],
            storage=StorageKind(value["storage"]),
            dtype=value["dtype"],
            shape=tuple(value.get("shape", ())),
            producer_instruction_id=value.get("producer_instruction_id"),
            source_location=_source_location(value.get("source_location")),
        )
        for definition_id, value in payload["values"].items()
    }
    return CanonicalVfInfo(
        context=tuple(_node(node) for node in payload["context"]),
        values=values,
        params=dict(payload.get("params", {})),
        uarch=dict(payload.get("uarch", {})),
        source=dict(payload.get("source", {})),
        schema_version=payload["schema_version"],
    )
