#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

from core.value_storage import ValueStorageLookup


def is_vreg(name: Any, value_storage: ValueStorageLookup | None = None) -> bool:
    return (value_storage or ValueStorageLookup()).is_register(name)


def materialize_dynamic_insts(ifu) -> List[Dict[str, Any]]:
    insts: List[Dict[str, Any]] = []
    while not ifu.done():
        inst = ifu.next_inst()
        if inst is None:
            break
        if "inst_id" not in inst and "id" in inst:
            inst["inst_id"] = inst["id"]
        insts.append(inst)
    return insts


def annotate_src_last_use(insts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    version_counter: Dict[str, int] = defaultdict(int)
    current_version: Dict[str, Tuple[str, int]] = {}
    version_uses: Dict[Tuple[str, int], List[Tuple[int, int]]] = defaultdict(list)
    src_versions_by_inst: Dict[int, List[Tuple[str, int] | None]] = {}

    for inst_idx, inst in enumerate(insts):
        srcs = inst.get("src", [])
        dsts = inst.get("dst", [])
        if isinstance(srcs, str):
            srcs = [srcs]
        if isinstance(dsts, str):
            dsts = [dsts]

        src_versions: List[Tuple[str, int] | None] = []
        for src_pos, src in enumerate(srcs):
            if not is_vreg(src):
                src_versions.append(None)
                continue
            ver = current_version.get(src)
            src_versions.append(ver)
            if ver is not None:
                version_uses[ver].append((inst_idx, src_pos))
        src_versions_by_inst[inst_idx] = src_versions

        for dst in dsts:
            if not is_vreg(dst):
                continue
            version_counter[dst] += 1
            current_version[dst] = (dst, version_counter[dst])

    last_use_points = {uses[-1] for uses in version_uses.values() if uses}
    total_use_count = {version: len(uses) for version, uses in version_uses.items()}

    for inst_idx, inst in enumerate(insts):
        srcs = inst.get("src", [])
        if isinstance(srcs, str):
            srcs = [srcs]
        flags = []
        use_counts = []
        for src_pos, src in enumerate(srcs):
            if not is_vreg(src):
                flags.append(False)
                use_counts.append(0)
                continue
            flags.append((inst_idx, src_pos) in last_use_points)
            ver = src_versions_by_inst.get(inst_idx, [None] * len(srcs))[src_pos]
            use_counts.append(total_use_count.get(ver, 0))
        inst["src_last_use_flags"] = flags
        inst["src_total_use_counts"] = use_counts

    rewrite_soon_by_inst: Dict[int, List[bool]] = {}
    next_write_idx: Dict[str, int] = {}
    for inst_idx in range(len(insts) - 1, -1, -1):
        inst = insts[inst_idx]
        srcs = inst.get("src", [])
        dsts = inst.get("dst", [])
        if isinstance(srcs, str):
            srcs = [srcs]
        if isinstance(dsts, str):
            dsts = [dsts]

        flags: List[bool] = []
        for src in srcs:
            if not is_vreg(src):
                flags.append(False)
                continue
            nxt = next_write_idx.get(src)
            flags.append(nxt is not None and (nxt - inst_idx) <= 2)
        rewrite_soon_by_inst[inst_idx] = flags

        for dst in dsts:
            if is_vreg(dst):
                next_write_idx[dst] = inst_idx

    for inst_idx, inst in enumerate(insts):
        inst["src_rewrite_soon_flags"] = rewrite_soon_by_inst.get(inst_idx, [])
    return insts
