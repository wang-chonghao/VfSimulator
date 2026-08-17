from __future__ import annotations

from typing import Any, Iterable

from api.frontend.builder import VfInfoValidationError
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from api.frontend.schema import (
    AffineExpression,
    CanonicalInstruction,
    CanonicalLoop,
    CanonicalMembar,
    CanonicalNode,
    CanonicalOperand,
    CanonicalVfInfo,
    InstructionClass,
    SourceLocation,
    StorageKind,
)
from api.frontend.validator import validate_canonical_vf_info


class CanonicalCoreCompatibilityError(ValueError):
    def __init__(self, issues: Iterable[str]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "CanonicalVfInfo uses semantics not yet supported by the current "
            f"Python Core: {'; '.join(self.issues)}"
        )


def _source_location(location: SourceLocation | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return {
        "source": location.source,
        "line": location.line,
        "column": location.column,
        "path": location.path,
    }


def _affine_expression(expression: AffineExpression) -> dict[str, Any]:
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


class CoreLoweringPass:
    """Convert validated canonical input to the current Python Core payload."""

    def lower(self, vf_info: CanonicalVfInfo) -> dict[str, Any]:
        validation = validate_canonical_vf_info(vf_info)
        if not validation.ok:
            raise VfInfoValidationError(validation.errors)

        ub_core_ids = {
            object_id: f"__canonical_ub__{object_id}"
            for object_id in vf_info.storage_objects
        }
        values: dict[str, dict[str, Any]] = {
            definition_id: {
                "value_id": definition_id,
                "logical_id": value.logical_id,
                "storage": value.storage.value,
                "dtype": value.dtype,
                "shape": list(value.shape),
                "producer_node_id": value.producer_node_id,
                "storage_object_id": value.storage_object_id,
            }
            for definition_id, value in vf_info.values.items()
            if value.storage != StorageKind.UB
        }
        for object_id, storage_object in vf_info.storage_objects.items():
            dtype = next(
                (
                    value.dtype
                    for value in vf_info.values.values()
                    if value.storage_object_id == object_id
                ),
                None,
            )
            core_id = ub_core_ids[object_id]
            values[core_id] = {
                "value_id": core_id,
                "logical_id": object_id,
                "storage": StorageKind.UB.value,
                "dtype": dtype,
                "shape": list(storage_object.shape),
                "storage_object_id": object_id,
            }

        return {
            "dtype": "fp32",
            "params": dict(vf_info.params),
            "uarch": dict(vf_info.uarch),
            "values": values,
            "program": self._lower_nodes(vf_info.context, ub_core_ids),
            "canonical_input": True,
            "canonical_schema_version": vf_info.schema_version,
            "canonical_source": dict(vf_info.source),
            "canonical_ub_map": dict(ub_core_ids),
        }

    def ensure_current_core_compatible(self, vf_info: CanonicalVfInfo) -> None:
        issues: list[str] = []

        def resolve(value: int | str) -> int | None:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value in vf_info.params:
                    return int(vf_info.params[value])
                try:
                    return int(value, 10)
                except ValueError:
                    return None
            return None

        def visit(nodes: Iterable[CanonicalNode]) -> None:
            for node in nodes:
                if isinstance(node, CanonicalInstruction):
                    if node.dependencies:
                        issues.append(
                            f"instruction {node.instruction_id} has explicit dependencies"
                        )
                    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(node.opcode)
                    if (
                        spec is None
                        and node.instruction_class != InstructionClass.COMPUTE
                    ):
                        issues.append(
                            f"unknown {node.instruction_class.value} instruction "
                            f"{node.instruction_id} cannot use Core ISA classification"
                        )
                    if node.instruction_class == InstructionClass.CONTROL:
                        issues.append(
                            f"control instruction {node.instruction_id} is not a Membar node"
                        )
                    continue
                if isinstance(node, CanonicalMembar):
                    if node.dependencies:
                        issues.append(
                            f"Membar {node.instruction_id} has explicit dependencies"
                        )
                    continue
                if node.carried_values:
                    issues.append(f"loop {node.loop_id} has loop-carried values")
                unroll = resolve(node.unroll)
                if unroll != 1:
                    issues.append(
                        f"loop {node.loop_id} has unsupported unroll={node.unroll}"
                    )
                start = resolve(node.induction.start)
                step = resolve(node.induction.step)
                if start != 0 or step != 1:
                    issues.append(
                        f"loop {node.loop_id} has unsupported induction "
                        f"start={node.induction.start}, step={node.induction.step}"
                    )
                visit(node.body)

        visit(vf_info.context)
        if issues:
            raise CanonicalCoreCompatibilityError(issues)

    def _lower_nodes(
        self,
        nodes: Iterable[CanonicalNode],
        ub_core_ids: dict[str, str],
    ) -> list[dict[str, Any]]:
        return [self._lower_node(node, ub_core_ids) for node in nodes]

    def _lower_node(
        self,
        node: CanonicalNode,
        ub_core_ids: dict[str, str],
    ) -> dict[str, Any]:
        if isinstance(node, CanonicalInstruction):
            return {
                "type": "inst",
                "op": node.opcode,
                "form": node.form,
                "src": [self._lower_operand(item, ub_core_ids) for item in node.inputs],
                "dst": [self._lower_operand(item, ub_core_ids) for item in node.outputs],
                "static_instruction_id": node.instruction_id,
                "instruction_class": node.instruction_class.value,
                "attributes": dict(node.attributes),
                "dependencies": [
                    {
                        "producer_node_id": item.producer_node_id,
                        "kind": item.kind.value,
                        "operand_index": item.operand_index,
                    }
                    for item in node.dependencies
                ],
                "memory_accesses": self._memory_accesses(node),
                "source_location": _source_location(node.source_location),
            }
        if isinstance(node, CanonicalMembar):
            return {
                "type": "membar",
                "barrier": node.barrier,
                "static_instruction_id": node.instruction_id,
                "source_location": _source_location(node.source_location),
            }
        return {
            "type": "loop",
            "name": node.loop_id,
            "iters": node.count,
            "unroll": node.unroll,
            "induction": {
                "variable_id": node.induction.variable_id,
                "start": node.induction.start,
                "step": node.induction.step,
            },
            "carried_values": [
                {
                    "logical_id": item.logical_id,
                    "entry_value_id": item.entry_value_id,
                    "back_edge_value_id": item.back_edge_value_id,
                    "exit_value_id": item.exit_value_id,
                }
                for item in node.carried_values
            ],
            "body": self._lower_nodes(node.body, ub_core_ids),
            "source_location": _source_location(node.source_location),
        }

    @staticmethod
    def _lower_operand(
        operand: CanonicalOperand,
        ub_core_ids: dict[str, str],
    ) -> str:
        if operand.memory_access is not None:
            return ub_core_ids[operand.memory_access.base_object_id]
        return operand.value_id

    @staticmethod
    def _memory_accesses(node: CanonicalInstruction) -> list[dict[str, Any]]:
        accesses: list[dict[str, Any]] = []
        for direction, operands in (("input", node.inputs), ("output", node.outputs)):
            for index, operand in enumerate(operands):
                memory = operand.memory_access
                if memory is None:
                    continue
                accesses.append(
                    {
                        "direction": direction,
                        "operand_index": index,
                        "value_id": operand.value_id,
                        "base_object_id": memory.base_object_id,
                        "offset": _affine_expression(memory.offset),
                        "access_kind": memory.access_kind.value,
                        "span": memory.span,
                        "alias_group": memory.alias_group,
                    }
                )
        return accesses


__all__ = ["CanonicalCoreCompatibilityError", "CoreLoweringPass"]
