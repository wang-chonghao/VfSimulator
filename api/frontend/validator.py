from __future__ import annotations

from typing import Any, Iterable

from api.frontend.diagnostics import Diagnostic, DiagnosticSeverity, ValidationResult
from api.frontend.schema import (
    CANONICAL_VF_INFO_SCHEMA_VERSION,
    AccessKind,
    CanonicalInstruction,
    CanonicalLoop,
    CanonicalMembar,
    CanonicalOperand,
    CanonicalVfInfo,
    DependencyKind,
    DependencyRef,
    InstructionClass,
    OperandRole,
    ScalarValue,
    StorageKind,
)


_SUPPORTED_MEMBAR_TYPES = frozenset({"VST_VLD", "VLD_VST"})
_SCALAR_TYPES = (type(None), bool, int, float, str)
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_INPUT_ROLES = frozenset(
    {OperandRole.SOURCE, OperandRole.MEMORY, OperandRole.SCALAR,
     OperandRole.PREDICATE, OperandRole.CONFIG}
)
_OUTPUT_ROLES = frozenset({OperandRole.DESTINATION, OperandRole.MEMORY})


def validate_canonical_vf_info(vf_info: CanonicalVfInfo) -> ValidationResult:
    diagnostics: list[Diagnostic] = []
    node_ids: set[str] = set()
    instruction_ids: set[str] = set()
    dependency_refs: list[tuple[DependencyRef, str, str]] = []

    def error(code: str, message: str, *, location=None, **context: Any) -> None:
        diagnostics.append(
            Diagnostic(code, DiagnosticSeverity.ERROR, message, location, context)
        )

    def validate_scalar_map(values: Any, path: str) -> None:
        if not isinstance(values, dict) and not hasattr(values, "items"):
            error("invalid_scalar_map", f"{path} must be a mapping", path=path)
            return
        for key, value in values.items():
            if not isinstance(key, str) or not isinstance(value, _SCALAR_TYPES):
                error(
                    "invalid_scalar_attribute",
                    f"{path} only supports string keys and scalar values",
                    path=f"{path}.{key}",
                    value_type=type(value).__name__,
                )

    if vf_info.schema_version != CANONICAL_VF_INFO_SCHEMA_VERSION:
        error(
            "unsupported_schema_version",
            f"Unsupported CanonicalVfInfo schema version {vf_info.schema_version}",
            supported_version=CANONICAL_VF_INFO_SCHEMA_VERSION,
            actual_version=vf_info.schema_version,
        )

    for name, value in vf_info.params.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not _INT64_MIN <= value <= _INT64_MAX
        ):
            error(
                "invalid_parameter_value",
                f"Parameter {name!r} must be an integer",
                parameter=name,
                value=value,
            )
    validate_scalar_map(vf_info.uarch, "uarch")
    validate_scalar_map(vf_info.source, "source")

    for definition_id, value in vf_info.values.items():
        if not definition_id or value.definition_id != definition_id:
            error(
                "invalid_value_identity",
                f"Value key {definition_id!r} must match definition_id",
                location=value.source_location,
                definition_id=definition_id,
                actual=value.definition_id,
            )
        if not value.logical_id:
            error(
                "missing_logical_id",
                f"Value definition {definition_id!r} must declare logical_id",
                location=value.source_location,
                definition_id=definition_id,
            )
        if not isinstance(value.storage, StorageKind):
            error(
                "unsupported_storage",
                f"Value {definition_id!r} has unsupported storage",
                location=value.source_location,
                definition_id=definition_id,
            )
        if not value.dtype:
            error(
                "missing_value_dtype",
                f"Value {definition_id!r} must declare dtype",
                location=value.source_location,
                definition_id=definition_id,
            )
        if any(isinstance(dim, bool) or not isinstance(dim, int) or dim < 0 for dim in value.shape):
            error(
                "invalid_value_shape",
                f"Value {definition_id!r} has invalid shape",
                location=value.source_location,
                definition_id=definition_id,
                shape=value.shape,
            )

    def resolve_int(value: int | str, path: str) -> int | None:
        if isinstance(value, bool):
            error("invalid_integer_expression", f"{path} must resolve to integer", path=path)
            return None
        if isinstance(value, int):
            if _INT64_MIN <= value <= _INT64_MAX:
                return value
            error("integer_expression_out_of_range", f"{path} exceeds int64", path=path)
            return None
        if isinstance(value, str) and value in vf_info.params:
            return vf_info.params[value]
        if isinstance(value, str):
            try:
                parsed = int(value, 10)
            except ValueError:
                parsed = None
            if parsed is not None and _INT64_MIN <= parsed <= _INT64_MAX:
                return parsed
        error(
            "unresolved_parameter",
            f"{path} references unknown parameter {value!r}",
            path=path,
            parameter=value,
        )
        return None

    def register_node_id(node_id: str, path: str, location) -> None:
        if not node_id or node_id in node_ids:
            error(
                "duplicate_node_id",
                f"Node ID {node_id!r} must be non-empty and globally unique",
                location=location,
                path=path,
                node_id=node_id,
            )
        node_ids.add(node_id)

    def validate_dependencies(
        dependencies: Iterable[DependencyRef],
        consumer_id: str,
        path: str,
    ) -> None:
        for index, dependency in enumerate(dependencies):
            dependency_path = f"{path}[{index}]"
            if dependency.producer_instruction_id == consumer_id:
                error(
                    "self_dependency",
                    f"Instruction {consumer_id!r} cannot depend on itself",
                    path=dependency_path,
                )
            if not isinstance(dependency.kind, DependencyKind):
                error(
                    "unsupported_dependency_kind",
                    "Dependency kind must be data, memory, or control",
                    path=dependency_path,
                )
            if dependency.operand_index is not None and dependency.operand_index < 0:
                error(
                    "invalid_dependency_operand_index",
                    "Dependency operand_index must be non-negative",
                    path=dependency_path,
                )
            dependency_refs.append((dependency, consumer_id, dependency_path))

    def validate_operand(
        operand: CanonicalOperand,
        *,
        direction: str,
        path: str,
        location,
        induction_variables: set[str],
    ) -> None:
        value = vf_info.values.get(operand.value_id)
        if value is None:
            error(
                "unknown_value_reference",
                f"{path} references unknown value definition {operand.value_id!r}",
                location=location,
                path=path,
                value_id=operand.value_id,
            )
            return
        if not isinstance(operand.role, OperandRole):
            error(
                "unsupported_operand_role",
                f"{path} has unsupported operand role {operand.role!r}",
                location=location,
                path=path,
            )
        else:
            allowed_roles = _INPUT_ROLES if direction == "input" else _OUTPUT_ROLES
            if operand.role not in allowed_roles:
                error(
                    "operand_role_direction_mismatch",
                    f"{path} role is not valid for an {direction} operand",
                    location=location,
                    path=path,
                    role=operand.role.value,
                )
        if operand.dtype is not None and operand.dtype != value.dtype:
            error(
                "operand_dtype_mismatch",
                f"{path} dtype does not match referenced value definition",
                location=location,
                path=path,
                operand_dtype=operand.dtype,
                value_dtype=value.dtype,
            )
        memory = operand.memory_access
        if value.storage == StorageKind.UB and memory is None:
            error(
                "missing_memory_access",
                f"UB operand {operand.value_id!r} requires structured memory access",
                location=location,
                path=path,
            )
            return
        if value.storage != StorageKind.UB and memory is not None:
            error(
                "memory_access_on_non_ub_value",
                f"Non-UB operand {operand.value_id!r} cannot carry memory access",
                location=location,
                path=path,
            )
            return
        if memory is None:
            return
        if operand.role != OperandRole.MEMORY:
            error(
                "memory_operand_role_mismatch",
                f"Memory operand {operand.value_id!r} must use role=memory",
                location=location,
                path=path,
            )
        if memory.base_value_id != operand.value_id:
            error(
                "memory_base_operand_mismatch",
                "Memory base must match the operand value definition",
                location=location,
                path=path,
                value_id=operand.value_id,
                base_value_id=memory.base_value_id,
            )
        expected_kind = AccessKind.READ if direction == "input" else AccessKind.WRITE
        if memory.access_kind != expected_kind:
            error(
                "memory_access_direction_mismatch",
                f"{path} requires {expected_kind.value} memory access",
                location=location,
                path=path,
            )
        if memory.span is not None and memory.span <= 0:
            error("invalid_memory_span", f"{path} span must be positive", path=path)
        seen_terms: set[str] = set()
        for term in memory.offset.terms:
            if not term.variable_id or term.variable_id in seen_terms:
                error(
                    "invalid_affine_term",
                    "Affine variables must be non-empty and unique",
                    location=location,
                    path=path,
                    variable_id=term.variable_id,
                )
            seen_terms.add(term.variable_id)
            if term.variable_id not in induction_variables and term.variable_id not in vf_info.params:
                error(
                    "undeclared_affine_variable",
                    f"Affine variable {term.variable_id!r} is not in loop scope or params",
                    location=location,
                    path=path,
                    variable_id=term.variable_id,
                )

    def validate_nodes(nodes, path: str, induction_variables: set[str]) -> None:
        for index, node in enumerate(nodes):
            node_path = f"{path}[{index}]"
            if isinstance(node, CanonicalInstruction):
                register_node_id(node.instruction_id, node_path, node.source_location)
                instruction_ids.add(node.instruction_id)
                if not node.opcode:
                    error("missing_opcode", f"{node_path} must declare opcode", path=node_path)
                if not isinstance(node.instruction_class, InstructionClass):
                    error(
                        "missing_instruction_class",
                        f"{node_path} must declare instruction class",
                        path=node_path,
                    )
                if not node.form:
                    error("missing_instruction_form", f"{node_path} must declare form", path=node_path)
                validate_scalar_map(node.attributes, f"{node_path}.attributes")
                for operand_index, operand in enumerate(node.inputs):
                    validate_operand(
                        operand,
                        direction="input",
                        path=f"{node_path}.inputs[{operand_index}]",
                        location=node.source_location,
                        induction_variables=induction_variables,
                    )
                    value = vf_info.values.get(operand.value_id)
                    if (
                        value is not None
                        and value.producer_instruction_id == node.instruction_id
                    ):
                        error(
                            "self_produced_input",
                            "Instruction input cannot reference its own output definition",
                            location=node.source_location,
                            path=f"{node_path}.inputs[{operand_index}]",
                            value_id=operand.value_id,
                        )
                for operand_index, operand in enumerate(node.outputs):
                    validate_operand(
                        operand,
                        direction="output",
                        path=f"{node_path}.outputs[{operand_index}]",
                        location=node.source_location,
                        induction_variables=induction_variables,
                    )
                    value = vf_info.values.get(operand.value_id)
                    if (
                        value is not None
                        and value.producer_instruction_id != node.instruction_id
                    ):
                        error(
                            "output_producer_mismatch",
                            "Output value definition must name its producing instruction",
                            location=node.source_location,
                            path=f"{node_path}.outputs[{operand_index}]",
                            value_id=operand.value_id,
                            expected_producer=node.instruction_id,
                            actual_producer=value.producer_instruction_id,
                        )
                has_read_memory = any(
                    operand.memory_access is not None
                    and operand.memory_access.access_kind == AccessKind.READ
                    for operand in node.inputs
                )
                has_write_memory = any(
                    operand.memory_access is not None
                    and operand.memory_access.access_kind == AccessKind.WRITE
                    for operand in node.outputs
                )
                if node.instruction_class == InstructionClass.LOAD and not has_read_memory:
                    error("load_without_memory_read", "Load class requires a memory read", path=node_path)
                if node.instruction_class == InstructionClass.STORE and not has_write_memory:
                    error("store_without_memory_write", "Store class requires a memory write", path=node_path)
                validate_dependencies(node.dependencies, node.instruction_id, f"{node_path}.dependencies")
                continue

            if isinstance(node, CanonicalLoop):
                register_node_id(node.loop_id, node_path, node.source_location)
                count = resolve_int(node.count, f"{node_path}.count")
                unroll = resolve_int(node.unroll, f"{node_path}.unroll")
                start = resolve_int(node.induction.start, f"{node_path}.induction.start")
                step = resolve_int(node.induction.step, f"{node_path}.induction.step")
                if count is not None and count < 0:
                    error("invalid_loop_count", "Loop count must be non-negative", path=node_path)
                if unroll is not None and unroll <= 0:
                    error("invalid_loop_unroll", "Loop unroll must be positive", path=node_path)
                if start is not None and not isinstance(start, int):
                    error("invalid_induction_start", "Induction start must be integer", path=node_path)
                if step is not None and step == 0:
                    error("invalid_induction_step", "Induction step cannot be zero", path=node_path)
                variable_id = node.induction.variable_id
                if not variable_id or variable_id in induction_variables or variable_id in vf_info.params:
                    error(
                        "invalid_induction_variable",
                        "Induction variable must be non-empty and unique in scope",
                        path=node_path,
                        variable_id=variable_id,
                    )
                carried_logical_ids: set[str] = set()
                for carried_index, carried in enumerate(node.carried_values):
                    carried_path = f"{node_path}.carried_values[{carried_index}]"
                    if not carried.logical_id or carried.logical_id in carried_logical_ids:
                        error("duplicate_loop_carried_value", "Loop-carried logical_id must be unique", path=carried_path)
                    carried_logical_ids.add(carried.logical_id)
                    definitions = [
                        vf_info.values.get(carried.entry_value_id),
                        vf_info.values.get(carried.back_edge_value_id),
                        vf_info.values.get(carried.exit_value_id),
                    ]
                    if any(value is None for value in definitions):
                        error("unknown_loop_carried_value", "Loop-carried relation references unknown definition", path=carried_path)
                    elif any(value.logical_id != carried.logical_id for value in definitions if value is not None):
                        error("loop_carried_logical_id_mismatch", "Loop-carried definitions must share logical_id", path=carried_path)
                validate_nodes(
                    node.body,
                    f"{node_path}.body",
                    induction_variables | {variable_id},
                )
                continue

            if isinstance(node, CanonicalMembar):
                register_node_id(node.instruction_id, node_path, node.source_location)
                instruction_ids.add(node.instruction_id)
                if node.barrier not in _SUPPORTED_MEMBAR_TYPES:
                    error("unsupported_membar_type", f"Unsupported Membar type {node.barrier!r}", path=node_path)
                validate_dependencies(node.dependencies, node.instruction_id, f"{node_path}.dependencies")
                continue

            error(
                "unsupported_canonical_node",
                f"{node_path} contains unsupported node {type(node).__name__}",
                path=node_path,
            )

    validate_nodes(vf_info.context, "context", set())

    for value_id, value in vf_info.values.items():
        producer = value.producer_instruction_id
        if producer is not None and producer not in instruction_ids:
            error(
                "unknown_value_producer",
                f"Value {value_id!r} references unknown producer {producer!r}",
                definition_id=value_id,
                producer_instruction_id=producer,
            )
    for dependency, consumer_id, path in dependency_refs:
        if dependency.producer_instruction_id not in instruction_ids:
            error(
                "unknown_dependency_producer",
                f"Dependency of {consumer_id!r} references unknown producer",
                path=path,
                producer_instruction_id=dependency.producer_instruction_id,
            )

    return ValidationResult(tuple(diagnostics))


__all__ = ["validate_canonical_vf_info"]
