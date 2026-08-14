from __future__ import annotations

from enum import Enum
from typing import Any


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


class DType(_StrEnum):
    FP32 = "fp32"
    FP16 = "fp16"
    BF16 = "bf16"
    INT32 = "int32"
    UINT32 = "uint32"
    BOOL = "bool"


class StorageKind(_StrEnum):
    REGISTER = "Register"
    UB = "UB"
    SCALAR = "Scalar"


class MembarType(_StrEnum):
    VST_VLD = "VST_VLD"
    VLD_VST = "VLD_VST"


class OpCode(_StrEnum):
    VLDS = "VLDS"
    VSTS = "VSTS"
    VSTUS = "VSTUS"
    VSTAS = "VSTAS"
    VADD = "VADD"
    VADDS = "VADDS"
    VMUL = "VMUL"
    VMULS = "VMULS"
    VCVT = "VCVT"
    VCVT_F32_TO_F16 = "VCVT_F32_TO_F16"
    VCVT_F16_TO_F32 = "VCVT_F16_TO_F32"
    VCVT_F32_TO_S32 = "VCVT_F32_TO_S32"
    VCVT_S32_TO_F32 = "VCVT_S32_TO_F32"
    VPACK = "VPACK"
    VSSTB = "VSSTB"
    VEXPDIF = "VEXPDIF"
    VMULSCVT = "VMULSCVT"


DTYPE_ALIASES = {
    "f32": DType.FP32,
    "float32": DType.FP32,
    "fp32": DType.FP32,
    "f16": DType.FP16,
    "float16": DType.FP16,
    "fp16": DType.FP16,
    "bf16": DType.BF16,
    "bfloat16": DType.BF16,
    "s32": DType.INT32,
    "i32": DType.INT32,
    "int32": DType.INT32,
    "u32": DType.UINT32,
    "uint32": DType.UINT32,
    "bool": DType.BOOL,
    "boolean": DType.BOOL,
    "predicate": DType.BOOL,
}

FORM_DTYPE_ALIASES = {
    "f32": DType.FP32,
    "f16": DType.FP16,
    "bf16": DType.BF16,
    "s32": DType.INT32,
    "i32": DType.INT32,
    "u32": DType.UINT32,
}

DTYPE_TO_COMPACT = {
    DType.FP32.value: "f32",
    DType.FP16.value: "f16",
    DType.BF16.value: "bf16",
    DType.INT32.value: "s32",
    DType.UINT32.value: "u32",
    DType.BOOL.value: "bool",
}

OPCODE_ALIASES = {
    "vld": OpCode.VLDS,
    "vlds": OpCode.VLDS,
    "vst": OpCode.VSTS,
    "vsts": OpCode.VSTS,
    "vstus": OpCode.VSTUS,
    "vstas": OpCode.VSTAS,
    "vadd": OpCode.VADD,
    "vadds": OpCode.VADDS,
    "vmul": OpCode.VMUL,
    "vmuls": OpCode.VMULS,
    "vcvt": OpCode.VCVT,
    "vcvt_f32_to_f16": OpCode.VCVT_F32_TO_F16,
    "vcvt_f16_to_f32": OpCode.VCVT_F16_TO_F32,
    "vcvt_f32_to_s32": OpCode.VCVT_F32_TO_S32,
    "vcvt_s32_to_f32": OpCode.VCVT_S32_TO_F32,
    "vpack": OpCode.VPACK,
    "vsstb": OpCode.VSSTB,
}

VCVT_SPECIALIZATIONS = {
    "f32_to_f16": OpCode.VCVT_F32_TO_F16,
    "f16_to_f32": OpCode.VCVT_F16_TO_F32,
    "f32_to_s32": OpCode.VCVT_F32_TO_S32,
    "s32_to_f32": OpCode.VCVT_S32_TO_F32,
}

STORAGE_ALIASES = {
    "register": StorageKind.REGISTER,
    "reg": StorageKind.REGISTER,
    "vreg": StorageKind.REGISTER,
    "ub": StorageKind.UB,
    "unified_buffer": StorageKind.UB,
    "memory": StorageKind.UB,
    "mem": StorageKind.UB,
    "scalar": StorageKind.SCALAR,
    "imm": StorageKind.SCALAR,
}

MEMBAR_ALIASES = {
    "vst_vld": MembarType.VST_VLD,
    "vld_vst": MembarType.VLD_VST,
}


def _symbol_text(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def normalize_dtype(value: Any | None, *, default: str | None = None) -> str | None:
    if value is None or value == "":
        return default
    text = _symbol_text(value).strip().lower()
    normalized = DTYPE_ALIASES.get(text)
    return normalized.value if normalized is not None else text


def normalize_form(value: Any | None, *, default: str | None = None) -> str | None:
    if value is None or value == "":
        return default
    text = _symbol_text(value).strip().lower()
    if "_to_" in text:
        src, dst = text.split("_to_", 1)
        src_dtype = FORM_DTYPE_ALIASES.get(src, DTYPE_ALIASES.get(src))
        dst_dtype = FORM_DTYPE_ALIASES.get(dst, DTYPE_ALIASES.get(dst))
        src_key = DTYPE_TO_COMPACT.get(src_dtype.value if src_dtype else src, src)
        dst_key = DTYPE_TO_COMPACT.get(dst_dtype.value if dst_dtype else dst, dst)
        return f"{src_key}_to_{dst_key}"
    return normalize_dtype(text, default=default)


def compact_dtype(value: Any | None) -> str | None:
    dtype = normalize_dtype(value)
    if dtype is None:
        return None
    return DTYPE_TO_COMPACT.get(dtype, dtype)


def normalize_opcode(value: Any) -> str:
    text = _symbol_text(value).strip()
    if not text:
        return ""
    normalized = OPCODE_ALIASES.get(text.lower())
    return normalized.value if normalized is not None else text.upper()


def normalize_storage(value: Any) -> str:
    text = _symbol_text(value).strip()
    normalized = STORAGE_ALIASES.get(text.lower())
    if normalized is None:
        raise ValueError(f"Unsupported value storage: {value}")
    return normalized.value


def normalize_membar_type(value: Any | None, *, default: str = "VST_VLD") -> str:
    text = _symbol_text(value if value not in (None, "") else default).strip().upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    normalized = MEMBAR_ALIASES.get(text.lower())
    return normalized.value if normalized is not None else text


def specialize_opcode(op: Any, form: Any | None) -> str:
    canonical_op = normalize_opcode(op)
    canonical_form = normalize_form(form) if form else None
    if canonical_op == OpCode.VCVT.value and canonical_form:
        specialized = VCVT_SPECIALIZATIONS.get(str(canonical_form))
        if specialized is not None:
            return specialized.value
    return canonical_op


__all__ = [
    "DType",
    "DTYPE_ALIASES",
    "MEMBAR_ALIASES",
    "MembarType",
    "OPCODE_ALIASES",
    "OpCode",
    "STORAGE_ALIASES",
    "StorageKind",
    "VCVT_SPECIALIZATIONS",
    "compact_dtype",
    "normalize_dtype",
    "normalize_form",
    "normalize_membar_type",
    "normalize_opcode",
    "normalize_storage",
    "specialize_opcode",
]
