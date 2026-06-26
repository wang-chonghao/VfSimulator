from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from api.vf_costmodel import Membar, MemInfo, VFInfo, VFInst, VFLoop, VFNode


@dataclass
class VFInfoLowerer:
    """
    Lower public VFInfo objects into the simulator's current program payload.

    Public API operands carry explicit locations, so user-visible names can be
    arbitrary. The existing core still recognizes operands by internal prefixes,
    so this adapter maps:
    - Register operands -> V0, V1, ...
    - UB operands       -> mem0, mem1, ...
    """

    _register_names: Dict[str, str] = field(default_factory=dict)
    _ub_names: Dict[str, str] = field(default_factory=dict)

    def lower(self, vf_info: VFInfo, dtype: str = "fp32") -> Dict[str, Any]:
        return {
            "dtype": dtype,
            "params": {},
            "program": self._lower_body(vf_info.context),
            "api_symbol_map": {
                "Register": dict(self._register_names),
                "UB": dict(self._ub_names),
            },
        }

    def _lower_body(self, body: List[VFNode]) -> List[Dict[str, Any]]:
        return [self._lower_node(node) for node in body]

    def _lower_node(self, node: VFNode) -> Dict[str, Any]:
        if isinstance(node, VFInst):
            inst = {
                "type": "inst",
                "op": str(node.name),
                "src": [self._lower_operand(operand) for operand in node.src],
                "dst": [self._lower_operand(operand) for operand in node.dst],
            }
            if node.form:
                inst["form"] = str(node.form)
            return inst
        if isinstance(node, VFLoop):
            return {
                "type": "loop",
                "iters": int(node.count),
                "unroll": int(node.unroll),
                "body": self._lower_body(node.body),
            }
        if isinstance(node, Membar):
            return {
                "type": "membar",
                "barrier": str(node.type),
            }
        raise TypeError(f"Unsupported VFInfo node: {type(node).__name__}")

    def _lower_operand(self, operand: MemInfo) -> str:
        key = str(operand.name)
        if operand.location == "Register":
            if key not in self._register_names:
                self._register_names[key] = f"V{len(self._register_names)}"
            return self._register_names[key]
        if operand.location == "UB":
            if key not in self._ub_names:
                self._ub_names[key] = f"mem{len(self._ub_names)}"
            return self._ub_names[key]
        raise ValueError(f"Unsupported operand location: {operand.location}")
