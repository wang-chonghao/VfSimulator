from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, TypeAlias


CANONICAL_VF_INFO_SCHEMA_VERSION = 1
ScalarValue: TypeAlias = None | bool | int | float | str


class StorageKind(str, Enum):
    REGISTER = "Register"
    UB = "UB"
    SCALAR = "Scalar"


class InstructionClass(str, Enum):
    LOAD = "load"
    STORE = "store"
    COMPUTE = "compute"
    CONTROL = "control"


class OperandRole(str, Enum):
    SOURCE = "source"
    DESTINATION = "destination"
    MEMORY = "memory"
    SCALAR = "scalar"
    PREDICATE = "predicate"
    CONFIG = "config"


class AccessKind(str, Enum):
    READ = "read"
    WRITE = "write"


class DependencyKind(str, Enum):
    MEMORY = "memory"
    CONTROL = "control"


@dataclass(frozen=True)
class SourceLocation:
    source: str | None = None
    line: int | None = None
    column: int | None = None
    path: str | None = None


@dataclass(frozen=True)
class AffineTerm:
    variable_id: str
    coefficient: int = 1


@dataclass(frozen=True)
class AffineExpression:
    constant: int = 0
    terms: tuple[AffineTerm, ...] = ()


@dataclass(frozen=True)
class MemoryAccess:
    base_object_id: str
    offset: AffineExpression
    access_kind: AccessKind
    span: int | None = None
    alias_group: str | None = None


@dataclass(frozen=True)
class CanonicalStorageObject:
    object_id: str
    storage: StorageKind
    shape: tuple[int, ...] = ()
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class CanonicalValue:
    definition_id: str
    logical_id: str
    storage: StorageKind
    dtype: str
    shape: tuple[int, ...] = ()
    producer_node_id: str | None = None
    storage_object_id: str | None = None
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class CanonicalOperand:
    value_id: str
    role: OperandRole
    dtype: str | None = None
    memory_access: MemoryAccess | None = None


@dataclass(frozen=True)
class DependencyRef:
    producer_node_id: str
    kind: DependencyKind
    operand_index: int | None = None


@dataclass(frozen=True)
class CanonicalInstruction:
    instruction_id: str
    opcode: str
    instruction_class: InstructionClass
    form: str
    inputs: tuple[CanonicalOperand, ...] = ()
    outputs: tuple[CanonicalOperand, ...] = ()
    dependencies: tuple[DependencyRef, ...] = ()
    attributes: Mapping[str, ScalarValue] = field(default_factory=dict)
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class InductionVariable:
    variable_id: str
    start: int | str = 0
    step: int | str = 1


@dataclass(frozen=True)
class LoopCarriedValue:
    logical_id: str
    entry_value_id: str
    back_edge_value_id: str
    exit_value_id: str


@dataclass(frozen=True)
class CanonicalLoop:
    loop_id: str
    induction: InductionVariable
    count: int | str
    unroll: int | str = 1
    carried_values: tuple[LoopCarriedValue, ...] = ()
    body: tuple[CanonicalNode, ...] = ()
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class CanonicalMembar:
    instruction_id: str
    barrier: str
    dependencies: tuple[DependencyRef, ...] = ()
    source_location: SourceLocation | None = None


CanonicalNode: TypeAlias = CanonicalInstruction | CanonicalLoop | CanonicalMembar


@dataclass(frozen=True)
class CanonicalVfInfo:
    context: tuple[CanonicalNode, ...]
    values: Mapping[str, CanonicalValue]
    storage_objects: Mapping[str, CanonicalStorageObject] = field(default_factory=dict)
    params: Mapping[str, int] = field(default_factory=dict)
    uarch: Mapping[str, ScalarValue] = field(default_factory=dict)
    source: Mapping[str, ScalarValue] = field(default_factory=dict)
    schema_version: int = CANONICAL_VF_INFO_SCHEMA_VERSION
