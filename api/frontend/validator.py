from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from api.frontend.diagnostics import Diagnostic, DiagnosticSeverity, ValidationResult
from api.frontend.instruction_catalog import (
    ArgumentKind,
    DEFAULT_INSTRUCTION_CATALOG,
    OperandDirection,
)
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
    SourceLocation,
    StorageKind,
)
from api.frontend.uarch_validation import validate_uarch_overrides


_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_INPUT_ROLES = frozenset(
    {
        OperandRole.SOURCE,
        OperandRole.MEMORY,
        OperandRole.SCALAR,
        OperandRole.PREDICATE,
        OperandRole.CONFIG,
    }
)
_OUTPUT_ROLES = frozenset({OperandRole.DESTINATION, OperandRole.MEMORY})


@dataclass(frozen=True)
class _NodeInfo:
    scope: tuple[str, ...]
    order: int
    kind: str
    location: SourceLocation | None


def validate_canonical_vf_info(vf_info: CanonicalVfInfo) -> ValidationResult:
    diagnostics: list[Diagnostic] = []
    registered_node_ids: set[str] = set()
    dependency_refs: list[
        tuple[DependencyRef, str, str, SourceLocation | None]
    ] = []
    node_info: dict[str, _NodeInfo] = {}
    produced_definitions: dict[str, list[str]] = {}
    next_order = 0
    current_node_location: SourceLocation | None = None

    def error(code: str, message: str, *, location=None, **context: Any) -> None:
        effective_location = (
            location if location is not None else current_node_location
        )
        diagnostics.append(
            Diagnostic(
                code,
                DiagnosticSeverity.ERROR,
                message,
                effective_location,
                context,
            )
        )

    def validate_int64(
        value: Any,
        path: str,
        *,
        minimum: int | None = None,
        code: str = "invalid_int64",
    ) -> bool:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, int)
            and _INT64_MIN <= value <= _INT64_MAX
            and (minimum is None or value >= minimum)
        )
        if not valid:
            error(code, f"{path} must be a valid int64", path=path, value=value)
        return valid

    def validate_scalar(value: Any, path: str) -> None:
        if value is None or isinstance(value, (bool, str)):
            return
        if isinstance(value, int):
            validate_int64(value, path, code="invalid_scalar_attribute")
            return
        if isinstance(value, float) and math.isfinite(value):
            return
        error(
            "invalid_scalar_attribute",
            f"{path} must be a finite JSON scalar",
            path=path,
            value_type=type(value).__name__,
        )

    def validate_scalar_map(values: Any, path: str) -> None:
        if not hasattr(values, "items"):
            error("invalid_scalar_map", f"{path} must be a mapping", path=path)
            return
        for key, value in values.items():
            if not isinstance(key, str):
                error(
                    "invalid_scalar_attribute",
                    f"{path} only supports string keys",
                    path=path,
                    key_type=type(key).__name__,
                )
                continue
            validate_scalar(value, f"{path}.{key}")

    def validate_location(location: SourceLocation | None, path: str) -> None:
        if location is None:
            return
        if location.line is not None:
            validate_int64(location.line, f"{path}.line", minimum=1)
        if location.column is not None:
            validate_int64(location.column, f"{path}.column", minimum=1)

    def index_nodes(nodes, scope: tuple[str, ...]) -> None:
        nonlocal next_order
        for node in nodes:
            if isinstance(node, CanonicalInstruction):
                node_id, kind = node.instruction_id, "instruction"
            elif isinstance(node, CanonicalLoop):
                node_id, kind = node.loop_id, "loop"
            elif isinstance(node, CanonicalMembar):
                node_id, kind = node.instruction_id, "membar"
            else:
                next_order += 1
                continue
            info = _NodeInfo(scope, next_order, kind, node.source_location)
            next_order += 1
            node_info.setdefault(node_id, info)
            if isinstance(node, CanonicalLoop):
                index_nodes(node.body, scope + (node.loop_id,))

    index_nodes(vf_info.context, ())

    if (
        isinstance(vf_info.schema_version, bool)
        or not isinstance(vf_info.schema_version, int)
        or vf_info.schema_version != CANONICAL_VF_INFO_SCHEMA_VERSION
    ):
        error(
            "unsupported_schema_version",
            f"Unsupported CanonicalVfInfo schema version {vf_info.schema_version}",
            supported_version=CANONICAL_VF_INFO_SCHEMA_VERSION,
            actual_version=vf_info.schema_version,
        )

    for name, value in vf_info.params.items():
        validate_int64(
            value,
            f"params.{name}",
            code="invalid_parameter_value",
        )
    diagnostics.extend(validate_uarch_overrides(vf_info.uarch).diagnostics)
    validate_scalar_map(vf_info.source, "source")

    for object_id, storage_object in vf_info.storage_objects.items():
        path = f"storage_objects.{object_id}"
        if not object_id or storage_object.object_id != object_id:
            error(
                "invalid_storage_object_identity",
                "Storage object key must match object_id",
                location=storage_object.source_location,
                path=path,
            )
        if storage_object.storage != StorageKind.UB:
            error(
                "unsupported_storage_object_kind",
                "Canonical v1 storage objects must use UB storage",
                location=storage_object.source_location,
                path=path,
            )
        validate_location(storage_object.source_location, f"{path}.source_location")
        for index, dim in enumerate(storage_object.shape):
            validate_int64(dim, f"{path}.shape[{index}]", minimum=0)

    for definition_id, value in vf_info.values.items():
        path = f"values.{definition_id}"
        if not definition_id or value.definition_id != definition_id:
            error(
                "invalid_value_identity",
                "Value key must match definition_id",
                location=value.source_location,
                path=path,
            )
        if not value.logical_id:
            error("missing_logical_id", "Value must declare logical_id", path=path)
        if not isinstance(value.storage, StorageKind):
            error("unsupported_storage", "Value has unsupported storage", path=path)
        if not value.dtype:
            error("missing_value_dtype", "Value must declare dtype", path=path)
        for index, dim in enumerate(value.shape):
            validate_int64(dim, f"{path}.shape[{index}]", minimum=0)
        validate_location(value.source_location, f"{path}.source_location")
        if value.storage == StorageKind.UB:
            if not value.storage_object_id or value.storage_object_id not in vf_info.storage_objects:
                error(
                    "unknown_storage_object",
                    "UB value must reference a declared storage object",
                    path=path,
                    storage_object_id=value.storage_object_id,
                )
        elif value.storage_object_id is not None:
            error(
                "storage_object_on_non_ub_value",
                "Only UB values may reference a storage object",
                path=path,
            )

    def resolve_int(value: int | str, path: str) -> int | None:
        if not isinstance(value, bool) and isinstance(value, int):
            return value if validate_int64(value, path) else None
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
            f"{path} does not resolve to int64",
            path=path,
            expression=value,
        )
        return None

    def register_node_id(node_id: str, path: str, location) -> None:
        if not node_id or node_id in registered_node_ids:
            error(
                "duplicate_node_id",
                "Node ID must be non-empty and globally unique",
                location=location,
                path=path,
                node_id=node_id,
            )
        registered_node_ids.add(node_id)

    def validate_dependencies(
        dependencies: Iterable[DependencyRef],
        consumer_id: str,
        path: str,
    ) -> None:
        consumer = node_info.get(consumer_id)
        for index, dependency in enumerate(dependencies):
            dependency_path = f"{path}[{index}]"
            if dependency.producer_node_id == consumer_id:
                error("self_dependency", "Node cannot depend on itself", path=dependency_path)
            if not isinstance(dependency.kind, DependencyKind):
                error(
                    "unsupported_dependency_kind",
                    "Explicit dependency must be memory or control",
                    path=dependency_path,
                )
            if dependency.operand_index is not None:
                validate_int64(
                    dependency.operand_index,
                    f"{dependency_path}.operand_index",
                    minimum=0,
                    code="invalid_dependency_operand_index",
                )
            producer = node_info.get(dependency.producer_node_id)
            if producer is not None and consumer is not None:
                visible = (
                    producer.order < consumer.order
                    and consumer.scope[: len(producer.scope)] == producer.scope
                    and not (
                        producer.kind == "loop"
                        and len(consumer.scope) > len(producer.scope)
                        and consumer.scope[len(producer.scope)]
                        == dependency.producer_node_id
                    )
                )
                if not visible:
                    error(
                        "dependency_producer_not_visible",
                        "Dependency producer must precede the consumer in a visible scope",
                        path=dependency_path,
                    )
            dependency_refs.append(
                (
                    dependency,
                    consumer_id,
                    dependency_path,
                    current_node_location,
                )
            )

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
                "Operand references unknown value definition",
                location=location,
                path=path,
                value_id=operand.value_id,
            )
            return
        if not isinstance(operand.role, OperandRole):
            error("unsupported_operand_role", "Unsupported operand role", path=path)
        else:
            allowed_roles = _INPUT_ROLES if direction == "input" else _OUTPUT_ROLES
            if operand.role not in allowed_roles:
                error(
                    "operand_role_direction_mismatch",
                    "Operand role is invalid for its direction",
                    path=path,
                    role=operand.role.value,
                )
        if operand.dtype is not None and operand.dtype != value.dtype:
            error("operand_dtype_mismatch", "Operand dtype differs from value", path=path)
        memory = operand.memory_access
        if value.storage == StorageKind.UB and memory is None:
            error("missing_memory_access", "UB operand requires memory metadata", path=path)
            return
        if value.storage != StorageKind.UB and memory is not None:
            error(
                "memory_access_on_non_ub_value",
                "Only UB operands may carry memory metadata",
                path=path,
            )
            return
        if memory is None:
            return
        if operand.role != OperandRole.MEMORY:
            error("memory_operand_role_mismatch", "Memory operand must use memory role", path=path)
        if memory.base_object_id != value.storage_object_id:
            error(
                "memory_base_object_mismatch",
                "Memory base must match the value's stable storage object",
                path=path,
                base_object_id=memory.base_object_id,
                storage_object_id=value.storage_object_id,
            )
        if memory.base_object_id not in vf_info.storage_objects:
            error("unknown_memory_base_object", "Memory base object is not declared", path=path)
        expected_kind = AccessKind.READ if direction == "input" else AccessKind.WRITE
        if not isinstance(memory.access_kind, AccessKind):
            error(
                "unsupported_memory_access_kind",
                "Memory access kind must be an AccessKind enum value",
                path=path,
            )
        elif memory.access_kind != expected_kind:
            error("memory_access_direction_mismatch", "Memory direction mismatch", path=path)
        if memory.span is not None:
            validate_int64(memory.span, f"{path}.memory_access.span", minimum=1,
                           code="invalid_memory_span")
        validate_int64(memory.offset.constant, f"{path}.memory_access.offset.constant")
        seen_terms: set[str] = set()
        for index, term in enumerate(memory.offset.terms):
            term_path = f"{path}.memory_access.offset.terms[{index}]"
            if not term.variable_id or term.variable_id in seen_terms:
                error("invalid_affine_term", "Affine variables must be unique", path=term_path)
            seen_terms.add(term.variable_id)
            validate_int64(term.coefficient, f"{term_path}.coefficient")
            if (
                term.variable_id not in induction_variables
                and term.variable_id not in vf_info.params
            ):
                error(
                    "undeclared_affine_variable",
                    "Affine variable is not in loop scope or params",
                    path=term_path,
                    variable_id=term.variable_id,
                )

    def producer_visible_to(producer_id: str, consumer_id: str) -> bool:
        producer = node_info.get(producer_id)
        consumer = node_info.get(consumer_id)
        if not producer or not consumer:
            return False
        if producer.order >= consumer.order or consumer.scope[: len(producer.scope)] != producer.scope:
            return False
        if (
            producer.kind == "loop"
            and len(consumer.scope) > len(producer.scope)
            and consumer.scope[len(producer.scope)] == producer_id
        ):
            return False
        return True

    def definition_visible_before_loop(value, loop_info: _NodeInfo | None) -> bool:
        if value.producer_node_id is None:
            return True
        producer = node_info.get(value.producer_node_id)
        if producer is None or loop_info is None:
            return False
        return bool(
            producer.order < loop_info.order
            and loop_info.scope[: len(producer.scope)] == producer.scope
            and not (
                producer.kind == "loop"
                and len(loop_info.scope) > len(producer.scope)
                and loop_info.scope[len(producer.scope)]
                == value.producer_node_id
            )
        )

    def validate_nodes(nodes, path: str, induction_variables: set[str]) -> None:
        nonlocal current_node_location
        for index, node in enumerate(nodes):
            node_path = f"{path}[{index}]"
            previous_location = current_node_location
            current_node_location = getattr(node, "source_location", None)
            if isinstance(node, CanonicalInstruction):
                register_node_id(node.instruction_id, node_path, node.source_location)
                validate_location(node.source_location, f"{node_path}.source_location")
                if not node.opcode:
                    error("missing_opcode", "Instruction must declare opcode", path=node_path)
                if not isinstance(node.instruction_class, InstructionClass):
                    error("missing_instruction_class", "Instruction class is required", path=node_path)
                if not node.form:
                    error("missing_instruction_form", "Instruction form is required", path=node_path)
                catalog_spec = DEFAULT_INSTRUCTION_CATALOG.lookup(node.opcode)
                if catalog_spec is not None:
                    if node.opcode != catalog_spec.opcode:
                        error(
                            "noncanonical_opcode",
                            "Known opcode must use its canonical Catalog name",
                            path=node_path,
                        )
                    if node.instruction_class != catalog_spec.instruction_class:
                        error(
                            "catalog_instruction_class_mismatch",
                            "Instruction class conflicts with Catalog semantics",
                            path=node_path,
                        )
                    try:
                        resolved_opcode, _ = (
                            DEFAULT_INSTRUCTION_CATALOG.resolve_and_validate_form(
                                node.opcode, node.form
                            )
                        )
                        if resolved_opcode != node.opcode:
                            error(
                                "catalog_specialization_required",
                                "Virtual opcode/form must use its specialized opcode",
                                path=node_path,
                            )
                    except ValueError:
                        if not catalog_spec.virtual:
                            error(
                                "catalog_instruction_form_mismatch",
                                "Instruction form conflicts with Catalog semantics",
                                path=node_path,
                            )
                    if catalog_spec.align_state_operation is not None:
                        if (
                            node.attributes.get("align_state_operation")
                            != catalog_spec.align_state_operation
                            or not isinstance(
                                node.attributes.get("align_state_id"), str
                            )
                            or not node.attributes.get("align_state_id")
                        ):
                            error(
                                "catalog_align_state_mismatch",
                                "Instruction must declare its Catalog align-state operation and state ID",
                                path=node_path,
                            )
                validate_scalar_map(node.attributes, f"{node_path}.attributes")
                for operand_index, operand in enumerate(node.inputs):
                    operand_path = f"{node_path}.inputs[{operand_index}]"
                    validate_operand(
                        operand,
                        direction="input",
                        path=operand_path,
                        location=node.source_location,
                        induction_variables=induction_variables,
                    )
                    value = vf_info.values.get(operand.value_id)
                    if value is not None and value.producer_node_id is not None:
                        if value.producer_node_id == node.instruction_id:
                            error("self_produced_input", "Input references own output", path=operand_path)
                        elif not producer_visible_to(value.producer_node_id, node.instruction_id):
                            error(
                                "input_definition_not_visible",
                                "Input definition producer is not visible before instruction",
                                path=operand_path,
                            )
                for operand_index, operand in enumerate(node.outputs):
                    operand_path = f"{node_path}.outputs[{operand_index}]"
                    validate_operand(
                        operand,
                        direction="output",
                        path=operand_path,
                        location=node.source_location,
                        induction_variables=induction_variables,
                    )
                    value = vf_info.values.get(operand.value_id)
                    if value is not None and value.producer_node_id != node.instruction_id:
                        error(
                            "output_producer_mismatch",
                            "Output definition must name its producing instruction",
                            path=operand_path,
                        )
                    produced_definitions.setdefault(node.instruction_id, []).append(
                        operand.value_id
                    )
                has_read = any(
                    operand.memory_access is not None
                    and isinstance(operand.memory_access.access_kind, AccessKind)
                    and operand.memory_access.access_kind == AccessKind.READ
                    for operand in node.inputs
                )
                has_write = any(
                    operand.memory_access is not None
                    and isinstance(operand.memory_access.access_kind, AccessKind)
                    and operand.memory_access.access_kind == AccessKind.WRITE
                    for operand in node.outputs
                )
                has_input_memory = any(
                    operand.memory_access is not None for operand in node.inputs
                )
                has_output_memory = any(
                    operand.memory_access is not None for operand in node.outputs
                )
                if node.instruction_class == InstructionClass.LOAD and not has_read:
                    error("load_without_memory_read", "Load requires memory read", path=node_path)
                if node.instruction_class == InstructionClass.LOAD and has_output_memory:
                    error(
                        "instruction_class_memory_access_mismatch",
                        "Load instructions cannot write memory",
                        path=node_path,
                    )
                if node.instruction_class == InstructionClass.STORE and not has_write:
                    error("store_without_memory_write", "Store requires memory write", path=node_path)
                if node.instruction_class == InstructionClass.STORE and has_input_memory:
                    error(
                        "instruction_class_memory_access_mismatch",
                        "Store instructions cannot read memory",
                        path=node_path,
                    )
                if (
                    node.instruction_class
                    in (InstructionClass.COMPUTE, InstructionClass.CONTROL)
                    and (has_input_memory or has_output_memory)
                ):
                    error(
                        "instruction_class_memory_access_mismatch",
                        "Compute/control instructions cannot access memory",
                        path=node_path,
                    )
                if catalog_spec is not None:
                    expected_inputs = [
                        operand
                        for operand in catalog_spec.operands
                        if operand.direction == OperandDirection.INPUT
                    ]
                    expected_outputs = [
                        operand
                        for operand in catalog_spec.operands
                        if operand.direction == OperandDirection.OUTPUT
                    ]
                    actual_inputs = [
                        operand
                        for operand in node.inputs
                        if operand.role
                        not in (OperandRole.PREDICATE, OperandRole.CONFIG)
                    ]
                    actual_outputs = [
                        operand
                        for operand in node.outputs
                        if operand.role
                        not in (OperandRole.PREDICATE, OperandRole.CONFIG)
                    ]

                    def validate_catalog_operands(actual, expected, direction):
                        if len(actual) != len(expected):
                            error(
                                "catalog_operand_count_mismatch",
                                "Operand count conflicts with Catalog signature",
                                path=node_path,
                                direction=direction,
                                expected=len(expected),
                                actual=len(actual),
                            )
                            return
                        for operand_index, (operand, operand_spec) in enumerate(
                            zip(actual, expected)
                        ):
                            operand_path = (
                                f"{node_path}.{direction}[{operand_index}]"
                            )
                            if operand.role != operand_spec.role:
                                error(
                                    "catalog_operand_role_mismatch",
                                    "Operand role conflicts with Catalog signature",
                                    path=operand_path,
                                )
                            value = vf_info.values.get(operand.value_id)
                            if value is None:
                                continue
                            allowed_storage = {
                                ArgumentKind.REGISTER: {StorageKind.REGISTER},
                                ArgumentKind.UB: {StorageKind.UB},
                                ArgumentKind.SCALAR: {StorageKind.SCALAR},
                                ArgumentKind.REGISTER_OR_SCALAR: {
                                    StorageKind.REGISTER,
                                    StorageKind.SCALAR,
                                },
                            }.get(operand_spec.kind)
                            if (
                                allowed_storage is not None
                                and value.storage not in allowed_storage
                            ):
                                error(
                                    "catalog_operand_storage_mismatch",
                                    "Operand storage conflicts with Catalog signature",
                                    path=operand_path,
                                )

                    validate_catalog_operands(actual_inputs, expected_inputs, "inputs")
                    validate_catalog_operands(
                        actual_outputs, expected_outputs, "outputs"
                    )
                validate_dependencies(node.dependencies, node.instruction_id, f"{node_path}.dependencies")
                current_node_location = previous_location
                continue

            if isinstance(node, CanonicalLoop):
                register_node_id(node.loop_id, node_path, node.source_location)
                validate_location(node.source_location, f"{node_path}.source_location")
                count = resolve_int(node.count, f"{node_path}.count")
                unroll = resolve_int(node.unroll, f"{node_path}.unroll")
                resolve_int(node.induction.start, f"{node_path}.induction.start")
                step = resolve_int(node.induction.step, f"{node_path}.induction.step")
                if count is not None and count < 0:
                    error("invalid_loop_count", "Loop count must be non-negative", path=node_path)
                if unroll is not None and unroll <= 0:
                    error("invalid_loop_unroll", "Loop unroll must be positive", path=node_path)
                if step is not None and step == 0:
                    error("invalid_induction_step", "Induction step cannot be zero", path=node_path)
                variable_id = node.induction.variable_id
                if not variable_id or variable_id in induction_variables or variable_id in vf_info.params:
                    error("invalid_induction_variable", "Induction variable is invalid", path=node_path)
                loop_info = node_info.get(node.loop_id)
                loop_scope = (loop_info.scope if loop_info else ()) + (node.loop_id,)
                carried_logical_ids: set[str] = set()
                for carried_index, carried in enumerate(node.carried_values):
                    carried_path = f"{node_path}.carried_values[{carried_index}]"
                    if not carried.logical_id or carried.logical_id in carried_logical_ids:
                        error("duplicate_loop_carried_value", "Loop-carried logical_id must be unique", path=carried_path)
                    carried_logical_ids.add(carried.logical_id)
                    entry = vf_info.values.get(carried.entry_value_id)
                    back_edge = vf_info.values.get(carried.back_edge_value_id)
                    exit_value = vf_info.values.get(carried.exit_value_id)
                    definitions = (entry, back_edge, exit_value)
                    if any(value is None for value in definitions):
                        error("unknown_loop_carried_value", "Unknown loop-carried definition", path=carried_path)
                        continue
                    assert entry is not None and back_edge is not None and exit_value is not None
                    if (
                        entry.logical_id != carried.logical_id
                        or exit_value.logical_id != carried.logical_id
                    ):
                        error(
                            "loop_carried_logical_id_mismatch",
                            "Loop entry and exit logical IDs must match the carried state",
                            path=carried_path,
                        )
                    metadata = {
                        (value.storage, value.dtype, value.shape, value.storage_object_id)
                        for value in definitions
                    }
                    if len(metadata) != 1:
                        error("loop_carried_type_mismatch", "Loop-carried value metadata must match", path=carried_path)
                    if entry.producer_node_id is not None:
                        if not definition_visible_before_loop(entry, loop_info):
                            error("loop_entry_not_visible", "Loop entry is not visible before loop", path=carried_path)
                    if carried.back_edge_value_id != carried.entry_value_id:
                        producer = node_info.get(back_edge.producer_node_id or "")
                        visible_at_loop_tail = bool(
                            (producer and producer.scope == loop_scope)
                            or definition_visible_before_loop(back_edge, loop_info)
                        )
                        if not visible_at_loop_tail:
                            error(
                                "loop_back_edge_out_of_scope",
                                "Back-edge definition must be visible at loop tail",
                                path=carried_path,
                            )
                    if exit_value.producer_node_id != node.loop_id:
                        error("loop_exit_producer_mismatch", "Loop exit must be produced by loop node", path=carried_path)
                    produced_definitions.setdefault(node.loop_id, []).append(
                        carried.exit_value_id
                    )
                validate_nodes(node.body, f"{node_path}.body", induction_variables | {variable_id})
                current_node_location = previous_location
                continue

            if isinstance(node, CanonicalMembar):
                register_node_id(node.instruction_id, node_path, node.source_location)
                validate_location(node.source_location, f"{node_path}.source_location")
                if not node.barrier:
                    error("missing_membar_type", "Membar type is required", path=node_path)
                validate_dependencies(node.dependencies, node.instruction_id, f"{node_path}.dependencies")
                current_node_location = previous_location
                continue

            error("unsupported_canonical_node", "Unsupported canonical node", path=node_path)
            current_node_location = previous_location

    validate_nodes(vf_info.context, "context", set())

    for definition_id, value in vf_info.values.items():
        if value.producer_node_id is None:
            continue
        producer = node_info.get(value.producer_node_id)
        if producer is None:
            error(
                "unknown_value_producer",
                "Value references unknown producer node",
                location=value.source_location,
                definition_id=definition_id,
                producer_node_id=value.producer_node_id,
            )
            continue
        if producer.kind == "membar":
            error(
                "invalid_value_producer_kind",
                "Membar cannot produce a value definition",
                location=producer.location,
                definition_id=definition_id,
                producer_node_id=value.producer_node_id,
            )
            continue
        emitted_count = produced_definitions.get(value.producer_node_id, []).count(
            definition_id
        )
        if emitted_count == 0:
            error(
                "producer_definition_not_emitted",
                "Producer node does not emit the claimed value definition",
                location=producer.location,
                definition_id=definition_id,
                producer_node_id=value.producer_node_id,
            )
        elif emitted_count > 1:
            error(
                "definition_emitted_multiple_times",
                "Producer node emits the same definition more than once",
                location=producer.location,
                definition_id=definition_id,
                producer_node_id=value.producer_node_id,
            )
    for dependency, consumer_id, path, consumer_location in dependency_refs:
        if dependency.producer_node_id not in node_info:
            error(
                "unknown_dependency_producer",
                "Dependency references unknown producer node",
                location=consumer_location,
                path=path,
                consumer_id=consumer_id,
                producer_node_id=dependency.producer_node_id,
            )

    return ValidationResult(tuple(diagnostics))


__all__ = ["validate_canonical_vf_info"]
