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
  ins = db.get_inst_form("VCVT_F32_TO_F16", "f32_to_f16") -> dict
  d = db.get_defaults()                   -> dict
  f = db.get_forwarding_cycles("VADDS","VEXP","fp32")  -> int
  ii = db.get_ii("VADDS","VMULS","fp32")  -> int
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.param_compat import compatible_form, form_candidates, pair_key_candidates


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
        self._isa_schema_version: int = int(self._isa.get("schema_version", 1) or 1)
        self._warnings: Dict[tuple[Any, ...], Dict[str, Any]] = {}

        # ---------------- forwarding table (optional) ----------------
        self._fwd_default_offset: int = 3
        self._fwd_table: Dict[str, Any] = {}
        if self._forwarding_path is not None:
            try:
                fwd = _read_json(self._forwarding_path)
                if isinstance(fwd, dict):
                    # schema:
                    # {"default": 3, "forwarding": {"fp32": {"PROD": {"CONS": t}}}}
                    self._fwd_default_offset = int(fwd.get("default", fwd.get("defaults", 3)))
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

    # ---------------- warning / fallback helpers ----------------

    def _record_warning(self, kind: str, **payload: Any) -> None:
        key_items = []
        for k, v in sorted(payload.items()):
            if k in ("used_defaults", "sample_inst_ids"):
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, sort_keys=True, ensure_ascii=False)
            key_items.append((k, v))
        key = (kind, tuple(key_items))
        item = self._warnings.get(key)
        if item is None:
            item = {"kind": kind, **payload, "count": 0}
            self._warnings[key] = item
        item["count"] = int(item.get("count", 0)) + 1

    def record_warning(self, kind: str, **payload: Any) -> None:
        self._record_warning(kind, **payload)

    def get_warnings(self) -> list[Dict[str, Any]]:
        return sorted(
            (dict(item) for item in self._warnings.values()),
            key=lambda x: (str(x.get("kind", "")), str(x.get("op", "")), str(x.get("form", ""))),
        )

    @staticmethod
    def _fallback_op_class(op: str) -> str:
        opu = str(op or "").upper()
        if opu.startswith("VLD"):
            return "LOAD"
        if opu.startswith("VST"):
            return "STORE"
        return "COMPUTE"

    def _fallback_inst_form_params(
        self,
        op: str,
        form: Optional[str] = None,
        dtype: Optional[str] = None,
        *,
        kind: str,
    ) -> Dict[str, Any]:
        opu = str(op or "").upper()
        normalized_form = self._normalize_form_key(opu, form) or self._dtype_to_form(dtype)
        op_class = self._fallback_op_class(opu)
        params: Dict[str, Any] = _deep_merge(self._defaults, {})
        params.update(
            {
                "op": opu,
                "form": normalized_form,
                "dtype": str(dtype or normalized_form),
                "op_class": op_class,
                "latency": 9,
                "pipeline_startup_cost": 0,
                "pipeline_drain_cost": 0,
                "throughput": 1,
            }
        )
        if op_class == "COMPUTE":
            params.update({"EXU": "ALU", "dispatch_exu": "EXU01"})
        self._record_warning(
            kind,
            op=opu,
            form=normalized_form,
            used_defaults={
                "op_class": op_class,
                "latency": 9,
                **({"EXU": "ALU", "dispatch_exu": "EXU01"} if op_class == "COMPUTE" else {}),
            },
        )
        return params

    def get_inst(self, op: str, dtype: str = "fp32") -> Dict[str, Any]:
        """
        Return instruction parameters for given op and dtype.
        Result is merged with ISA defaults (defaults -> inst specific).
        """
        if self._is_v2_isa():
            return self.get_inst_form(op, form=dtype, dtype=dtype)

        opu = op.upper()
        node = self._insts.get(opu, {})
        if not isinstance(node, dict) or dtype not in node:
            return self._fallback_inst_form_params(
                opu,
                form=dtype,
                dtype=dtype,
                kind="unsupported_isa_op",
            )

        inst_params = node.get(dtype, {}) or {}
        if not isinstance(inst_params, dict):
            raise TypeError(f"Bad schema for {opu}/{dtype}: expected dict, got {type(inst_params)}")

        merged = _deep_merge(self._defaults, inst_params)
        merged["op"] = opu
        merged["dtype"] = dtype
        return merged

    def _is_v2_isa(self) -> bool:
        return self._isa_schema_version >= 2

    @staticmethod
    def _normalize_form_key(op: str, form: Optional[str]) -> str:
        opu = op.upper()
        if form is None or str(form) == "":
            return ""
        f = str(form)
        if "." in f or ":" in f:
            sep = "." if "." in f else ":"
            lhs, rhs = f.split(sep, 1)
            if lhs.upper() == opu:
                return rhs
        return f

    @staticmethod
    def _dtype_to_form(dtype: Optional[str]) -> str:
        return str(dtype or "fp32")

    @staticmethod
    def _legacy_vcvt_form(op: str) -> Optional[str]:
        mapping = {
            "VCVT_F32_TO_F16": "f32_to_f16",
            "VCVT_F16_TO_F32": "f16_to_f32",
            "VCVT_F32_TO_S32": "f32_to_s32",
            "VCVT_S32_TO_F32": "s32_to_f32",
        }
        return mapping.get(op.upper())

    def _select_v2_form(
        self,
        op: str,
        form: Optional[str] = None,
        dtype: Optional[str] = None,
        *,
        allow_fallback: bool = True,
    ) -> str:
        opu = op.upper()
        node = self._insts.get(opu, {})
        forms = node.get("forms", {}) if isinstance(node, dict) else {}
        if not isinstance(forms, dict):
            if allow_fallback:
                return self._normalize_form_key(opu, form) or self._dtype_to_form(dtype)
            raise KeyError(f"Instruction has no forms: op={opu}")

        candidates = []
        normalized = self._normalize_form_key(opu, form)
        if normalized:
            candidates.extend(form_candidates(normalized))
        elif dtype is not None:
            candidates.extend(form_candidates(self._dtype_to_form(dtype)))
        legacy = self._legacy_vcvt_form(opu)
        if legacy:
            candidates.append(legacy)

        for candidate in candidates:
            if candidate in forms:
                return candidate
        if allow_fallback:
            return candidates[0] if candidates else self._dtype_to_form(dtype)
        raise KeyError(f"Instruction form not found: op={opu}, form={form}, dtype={dtype}")

    def _v2_inst_form_params(
        self,
        op: str,
        form: str,
        dtype: Optional[str],
        *,
        requested_form: Optional[str] = None,
    ) -> Dict[str, Any]:
        opu = op.upper()
        node = self._insts.get(opu, {})
        if not isinstance(node, dict):
            raise KeyError(f"Instruction not found: op={opu}")
        forms = node.get("forms", {}) or {}
        inst_params = forms.get(form, {}) if isinstance(forms, dict) else {}
        if not isinstance(inst_params, dict):
            raise TypeError(f"Bad schema for {opu}.{form}: expected dict, got {type(inst_params)}")

        normalized_requested = self._normalize_form_key(opu, requested_form) or form
        op_defaults = {k: v for k, v in node.items() if k != "forms"}
        merged = _deep_merge(self._defaults, op_defaults)
        compatible = compatible_form(normalized_requested)
        if compatible and compatible != normalized_requested and compatible in forms:
            compatible_params = forms.get(compatible, {}) if isinstance(forms, dict) else {}
            if not isinstance(compatible_params, dict):
                raise TypeError(
                    f"Bad schema for {opu}.{compatible}: expected dict, got {type(compatible_params)}"
                )
            merged = _deep_merge(merged, compatible_params)
        merged = _deep_merge(merged, inst_params)
        merged["op"] = opu
        merged["form"] = normalized_requested
        merged["resolved_form"] = form
        merged["dtype"] = str(dtype or merged.get("dtype") or normalized_requested)
        if form != normalized_requested:
            self._record_warning(
                "compatible_isa_form_fallback",
                op=opu,
                requested_form=normalized_requested,
                used_form=form,
            )
        return merged

    def get_inst_form(
        self,
        op: str,
        form: Optional[str] = None,
        dtype: Optional[str] = None,
        allow_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Return instruction parameters for a concrete form.

        v2 schema stores per-op forms such as VADD.fp32 or
        VCVT_F32_TO_F16.f32_to_f16.  For v1 configs this falls back to dtype.
        """
        opu = op.upper()
        if self._is_v2_isa():
            if opu not in self._insts:
                if allow_fallback:
                    return self._fallback_inst_form_params(
                        opu,
                        form=form,
                        dtype=dtype,
                        kind="unsupported_isa_op",
                    )
                raise KeyError(f"Instruction not found: op={opu}")
            node = self._insts.get(opu, {})
            if not isinstance(node, dict):
                if allow_fallback:
                    return self._fallback_inst_form_params(
                        opu,
                        form=form,
                        dtype=dtype,
                        kind="unsupported_isa_op",
                    )
                raise KeyError(f"Instruction not found: op={opu}")
            forms = node.get("forms", {}) or {}
            if not isinstance(forms, dict):
                raise TypeError(f"Bad schema for {opu}.forms: expected dict, got {type(forms)}")
            selected = self._select_v2_form(
                opu,
                form=form,
                dtype=dtype,
                allow_fallback=allow_fallback,
            )
            if selected not in forms and allow_fallback:
                return self._fallback_inst_form_params(
                    opu,
                    form=selected,
                    dtype=dtype,
                    kind="unsupported_isa_form",
                )
            return self._v2_inst_form_params(
                opu,
                selected,
                dtype=dtype,
                requested_form=form,
            )

        legacy_dtype = str(dtype or form or "fp32")
        return self.get_inst(opu, legacy_dtype)

    def has_inst(self, op: str, dtype: str = "fp32") -> bool:
        opu = op.upper()
        node = self._insts.get(opu, {})
        if self._is_v2_isa():
            try:
                node = self._insts.get(opu, {})
                if not isinstance(node, dict):
                    return False
                forms = node.get("forms", {}) or {}
                if not isinstance(forms, dict):
                    return False
                selected = self._select_v2_form(opu, form=dtype, dtype=dtype, allow_fallback=False)
                return selected in forms
            except Exception:
                return False
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

    @staticmethod
    def _join_form_key(op: str, form: str) -> str:
        return f"{op.upper()}.{form}"

    @staticmethod
    def _split_form_key(op_or_key: str, form: Optional[str]) -> tuple[str, Optional[str]]:
        text = str(op_or_key)
        if form is not None:
            return text.upper(), str(form)
        for sep in (".", ":"):
            if sep in text:
                lhs, rhs = text.split(sep, 1)
                return lhs.upper(), rhs
        return text.upper(), None

    def _form_key_for_lookup(self, op: str, form: Optional[str], dtype: str) -> str:
        selected = self._select_v2_form(op, form=form, dtype=dtype, allow_fallback=True)
        return self._join_form_key(op, selected)

    def get_forwarding_cycles(
        self,
        producer_op: str,
        consumer_op: str,
        dtype: str = "fp32",
        producer_form: Optional[str] = None,
        consumer_form: Optional[str] = None,
    ) -> int:
        """
        Forwarding cycles for COMPUTE->COMPUTE dependency.

        Lookup order:
          1) forwarding.json explicit table:
             forwarding[dtype][PRODUCER][CONSUMER] -> cycles
          2) fallback: max(0, latency(PRODUCER) - default_offset)
             where default_offset is forwarding.json["default"] (default 3 if file missing)
        """
        p, parsed_pf = self._split_form_key(producer_op, producer_form)
        c, parsed_cf = self._split_form_key(consumer_op, consumer_form)
        producer_form = parsed_pf
        consumer_form = parsed_cf

        if self._is_v2_isa():
            try:
                p_form = self._normalize_form_key(p, producer_form) or self._dtype_to_form(dtype)
                c_form = self._normalize_form_key(c, consumer_form) or self._dtype_to_form(dtype)
                p_key = self._join_form_key(p, p_form)
                c_key = self._join_form_key(c, c_form)
                for lookup_p, lookup_c, used_compatible in pair_key_candidates(p, p_form, c, c_form):
                    prod_map = (self._fwd_table or {}).get(lookup_p, None)
                    if isinstance(prod_map, dict) and lookup_c in prod_map:
                        value = max(0, int(prod_map[lookup_c]))
                        if used_compatible:
                            self._record_warning(
                                "compatible_forwarding_pair_fallback",
                                producer=p_key,
                                consumer=c_key,
                                used_producer=lookup_p,
                                used_consumer=lookup_c,
                            )
                        return value
            except Exception:
                pass
        else:
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

        if self._is_v2_isa():
            try:
                lat = self.get_inst_form(p, form=producer_form, dtype=dtype).get("latency", 0)
            except Exception:
                lat = self.get_inst_param(p, "latency", dtype=dtype, default=0)
        else:
            lat = self.get_inst_param(p, "latency", dtype=dtype, default=0)
        try:
            lat_i = int(lat)
        except Exception:
            lat_i = 0
        p_params = self.get_inst_form(p, form=producer_form, dtype=dtype)
        c_params = self.get_inst_form(c, form=consumer_form, dtype=dtype)
        p_class = str(p_params.get("op_class", "")).upper()
        c_class = str(c_params.get("op_class", "")).upper()
        if p_class == "LOAD" and c_class == "STORE":
            default_fwd = 6
            rule = "load_store_default"
        else:
            default_fwd = max(0, lat_i - int(self._fwd_default_offset))
            rule = "producer_latency_minus_default_offset"
        self._record_warning(
            "missing_forwarding_pair",
            producer=self._join_form_key(p, str(p_params.get("form", producer_form or dtype))),
            consumer=self._join_form_key(c, str(c_params.get("form", consumer_form or dtype))),
            producer_latency=lat_i,
            used_default=default_fwd,
            rule=rule,
        )
        return default_fwd

    # ---------------- Initiation Interval API ----------------

    def get_ii(
        self,
        prev_op: str,
        cur_op: str,
        dtype: str = "fp32",
        prev_form: Optional[str] = None,
        cur_form: Optional[str] = None,
    ) -> int:
        """
        Initiation interval for compute issue on the same EXU.

        Semantics:
          cycle(cur_op) >= last_issue(prev_op_on_same_port) + II(prev_op, cur_op)

        Lookup order:
          1) Initiation_Interval.json explicit table:
             InitiationInterval[dtype][PREV_OP][CUR_OP] -> ii
          2) fallback: defaults value in Initiation_Interval.json (default 1 if file missing)
        """
        p, parsed_pf = self._split_form_key(prev_op, prev_form)
        c, parsed_cf = self._split_form_key(cur_op, cur_form)
        prev_form = parsed_pf
        cur_form = parsed_cf

        if self._is_v2_isa():
            try:
                p_form = self._normalize_form_key(p, prev_form) or self._dtype_to_form(dtype)
                c_form = self._normalize_form_key(c, cur_form) or self._dtype_to_form(dtype)
                p_key = self._join_form_key(p, p_form)
                c_key = self._join_form_key(c, c_form)
                for lookup_p, lookup_c, used_compatible in pair_key_candidates(p, p_form, c, c_form):
                    prev_map = (self._ii_table or {}).get(lookup_p, None)
                    if isinstance(prev_map, dict) and lookup_c in prev_map:
                        value = max(1, int(prev_map[lookup_c]))
                        if used_compatible:
                            self._record_warning(
                                "compatible_ii_pair_fallback",
                                prev=p_key,
                                cur=c_key,
                                used_prev=lookup_p,
                                used_cur=lookup_c,
                            )
                        return value
            except Exception:
                pass
        else:
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

        prev_params = self.get_inst_form(p, form=prev_form, dtype=dtype)
        cur_params = self.get_inst_form(c, form=cur_form, dtype=dtype)
        try:
            prev_latency = int(prev_params.get("latency", 9))
        except Exception:
            prev_latency = 9
        try:
            cur_latency = int(cur_params.get("latency", 9))
        except Exception:
            cur_latency = 9
        default_ii = 2 if (prev_latency - cur_latency) == 1 else 1
        self._record_warning(
            "missing_ii_pair",
            prev=self._join_form_key(p, str(prev_params.get("form", prev_form or dtype))),
            cur=self._join_form_key(c, str(cur_params.get("form", cur_form or dtype))),
            prev_latency=prev_latency,
            cur_latency=cur_latency,
            used_default=default_ii,
            rule="prev_latency_minus_cur_latency_eq_1" if default_ii == 2 else "default_one",
        )
        return max(1, int(default_ii))
