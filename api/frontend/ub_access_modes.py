from __future__ import annotations


def span_bytes_for_access_mode(
    access_mode: str | None,
    *,
    element_size_bytes: int,
) -> int | None:
    """Return only ISA-confirmed UB access spans for the experiment."""

    normalized = str(access_mode or "").strip().upper()
    if normalized.startswith("BRC_") or normalized.startswith("ONEPT_"):
        if normalized.endswith("B16"):
            return 2
        if normalized.endswith("B32"):
            return 4
        return int(element_size_bytes)
    return None


__all__ = [
    "span_bytes_for_access_mode",
]
