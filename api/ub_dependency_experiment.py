from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

from api.cce_adapter import parse_cce_canonical_with_ub_experiment_metadata
from api.frontend.ub_address_experiment import (
    ExperimentalCanonicalCoreLowering,
    PythonUbAddressExperimentMetadata,
    metadata_from_canonical,
    remove_directional_membars,
)
from api.frontend.schema import CanonicalVfInfo
from api.simulator_costmodel import CoreVfCostModel


class UbDependencyExperimentRunner:
    def __init__(self, model: CoreVfCostModel | None = None) -> None:
        self.model = model or CoreVfCostModel()

    def run_pair(
        self,
        vf_info: CanonicalVfInfo,
        metadata: PythonUbAddressExperimentMetadata | None = None,
    ) -> dict[str, Any]:
        if not isinstance(vf_info, CanonicalVfInfo):
            raise TypeError(
                "UB dependency experiment accepts CanonicalVfInfo only; "
                "adapt legacy input before running the A/B comparison"
            )
        root = Path(self.model.out_dir)
        baseline_model = replace(self.model, out_dir=root / "membar_global")
        local_model = replace(self.model, out_dir=root / "ub_local")

        metadata = metadata or metadata_from_canonical(vf_info)
        baseline = baseline_model.run_canonical_vf_info(vf_info)
        local_vf_info, removed_membars = remove_directional_membars(vf_info)
        payload = ExperimentalCanonicalCoreLowering().lower(
            local_vf_info, metadata
        )
        local = local_model._run_lowered_payload(payload)
        baseline_distribution = self._op_form_distribution(
            Path(baseline["results_dir"]) / "start_by_cycle.json"
        )
        local_distribution = self._op_form_distribution(
            Path(local["results_dir"]) / "start_by_cycle.json"
        )
        if baseline_distribution != local_distribution:
            raise RuntimeError(
                "UB dependency A/B runs produced different dynamic instruction "
                "distributions"
            )
        baseline_stream = self._dynamic_stream_identity(
            Path(baseline["results_dir"]) / "start_by_cycle.json"
        )
        local_stream = self._dynamic_stream_identity(
            Path(local["results_dir"]) / "start_by_cycle.json"
        )
        if baseline_stream != local_stream:
            raise RuntimeError(
                "UB dependency A/B runs produced different dynamic instruction "
                "orders"
            )
        baseline_cycles = int(baseline["vf_end_cycle"])
        local_cycles = int(local["vf_end_cycle"])
        return {
            "membar_global": baseline,
            "ub_local": local,
            "removed_membars": removed_membars,
            "cycle_reduction": baseline_cycles - local_cycles,
            "speedup": baseline_cycles / local_cycles if local_cycles else None,
            "global_membar_cycle": baseline_cycles,
            "local_dependency_cycle": local_cycles,
            "dynamic_instruction_count": sum(local_distribution.values()),
            "op_form_distribution_match": True,
            "dynamic_instruction_order_match": True,
            **local.get("memory_ordering_stats", {}),
            "membar_blocked_cycles": baseline.get(
                "memory_ordering_stats", {}
            ).get("membar_blocked_cycles", 0),
        }

    @staticmethod
    def _op_form_distribution(path: Path) -> Counter[tuple[str, str]]:
        distribution: Counter[tuple[str, str]] = Counter()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            distribution[(str(item.get("op")), str(item.get("form")))] += 1
        return distribution

    @staticmethod
    def _dynamic_stream_identity(path: Path) -> list[tuple[Any, ...]]:
        items = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        items.sort(key=lambda item: int(item.get("stream_seq", -1)))
        return [
            (
                str(item.get("static_instruction_id")),
                tuple(
                    (
                        str(level.get("loop_id")),
                        int(level.get("iteration", 0)),
                        str(level.get("induction_variable")),
                        int(level.get("induction_value", 0)),
                    )
                    for level in item.get("iteration_path", [])
                ),
                str(item.get("op")),
                str(item.get("form")),
            )
            for item in items
        ]

    def run_cce_pair(
        self,
        path: str | Path,
        *,
        kernel_name: str | None = None,
        loop_params: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        vf_info, metadata = parse_cce_canonical_with_ub_experiment_metadata(
            path,
            kernel_name=kernel_name,
            loop_params=loop_params,
        )
        return self.run_pair(vf_info, metadata)


__all__ = ["UbDependencyExperimentRunner"]
