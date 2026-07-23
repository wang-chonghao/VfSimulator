from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from api.vf_info import (
    Membar,
    ValueInfo,
    VFInfo,
    VFInst,
    VFLoop,
    VFNode,
    canonicalize_vf_info,
)


@dataclass
class VFInfoLowerer:
    """
    Lower public VFInfo objects into the simulator's current program payload.

    Public API operands carry explicit locations, so user-visible names can be
    arbitrary. This lowering keeps the historical V*/mem* symbols stable for
    dumps, and also emits a values table consumed by the core storage logic.
    """

    _register_names: Dict[str, str] = field(default_factory=dict)
    _ub_names: Dict[str, str] = field(default_factory=dict)
    _reserved_register_names: set[str] = field(default_factory=set)
    _reserved_ub_names: set[str] = field(default_factory=set)

    def lower(self, vf_info: VFInfo, dtype: str | None = None) -> Dict[str, Any]:
        vf_info = canonicalize_vf_info(vf_info)
        self._register_names.clear()
        self._ub_names.clear()
        self._reserved_register_names = {
            value.value_id
            for value in vf_info.values.values()
            if value.storage == "Register" and value.value_id[:1].lower() == "v"
        }
        self._reserved_ub_names = {
            value.value_id
            for value in vf_info.values.values()
            if value.storage == "UB" and value.value_id.lower().startswith("mem")
        }
        lowered_values = {
            self._lower_value_id(value): {
                "value_id": self._lower_value_id(value),
                "storage": value.storage,
                "dtype": value.dtype,
                "shape": list(value.shape),
            }
            for value in vf_info.values.values()
        }
        return {
            "dtype": str(dtype or vf_info.default_dtype),
            "params": dict(vf_info.params),
            "uarch": dict(vf_info.uarch),
            "values": lowered_values,
            "program": self._lower_body(vf_info.context, vf_info.values),
            "api_symbol_map": {
                "Register": dict(self._register_names),
                "UB": dict(self._ub_names),
            },
        }

    def _lower_body(
        self,
        body: List[VFNode],
        values: Dict[str, ValueInfo],
    ) -> List[Dict[str, Any]]:
        return [self._lower_node(node, values) for node in body]

    def _lower_node(
        self,
        node: VFNode,
        values: Dict[str, ValueInfo],
    ) -> Dict[str, Any]:
        if isinstance(node, VFInst):
            inst = {
                "type": "inst",
                "op": str(node.name),
                "src": [self._lower_value_id(values[value_id]) for value_id in node.src],
                "dst": [self._lower_value_id(values[value_id]) for value_id in node.dst],
            }
            if node.form:
                inst["form"] = str(node.form)
            return inst
        if isinstance(node, VFLoop):
            loop = {
                "type": "loop",
                "iters": node.count,
                "unroll": node.unroll,
                "body": self._lower_body(node.body, values),
            }
            if node.loop_id:
                loop["name"] = str(node.loop_id)
            return loop
        if isinstance(node, Membar):
            return {
                "type": "membar",
                "barrier": str(node.type),
            }
        raise TypeError(f"Unsupported VFInfo node: {type(node).__name__}")

    def _lower_value_id(self, value: ValueInfo) -> str:
        key = str(value.value_id)
        if value.storage == "Register":
            if key not in self._register_names:
                if key[:1].lower() == "v":
                    self._register_names[key] = key
                else:
                    index = len(self._register_names)
                    candidate = f"V{index}"
                    while candidate in self._reserved_register_names:
                        index += 1
                        candidate = f"V{index}"
                    self._register_names[key] = candidate
                    self._reserved_register_names.add(candidate)
            return self._register_names[key]
        if value.storage == "UB":
            if key not in self._ub_names:
                if key.lower().startswith("mem"):
                    self._ub_names[key] = key
                else:
                    index = len(self._ub_names)
                    candidate = f"mem{index}"
                    while candidate in self._reserved_ub_names:
                        index += 1
                        candidate = f"mem{index}"
                    self._ub_names[key] = candidate
                    self._reserved_ub_names.add(candidate)
            return self._ub_names[key]
        if value.storage == "Scalar":
            return key
        raise ValueError(f"Unsupported value storage: {value.storage}")
