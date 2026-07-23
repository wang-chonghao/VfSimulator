#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from core.value_storage import ValueStorageLookup


Version = Tuple[str, int]


def is_vreg(name: Any, value_storage: ValueStorageLookup | None = None) -> bool:
    return (value_storage or ValueStorageLookup()).is_register(name)


def _as_list(x: Any) -> List[Any]:
    if isinstance(x, list):
        return list(x)
    if x is None:
        return []
    return [x]


def _vreg_sort_key(name: str) -> Tuple[int, str]:
    suffix = name[1:] if len(name) > 1 else ""
    return (int(suffix) if suffix.isdigit() else 10**9, name)


def _next_fresh_vreg(slot_pool: List[str], value_storage: ValueStorageLookup | None = None) -> str:
    used = {str(x) for x in slot_pool}
    max_idx = -1
    for name in used:
        if name[:1].lower() == "v" and name[1:].isdigit():
            max_idx = max(max_idx, int(name[1:]))
    cand = max_idx + 1
    while True:
        name = f"V{cand}"
        if name not in used:
            return name
        cand += 1


def _analyze_versions(body: List[Dict[str, Any]], value_storage: ValueStorageLookup) -> Tuple[Dict[int, List[Optional[Version]]], Dict[int, List[Optional[Version]]], Dict[Version, int]]:
    current_version_by_vreg: Dict[str, Version] = {}
    version_counter: Dict[str, int] = {}
    src_versions_by_inst: Dict[int, List[Optional[Version]]] = {}
    dst_versions_by_inst: Dict[int, List[Optional[Version]]] = {}
    last_use: Dict[Version, int] = {}

    for idx, inst in enumerate(body):
        srcs = _as_list(inst.get("src", []))
        dsts = _as_list(inst.get("dst", []))

        src_versions: List[Optional[Version]] = []
        for src in srcs:
            if is_vreg(src, value_storage):
                ver = current_version_by_vreg.get(str(src))
                src_versions.append(ver)
                if ver is not None:
                    last_use[ver] = idx
            else:
                src_versions.append(None)
        src_versions_by_inst[idx] = src_versions

        dst_versions: List[Optional[Version]] = []
        for dst in dsts:
            if is_vreg(dst, value_storage):
                key = str(dst)
                version_counter[key] = int(version_counter.get(key, 0)) + 1
                ver = (key, int(version_counter[key]))
                current_version_by_vreg[key] = ver
                dst_versions.append(ver)
            else:
                dst_versions.append(None)
        dst_versions_by_inst[idx] = dst_versions

    return src_versions_by_inst, dst_versions_by_inst, last_use


def _normalize_single_level_loop_body(body: List[Dict[str, Any]], value_storage: ValueStorageLookup) -> Tuple[List[Dict[str, Any]], int]:
    """
    Reassign dst vregs based on future src liveness, independent of source-code
    naming style.

    Rule:
    - for each dst vreg, prefer a previously used vreg slot
    - but that slot's current value must not appear as a later src
    - if no previous slot satisfies this, allocate a fresh slot

    Conservative scope:
    - flat loop body only
    - single-dst instructions only
    - VST excluded (no vreg dst)
    """
    src_versions_by_inst, dst_versions_by_inst, last_use = _analyze_versions(body, value_storage)

    current_slot_by_vreg: Dict[str, str] = {}
    slot_of_version: Dict[Version, str] = {}
    slot_occupant: Dict[str, Optional[Version]] = {}
    slot_pool: List[str] = []
    changed = 0

    for idx, inst in enumerate(body):
        srcs = _as_list(inst.get("src", []))
        dsts = _as_list(inst.get("dst", []))
        src_versions = src_versions_by_inst.get(idx, [])
        dst_versions = dst_versions_by_inst.get(idx, [])

        new_srcs = list(srcs)
        src_slots_in_use: List[str] = []
        for pos, src in enumerate(srcs):
            if not is_vreg(src, value_storage):
                continue
            ver = src_versions[pos] if pos < len(src_versions) else None
            if ver is None:
                slot = current_slot_by_vreg.get(str(src), str(src))
            else:
                slot = slot_of_version.get(ver, current_slot_by_vreg.get(ver[0], ver[0]))
            new_srcs[pos] = slot
            src_slots_in_use.append(slot)

        new_dsts = list(dsts)
        if len(dsts) == 1 and is_vreg(dsts[0], value_storage):
            dst_name = str(dsts[0])
            dst_ver = dst_versions[0] if dst_versions else None
            if dst_ver is not None:
                candidate_slots: List[str] = []
                # Reuse slots whose current occupant has no later src use.
                for slot in slot_pool:
                    occ = slot_occupant.get(slot)
                    if occ is None or int(last_use.get(occ, -1)) < idx:
                        candidate_slots.append(slot)
                # Also allow reusing a src slot whose current value dies at this instruction.
                for pos, ver in enumerate(src_versions):
                    if ver is None:
                        continue
                    if int(last_use.get(ver, -1)) == idx:
                        slot = new_srcs[pos]
                        if slot not in candidate_slots:
                            candidate_slots.append(slot)

                chosen_slot: Optional[str] = None
                # Prefer in-place reuse when a dying unary src exists.
                if len(new_srcs) == 1 and candidate_slots and new_srcs[0] in candidate_slots:
                    chosen_slot = new_srcs[0]
                elif candidate_slots:
                    chosen_slot = sorted(candidate_slots, key=_vreg_sort_key)[0]
                else:
                    if dst_name not in slot_pool:
                        chosen_slot = dst_name
                        slot_pool.append(chosen_slot)
                    else:
                        chosen_slot = _next_fresh_vreg(slot_pool, value_storage)
                        slot_pool.append(chosen_slot)

                if chosen_slot not in slot_pool:
                    slot_pool.append(chosen_slot)
                slot_of_version[dst_ver] = chosen_slot
                current_slot_by_vreg[dst_name] = chosen_slot
                slot_occupant[chosen_slot] = dst_ver
                new_dsts[0] = chosen_slot

        if new_srcs != srcs:
            inst["src"] = new_srcs
            changed += 1
        if new_dsts != dsts:
            inst["dst"] = new_dsts
            changed += 1

    return body, changed


def _normalize_node(node: Any, value_storage: ValueStorageLookup) -> Tuple[Any, int]:
    if isinstance(node, list):
        out: List[Any] = []
        total_changed = 0
        for item in node:
            new_item, changed = _normalize_node(item, value_storage)
            out.append(new_item)
            total_changed += changed
        return out, total_changed

    if not isinstance(node, dict):
        return node, 0

    out = dict(node)
    body = out.get("body")
    if not isinstance(body, list):
        return out, 0

    if out.get("type") == "loop" and all(isinstance(x, dict) and x.get("type") == "inst" for x in body):
        body_copy = [dict(x) for x in body]
        new_body, changed = _normalize_single_level_loop_body(body_copy, value_storage)
        out["body"] = new_body
        return out, changed

    new_body, changed = _normalize_node(body, value_storage)
    out["body"] = new_body
    return out, changed


def normalize_program_vreg_live_ranges(program: List[Dict[str, Any]], values: Dict[str, Any] | None = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    program_copy = deepcopy(program)
    new_program, changed = _normalize_node(program_copy, ValueStorageLookup(values))
    stats = {
        "enabled": True,
        "changed_fields": int(changed),
    }
    return new_program, stats
