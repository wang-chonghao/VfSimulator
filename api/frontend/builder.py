from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from api.frontend.diagnostics import Diagnostic
from api.frontend.schema import (
    CANONICAL_VF_INFO_SCHEMA_VERSION,
    CanonicalInstruction,
    CanonicalLoop,
    CanonicalMembar,
    CanonicalNode,
    CanonicalOperand,
    CanonicalStorageObject,
    CanonicalValue,
    CanonicalVfInfo,
    DependencyRef,
    InductionVariable,
    InstructionClass,
    LoopCarriedValue,
    ScalarValue,
    SourceLocation,
    StorageKind,
)
from api.frontend.validator import validate_canonical_vf_info


class VfInfoValidationError(ValueError):
    def __init__(self, diagnostics: Iterable[Diagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        details = "; ".join(
            f"{item.code}: {item.message}" for item in self.diagnostics
        )
        super().__init__(f"CanonicalVfInfo validation failed: {details}")


@dataclass(frozen=True)
class _LoopDefinition:
    loop_id: str
    induction: InductionVariable
    count: int | str
    unroll: int | str
    carried_values: tuple[LoopCarriedValue, ...]
    source_location: SourceLocation | None


class _LoopContext:
    def __init__(self, builder: VfInfoBuilder, definition: _LoopDefinition) -> None:
        self._builder = builder
        self._definition = definition
        self._node_ids_before: set[str] | None = None
        self._values_before: dict[str, CanonicalValue] | None = None
        self._storage_objects_before: dict[str, CanonicalStorageObject] | None = None

    def __enter__(self) -> VfInfoBuilder:
        self._node_ids_before = set(self._builder._node_ids)
        self._values_before = dict(self._builder._values)
        self._storage_objects_before = dict(self._builder._storage_objects)
        self._builder._reserve_node_id(self._definition.loop_id)
        self._builder._body_stack.append([])
        self._builder._loop_stack.append(self)
        return self._builder

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if not self._builder._loop_stack or self._builder._loop_stack[-1] is not self:
            raise RuntimeError("VfInfoBuilder loop scopes must close in stack order")
        self._builder._loop_stack.pop()
        body = self._builder._body_stack.pop()
        if exc_type is not None:
            assert self._node_ids_before is not None
            assert self._values_before is not None
            assert self._storage_objects_before is not None
            self._builder._node_ids = self._node_ids_before
            self._builder._values = self._values_before
            self._builder._storage_objects = self._storage_objects_before
            return False
        self._builder._body_stack[-1].append(
            CanonicalLoop(
                loop_id=self._definition.loop_id,
                induction=self._definition.induction,
                count=self._definition.count,
                unroll=self._definition.unroll,
                carried_values=self._definition.carried_values,
                body=tuple(body),
                source_location=self._definition.source_location,
            )
        )
        return False


class VfInfoBuilder:
    """Explicit builder for validated ``CanonicalVfInfo`` objects."""

    def __init__(
        self,
        *,
        params: Mapping[str, int] | None = None,
        uarch: Mapping[str, ScalarValue] | None = None,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> None:
        self._values: dict[str, CanonicalValue] = {}
        self._storage_objects: dict[str, CanonicalStorageObject] = {}
        self._context: list[CanonicalNode] = []
        self._body_stack: list[list[CanonicalNode]] = [self._context]
        self._loop_stack: list[_LoopContext] = []
        self._node_ids: set[str] = set()
        self._params = dict(params or {})
        self._uarch = dict(uarch or {})
        self._source = dict(source or {})

    def register_storage_object(
        self,
        object_id: str,
        *,
        storage: StorageKind = StorageKind.UB,
        shape: Iterable[int] = (),
        source_location: SourceLocation | None = None,
    ) -> CanonicalStorageObject:
        if object_id in self._storage_objects:
            raise ValueError(f"Duplicate storage object ID: {object_id}")
        storage_object = CanonicalStorageObject(
            object_id=object_id,
            storage=storage,
            shape=tuple(shape),
            source_location=source_location,
        )
        self._storage_objects[object_id] = storage_object
        return storage_object

    def register_value(
        self,
        definition_id: str,
        *,
        logical_id: str,
        storage: StorageKind,
        dtype: str,
        shape: Iterable[int] = (),
        producer_node_id: str | None = None,
        storage_object_id: str | None = None,
        source_location: SourceLocation | None = None,
    ) -> CanonicalValue:
        if definition_id in self._values:
            raise ValueError(f"Duplicate value definition ID: {definition_id}")
        value = CanonicalValue(
            definition_id=definition_id,
            logical_id=logical_id,
            storage=storage,
            dtype=dtype,
            shape=tuple(shape),
            producer_node_id=producer_node_id,
            storage_object_id=storage_object_id,
            source_location=source_location,
        )
        self._values[definition_id] = value
        return value

    def add_instruction(
        self,
        instruction_id: str,
        *,
        opcode: str,
        instruction_class: InstructionClass,
        form: str,
        inputs: Iterable[CanonicalOperand] = (),
        outputs: Iterable[CanonicalOperand] = (),
        dependencies: Iterable[DependencyRef] = (),
        attributes: Mapping[str, ScalarValue] | None = None,
        source_location: SourceLocation | None = None,
    ) -> CanonicalInstruction:
        instruction = CanonicalInstruction(
            instruction_id=instruction_id,
            opcode=opcode,
            instruction_class=instruction_class,
            form=form,
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            dependencies=tuple(dependencies),
            attributes=dict(attributes or {}),
            source_location=source_location,
        )
        self._reserve_node_id(instruction_id)
        self._body_stack[-1].append(instruction)
        return instruction

    def add_membar(
        self,
        instruction_id: str,
        *,
        barrier: str,
        dependencies: Iterable[DependencyRef] = (),
        source_location: SourceLocation | None = None,
    ) -> CanonicalMembar:
        membar = CanonicalMembar(
            instruction_id=instruction_id,
            barrier=barrier,
            dependencies=tuple(dependencies),
            source_location=source_location,
        )
        self._reserve_node_id(instruction_id)
        self._body_stack[-1].append(membar)
        return membar

    def loop(
        self,
        loop_id: str,
        *,
        induction: InductionVariable,
        count: int | str,
        unroll: int | str = 1,
        carried_values: Iterable[LoopCarriedValue] = (),
        source_location: SourceLocation | None = None,
    ) -> _LoopContext:
        return _LoopContext(
            self,
            _LoopDefinition(
                loop_id=loop_id,
                induction=induction,
                count=count,
                unroll=unroll,
                carried_values=tuple(carried_values),
                source_location=source_location,
            ),
        )

    def add_loop(
        self,
        loop_id: str,
        *,
        induction: InductionVariable,
        count: int | str,
        unroll: int | str = 1,
        carried_values: Iterable[LoopCarriedValue] = (),
        body: Iterable[CanonicalNode] = (),
        source_location: SourceLocation | None = None,
    ) -> CanonicalLoop:
        body_nodes = tuple(body)
        carried_value_nodes = tuple(carried_values)
        new_ids = {loop_id}

        def collect(nodes: Iterable[CanonicalNode]) -> None:
            for node in nodes:
                node_id = (
                    node.instruction_id
                    if isinstance(node, (CanonicalInstruction, CanonicalMembar))
                    else node.loop_id
                )
                if not node_id or node_id in new_ids:
                    raise ValueError(f"Duplicate or empty node ID: {node_id}")
                new_ids.add(node_id)
                if isinstance(node, CanonicalLoop):
                    collect(node.body)

        collect(body_nodes)
        duplicate = new_ids & self._node_ids
        if duplicate:
            raise ValueError(f"Duplicate node ID: {sorted(duplicate)[0]}")
        loop = CanonicalLoop(
            loop_id=loop_id,
            induction=induction,
            count=count,
            unroll=unroll,
            carried_values=carried_value_nodes,
            body=body_nodes,
            source_location=source_location,
        )
        self._node_ids.update(new_ids)
        self._body_stack[-1].append(loop)
        return loop

    def build(self) -> CanonicalVfInfo:
        if self._loop_stack:
            raise RuntimeError("Cannot build CanonicalVfInfo inside an open loop scope")
        vf_info = CanonicalVfInfo(
            context=tuple(self._context),
            values=dict(self._values),
            storage_objects=dict(self._storage_objects),
            params=dict(self._params),
            uarch=dict(self._uarch),
            source=dict(self._source),
            schema_version=CANONICAL_VF_INFO_SCHEMA_VERSION,
        )
        validation = validate_canonical_vf_info(vf_info)
        if not validation.ok:
            raise VfInfoValidationError(validation.errors)
        return vf_info

    def _reserve_node_id(self, node_id: str) -> None:
        if not node_id or node_id in self._node_ids:
            raise ValueError(f"Duplicate or empty node ID: {node_id}")
        self._node_ids.add(node_id)
