#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from core.isa_traits import get_op_class


@dataclass
class MembarState:
    stream_seq: int
    pc: int
    barrier: str
    wait_class: str
    block_class: str
    released: bool = False


class ControlUnit:
    """Control-side model for explicit vector memory barriers."""

    _SUPPORTED = {
        "VST_VLD": ("STORE", "LOAD"),
        "VLD_VST": ("LOAD", "STORE"),
    }

    def __init__(self, pdb: Any, dtype: str = "fp32") -> None:
        self.db = pdb
        self.dtype = str(dtype)
        self.barriers: List[MembarState] = []

    @staticmethod
    def normalize_barrier(raw: Any) -> str:
        text = str(raw or "VST_VLD").strip().upper()
        if "." in text:
            text = text.rsplit(".", 1)[-1]
        return text

    def accept_membar(self, node: Dict[str, Any]) -> None:
        barrier = self.normalize_barrier(node.get("barrier", node.get("type", "VST_VLD")))
        if barrier not in self._SUPPORTED:
            if hasattr(self.db, "record_warning"):
                self.db.record_warning(
                    "unsupported_membar_type",
                    barrier=barrier,
                    pc=int(node.get("pc", -1)),
                    stream_seq=int(node.get("stream_seq", -1)),
                )
            return
        wait_class, block_class = self._SUPPORTED[barrier]
        self.barriers.append(
            MembarState(
                stream_seq=int(node.get("stream_seq", -1)),
                pc=int(node.get("pc", -1)),
                barrier=barrier,
                wait_class=wait_class,
                block_class=block_class,
            )
        )

    def update(self, has_pending_prior: Callable[[int, str], bool]) -> None:
        for barrier in self.barriers:
            if barrier.released:
                continue
            barrier.released = not has_pending_prior(
                int(barrier.stream_seq),
                str(barrier.wait_class),
            )

    def blocks(self, inst: Dict[str, Any]) -> bool:
        stream_seq = int(inst.get("stream_seq", -1))
        if stream_seq < 0:
            return False
        op_class = get_op_class(
            inst.get("op", ""),
            self.db,
            str(inst.get("form", "") or self.dtype),
        )
        for barrier in self.barriers:
            if barrier.released:
                continue
            if stream_seq <= int(barrier.stream_seq):
                continue
            if op_class == barrier.block_class:
                return True
        return False
