#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class MissingIsaConfigError(KeyError):
    pass


@dataclass(frozen=True)
class OpTraits:
    op_class: str

    @property
    def is_load(self) -> bool:
        return self.op_class == "LOAD"

    @property
    def is_store(self) -> bool:
        return self.op_class == "STORE"

    @property
    def is_compute(self) -> bool:
        return self.op_class == "COMPUTE"


def canonical_op(op: Any) -> str:
    return str(op or "").upper()


def _read_isa_entry(pdb: Any, op: str, dtype: str) -> dict[str, Any]:
    if pdb is None:
        return {}
    lookup_ops = [canonical_op(op)]
    opu = str(op or "").upper()
    if opu not in lookup_ops:
        lookup_ops.append(opu)
    try:
        for lookup_op in lookup_ops:
            try:
                return dict(pdb.get_inst(lookup_op, dtype=dtype))
            except Exception:
                continue
    except Exception:
        pass
    return {}


def get_op_traits(op: Any, pdb: Any = None, dtype: str = "fp32") -> OpTraits:
    opu = str(op or "").upper()
    inst = _read_isa_entry(pdb, opu, dtype)

    op_class = str(
        inst.get("op_class", inst.get("class", inst.get("category", "")))
    ).upper()
    if op_class in ("LOAD", "STORE", "COMPUTE"):
        return OpTraits(op_class=op_class)

    # Compatibility with the first draft schema while configs are migrating.
    unit = str(inst.get("unit", "")).upper()
    lsu_op = str(inst.get("lsu_op", "")).upper()
    if unit == "LSU":
        if lsu_op == "LOAD":
            return OpTraits(op_class="LOAD")
        if lsu_op == "STORE":
            return OpTraits(op_class="STORE")

    if unit == "EXU":
        return OpTraits(op_class="COMPUTE")

    # Compatibility only for currently covered LSU ops.
    canon = canonical_op(opu)
    if canon == "VLDS":
        return OpTraits(op_class="LOAD")
    if canon == "VSTS":
        return OpTraits(op_class="STORE")

    if canon in ("VLD", "VST", "VSTUS", "VSTAS"):
        raise MissingIsaConfigError(
            f"Missing ISA config for LSU op={canon!r}, dtype={dtype!r}"
        )

    return OpTraits(op_class="COMPUTE")


def is_load_op(op: Any, pdb: Any = None, dtype: str = "fp32") -> bool:
    return get_op_traits(op, pdb=pdb, dtype=dtype).is_load


def get_op_class(op: Any, pdb: Any = None, dtype: str = "fp32") -> str:
    return get_op_traits(op, pdb=pdb, dtype=dtype).op_class


def is_store_op(op: Any, pdb: Any = None, dtype: str = "fp32") -> bool:
    return get_op_traits(op, pdb=pdb, dtype=dtype).is_store


def is_compute_op(op: Any, pdb: Any = None, dtype: str = "fp32") -> bool:
    return get_op_traits(op, pdb=pdb, dtype=dtype).is_compute


def uses_lsq(op: Any, pdb: Any = None, dtype: str = "fp32") -> bool:
    traits = get_op_traits(op, pdb=pdb, dtype=dtype)
    return traits.is_load or traits.is_store


def uses_shq_queue(op: Any, pdb: Any = None, dtype: str = "fp32") -> bool:
    return is_compute_op(op, pdb=pdb, dtype=dtype)


def uses_shared_shq_credit(op: Any, pdb: Any = None, dtype: str = "fp32") -> bool:
    traits = get_op_traits(op, pdb=pdb, dtype=dtype)
    return traits.is_compute or traits.is_store
