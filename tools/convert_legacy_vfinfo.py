#!/usr/bin/env python3
"""Offline converter from the retired JSON trace shape to CanonicalVfInfo v1."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.frontend.serialization import canonical_vf_info_to_dict
from api.json_adapter import LegacyCanonicalJsonAdapter


def convert_legacy_payload(
    payload: Mapping[str, Any], *, target: str = "python"
) -> dict[str, Any]:
    if target not in {"python", "cpp"}:
        raise ValueError(f"unsupported conversion target: {target}")
    canonical = LegacyCanonicalJsonAdapter.from_payload(dict(payload))
    converted = canonical_vf_info_to_dict(canonical)
    if target == "cpp":
        converted["uarch"].pop("canonical_dynamic_instruction_limit", None)
    return converted


def convert_legacy_file(
    source: Path, destination: Path, *, target: str = "python"
) -> None:
    with source.open("r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    converted = convert_legacy_payload(payload, target=target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        json.dump(converted, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a legacy VfSimulator JSON trace to CanonicalVfInfo v1"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--target", choices=("python", "cpp"), default="python")
    args = parser.parse_args()
    convert_legacy_file(args.source, args.destination, target=args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
