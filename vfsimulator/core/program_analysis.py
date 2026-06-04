#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List


class ProgramAnalyzer:
    def __init__(self, params: Dict[str, Any]) -> None:
        self.params = params

    @staticmethod
    def is_vreg_name(name: Any) -> bool:
        return isinstance(name, str) and name.startswith("v") and name[1:].isdigit()

    def resolve_bound(self, bound: Any) -> int:
        if isinstance(bound, int):
            return bound
        if isinstance(bound, str):
            if bound.isdigit():
                return int(bound)
            if bound in self.params:
                return int(self.params[bound])
        raise ValueError(f"Unsupported loop bound: {bound}")

    def resolve_unroll_value(self, unroll: Any) -> int:
        if isinstance(unroll, int):
            return max(1, int(unroll))
        if isinstance(unroll, str):
            if unroll.isdigit():
                return max(1, int(unroll))
            if unroll in self.params:
                return max(1, int(self.params[unroll]))
        return 1

    def iter_insts(self, node: Any):
        if isinstance(node, list):
            for x in node:
                yield from self.iter_insts(x)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "inst":
            yield node
        body = node.get("body")
        if isinstance(body, list):
            for x in body:
                yield from self.iter_insts(x)

    def collect_vreg_capacity_warnings(
        self,
        program: List[Dict[str, Any]],
        preg_num: int,
    ) -> List[Dict[str, Any]]:
        warnings: List[Dict[str, Any]] = []

        def walk(nodes: Any, path: str) -> None:
            if not isinstance(nodes, list):
                return
            loop_idx = 0
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("type") != "loop":
                    continue

                loop_idx += 1
                loop_path = f"{path}.loop{loop_idx}"
                unroll = self.resolve_unroll_value(node.get("unroll", 1))
                body = node.get("body", [])

                vregs = set()
                for inst in self.iter_insts(body):
                    for x in inst.get("src", []) or []:
                        if self.is_vreg_name(x):
                            vregs.add(x)
                    for x in inst.get("dst", []) or []:
                        if self.is_vreg_name(x):
                            vregs.add(x)

                base_vreg_namespace = len(vregs)
                expanded_vreg_namespace = base_vreg_namespace * max(1, unroll)

                if expanded_vreg_namespace > preg_num:
                    warnings.append(
                        {
                            "kind": "vreg_namespace_overflow_risk",
                            "loop_path": loop_path,
                            "preg_num": int(preg_num),
                            "base_vreg_namespace": int(base_vreg_namespace),
                            "unroll": int(unroll),
                            "expanded_vreg_namespace": int(expanded_vreg_namespace),
                            "message": (
                                "Unroll-expanded virtual-register namespace exceeds physical register count. "
                                "Prediction may be low-confidence for this case."
                            ),
                        }
                    )

                walk(body, loop_path)

        walk(program, "program")
        return warnings

    def infer_nested_bounds_from_loop(self, loop_node: Dict[str, Any]) -> List[int]:
        bounds: List[int] = []
        node = loop_node

        while isinstance(node, dict) and node.get("type") == "loop" and len(bounds) < 3:
            bounds.append(self.resolve_bound(node.get("iters")))
            body = node.get("body", [])

            next_loop = None
            if isinstance(body, list):
                for item in body:
                    if isinstance(item, dict) and item.get("type") == "loop":
                        next_loop = item
                        break

            if next_loop is None:
                break
            node = next_loop

        return bounds

    def infer_top_block_loop_bounds(
        self,
        program: List[Dict[str, Any]],
    ) -> Dict[int, List[int]]:
        result: Dict[int, List[int]] = {}
        tbid = 0

        if not isinstance(program, list):
            return {0: []}

        for node in program:
            if isinstance(node, dict) and node.get("type") == "loop":
                result[tbid] = self.infer_nested_bounds_from_loop(node)
                tbid += 1

        if not result:
            result[0] = []

        return result
