from __future__ import annotations

from typing import Iterable


COMPATIBLE_FORM_FALLBACKS = {
    "b16": "fp16",
    "b32": "fp32",
}


def compatible_form(form: str | None) -> str | None:
    if form is None:
        return None
    return COMPATIBLE_FORM_FALLBACKS.get(str(form))


def form_candidates(form: str | None) -> list[str]:
    if form is None or str(form) == "":
        return []
    exact = str(form)
    compatible = compatible_form(exact)
    return [exact, compatible] if compatible and compatible != exact else [exact]


def pair_key_candidates(
    producer_op: str,
    producer_form: str,
    consumer_op: str,
    consumer_form: str,
) -> Iterable[tuple[str, str, bool]]:
    producer_forms = form_candidates(producer_form)
    consumer_forms = form_candidates(consumer_form)
    for p_form in producer_forms:
        for c_form in consumer_forms:
            used_compatible = p_form != producer_form or c_form != consumer_form
            yield f"{producer_op.upper()}.{p_form}", f"{consumer_op.upper()}.{c_form}", used_compatible
