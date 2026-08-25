from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from api.frontend.schema import SourceLocation
from api.input_symbols import (
    compact_dtype,
    normalize_dtype,
    normalize_form,
    normalize_membar_type,
    normalize_opcode,
    normalize_storage,
)


AdapterStorageKind: TypeAlias = Literal["Register", "UB", "Scalar"]


@dataclass(frozen=True)
class AdapterValue:
    value_id: str
    storage: AdapterStorageKind = "Register"
    dtype: str | None = None
    shape: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.value_id:
            raise ValueError("AdapterValue requires a non-empty value_id")
        object.__setattr__(self, "storage", normalize_storage(self.storage))
        object.__setattr__(self, "dtype", normalize_dtype(self.dtype))
        object.__setattr__(self, "shape", tuple(int(dim) for dim in self.shape))


AdapterValueRef: TypeAlias = "str | AdapterValue"


@dataclass(frozen=True)
class AdapterMemoryAccess:
    value_id: str
    access_kind: Literal["read", "write"]
    offset: int | str = 0
    span: int | None = None
    mode: str | None = None


@dataclass
class AdapterInstruction:
    name: str
    src: list[AdapterValueRef]
    dst: list[AdapterValueRef]
    form: str | None = None
    instruction_class: str | None = None
    memory_accesses: tuple[AdapterMemoryAccess, ...] = ()
    source_location: SourceLocation | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    supplemental_inputs: tuple[AdapterValueRef, ...] = ()


@dataclass(frozen=True)
class AdapterAlias:
    destination: AdapterValueRef
    source: AdapterValueRef
    source_location: SourceLocation | None = None


@dataclass
class AdapterMembar:
    type: str = "VST_VLD"
    source_location: SourceLocation | None = None


@dataclass
class AdapterLoop:
    count: int | str
    unroll: int | str = 1
    body: list[AdapterNode] = field(default_factory=list)
    loop_id: str | None = None
    induction_variable: str | None = None
    induction_start: int | str = 0
    induction_step: int | str = 1
    source_location: SourceLocation | None = None


AdapterNode: TypeAlias = "AdapterLoop | AdapterInstruction | AdapterAlias | AdapterMembar"


@dataclass
class AdapterProgram:
    context: list[AdapterNode]
    values: dict[str, AdapterValue] = field(default_factory=dict)
    params: dict[str, int] = field(default_factory=dict)
    default_dtype: str = "fp32"
    uarch: dict[str, Any] = field(default_factory=dict)


def _storage_from_id(value_id: str) -> AdapterStorageKind:
    lower = value_id.lower()
    if lower.startswith("mem"):
        return "UB"
    if lower.startswith("v"):
        return "Register"
    return "Scalar"


def _merge_value(
    existing: AdapterValue | None, incoming: AdapterValue
) -> AdapterValue:
    if existing is None:
        return incoming
    if existing.storage != incoming.storage:
        raise ValueError(
            f"Conflicting storage for value {incoming.value_id}: "
            f"{existing.storage} vs {incoming.storage}"
        )
    if existing.dtype and incoming.dtype and existing.dtype != incoming.dtype:
        raise ValueError(
            f"Conflicting dtype for value {incoming.value_id}: "
            f"{existing.dtype} vs {incoming.dtype}"
        )
    if existing.shape and incoming.shape and existing.shape != incoming.shape:
        raise ValueError(
            f"Conflicting shape for value {incoming.value_id}: "
            f"{existing.shape} vs {incoming.shape}"
        )
    return AdapterValue(
        existing.value_id,
        existing.storage,
        existing.dtype or incoming.dtype,
        existing.shape or incoming.shape,
    )


def _infer_form(
    inst: AdapterInstruction,
    values: dict[str, AdapterValue],
    default_dtype: str,
) -> str:
    if inst.form:
        return str(normalize_form(inst.form))
    src_dtypes = [values[str(item)].dtype for item in inst.src if values[str(item)].dtype]
    dst_dtypes = [values[str(item)].dtype for item in inst.dst if values[str(item)].dtype]
    if src_dtypes and dst_dtypes and src_dtypes[0] != dst_dtypes[0]:
        return f"{compact_dtype(src_dtypes[0])}_to_{compact_dtype(dst_dtypes[0])}"
    return str(normalize_dtype((dst_dtypes or src_dtypes or [default_dtype])[0]))


def normalize_adapter_program(program: AdapterProgram) -> AdapterProgram:
    """Normalize parser IR before canonical definition construction."""

    values = dict(program.values)

    def register(ref: AdapterValueRef) -> str:
        if isinstance(ref, AdapterValue):
            values[ref.value_id] = _merge_value(values.get(ref.value_id), ref)
            return ref.value_id
        value_id = str(ref)
        values.setdefault(value_id, AdapterValue(value_id, _storage_from_id(value_id)))
        return value_id

    def visit(nodes: list[AdapterNode]) -> list[AdapterNode]:
        normalized: list[AdapterNode] = []
        for node in nodes:
            if isinstance(node, AdapterInstruction):
                src = [register(item) for item in node.src]
                dst = [register(item) for item in node.dst]
                supplemental = tuple(register(item) for item in node.supplemental_inputs)
                current = AdapterInstruction(
                    name=normalize_opcode(node.name),
                    src=src,
                    dst=dst,
                    form=normalize_form(node.form) if node.form else None,
                    instruction_class=node.instruction_class,
                    memory_accesses=tuple(node.memory_accesses),
                    source_location=node.source_location,
                    attributes=dict(node.attributes),
                    supplemental_inputs=supplemental,
                )
                current.form = _infer_form(current, values, program.default_dtype)
                normalized.append(current)
            elif isinstance(node, AdapterLoop):
                normalized.append(
                    AdapterLoop(
                        node.count,
                        node.unroll,
                        visit(node.body),
                        node.loop_id,
                        node.induction_variable,
                        node.induction_start,
                        node.induction_step,
                        node.source_location,
                    )
                )
            elif isinstance(node, AdapterAlias):
                normalized.append(
                    AdapterAlias(
                        register(node.destination),
                        register(node.source),
                        node.source_location,
                    )
                )
            elif isinstance(node, AdapterMembar):
                normalized.append(
                    AdapterMembar(normalize_membar_type(node.type), node.source_location)
                )
            else:
                raise TypeError(f"Unsupported adapter node: {type(node).__name__}")
        return normalized

    context = visit(program.context)
    for value_id, value in tuple(values.items()):
        values[value_id] = AdapterValue(
            value.value_id,
            value.storage,
            value.dtype or program.default_dtype,
            value.shape,
        )
    return AdapterProgram(
        context=context,
        values=values,
        params=dict(program.params),
        default_dtype=str(normalize_dtype(program.default_dtype, default="fp32")),
        uarch=dict(program.uarch),
    )


__all__ = [
    "AdapterAlias",
    "AdapterInstruction",
    "AdapterLoop",
    "AdapterMemoryAccess",
    "AdapterMembar",
    "AdapterNode",
    "AdapterProgram",
    "AdapterValue",
    "normalize_adapter_program",
]
