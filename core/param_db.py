#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
configs.py  (ParamDB)

Reads:
  - configs/isa.json
  - configs/uarch.json
  - configs/forwarding.json           (optional)
  - configs/InitiationInterval.json  (optional)

Also supports fallback locations:
  - ./isa.json, ./uarch.json, ./forwarding.json, ./Initiation_Interval.json
  - environment variables:
      ISA_JSON_PATH
      UARCH_JSON_PATH
      FORWARDING_JSON_PATH
      II_JSON_PATH

API:
  db = ParamDB(base_dir=...)
  u = db.get_uarch()                      -> dict
  ins = db.get_inst("VADDS","fp32")       -> dict (merged with defaults if present)
  d = db.get_defaults()                   -> dict
  f = db.get_forwarding_cycles("VADDS","VEXP","fp32")  -> int
  ii = db.get_ii("VADDS","VMULS","fp32")  -> int
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Return merged dict: a overlaid by b (b wins)."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


@dataclass
class ParamDB:
    """
    Param database for VF simulator.

    base_dir: directory that contains this file by default; we search:
      base_dir/configs/isa.json
      base_dir/configs/uarch.json
      base_dir/configs/forwarding.json
      base_dir/configs/InitiationInterval.json

    You can override by passing paths, or by setting env vars:
      ISA_JSON_PATH, UARCH_JSON_PATH, FORWARDING_JSON_PATH, II_JSON_PATH
    """
    base_dir: Optional[str] = None
    isa_path: Optional[str] = None
    uarch_path: Optional[str] = None
    forwarding_path: Optional[str] = None
    ii_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.base_dir is None:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))

        self._isa_path = self._resolve_path(
            explicit=self.isa_path,
            env_key="ISA_JSON_PATH",
            rel_candidates=[
                os.path.join("configs", "isa.json"),
                "isa.json",
            ],
        )
        self._uarch_path = self._resolve_path(
            explicit=self.uarch_path,
            env_key="UARCH_JSON_PATH",
            rel_candidates=[
                os.path.join("configs", "uarch.json"),
                "uarch.json",
            ],
        )

        # forwarding.json is optional
        self._forwarding_path = self._resolve_path_optional(
            explicit=self.forwarding_path,
            env_key="FORWARDING_JSON_PATH",
            rel_candidates=[
                os.path.join("configs", "forwarding.json"),
                "forwarding.json",
            ],
        )

        # Initiation_Interval.json is optional
        self._ii_path = self._resolve_path_optional(
            explicit=self.ii_path,
            env_key="II_JSON_PATH",
            rel_candidates=[
                os.path.join("configs", "InitiationInterval.json"),
                "Initiation_Interval.json",
            ],
        )

        self._isa: Dict[str, Any] = _read_json(self._isa_path)
        self._uarch: Dict[str, Any] = _read_json(self._uarch_path)

        self._defaults: Dict[str, Any] = self._isa.get("defaults", {}) or {}
        self._insts: Dict[str, Any] = self._isa.get("instructions", {}) or {}

        # ---------------- forwarding table (optional) ----------------
        self._fwd_default_offset: int = 3
        self._fwd_table: Dict[str, Any] = {}
        if self._forwarding_path is not None:
            try:
                fwd = _read_json(self._forwarding_path)
                if isinstance(fwd, dict):
                    # schema:
                    # {"default": 3, "forwarding": {"fp32": {"PROD": {"CONS": t}}}}
                    self._fwd_default_offset = int(fwd.get("default", 3))
                    self._fwd_table = fwd.get("forwarding", {}) or {}
            except Exception:
                self._fwd_default_offset = 3
                self._fwd_table = {}

        # ---------------- Initiation Interval table (optional) ----------------
        self._ii_default: int = 1
        self._ii_table: Dict[str, Any] = {}
        if self._ii_path is not None:
            try:
                ii_db = _read_json(self._ii_path)
                if isinstance(ii_db, dict):
                    # schema:
                    # {
                    #   "defaults": 1,
                    #   "InitiationInterval": {
                    #       "fp32": {
                    #           "PREV_OP": {"CUR_OP": 1}
                    #       }
                    #   }
                    # }
                    self._ii_default = int(ii_db.get("defaults", 1))
                    self._ii_table = ii_db.get("InitiationInterval", {}) or {}
            except Exception:
                self._ii_default = 1
                self._ii_table = {}

    def _resolve_path(self, *, explicit: Optional[str], env_key: str, rel_candidates: list[str]) -> str:
        if explicit:
            p = os.path.abspath(explicit)
            if not os.path.exists(p):
                raise FileNotFoundError(f"{env_key}: explicit path not found: {p}")
            return p

        envp = os.environ.get(env_key)
        if envp:
            p = os.path.abspath(envp)
            if not os.path.exists(p):
                raise FileNotFoundError(f"{env_key}: env path not found: {p}")
            return p

        assert self.base_dir is not None
        tried = []
        for rel in rel_candidates:
            p = os.path.abspath(os.path.join(self.base_dir, rel))
            tried.append(p)
            if os.path.exists(p):
                return p

        raise FileNotFoundError(
            f"Could not locate {env_key}. Tried:\n  " + "\n  ".join(tried)
        )

    def _resolve_path_optional(self, *, explicit: Optional[str], env_key: str, rel_candidates: list[str]) -> Optional[str]:
        if explicit:
            p = os.path.abspath(explicit)
            if not os.path.exists(p):
                raise FileNotFoundError(f"{env_key}: explicit path not found: {p}")
            return p

        envp = os.environ.get(env_key)
        if envp:
            p = os.path.abspath(envp)
            if not os.path.exists(p):
                raise FileNotFoundError(f"{env_key}: env path not found: {p}")
            return p

        assert self.base_dir is not None
        for rel in rel_candidates:
            p = os.path.abspath(os.path.join(self.base_dir, rel))
            if os.path.exists(p):
                return p
        return None

    # ---------------- public API ----------------

    def get_uarch(self) -> Dict[str, Any]:
        """Return uarch dict as-is."""
        return dict(self._uarch)

    def get_defaults(self) -> Dict[str, Any]:
        """Return ISA defaults section (may be empty)."""
        return dict(self._defaults)

    def get_inst(self, op: str, dtype: str = "fp32") -> Dict[str, Any]:
        """
        Return instruction parameters for given op and dtype.
        Result is merged with ISA defaults (defaults -> inst specific).
        """
        opu = op.upper()
        node = self._insts.get(opu, {})
        if not isinstance(node, dict) or dtype not in node:
            raise KeyError(f"Instruction not found: op={opu}, dtype={dtype}")

        inst_params = node.get(dtype, {}) or {}
        if not isinstance(inst_params, dict):
            raise TypeError(f"Bad schema for {opu}/{dtype}: expected dict, got {type(inst_params)}")

        merged = _deep_merge(self._defaults, inst_params)
        merged["op"] = opu
        merged["dtype"] = dtype
        return merged

    def has_inst(self, op: str, dtype: str = "fp32") -> bool:
        opu = op.upper()
        node = self._insts.get(opu, {})
        return isinstance(node, dict) and dtype in node

    def get_uarch_int(self, key: str, default: int) -> int:
        v = self._uarch.get(key, default)
        try:
            return int(v)
        except Exception:
            return default

    # ---------------- ISA convenience API ----------------

    def get_inst_param(self, op: str, key: str, dtype: str = "fp32", default: Any = None) -> Any:
        """Convenience accessor: read a single parameter from ISA (defaults merged)."""
        try:
            d = self.get_inst(op, dtype)
            return d.get(key, default)
        except Exception:
            return default

    # ---------------- forwarding API ----------------

    def get_forwarding_cycles(self, producer_op: str, consumer_op: str, dtype: str = "fp32") -> int:
        """
        Forwarding cycles for COMPUTE->COMPUTE dependency.

        Lookup order:
          1) forwarding.json explicit table:
             forwarding[dtype][PRODUCER][CONSUMER] -> cycles
          2) fallback: max(0, latency(PRODUCER) - default_offset)
             where default_offset is forwarding.json["default"] (default 3 if file missing)
        """
        p = producer_op.upper()
        c = consumer_op.upper()

        prod_map = None
        try:
            prod_map = (self._fwd_table or {}).get(dtype, {}).get(p, None)
        except Exception:
            prod_map = None

        if isinstance(prod_map, dict) and c in prod_map:
            try:
                return max(0, int(prod_map[c]))
            except Exception:
                pass

        lat = self.get_inst_param(p, "latency", dtype=dtype, default=0)
        try:
            lat_i = int(lat)
        except Exception:
            lat_i = 0
        return max(0, lat_i - int(self._fwd_default_offset))

    # ---------------- Initiation Interval API ----------------

    def get_ii(self, prev_op: str, cur_op: str, dtype: str = "fp32") -> int:
        """
        Initiation interval for compute issue on the same EXU.

        Semantics:
          cycle(cur_op) >= last_issue(prev_op_on_same_port) + II(prev_op, cur_op)

        Lookup order:
          1) Initiation_Interval.json explicit table:
             InitiationInterval[dtype][PREV_OP][CUR_OP] -> ii
          2) fallback: defaults value in Initiation_Interval.json (default 1 if file missing)
        """
        p = prev_op.upper()
        c = cur_op.upper()

        prev_map = None
        try:
            prev_map = (self._ii_table or {}).get(dtype, {}).get(p, None)
        except Exception:
            prev_map = None

        if isinstance(prev_map, dict) and c in prev_map:
            try:
                return max(1, int(prev_map[c]))
            except Exception:
                pass

        return max(1, int(self._ii_default))