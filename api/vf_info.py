from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Literal, TypeAlias

from api.input_symbols import (
    compact_dtype,
    normalize_dtype,
    normalize_form,
    normalize_membar_type,
    normalize_opcode,
    normalize_storage,
    specialize_opcode,
)


ValueStorageKind: TypeAlias = Literal["Register", "UB", "Scalar"]


@dataclass(frozen=True, init=False)
class ValueInfo:
    """Typed value referenced by instructions in a VF program.

    ``name`` and ``location`` are accepted as compatibility aliases for the
    original ``MemInfo`` constructor.
    """

    value_id: str
    storage: ValueStorageKind
    dtype: str | None
    shape: tuple[int, ...]

    def __init__(
        self,
        value_id: str | None = None,
        storage: ValueStorageKind = "Register",
        dtype: str | None = None,
        shape: List[int] | tuple[int, ...] = (),
        *,
        name: str | None = None,
        location: ValueStorageKind | None = None,
    ) -> None:
        resolved_id = value_id if value_id is not None else name
        if resolved_id is None or not str(resolved_id):
            raise ValueError("ValueInfo requires a non-empty value_id")
        resolved_storage = normalize_storage(location if location is not None else storage)
        object.__setattr__(self, "value_id", str(resolved_id))
        object.__setattr__(self, "storage", resolved_storage)
        object.__setattr__(self, "dtype", normalize_dtype(dtype))
        object.__setattr__(self, "shape", tuple(int(dim) for dim in shape))

    @property
    def name(self) -> str:
        return self.value_id

    @property
    def location(self) -> ValueStorageKind:
        return self.storage


# Compatibility name retained for existing callers.
MemInfo = ValueInfo


ValueRef: TypeAlias = "str | ValueInfo"
VFNode: TypeAlias = "VFLoop | VFInst | Membar"


@dataclass
class VFInst:
    name: str
    src: list[ValueRef]
    dst: list[ValueRef]
    form: str | None = None

    @property
    def op(self) -> str:
        return self.name


@dataclass
class Membar:
    type: str = "VST_VLD"


@dataclass
class VFLoop:
    count: int | str
    unroll: int | str = 1
    body: list[VFNode] = field(default_factory=list)
    loop_id: str | None = None


@dataclass
class VFInfo:
    context: list[VFNode]
    values: dict[str, ValueInfo] = field(default_factory=dict)
    params: dict[str, int] = field(default_factory=dict)
    default_dtype: str = "fp32"
    uarch: dict[str, Any] = field(default_factory=dict)


def _storage_from_id(value_id: str) -> ValueStorageKind:
    lower = value_id.lower()
    if lower.startswith("mem"):
        return "UB"
    if lower.startswith("v"):
        return "Register"
    return "Scalar"


def _dtype_from_conversion_form(form: str | None) -> tuple[str | None, str | None]:
    if not form or "_to_" not in form:
        return None, None
    src, dst = form.split("_to_", 1)
    return normalize_dtype(src), normalize_dtype(dst)


def _merge_value(existing: ValueInfo | None, incoming: ValueInfo) -> ValueInfo:
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
    return ValueInfo(
        existing.value_id,
        existing.storage,
        existing.dtype or incoming.dtype,
        existing.shape or incoming.shape,
    )


def _infer_inst_form(inst: VFInst, values: dict[str, ValueInfo], default_dtype: str) -> str:
    if inst.form:
        return str(normalize_form(inst.form))
    src_dtypes = [values[value_id].dtype for value_id in inst.src if values[value_id].dtype]
    dst_dtypes = [values[value_id].dtype for value_id in inst.dst if values[value_id].dtype]
    if src_dtypes and dst_dtypes and src_dtypes[0] != dst_dtypes[0]:
        src = compact_dtype(src_dtypes[0])
        dst = compact_dtype(dst_dtypes[0])
        return f"{src}_to_{dst}"
    return str(normalize_dtype((dst_dtypes or src_dtypes or [default_dtype])[0]))


def canonicalize_vf_info(vf_info: VFInfo) -> VFInfo:
    """Return a validated VfInfo whose instruction operands are value IDs."""

    values: dict[str, ValueInfo] = {
        value.value_id: ValueInfo(
            value.value_id,
            value.storage,
            value.dtype,
            value.shape,
        )
        for value in vf_info.values.values()
    }

    def register(ref: ValueRef) -> str:
        if isinstance(ref, ValueInfo):
            values[ref.value_id] = _merge_value(values.get(ref.value_id), ref)
            return ref.value_id
        value_id = str(ref)
        values.setdefault(
            value_id,
            ValueInfo(value_id, _storage_from_id(value_id)),
        )
        return value_id

    def normalize_nodes(nodes: list[VFNode]) -> list[VFNode]:
        normalized: list[VFNode] = []
        for node in nodes:
            if isinstance(node, VFInst):
                src_ids = [register(ref) for ref in node.src]
                dst_ids = [register(ref) for ref in node.dst]
                normalized.append(
                    VFInst(
                        normalize_opcode(node.name),
                        src_ids,
                        dst_ids,
                        normalize_form(node.form) if node.form else None,
                    )
                )
            elif isinstance(node, VFLoop):
                normalized.append(
                    VFLoop(
                        node.count,
                        node.unroll,
                        normalize_nodes(node.body),
                        node.loop_id,
                    )
                )
            elif isinstance(node, Membar):
                normalized.append(Membar(normalize_membar_type(node.type)))
            else:
                raise TypeError(f"Unsupported VFInfo node: {type(node).__name__}")
        return normalized

    context = normalize_nodes(vf_info.context)

    def complete_nodes(nodes: list[VFNode]) -> list[VFNode]:
        completed: list[VFNode] = []
        for node in nodes:
            if isinstance(node, VFLoop):
                completed.append(replace(node, body=complete_nodes(node.body)))
                continue
            if not isinstance(node, VFInst):
                completed.append(node)
                continue

            src_dtype, dst_dtype = _dtype_from_conversion_form(node.form)
            simple_form = node.form if node.form and "_to_" not in node.form else None
            for value_id in node.src:
                value = values[value_id]
                dtype = value.dtype or src_dtype or simple_form or normalize_dtype(vf_info.default_dtype)
                values[value_id] = replace(value, dtype=str(dtype))
            for value_id in node.dst:
                value = values[value_id]
                dtype = value.dtype or dst_dtype or simple_form or normalize_dtype(vf_info.default_dtype)
                values[value_id] = replace(value, dtype=str(dtype))
            completed.append(node)
        return completed

    context = complete_nodes(context)

    def resolve_forms(nodes: list[VFNode]) -> list[VFNode]:
        out: list[VFNode] = []
        for node in nodes:
            if isinstance(node, VFLoop):
                out.append(replace(node, body=resolve_forms(node.body)))
            elif isinstance(node, VFInst):
                form = _infer_inst_form(node, values, vf_info.default_dtype)
                op = specialize_opcode(node.name, form)
                out.append(replace(node, name=op, form=form))
            else:
                out.append(node)
        return out

    return VFInfo(
        context=resolve_forms(context),
        values=values,
        params={str(key): int(value) for key, value in vf_info.params.items()},
        default_dtype=str(normalize_dtype(vf_info.default_dtype, default="fp32")),
        uarch=dict(vf_info.uarch),
    )


__all__ = [
    "Membar",
    "MemInfo",
    "ValueInfo",
    "ValueStorageKind",
    "VFInfo",
    "VFInst",
    "VFLoop",
    "VFNode",
    "canonicalize_vf_info",
]
