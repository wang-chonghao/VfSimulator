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
    CanonicalStorageObject,
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


def _source_location_to_dict(value: SourceLocation | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "source": value.source,
        "line": value.line,
        "column": value.column,
        "path": value.path,
    }


def _dependency_to_dict(value: DependencyRef) -> dict[str, Any]:
    return {
        "producer_node_id": value.producer_node_id,
        "kind": value.kind.value,
        "operand_index": value.operand_index,
    }


def _operand_to_dict(value: CanonicalOperand) -> dict[str, Any]:
    memory = None
    if value.memory_access is not None:
        memory = {
            "base_object_id": value.memory_access.base_object_id,
            "offset": {
                "constant": value.memory_access.offset.constant,
                "terms": [
                    {
                        "variable_id": term.variable_id,
                        "coefficient": term.coefficient,
                    }
                    for term in value.memory_access.offset.terms
                ],
            },
            "access_kind": value.memory_access.access_kind.value,
            "span": value.memory_access.span,
            "alias_group": value.memory_access.alias_group,
        }
    return {
        "value_id": value.value_id,
        "role": value.role.value,
        "dtype": value.dtype,
        "memory_access": memory,
    }


def _node_to_dict(value) -> dict[str, Any]:
    if isinstance(value, CanonicalInstruction):
        return {
            "kind": "instruction",
            "instruction_id": value.instruction_id,
            "opcode": value.opcode,
            "instruction_class": value.instruction_class.value,
            "form": value.form,
            "inputs": [_operand_to_dict(item) for item in value.inputs],
            "outputs": [_operand_to_dict(item) for item in value.outputs],
            "dependencies": [
                _dependency_to_dict(item) for item in value.dependencies
            ],
            "attributes": dict(value.attributes),
            "source_location": _source_location_to_dict(value.source_location),
        }
    if isinstance(value, CanonicalLoop):
        return {
            "kind": "loop",
            "loop_id": value.loop_id,
            "induction": {
                "variable_id": value.induction.variable_id,
                "start": value.induction.start,
                "step": value.induction.step,
            },
            "count": value.count,
            "unroll": value.unroll,
            "carried_values": [
                {
                    "logical_id": item.logical_id,
                    "entry_value_id": item.entry_value_id,
                    "back_edge_value_id": item.back_edge_value_id,
                    "exit_value_id": item.exit_value_id,
                }
                for item in value.carried_values
            ],
            "body": [_node_to_dict(item) for item in value.body],
            "source_location": _source_location_to_dict(value.source_location),
        }
    if isinstance(value, CanonicalMembar):
        return {
            "kind": "membar",
            "instruction_id": value.instruction_id,
            "barrier": value.barrier,
            "dependencies": [
                _dependency_to_dict(item) for item in value.dependencies
            ],
            "source_location": _source_location_to_dict(value.source_location),
        }
    raise TypeError(f"unsupported canonical node: {type(value).__name__}")


def canonical_vf_info_to_dict(vf_info: CanonicalVfInfo) -> dict[str, Any]:
    """Encode ``CanonicalVfInfo`` using the language-neutral v1 contract."""

    return {
        "schema_version": vf_info.schema_version,
        "context": [_node_to_dict(node) for node in vf_info.context],
        "values": {
            definition_id: {
                "definition_id": value.definition_id,
                "logical_id": value.logical_id,
                "storage": value.storage.value,
                "dtype": value.dtype,
                "shape": list(value.shape),
                "producer_node_id": value.producer_node_id,
                "storage_object_id": value.storage_object_id,
                "source_location": _source_location_to_dict(value.source_location),
            }
            for definition_id, value in vf_info.values.items()
        },
        "storage_objects": {
            object_id: {
                "object_id": value.object_id,
                "storage": value.storage.value,
                "shape": list(value.shape),
                "source_location": _source_location_to_dict(value.source_location),
            }
            for object_id, value in vf_info.storage_objects.items()
        },
        "params": dict(vf_info.params),
        "uarch": dict(vf_info.uarch),
        "source": dict(vf_info.source),
    }


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
        producer_node_id=value["producer_node_id"],
        kind=DependencyKind(value["kind"]),
        operand_index=value.get("operand_index"),
    )


def _operand(value: Mapping[str, Any]) -> CanonicalOperand:
    memory_value = value.get("memory_access")
    memory = None
    if memory_value is not None:
        offset = memory_value["offset"]
        memory = MemoryAccess(
            base_object_id=memory_value["base_object_id"],
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
            producer_node_id=value.get("producer_node_id"),
            storage_object_id=value.get("storage_object_id"),
            source_location=_source_location(value.get("source_location")),
        )
        for definition_id, value in payload["values"].items()
    }
    storage_objects = {
        object_id: CanonicalStorageObject(
            object_id=value["object_id"],
            storage=StorageKind(value["storage"]),
            shape=tuple(value.get("shape", ())),
            source_location=_source_location(value.get("source_location")),
        )
        for object_id, value in payload.get("storage_objects", {}).items()
    }
    return CanonicalVfInfo(
        context=tuple(_node(node) for node in payload["context"]),
        values=values,
        storage_objects=storage_objects,
        params=dict(payload.get("params", {})),
        uarch=dict(payload.get("uarch", {})),
        source=dict(payload.get("source", {})),
        schema_version=payload["schema_version"],
    )
