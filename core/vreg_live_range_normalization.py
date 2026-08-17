#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Set, Tuple

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


def _ensure_register_value(values: Dict[str, Any], slot: str, source_value_id: str) -> None:
    if slot in values:
        return

    source = values.get(source_value_id)
    if isinstance(source, dict):
        value = dict(source)
    else:
        value = {}
        for attr in ("dtype", "shape"):
            if hasattr(source, attr):
                value[attr] = getattr(source, attr)
    value["value_id"] = slot
    value["storage"] = "Register"
    values[slot] = value


def _normalize_single_level_loop_body(
    body: List[Dict[str, Any]],
    value_storage: ValueStorageLookup,
    values: Dict[str, Any],
    has_back_edge: bool = False,
) -> Tuple[List[Dict[str, Any]], int, Dict[str, str]]:
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
    first_access, written = _collect_loop_vreg_accesses(body, value_storage, {})
    loop_carried_vregs = {
        name
        for name, access in first_access.items()
        if has_back_edge and access == "read" and name in written
    }

    current_slot_by_vreg: Dict[str, str] = {}
    slot_of_version: Dict[Version, str] = {}
    slot_occupant: Dict[str, Optional[Version]] = {}
    slot_pool: List[str] = sorted(loop_carried_vregs, key=_vreg_sort_key)
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
                    if slot in loop_carried_vregs:
                        continue
                    occ = slot_occupant.get(slot)
                    if occ is None or int(last_use.get(occ, -1)) < idx:
                        candidate_slots.append(slot)
                # Also allow reusing a src slot whose current value dies at this instruction.
                for pos, ver in enumerate(src_versions):
                    if ver is None:
                        continue
                    if int(last_use.get(ver, -1)) == idx:
                        slot = new_srcs[pos]
                        if slot in loop_carried_vregs:
                            continue
                        if slot not in candidate_slots:
                            candidate_slots.append(slot)

                chosen_slot: Optional[str] = None
                if dst_name in loop_carried_vregs:
                    chosen_slot = dst_name
                # Prefer in-place reuse when a dying unary src exists.
                elif len(new_srcs) == 1 and candidate_slots and new_srcs[0] in candidate_slots:
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
                _ensure_register_value(values, chosen_slot, dst_name)
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

    exit_aliases = {
        logical: slot
        for logical, slot in current_slot_by_vreg.items()
        if logical != slot
    }
    return body, changed, exit_aliases


def _apply_src_aliases(
    node: Any,
    aliases: Dict[str, str],
    value_storage: ValueStorageLookup,
    params: Dict[str, Any],
) -> Tuple[Any, int, Set[str]]:
    if isinstance(node, list):
        out: List[Any] = []
        changed = 0
        killed_aliases: Set[str] = set()
        active_aliases = dict(aliases)
        for item in node:
            new_item, item_changed, item_kills = _apply_src_aliases(
                item,
                active_aliases,
                value_storage,
                params,
            )
            out.append(new_item)
            changed += item_changed
            killed_aliases.update(item_kills)
            for logical_vreg in item_kills:
                active_aliases.pop(logical_vreg, None)
        return out, changed, killed_aliases
    if not isinstance(node, dict):
        return node, 0, set()
    out = dict(node)
    changed = 0
    if out.get("type") == "inst":
        srcs = _as_list(out.get("src", []))
        new_srcs = [
            aliases.get(str(src), src) if is_vreg(src, value_storage) else src
            for src in srcs
        ]
        if new_srcs != srcs:
            out["src"] = new_srcs
            changed += 1
        killed_aliases = {
            str(dst)
            for dst in _as_list(out.get("dst", []))
            if is_vreg(dst, value_storage)
        }
        return out, changed, killed_aliases
    body = out.get("body")
    if isinstance(body, list):
        if out.get("type") == "loop":
            if _resolve_loop_iters(out.get("iters", 1), params) <= 0:
                return out, 0, set()

            first_access, written = _collect_loop_vreg_accesses(
                body,
                value_storage,
                params,
            )
            carried_aliases = {
                logical: target
                for logical, target in aliases.items()
                if first_access.get(logical) == "read" and logical in written
            }
            if carried_aliases:
                body, rename_changed = _rename_vregs(
                    body,
                    carried_aliases,
                    value_storage,
                )
                changed += rename_changed

            remaining_aliases = {
                logical: target
                for logical, target in aliases.items()
                if logical not in carried_aliases
            }
            new_body, body_changed, _ = _apply_src_aliases(
                body,
                remaining_aliases,
                value_storage,
                params,
            )
            if body_changed or new_body != out.get("body"):
                out["body"] = new_body
                changed += body_changed
            killed_aliases = written.difference(carried_aliases)
            return out, changed, killed_aliases

        new_body, body_changed, killed_aliases = _apply_src_aliases(
            body,
            aliases,
            value_storage,
            params,
        )
        if body_changed:
            out["body"] = new_body
            changed += body_changed
        return out, changed, killed_aliases
    return out, changed, set()


def _resolve_loop_iters(iters: Any, params: Dict[str, Any]) -> int:
    if iters is None or isinstance(iters, bool):
        return 1
    if isinstance(iters, (int, float)):
        return max(0, int(iters))
    if isinstance(iters, str):
        value = iters.strip()
        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return max(0, int(value))
        if value in params:
            try:
                return max(0, int(params[value]))
            except (TypeError, ValueError):
                return 1
    return 1


def _collect_loop_vreg_accesses(
    node: Any,
    value_storage: ValueStorageLookup,
    params: Dict[str, Any],
) -> Tuple[Dict[str, str], Set[str]]:
    first_access: Dict[str, str] = {}
    written: Set[str] = set()

    def visit(current: Any) -> None:
        if isinstance(current, list):
            for item in current:
                visit(item)
            return
        if not isinstance(current, dict):
            return
        if current.get("type") == "loop" and _resolve_loop_iters(
            current.get("iters", 1),
            params,
        ) <= 0:
            return
        if current.get("type") == "inst":
            for src in _as_list(current.get("src", [])):
                if is_vreg(src, value_storage):
                    first_access.setdefault(str(src), "read")
            for dst in _as_list(current.get("dst", [])):
                if is_vreg(dst, value_storage):
                    name = str(dst)
                    first_access.setdefault(name, "write")
                    written.add(name)
            return
        visit(current.get("body"))

    visit(node)
    return first_access, written


def _rename_vregs(
    node: Any,
    aliases: Dict[str, str],
    value_storage: ValueStorageLookup,
) -> Tuple[Any, int]:
    if isinstance(node, list):
        out: List[Any] = []
        changed = 0
        for item in node:
            new_item, item_changed = _rename_vregs(item, aliases, value_storage)
            out.append(new_item)
            changed += item_changed
        return out, changed
    if not isinstance(node, dict):
        return node, 0

    out = dict(node)
    changed = 0
    if out.get("type") == "inst":
        for field in ("src", "dst"):
            operands = _as_list(out.get(field, []))
            new_operands = [
                aliases.get(str(operand), operand)
                if is_vreg(operand, value_storage)
                else operand
                for operand in operands
            ]
            if new_operands != operands:
                out[field] = new_operands
                changed += 1
        return out, changed

    body = out.get("body")
    if isinstance(body, list):
        new_body, body_changed = _rename_vregs(body, aliases, value_storage)
        if body_changed:
            out["body"] = new_body
            changed += body_changed
    return out, changed


def _normalize_node(
    node: Any,
    value_storage: ValueStorageLookup,
    values: Dict[str, Any],
    params: Dict[str, Any],
) -> Tuple[Any, int, Dict[str, str]]:
    if isinstance(node, list):
        out: List[Any] = []
        total_changed = 0
        active_aliases: Dict[str, str] = {}
        for item in node:
            item_for_normalize, alias_changed, killed_aliases = _apply_src_aliases(
                item,
                active_aliases,
                value_storage,
                params,
            )
            new_item, changed, child_aliases = _normalize_node(
                item_for_normalize,
                value_storage,
                values,
                params,
            )
            out.append(new_item)
            total_changed += alias_changed + changed
            for logical_vreg in killed_aliases:
                active_aliases.pop(logical_vreg, None)
            active_aliases.update(child_aliases)
        return out, total_changed, active_aliases

    if not isinstance(node, dict):
        return node, 0, {}

    out = dict(node)
    body = out.get("body")
    if not isinstance(body, list):
        return out, 0, {}

    if out.get("type") == "loop" and _resolve_loop_iters(
        out.get("iters", 1),
        params,
    ) <= 0:
        return out, 0, {}

    if out.get("type") == "loop" and all(isinstance(x, dict) and x.get("type") == "inst" for x in body):
        body_copy = [dict(x) for x in body]
        new_body, changed, aliases = _normalize_single_level_loop_body(
            body_copy,
            value_storage,
            values,
            has_back_edge=_resolve_loop_iters(out.get("iters", 1), params) > 1,
        )
        out["body"] = new_body
        return out, changed, aliases

    new_body, changed, aliases = _normalize_node(body, value_storage, values, params)
    out["body"] = new_body
    return out, changed, aliases


def normalize_program_vreg_live_ranges(
    program: List[Dict[str, Any]],
    values: Dict[str, Any] | None = None,
    params: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    program_copy = deepcopy(program)
    new_values = deepcopy(values or {})
    new_program, changed, _ = _normalize_node(
        program_copy,
        ValueStorageLookup(new_values),
        new_values,
        dict(params or {}),
    )
    stats = {
        "enabled": True,
        "changed_fields": int(changed),
    }
    return new_program, new_values, stats
