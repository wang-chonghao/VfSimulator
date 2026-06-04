from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, TypeAlias


OperandLocation: TypeAlias = Literal["Register", "UB"]
VFNode: TypeAlias = "VFLoop | VFInst | Membar"


@dataclass
class MemInfo:
    name: str
    location: OperandLocation = "Register"


@dataclass
class VFInst:
    name: str
    src: list[MemInfo]
    dst: list[MemInfo]


@dataclass
class Membar:
    type: str = "VST_VLD"


@dataclass
class VFLoop:
    count: int
    unroll: int
    body: list[VFNode]


@dataclass
class VFInfo:
    context: list[VFNode]


class VfCostModel(ABC):
    @abstractmethod
    def predict_vf_cycles(self, vf_info: VFInfo) -> int:
        pass
