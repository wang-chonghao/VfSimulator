from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from api.vf_info import (
    Membar,
    ValueInfo,
    VFInfo,
    VFInst,
    VFLoop,
    VFNode,
    canonicalize_vf_info,
)


class JsonVfInfoAdapter:
    """Translate legacy or typed JSON payloads into canonical VfInfo."""

    @staticmethod
    def load(path: str | Path) -> VFInfo:
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return JsonVfInfoAdapter.from_payload(payload)

    @staticmethod
    def from_payload(payload: Dict[str, Any]) -> VFInfo:
        if not isinstance(payload, dict):
            raise RuntimeError("JSON trace root must be an object")
        program = payload.get("program")
        if not isinstance(program, list):
            raise RuntimeError("trace.json key 'program' must be an array")

        values: dict[str, ValueInfo] = {}
        raw_values = payload.get("values", {}) or {}
        if isinstance(raw_values, list):
            raw_values = {
                str(value.get("value_id")): value
                for value in raw_values
                if isinstance(value, dict) and value.get("value_id") is not None
            }
        if not isinstance(raw_values, dict):
            raise RuntimeError("trace.json key 'values' must be an object or array")
        for value_id, raw in raw_values.items():
            if not isinstance(raw, dict):
                raise RuntimeError(f"value {value_id!r} must be an object")
            values[str(value_id)] = ValueInfo(
                str(raw.get("value_id", value_id)),
                raw.get("storage", raw.get("location", "Register")),
                raw.get("dtype"),
                raw.get("shape", []),
            )

        def parse_nodes(raw_nodes: list[Any]) -> list[VFNode]:
            nodes: list[VFNode] = []
            for raw in raw_nodes:
                if not isinstance(raw, dict):
                    raise RuntimeError("program node must be an object")
                node_type = raw.get("type")
                if node_type == "inst":
                    nodes.append(
                        VFInst(
                            name=str(raw.get("op", "")),
                            src=[str(value) for value in raw.get("src", [])],
                            dst=[str(value) for value in raw.get("dst", [])],
                            form=str(raw["form"]) if raw.get("form") else None,
                        )
                    )
                elif node_type == "loop":
                    body = raw.get("body", [])
                    if not isinstance(body, list):
                        raise RuntimeError("loop body must be an array")
                    nodes.append(
                        VFLoop(
                            count=raw.get("iters", 1),
                            unroll=raw.get("unroll", 1),
                            body=parse_nodes(body),
                            loop_id=str(raw["name"]) if raw.get("name") else None,
                        )
                    )
                elif node_type == "membar":
                    nodes.append(Membar(str(raw.get("barrier", "VST_VLD"))))
                else:
                    raise RuntimeError(f"unsupported program node type: {node_type}")
            return nodes

        return canonicalize_vf_info(
            VFInfo(
                context=parse_nodes(program),
                values=values,
                params=dict(payload.get("params", {}) or {}),
                default_dtype=str(payload.get("dtype", "fp32")),
                uarch=dict(payload.get("uarch", {}) or {}),
            )
        )


__all__ = ["JsonVfInfoAdapter"]
