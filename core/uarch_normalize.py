#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict


MAINLINE_MODEL_NAME = "queue_level4"


def get_ooo_model_name(uarch: Dict[str, Any]) -> str:
    """
    Return the normalized mainline model name.

    The repository now keeps only one concrete backend. Older callers may still
    pass legacy model labels, but they are all normalized onto the same
    queue-level4 mainline implementation.
    """
    _ = str(uarch.get("ooo_model", MAINLINE_MODEL_NAME)).strip().lower()
    return MAINLINE_MODEL_NAME


def normalize_mainline_uarch(uarch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize the current mainline uarch configuration:
    - SHQ -> EXQ -> EXU staging enabled
    - SHQ finite-credit model enabled
    - IDU-visible credit-delay model enabled
    - finite EXQ depth enabled
    - per-port inflight cap enabled
    """
    cfg = dict(uarch)
    cfg["ooo_model"] = MAINLINE_MODEL_NAME

    cfg["enable_isu_queue_model"] = bool(cfg.get("enable_isu_queue_model", True))
    cfg["shq_depth"] = int(cfg.get("shq_depth", 58))
    cfg["exq_depth"] = int(cfg.get("exq_depth", 26))
    cfg["enforce_same_cycle_src_hazard"] = bool(
        cfg.get("enforce_same_cycle_src_hazard", False)
    )
    cfg["admit_blocked_to_exq"] = bool(cfg.get("admit_blocked_to_exq", False))

    cfg["enable_shq_credit_model"] = bool(cfg.get("enable_shq_credit_model", True))
    cfg["shq_release_delay"] = int(cfg.get("shq_release_delay", 1))

    cfg["enable_credit_visibility_delay"] = bool(
        cfg.get("enable_credit_visibility_delay", True)
    )
    cfg["idu_visible_preg_delay"] = int(cfg.get("idu_visible_preg_delay", 0))
    cfg["idu_visible_shq_delay"] = int(cfg.get("idu_visible_shq_delay", 0))
    cfg["idu_to_ooo_delay"] = int(cfg.get("idu_to_ooo_delay", 1))
    cfg["vloop_to_dispatch_delay"] = int(cfg.get("vloop_to_dispatch_delay", 2))
    cfg["idu_dispatch_start_advance"] = int(cfg.get("idu_dispatch_start_advance", 2))
    cfg["initial_top_block_vloop_start_cycle"] = int(
        cfg.get("initial_top_block_vloop_start_cycle", 19)
    )
    cfg["nested_vloop_initial_start_gap"] = int(
        cfg.get("nested_vloop_initial_start_gap", 1)
    )
    cfg["loop1_min_feedback_gap"] = int(cfg.get("loop1_min_feedback_gap", 7))
    cfg["innermost_iter_dispatch_stride"] = int(
        cfg.get("innermost_iter_dispatch_stride", 1)
    )
    cfg["consumer_release_start_offset"] = int(
        cfg.get("consumer_release_start_offset", 4)
    )
    cfg["load_done_latency"] = int(cfg.get("load_done_latency", 9))
    cfg["global_shq_preg_gate"] = bool(cfg.get("global_shq_preg_gate", False))
    cfg["use_explicit_idu_credit_bank"] = bool(
        cfg.get("use_explicit_idu_credit_bank", False)
    )

    cfg["compute_inflight_cap"] = int(cfg.get("compute_inflight_cap", 0))
    cfg["exq_issue_inflight_cap_per_port"] = int(
        cfg.get("exq_issue_inflight_cap_per_port", 7)
    )
    cfg["exq_capacity_counts_inflight"] = bool(
        cfg.get("exq_capacity_counts_inflight", False)
    )

    cfg["_ooo_uarch_resolved"] = True
    return cfg


def apply_theoretical_limit_overrides(uarch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Semi-ideal theoretical limit:
    - preserve VLOOP / nested-loop / cross-iteration exposure rules
    - preserve dependency, forwarding, II, latency, FU/port constraints
    - remove front-end width/capacity bottlenecks and queue transport delays
    - remove preg / SHQ / EXQ / inflight capacity bottlenecks
    """
    cfg = dict(uarch)

    huge = 10**18

    cfg["vloop_to_dispatch_delay"] = int(
        cfg.get("theoretical_limit_vloop_to_dispatch_delay", 4)
    )
    cfg["idu_dispatch_start_advance"] = int(
        cfg.get("theoretical_limit_idu_dispatch_start_advance", 0)
    )

    cfg["IDU_window_width"] = int(cfg.get("theoretical_limit_idu_window_width", huge))
    cfg["IDU_issue_width"] = int(cfg.get("theoretical_limit_idu_issue_width", huge))

    cfg["OoO_window_width"] = int(cfg.get("theoretical_limit_ooo_window_width", huge))
    cfg["LDQ_width"] = int(cfg.get("theoretical_limit_ldq_width", huge))
    cfg["shq_depth"] = int(cfg.get("theoretical_limit_shq_depth", huge))
    cfg["exq_depth"] = int(cfg.get("theoretical_limit_exq_depth", huge))

    cfg["idu_to_ooo_delay"] = int(cfg.get("theoretical_limit_idu_to_ooo_delay", 0))
    cfg["exq_recv_delay"] = int(cfg.get("theoretical_limit_exq_recv_delay", 0))
    cfg["shq_release_delay"] = int(cfg.get("theoretical_limit_shq_release_delay", 0))
    cfg["idu_visible_preg_delay"] = int(
        cfg.get("theoretical_limit_idu_visible_preg_delay", 0)
    )
    cfg["idu_visible_shq_delay"] = int(
        cfg.get("theoretical_limit_idu_visible_shq_delay", 0)
    )

    cfg["shq_to_exq_port_per_cycle"] = int(
        cfg.get("theoretical_limit_shq_to_exq_port_per_cycle", huge)
    )
    cfg["compute_inflight_cap"] = int(
        cfg.get("theoretical_limit_compute_inflight_cap", 0)
    )
    cfg["exq_issue_inflight_cap_per_port"] = int(
        cfg.get("theoretical_limit_exq_issue_inflight_cap_per_port", 0)
    )
    cfg["global_shq_preg_gate"] = bool(
        cfg.get("theoretical_limit_global_shq_preg_gate", False)
    )
    cfg["use_explicit_idu_credit_bank"] = bool(
        cfg.get("theoretical_limit_use_explicit_idu_credit_bank", False)
    )

    if bool(cfg.get("theoretical_limit_direct_issue", False)):
        cfg["enable_isu_queue_model"] = False
        cfg["enable_shq_credit_model"] = False
        cfg["enable_credit_visibility_delay"] = False
        cfg["admit_blocked_to_exq"] = False
        cfg["exq_recv_delay"] = 0
        cfg["shq_to_exq_port_per_cycle"] = int(huge)
        cfg["compute_inflight_cap"] = 0
        cfg["exq_issue_inflight_cap_per_port"] = 0

    cfg["_ooo_uarch_resolved"] = True
    return cfg
