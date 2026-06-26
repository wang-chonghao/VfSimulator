from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, TypeAlias


ProgramNode: TypeAlias = "VfSimInst | VfSimLoop | VfSimMembar"


def _validate_str_list(values: List[str], field_name: str) -> None:
    if not isinstance(values, list):
        raise TypeError(f"{field_name} must be a list")
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} entries must be non-empty strings")


@dataclass
class VfSimInst:
    op: str
    src: List[str] = field(default_factory=list)
    dst: List[str] = field(default_factory=list)
    form: str | None = None
    config: Dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.op, str) or not self.op:
            raise ValueError("VfSimInst.op must be a non-empty string")
        _validate_str_list(self.src, "VfSimInst.src")
        _validate_str_list(self.dst, "VfSimInst.dst")
        if self.form is not None and (not isinstance(self.form, str) or not self.form):
            raise ValueError("VfSimInst.form must be a non-empty string when provided")
        if self.config is not None and not isinstance(self.config, dict):
            raise TypeError("VfSimInst.config must be a dict when provided")

    def to_trace_node(self) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "type": "inst",
            "op": self.op,
            "src": list(self.src),
            "dst": list(self.dst),
        }
        if self.form:
            node["form"] = self.form
        if self.config:
            node["config"] = dict(self.config)
        return node


@dataclass
class VfSimMembar:
    barrier: str = "VST_VLD"

    def __post_init__(self) -> None:
        if not isinstance(self.barrier, str) or not self.barrier:
            raise ValueError("VfSimMembar.barrier must be a non-empty string")

    def to_trace_node(self) -> Dict[str, Any]:
        return {
            "type": "membar",
            "barrier": self.barrier,
        }


@dataclass
class VfSimLoop:
    count: int | str
    body: List[ProgramNode]
    name: str | None = None
    unroll: int | str = 1

    def __post_init__(self) -> None:
        if not isinstance(self.count, (int, str)) or isinstance(self.count, bool):
            raise TypeError("VfSimLoop.count must be an int or parameter name")
        if isinstance(self.count, int) and self.count < 1:
            raise ValueError("VfSimLoop.count must be >= 1")
        if isinstance(self.count, str) and not self.count:
            raise ValueError("VfSimLoop.count parameter name must be non-empty")

        if not isinstance(self.unroll, (int, str)) or isinstance(self.unroll, bool):
            raise TypeError("VfSimLoop.unroll must be an int or parameter name")
        if isinstance(self.unroll, int) and self.unroll < 1:
            raise ValueError("VfSimLoop.unroll must be >= 1")
        if isinstance(self.unroll, str) and not self.unroll:
            raise ValueError("VfSimLoop.unroll parameter name must be non-empty")

        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError("VfSimLoop.name must be a non-empty string when provided")
        if not isinstance(self.body, list):
            raise TypeError("VfSimLoop.body must be a list")
        for node in self.body:
            if not isinstance(node, (VfSimInst, VfSimLoop, VfSimMembar)):
                raise TypeError(f"Unsupported VfSimLoop body node: {type(node).__name__}")

    def to_trace_node(self) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "type": "loop",
            "iters": self.count,
            "unroll": self.unroll,
            "body": [to_trace_node(child) for child in self.body],
        }
        if self.name is not None:
            node["name"] = self.name
        return node


@dataclass
class VfSimProgram:
    body: List[ProgramNode]
    dtype: str = "fp32"
    params: Dict[str, Any] | None = None
    config: Dict[str, Any] | None = None
    uarch: Dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dtype, str) or not self.dtype:
            raise ValueError("VfSimProgram.dtype must be a non-empty string")
        if not isinstance(self.body, list):
            raise TypeError("VfSimProgram.body must be a list")
        for node in self.body:
            if not isinstance(node, (VfSimInst, VfSimLoop, VfSimMembar)):
                raise TypeError(f"Unsupported VfSimProgram body node: {type(node).__name__}")
        if self.params is not None and not isinstance(self.params, dict):
            raise TypeError("VfSimProgram.params must be a dict when provided")
        if self.config is not None and not isinstance(self.config, dict):
            raise TypeError("VfSimProgram.config must be a dict when provided")
        if self.uarch is not None and not isinstance(self.uarch, dict):
            raise TypeError("VfSimProgram.uarch must be a dict when provided")

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "dtype": self.dtype,
            "params": dict(self.params or {}),
            "program": [to_trace_node(node) for node in self.body],
        }
        if self.config:
            payload["config"] = dict(self.config)
        if self.uarch:
            payload["uarch"] = dict(self.uarch)
        return payload


def to_trace_node(node: ProgramNode) -> Dict[str, Any]:
    if isinstance(node, (VfSimInst, VfSimLoop, VfSimMembar)):
        return node.to_trace_node()
    raise TypeError(f"Unsupported VfSimProgram node: {type(node).__name__}")


def coerce_trace_program(program: Any) -> List[Dict[str, Any]]:
    """
    Convert supported program inputs to the legacy trace-program shape.

    The core analyzer/normalizer/Flattener stack historically consumes a list
    of trace dict nodes. This helper keeps JSON traces unchanged while allowing
    the new Program IR to enter the same path.
    """
    if isinstance(program, VfSimProgram):
        return [to_trace_node(node) for node in program.body]
    if isinstance(program, list):
        if all(isinstance(node, dict) for node in program):
            return program
        return [to_trace_node(node) for node in program]
    raise TypeError(f"Unsupported program input: {type(program).__name__}")
