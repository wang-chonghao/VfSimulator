from __future__ import annotations

from typing import Any, Dict

from core.uarch_normalize import apply_theoretical_limit_overrides, normalize_mainline_uarch


MAINLINE = "mainline"
THEORY_VLOOP_ONLY = "theory_vloop_only"
THEORY_DIRECT_ISSUE = "theory_direct_issue"
DEFAULT_THEORY = THEORY_DIRECT_ISSUE

SUPPORTED_MODELS = (MAINLINE, THEORY_VLOOP_ONLY, THEORY_DIRECT_ISSUE)

_ALIASES = {
    MAINLINE: MAINLINE,
    "queue_level4": MAINLINE,
    "level4": MAINLINE,
    "theory": DEFAULT_THEORY,
    "theoretical_limit": DEFAULT_THEORY,
    "theoretical-limit": DEFAULT_THEORY,
    THEORY_VLOOP_ONLY: THEORY_VLOOP_ONLY,
    "theoretical_limit_vloop_only": THEORY_VLOOP_ONLY,
    "theoretical-limit-vloop-only": THEORY_VLOOP_ONLY,
    THEORY_DIRECT_ISSUE: THEORY_DIRECT_ISSUE,
    "theory_vloop_only_legacy_forwarding_direct_issue": THEORY_DIRECT_ISSUE,
    "theoretical_limit_vloop_only_legacy_forwarding_direct_issue": THEORY_DIRECT_ISSUE,
    "theoretical-limit-vloop-only-legacy-forwarding-direct-issue": THEORY_DIRECT_ISSUE,
}


def normalize_model_name(model: str) -> str:
    key = str(model or MAINLINE).strip().lower()
    try:
        return _ALIASES[key]
    except KeyError as exc:
        supported = ", ".join(SUPPORTED_MODELS)
        raise ValueError(f"Unsupported VfSimulator model: {model}. Supported models: {supported}") from exc


def apply_vfsim_model(uarch: Dict[str, Any], model: str) -> Dict[str, Any]:
    model_name = normalize_model_name(model)
    cfg = dict(uarch)
    cfg["ooo_model"] = "queue_level4"

    if model_name == THEORY_VLOOP_ONLY:
        cfg["theoretical_limit_mode"] = True
        cfg["theoretical_limit_vloop_only"] = True
    elif model_name == THEORY_DIRECT_ISSUE:
        cfg["theoretical_limit_mode"] = True
        cfg["theoretical_limit_vloop_only"] = True
        cfg["theoretical_limit_legacy_forwarding"] = True
        cfg["theoretical_limit_direct_issue"] = True

    cfg = normalize_mainline_uarch(cfg)
    if model_name != MAINLINE:
        cfg = apply_theoretical_limit_overrides(cfg)
    cfg["vfsim_model"] = model_name
    return cfg
