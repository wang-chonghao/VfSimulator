from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .schema import InstructionClass, OperandRole, StorageKind


class FormRule(str, Enum):
    OPERAND_DTYPE = "operand_dtype"
    CONVERSION = "conversion"
    FIXED = "fixed"


@dataclass(frozen=True)
class OperandSpec:
    name: str
    argument_index: int
    role: OperandRole
    storage: StorageKind


@dataclass(frozen=True)
class InstructionSpec:
    opcode: str
    instruction_class: InstructionClass
    aliases: tuple[str, ...] = ()
    forms: frozenset[str] = frozenset()
    operands: tuple[OperandSpec, ...] = ()
    form_rule: FormRule = FormRule.OPERAND_DTYPE
    fixed_form: str | None = None
    specializations: Mapping[str, str] = MappingProxyType({})
    virtual: bool = False
    timing_optional: bool = False


@dataclass(frozen=True)
class CatalogTimingDifference:
    catalog_without_timing: frozenset[str]
    timing_without_catalog: frozenset[str]
    forms_without_timing: Mapping[str, frozenset[str]]

    @property
    def is_empty(self) -> bool:
        return not (
            self.catalog_without_timing
            or self.timing_without_catalog
            or self.forms_without_timing
        )


_COMPUTE_SIGNATURE = (
    OperandSpec("dst", 0, OperandRole.DESTINATION, StorageKind.REGISTER),
    OperandSpec("src", 1, OperandRole.SOURCE, StorageKind.REGISTER),
)
_LOAD_SIGNATURE = (
    OperandSpec("dst", 0, OperandRole.DESTINATION, StorageKind.REGISTER),
    OperandSpec("memory", 1, OperandRole.MEMORY, StorageKind.UB),
)
_STORE_SIGNATURE = (
    OperandSpec("src", 0, OperandRole.SOURCE, StorageKind.REGISTER),
    OperandSpec("memory", 1, OperandRole.MEMORY, StorageKind.UB),
)
_FP_FORMS = frozenset({"fp16", "fp32"})


def _compute(
    opcode: str,
    *,
    aliases: tuple[str, ...] = (),
    forms: frozenset[str] = _FP_FORMS,
    form_rule: FormRule = FormRule.OPERAND_DTYPE,
    fixed_form: str | None = None,
    specializations: Mapping[str, str] = MappingProxyType({}),
    virtual: bool = False,
) -> InstructionSpec:
    return InstructionSpec(
        opcode=opcode,
        instruction_class=InstructionClass.COMPUTE,
        aliases=aliases,
        forms=forms,
        operands=_COMPUTE_SIGNATURE,
        form_rule=form_rule,
        fixed_form=fixed_form,
        specializations=specializations,
        virtual=virtual,
    )


def _default_specs() -> tuple[InstructionSpec, ...]:
    regular_compute = (
        "VADDS", "VEXP", "VADD", "VMULS", "VDIV", "VABS", "VSUB",
        "VMUL", "VMAXS", "VMINS", "VMAX", "VMIN", "VCMAX", "VCMIN",
        "VCADD", "VDUP", "VAND", "VCGMAX", "VCMP_EQ", "VSEL", "VSHLS",
        "VSHRS", "VBR",
    )
    specs = [_compute(opcode) for opcode in regular_compute]
    specs.extend((
        InstructionSpec(
            "VLDS", InstructionClass.LOAD, ("VLD",), _FP_FORMS,
            _LOAD_SIGNATURE,
        ),
        InstructionSpec(
            "VSTS", InstructionClass.STORE, ("VST",),
            frozenset({"bf16", "fp16", "fp32"}), _STORE_SIGNATURE,
        ),
        InstructionSpec(
            "VSTUS", InstructionClass.STORE, (), frozenset(),
            _STORE_SIGNATURE, timing_optional=True,
        ),
        InstructionSpec(
            "VSTAS", InstructionClass.STORE, (), frozenset(),
            _STORE_SIGNATURE, timing_optional=True,
        ),
        _compute(
            "VCVT",
            forms=frozenset(),
            form_rule=FormRule.CONVERSION,
            specializations=MappingProxyType({
                "f32_to_f16": "VCVT_F32_TO_F16",
                "f32_to_bf16": "VCVT_F32_TO_BF16",
                "f16_to_f32": "VCVT_F16_TO_F32",
                "f32_to_s32": "VCVT_F32_TO_S32",
                "s32_to_f32": "VCVT_S32_TO_F32",
            }),
            virtual=True,
        ),
        _compute("VCVT_F16_TO_F32", forms=frozenset({"f16_to_f32"}),
                 form_rule=FormRule.FIXED, fixed_form="f16_to_f32"),
        _compute("VCVT_F32_TO_F16", forms=frozenset({"f32_to_f16"}),
                 form_rule=FormRule.FIXED, fixed_form="f32_to_f16"),
        _compute("VCVT_F32_TO_BF16", forms=frozenset({"f32_to_bf16"}),
                 form_rule=FormRule.FIXED, fixed_form="f32_to_bf16"),
        _compute("VCVT_F32_TO_S32", forms=frozenset({"f32_to_s32"}),
                 form_rule=FormRule.FIXED, fixed_form="f32_to_s32"),
        _compute("VCVT_S32_TO_F32", forms=frozenset({"s32_to_f32"}),
                 form_rule=FormRule.FIXED, fixed_form="s32_to_f32"),
        _compute("VPACK", forms=frozenset({"b32"}), form_rule=FormRule.FIXED,
                 fixed_form="b32"),
        InstructionSpec(
            "VSSTB", InstructionClass.STORE, (), frozenset({"b16"}),
            _STORE_SIGNATURE, FormRule.FIXED, "b16",
        ),
        _compute("VEXPDIF", forms=frozenset({"fp32"})),
        _compute("VMULSCVT", forms=frozenset({"f32_to_f16"}),
                 form_rule=FormRule.CONVERSION),
    ))
    return tuple(specs)


class InstructionCatalog:
    def __init__(self, specs: Iterable[InstructionSpec]) -> None:
        by_opcode: dict[str, InstructionSpec] = {}
        aliases: dict[str, str] = {}
        for spec in specs:
            opcode = spec.opcode.upper()
            if opcode != spec.opcode or opcode in by_opcode:
                raise ValueError(f"Invalid or duplicate canonical opcode: {spec.opcode}")
            by_opcode[opcode] = spec
            for alias in (opcode, *spec.aliases):
                key = alias.strip().lower()
                previous = aliases.get(key)
                if not key or (previous is not None and previous != opcode):
                    raise ValueError(f"Conflicting opcode alias: {alias}")
                aliases[key] = opcode
        self._by_opcode = MappingProxyType(by_opcode)
        self._aliases = MappingProxyType(aliases)

    @property
    def specs(self) -> Mapping[str, InstructionSpec]:
        return self._by_opcode

    @property
    def aliases(self) -> Mapping[str, str]:
        return self._aliases

    def lookup(self, value: Any) -> InstructionSpec | None:
        opcode = self.canonical_opcode(value)
        return self._by_opcode.get(opcode)

    def canonical_opcode(self, value: Any) -> str:
        text = str(getattr(value, "value", value)).strip()
        if not text:
            return ""
        return self._aliases.get(text.lower(), text.upper())

    def specialize(self, opcode: Any, form: str | None) -> str:
        canonical = self.canonical_opcode(opcode)
        spec = self._by_opcode.get(canonical)
        if spec is None or form is None:
            return canonical
        return spec.specializations.get(form, canonical)

    def compare_timing_config(self, payload: Mapping[str, Any]) -> CatalogTimingDifference:
        instructions = payload.get("instructions", {})
        if not isinstance(instructions, Mapping):
            raise ValueError("ISA timing config must contain an instructions mapping")
        normalized_instructions = {
            str(op).upper(): spec for op, spec in instructions.items()
        }
        timing_ops = set(normalized_instructions)
        required_ops = {
            op for op, spec in self._by_opcode.items()
            if not spec.virtual and not spec.timing_optional
        }
        forms_without_timing: dict[str, frozenset[str]] = {}
        for opcode in required_ops & timing_ops:
            timing_spec = normalized_instructions[opcode]
            if not isinstance(timing_spec, Mapping):
                raise ValueError(f"ISA timing entry {opcode} must be a mapping")
            timing_forms = set((timing_spec.get("forms") or {}).keys())
            missing = self._by_opcode[opcode].forms - timing_forms
            if missing:
                forms_without_timing[opcode] = frozenset(missing)
        return CatalogTimingDifference(
            catalog_without_timing=frozenset(required_ops - timing_ops),
            timing_without_catalog=frozenset(timing_ops - set(self._by_opcode)),
            forms_without_timing=MappingProxyType(forms_without_timing),
        )


DEFAULT_INSTRUCTION_CATALOG = InstructionCatalog(_default_specs())


__all__ = [
    "CatalogTimingDifference",
    "DEFAULT_INSTRUCTION_CATALOG",
    "FormRule",
    "InstructionCatalog",
    "InstructionSpec",
    "OperandSpec",
]
