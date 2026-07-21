#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

from vfsimulator.core.program_analysis import ProgramAnalyzer


def _rename_unroll_lane(values: Any, lane: int) -> List[Any]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    return [f"{value}_lane{lane}" if isinstance(value, str) else value for value in values]


def _expand_body(
    body: List[Dict[str, Any]],
    unroll: int,
) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    for inst in body:
        for lane in range(unroll):
            clone = deepcopy(inst)
            clone["src"] = _rename_unroll_lane(clone.get("src", []), lane)
            clone["dst"] = _rename_unroll_lane(clone.get("dst", []), lane)
            expanded.append(clone)
    return expanded


def canonicalize_single_super_iteration_loops(
    program: List[Dict[str, Any]],
    params: Dict[str, Any] | None = None,
    *,
    pdb: Any = None,
    dtype: str = "fp32",
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Expand innermost loops whose unroll leaves one super-iteration."""

    analyzer = ProgramAnalyzer(dict(params or {}))
    stats = {"expanded_loops": 0, "expanded_instructions": 0}

    def rewrite(nodes: Any) -> Any:
        if not isinstance(nodes, list):
            return deepcopy(nodes)

        out: List[Any] = []
        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                out.append(deepcopy(raw_node))
                continue

            node = deepcopy(raw_node)
            if node.get("type") != "loop":
                out.append(node)
                continue

            body = node.get("body", [])
            if not isinstance(body, list):
                out.append(node)
                continue

            is_innermost = not any(
                isinstance(child, dict) and child.get("type") == "loop"
                for child in body
            )
            body_is_instructions = all(
                isinstance(child, dict) and child.get("type") == "inst"
                for child in body
            )
            iters = analyzer.resolve_bound(node.get("iters", 1))
            unroll = analyzer.resolve_unroll_value(node.get("unroll", 1))

            should_expand = iters == 1 or (unroll > 1 and iters == unroll)
            if is_innermost and body_is_instructions and should_expand:
                expanded = _expand_body(body, unroll)
                stats["expanded_loops"] += 1
                stats["expanded_instructions"] += len(expanded)
                out.extend(expanded)
                continue

            node["body"] = rewrite(body)
            out.append(node)
        return out

    return rewrite(program), stats
