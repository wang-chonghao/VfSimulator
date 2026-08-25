from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from api.input_symbols import normalize_dtype

from .adapter_ir import (
    AdapterAlias,
    AdapterInstruction,
    AdapterLoop,
    AdapterMemoryAccess,
    AdapterMembar,
    AdapterNode,
    AdapterProgram,
    normalize_adapter_program,
)

from .instruction_catalog import DEFAULT_INSTRUCTION_CATALOG, OperandDirection
from .schema import (
    AccessKind,
    AffineExpression,
    AffineTerm,
    CanonicalInstruction,
    CanonicalLoop,
    CanonicalMembar,
    CanonicalNode,
    CanonicalOperand,
    CanonicalStorageObject,
    CanonicalValue,
    CanonicalVfInfo,
    InductionVariable,
    InstructionClass,
    LoopCarriedValue,
    MemoryAccess,
    OperandRole,
    StorageKind,
)
from .validator import validate_canonical_vf_info


def _storage_kind(value: str) -> StorageKind:
    return StorageKind(value)


def _safe_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return text or "value"


def _affine_expression(
    value: int | str,
    *,
    params: Mapping[str, int],
    induction_variables: frozenset[str],
) -> AffineExpression:
    if isinstance(value, int) and not isinstance(value, bool):
        return AffineExpression(constant=value)
    try:
        tree = ast.parse(str(value).strip(), mode="eval")
    except SyntaxError as error:
        raise ValueError(f"Invalid affine offset expression: {value}") from error

    def add(
        lhs: tuple[int, dict[str, int]],
        rhs: tuple[int, dict[str, int]],
        scale: int = 1,
    ) -> tuple[int, dict[str, int]]:
        constant = lhs[0] + scale * rhs[0]
        terms = dict(lhs[1])
        for variable, coefficient in rhs[1].items():
            terms[variable] = terms.get(variable, 0) + scale * coefficient
            if terms[variable] == 0:
                del terms[variable]
        return constant, terms

    def evaluate(node: ast.AST) -> tuple[int, dict[str, int]]:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and not isinstance(node.value, bool):
                return int(node.value), {}
            raise ValueError(f"Non-integer affine offset constant: {value}")
        if isinstance(node, ast.Name):
            if node.id in params:
                return int(params[node.id]), {}
            if node.id in induction_variables:
                return 0, {node.id: 1}
            raise ValueError(f"Unknown affine offset symbol {node.id!r}: {value}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = evaluate(node.operand)
            return result if isinstance(node.op, ast.UAdd) else (-result[0], {k: -v for k, v in result[1].items()})
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            return add(evaluate(node.left), evaluate(node.right), -1 if isinstance(node.op, ast.Sub) else 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if left[1] and right[1]:
                raise ValueError(f"Non-affine variable multiplication: {value}")
            if left[1]:
                factor = right[0]
                return left[0] * factor, {key: coefficient * factor for key, coefficient in left[1].items()}
            factor = left[0]
            return right[0] * factor, {key: coefficient * factor for key, coefficient in right[1].items()}
        raise ValueError(f"Unsupported affine offset expression: {value}")

    constant, terms = evaluate(tree)
    return AffineExpression(
        constant=constant,
        terms=tuple(
            AffineTerm(variable_id=variable, coefficient=coefficient)
            for variable, coefficient in sorted(terms.items())
        ),
    )


@dataclass
class _Counters:
    instruction: int = 0
    loop: int = 0
    membar: int = 0
    definition: int = 0


class ValueVersioningPass:
    """Convert private adapter IR into canonical value definitions."""

    def __init__(self) -> None:
        self._counters = _Counters()
        self._values: dict[str, CanonicalValue] = {}
        self._storage_objects: dict[str, CanonicalStorageObject] = {}
        self._logical_values = {}
        self._params: dict[str, int] = {}
        self._node_ids: set[str] = set()

    def run(
        self,
        program: AdapterProgram,
        *,
        source: Mapping[str, object] | None = None,
    ) -> CanonicalVfInfo:
        normalized = normalize_adapter_program(program)
        self._counters = _Counters()
        self._values = {}
        self._storage_objects = {}
        self._logical_values = dict(normalized.values)
        self._params = dict(normalized.params)
        self._node_ids = set()

        environment: dict[str, str] = {}
        context, _ = self._version_nodes(
            normalized.context,
            environment,
            frozenset(),
        )
        canonical = CanonicalVfInfo(
            context=tuple(context),
            values=dict(self._values),
            storage_objects=dict(self._storage_objects),
            params=dict(normalized.params),
            uarch=dict(normalized.uarch),
            source=dict(source or {}),
        )
        validation = validate_canonical_vf_info(canonical)
        if not validation.ok:
            from .builder import VfInfoValidationError

            raise VfInfoValidationError(validation.errors)
        return canonical

    def _next_node_id(self, kind: str, preferred: str | None = None) -> str:
        if preferred:
            node_id = str(preferred)
            if node_id in self._node_ids:
                raise ValueError(f"Duplicate node ID during value versioning: {node_id}")
            self._node_ids.add(node_id)
            return node_id
        while True:
            current = getattr(self._counters, kind)
            setattr(self._counters, kind, current + 1)
            node_id = f"{kind}.{current}"
            if node_id not in self._node_ids:
                self._node_ids.add(node_id)
                return node_id

    def _new_definition(
        self,
        logical_id: str,
        *,
        producer_node_id: str | None,
    ) -> str:
        logical = self._logical_values[logical_id]
        index = self._counters.definition
        self._counters.definition += 1
        definition_id = f"{_safe_id(logical_id)}.def{index}"
        storage = _storage_kind(logical.storage)
        storage_object_id = None
        if storage == StorageKind.UB:
            storage_object_id = f"ub.{_safe_id(logical_id)}"
            self._storage_objects.setdefault(
                storage_object_id,
                CanonicalStorageObject(
                    object_id=storage_object_id,
                    storage=StorageKind.UB,
                    shape=tuple(logical.shape),
                ),
            )
        self._values[definition_id] = CanonicalValue(
            definition_id=definition_id,
            logical_id=logical_id,
            storage=storage,
            dtype=str(normalize_dtype(logical.dtype, default="fp32")),
            shape=tuple(logical.shape),
            producer_node_id=producer_node_id,
            storage_object_id=storage_object_id,
        )
        return definition_id

    def _ensure_entry(self, logical_id: str, environment: dict[str, str]) -> str:
        definition_id = environment.get(logical_id)
        if definition_id is None:
            definition_id = self._new_definition(logical_id, producer_node_id=None)
            environment[logical_id] = definition_id
        return definition_id

    def _written_registers(self, nodes: Iterable[AdapterNode]) -> set[str]:
        written: set[str] = set()
        for node in nodes:
            if isinstance(node, AdapterInstruction):
                written.update(
                    str(value_id)
                    for value_id in node.dst
                    if self._logical_values[str(value_id)].storage == "Register"
                )
            elif isinstance(node, AdapterAlias):
                destination_id = str(node.destination)
                if self._logical_values[destination_id].storage == "Register":
                    written.add(destination_id)
            elif isinstance(node, AdapterLoop):
                written.update(self._written_registers(node.body))
        return written

    def _version_nodes(
        self,
        nodes: Iterable[AdapterNode],
        environment: dict[str, str],
        induction_variables: frozenset[str],
    ) -> tuple[list[CanonicalNode], dict[str, str]]:
        output: list[CanonicalNode] = []
        current = dict(environment)
        for node in nodes:
            if isinstance(node, AdapterInstruction):
                canonical, current = self._version_instruction(
                    node, current, induction_variables
                )
                output.append(canonical)
            elif isinstance(node, AdapterLoop):
                canonical, current = self._version_loop(
                    node, current, induction_variables
                )
                output.append(canonical)
            elif isinstance(node, AdapterAlias):
                source_id = str(node.source)
                destination_id = str(node.destination)
                source = self._logical_values[source_id]
                destination = self._logical_values[destination_id]
                if source.storage != "Register" or destination.storage != "Register":
                    raise ValueError("VFAlias requires register source and destination")
                if source.dtype != destination.dtype or source.shape != destination.shape:
                    raise ValueError(
                        "VFAlias source and destination must have matching dtype and shape"
                    )
                current[destination_id] = self._ensure_entry(source_id, current)
            elif isinstance(node, AdapterMembar):
                output.append(
                    CanonicalMembar(
                        instruction_id=self._next_node_id("membar"),
                        barrier=node.type,
                        source_location=node.source_location,
                    )
                )
            else:
                raise TypeError(f"Unsupported adapter node: {type(node).__name__}")
        return output, current

    def _version_instruction(
        self,
        node: AdapterInstruction,
        environment: dict[str, str],
        induction_variables: frozenset[str],
    ) -> tuple[CanonicalInstruction, dict[str, str]]:
        instruction_id = self._next_node_id("instruction")
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup(node.name)
        if node.instruction_class is not None:
            instruction_class = InstructionClass(node.instruction_class)
        elif spec is not None:
            instruction_class = spec.instruction_class
        else:
            instruction_class = InstructionClass.COMPUTE

        access_by_value = {item.value_id: item for item in node.memory_accesses}
        input_roles = [
            operand.role
            for operand in (spec.operands if spec is not None else ())
            if operand.direction == OperandDirection.INPUT
        ]
        output_roles = [
            operand.role
            for operand in (spec.operands if spec is not None else ())
            if operand.direction == OperandDirection.OUTPUT
        ]
        current = dict(environment)
        inputs: list[CanonicalOperand] = []
        for operand_index, raw_id in enumerate(
            [*node.src, *node.supplemental_inputs]
        ):
            logical_id = str(raw_id)
            logical = self._logical_values[logical_id]
            storage = _storage_kind(logical.storage)
            definition_id = self._ensure_entry(logical_id, current)
            inputs.append(
                self._operand(
                    definition_id,
                    logical_id,
                    storage,
                    is_output=False,
                    catalog_role=(
                        input_roles[operand_index]
                        if operand_index < len(input_roles)
                        else None
                    ),
                    access=access_by_value.get(logical_id),
                    induction_variables=induction_variables,
                )
            )

        outputs: list[CanonicalOperand] = []
        for operand_index, raw_id in enumerate(node.dst):
            logical_id = str(raw_id)
            logical = self._logical_values[logical_id]
            storage = _storage_kind(logical.storage)
            definition_id = self._new_definition(
                logical_id, producer_node_id=instruction_id
            )
            outputs.append(
                self._operand(
                    definition_id,
                    logical_id,
                    storage,
                    is_output=True,
                    catalog_role=(
                        output_roles[operand_index]
                        if operand_index < len(output_roles)
                        else None
                    ),
                    access=access_by_value.get(logical_id),
                    induction_variables=induction_variables,
                )
            )
            if storage != StorageKind.UB:
                current[logical_id] = definition_id

        return (
            CanonicalInstruction(
                instruction_id=instruction_id,
                opcode=node.name,
                instruction_class=instruction_class,
                form=str(node.form or "fp32"),
                inputs=tuple(inputs),
                outputs=tuple(outputs),
                attributes=dict(node.attributes),
                source_location=node.source_location,
            ),
            current,
        )

    def _operand(
        self,
        definition_id: str,
        logical_id: str,
        storage: StorageKind,
        *,
        is_output: bool,
        catalog_role: OperandRole | None,
        access: AdapterMemoryAccess | None,
        induction_variables: frozenset[str],
    ) -> CanonicalOperand:
        value = self._values[definition_id]
        if storage == StorageKind.UB:
            access_kind = AccessKind.WRITE if is_output else AccessKind.READ
            if access is not None:
                access_kind = AccessKind(access.access_kind)
            return CanonicalOperand(
                value_id=definition_id,
                role=catalog_role or OperandRole.MEMORY,
                dtype=value.dtype,
                memory_access=MemoryAccess(
                    base_object_id=str(value.storage_object_id),
                    offset=_affine_expression(
                        access.offset if access is not None else 0,
                        params=self._params,
                        induction_variables=induction_variables,
                    ),
                    access_kind=access_kind,
                    span=access.span if access is not None else None,
                ),
            )
        role = catalog_role or (
            OperandRole.DESTINATION
            if is_output
            else (
                OperandRole.SCALAR
                if storage == StorageKind.SCALAR
                else OperandRole.SOURCE
            )
        )
        return CanonicalOperand(definition_id, role, value.dtype)

    def _version_loop(
        self,
        node: AdapterLoop,
        environment: dict[str, str],
        parent_induction_variables: frozenset[str],
    ) -> tuple[CanonicalLoop, dict[str, str]]:
        loop_id = self._next_node_id("loop", node.loop_id)
        variable_id = node.induction_variable or f"iter_{_safe_id(loop_id)}"
        written = sorted(self._written_registers(node.body))
        entry_environment = dict(environment)
        entries = {
            logical_id: self._ensure_entry(logical_id, entry_environment)
            for logical_id in written
        }
        body, body_environment = self._version_nodes(
            node.body,
            entry_environment,
            parent_induction_variables | {variable_id},
        )

        carried: list[LoopCarriedValue] = []
        current = dict(environment)
        exit_by_back_edge: dict[str, str] = {}
        for logical_id in written:
            back_edge_id = body_environment[logical_id]
            exit_id = self._new_definition(logical_id, producer_node_id=loop_id)
            exit_by_back_edge[back_edge_id] = exit_id
            carried.append(
                LoopCarriedValue(
                    logical_id=logical_id,
                    entry_value_id=entries[logical_id],
                    back_edge_value_id=back_edge_id,
                    exit_value_id=exit_id,
                )
            )
            current[logical_id] = exit_id

        count = node.count
        if isinstance(count, str):
            count = self._params.get(count, int(count) if count.lstrip("+-").isdigit() else 1)
        if int(count) != 0:
            for logical_id, definition_id in body_environment.items():
                if logical_id not in written:
                    current[logical_id] = exit_by_back_edge.get(
                        definition_id, definition_id
                    )

        return (
            CanonicalLoop(
                loop_id=loop_id,
                induction=InductionVariable(
                    variable_id=variable_id,
                    start=node.induction_start,
                    step=node.induction_step,
                ),
                count=node.count,
                unroll=node.unroll,
                carried_values=tuple(carried),
                body=tuple(body),
                source_location=node.source_location,
            ),
            current,
        )


__all__ = ["ValueVersioningPass"]
