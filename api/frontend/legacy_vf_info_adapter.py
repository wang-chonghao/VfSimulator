from __future__ import annotations

from typing import Iterable, Mapping

from api.vf_info import VFInfo, VFInst, VFLoop, VFNode, ValueInfo, canonicalize_vf_info

from .adapter_ir import (
    AdapterAlias,
    AdapterInstruction,
    AdapterLoop,
    AdapterMemoryAccess,
    AdapterMembar,
    AdapterProgram,
    AdapterValue,
)
from .instruction_catalog import (
    DEFAULT_INSTRUCTION_CATALOG,
    ArgumentKind,
    OperandDirection,
)
from .schema import CanonicalVfInfo, ScalarValue
from .value_versioning import ValueVersioningPass


class LegacyVfInfoAdapter:
    """Repair legacy omissions, then convert migration-period VFInfo to canonical IR."""

    def to_canonical(
        self,
        vf_info: VFInfo,
        *,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> CanonicalVfInfo:
        normalized = canonicalize_vf_info(vf_info)
        self._complete_omitted_scalars(normalized)

        # The old streaming path had no canonical pre-expansion limit. Preserve
        # that compatibility explicitly as configuration, not source metadata.
        normalized.uarch.setdefault("canonical_dynamic_instruction_limit", 0)
        resolved_source = {"adapter": "legacy_vf_info"}
        resolved_source.update(source or {})
        return ValueVersioningPass().run(
            self._to_adapter_program(normalized), source=resolved_source
        )

    @staticmethod
    def _to_adapter_program(vf_info: VFInfo) -> AdapterProgram:
        from api.vf_info import Membar, VFAlias

        def convert_node(node):
            if isinstance(node, VFInst):
                return AdapterInstruction(
                    name=node.name,
                    src=list(node.src),
                    dst=list(node.dst),
                    form=node.form,
                    instruction_class=node.instruction_class,
                    memory_accesses=tuple(
                        AdapterMemoryAccess(
                            item.value_id,
                            item.access_kind,
                            item.offset,
                            item.span,
                            item.mode,
                        )
                        for item in node.memory_accesses
                    ),
                    source_location=node.source_location,
                    attributes=dict(node.attributes),
                    supplemental_inputs=tuple(node.supplemental_inputs),
                )
            if isinstance(node, VFLoop):
                return AdapterLoop(
                    count=node.count,
                    unroll=node.unroll,
                    body=[convert_node(item) for item in node.body],
                    loop_id=node.loop_id,
                    induction_variable=node.induction_variable,
                    induction_start=node.induction_start,
                    induction_step=node.induction_step,
                    source_location=node.source_location,
                )
            if isinstance(node, VFAlias):
                return AdapterAlias(
                    node.destination, node.source, node.source_location
                )
            if isinstance(node, Membar):
                return AdapterMembar(node.type, node.source_location)
            raise TypeError(f"Unsupported legacy VFInfo node: {type(node).__name__}")

        return AdapterProgram(
            context=[convert_node(node) for node in vf_info.context],
            values={
                value_id: AdapterValue(
                    value.value_id,
                    value.storage,
                    value.dtype,
                    value.shape,
                )
                for value_id, value in vf_info.values.items()
            },
            params=dict(vf_info.params),
            default_dtype=vf_info.default_dtype,
            uarch=dict(vf_info.uarch),
        )

    @staticmethod
    def _complete_omitted_scalars(vf_info: VFInfo) -> None:
        placeholder_index = 0

        def visit(nodes: Iterable[VFNode]) -> None:
            nonlocal placeholder_index
            for node in nodes:
                if isinstance(node, VFLoop):
                    visit(node.body)
                    continue
                if not isinstance(node, VFInst):
                    continue
                spec = DEFAULT_INSTRUCTION_CATALOG.lookup(node.name)
                if spec is None:
                    continue
                expected_inputs = [
                    operand
                    for operand in spec.operands
                    if operand.direction == OperandDirection.INPUT
                ]
                actual_count = len(node.src) + len(node.supplemental_inputs)
                missing = expected_inputs[actual_count:]
                if not missing or any(
                    operand.kind != ArgumentKind.SCALAR for operand in missing
                ):
                    continue

                supplemental = list(node.supplemental_inputs)
                for _ in missing:
                    value_id = f"__legacy_omitted_scalar_{placeholder_index}"
                    placeholder_index += 1
                    dtype = (
                        vf_info.values[str(node.src[0])].dtype
                        if node.src
                        else vf_info.default_dtype
                    ) or vf_info.default_dtype
                    vf_info.values[value_id] = ValueInfo(
                        value_id,
                        "Scalar",
                        dtype,
                    )
                    supplemental.append(value_id)
                node.supplemental_inputs = tuple(supplemental)

        visit(vf_info.context)


__all__ = ["LegacyVfInfoAdapter"]
