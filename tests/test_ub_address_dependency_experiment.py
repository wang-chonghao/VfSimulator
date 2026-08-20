import json
import tempfile
import unittest
from pathlib import Path

from api.cce_adapter import parse_cce_canonical_with_ub_experiment_metadata
from api.frontend.core_lowering import CoreLoweringPass
from api.frontend.ub_access_modes import span_bytes_for_access_mode
from api.frontend.ub_address_experiment import (
    ExperimentalCanonicalCoreLowering,
    PythonUbAddressExperimentMetadata,
    remove_directional_membars,
)
from api.frontend.value_versioning import ValueVersioningPass
from api.simulator_costmodel import CoreVfCostModel
from api.ub_dependency_experiment import UbDependencyExperimentRunner
from api.vf_info import Membar, ValueInfo, VFInfo, VFInst, VFLoop, VFMemoryAccess
from core.flatten import Flattener
from core.ifu import IFUUnroll
from core.ooo import OoOCore, Uop
from core.param_db import ParamDB
from core.ub_address_dependency import (
    DynamicMemoryRange,
    dependency_conflict,
    ranges_overlap,
)


ROOT = Path(__file__).resolve().parents[1]


class UbAddressDependencyExperimentTest(unittest.TestCase):
    @staticmethod
    def _read_json_lines(path: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _local_dependency_vf_info(
        count: int = 4,
        *,
        target: str = "tmp",
        span: int | None = 1,
        unroll: int = 1,
        load_offset: str = "i",
    ) -> VFInfo:
        values = {
            "source": ValueInfo("source", "UB", "fp32"),
            "tmp": ValueInfo("tmp", "UB", "fp32"),
            "other": ValueInfo("other", "UB", "fp32"),
            "value": ValueInfo("value", "Register", "fp32"),
            "result": ValueInfo("result", "Register", "fp32"),
        }
        return VFInfo(
            values=values,
            context=[
                VFLoop(
                    count=count,
                    unroll=unroll,
                    induction_variable="i",
                    body=[
                        VFInst(
                            "VLDS",
                            ["source"],
                            ["value"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("source", "read", "i", span=1),
                            ),
                        ),
                        VFInst(
                            "VSTS",
                            ["value"],
                            ["tmp"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("tmp", "write", "i", span=span),
                            ),
                        ),
                    ],
                ),
                Membar("VST_VLD"),
                VFLoop(
                    count=count,
                    unroll=unroll,
                    induction_variable="i",
                    body=[
                        VFInst(
                            "VLDS",
                            [target],
                            ["result"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess(
                                    target,
                                    "read",
                                    load_offset,
                                    span=span,
                                ),
                            ),
                        )
                    ],
                ),
            ],
        )

    def test_post_update_generator_uses_initial_address_before_delta(self):
        source = """
        void post_update(__ubuf__ float *scores) {
          __VEC_SCOPE__ {
            vector_f32 value;
            __ubuf__ float *p0 = scores + 4 * kCols;
            vlds(value, p0, 4 * kCols, NORM, POST_UPDATE);
            vlds(value, p0, 4 * kCols, NORM, POST_UPDATE);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "post_update.cce"
            path.write_text(source, encoding="utf-8")
            canonical, metadata = parse_cce_canonical_with_ub_experiment_metadata(
                path,
                "post_update",
                {"kCols": 16},
            )

        payload = ExperimentalCanonicalCoreLowering().lower(canonical, metadata)
        linear = Flattener(dict(canonical.params)).flatten(payload["program"])
        ifu = IFUUnroll(
            linear,
            dict(canonical.params),
            structured_value_identity=True,
            ub_dependency_mode="range_overlap",
        )
        first = ifu.next_inst()
        second = ifu.next_inst()
        self.assertEqual(first["memory_ranges"][0]["byte_start"], 256)
        self.assertEqual(second["memory_ranges"][0]["byte_start"], 512)
        self.assertEqual(
            first["ub_address_accesses"][0]["post_update_delta_bytes"]["constant"],
            256,
        )

    def test_bfloat16_t_pointer_offsets_use_two_byte_elements(self):
        source = """
        void bf16_pointer(__ubuf__ bfloat16_t *scores) {
          __VEC_SCOPE__ {
            vector_f16 value;
            vlds(value, scores, 1, BRC_B16);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bf16_pointer.cce"
            path.write_text(source, encoding="utf-8")
            canonical, metadata = parse_cce_canonical_with_ub_experiment_metadata(
                path, "bf16_pointer"
            )
        payload = ExperimentalCanonicalCoreLowering().lower(canonical, metadata)
        ifu = IFUUnroll(
            Flattener({}).flatten(payload["program"]),
            {},
            structured_value_identity=True,
            ub_dependency_mode="range_overlap",
        )
        memory_range = ifu.next_inst()["memory_ranges"][0]
        self.assertEqual(memory_range["byte_start"], 2)
        self.assertEqual(memory_range["byte_end"], 4)

    def test_unknown_ub_element_width_is_rejected_in_experiment_mode(self):
        source = """
        void unknown_pointer(__ubuf__ custom_t *scores) {
          __VEC_SCOPE__ {
            vector_f32 value;
            vlds(value, scores, 1, BRC_B32);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unknown_pointer.cce"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "known element width"):
                parse_cce_canonical_with_ub_experiment_metadata(
                    path, "unknown_pointer"
                )

    def test_membar_inside_unrolled_loop_keeps_baseline_dynamic_order(self):
        values = {
            "source": ValueInfo("source", "UB", "fp32"),
            "tmp": ValueInfo("tmp", "UB", "fp32"),
            "value": ValueInfo("value", "Register", "fp32"),
            "result": ValueInfo("result", "Register", "fp32"),
        }
        vf_info = VFInfo(
            values=values,
            context=[
                VFLoop(
                    count=4,
                    unroll=2,
                    induction_variable="i",
                    body=[
                        VFInst(
                            "VLDS",
                            ["source"],
                            ["value"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("source", "read", "i", span=1),
                            ),
                        ),
                        VFInst(
                            "VSTS",
                            ["value"],
                            ["tmp"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("tmp", "write", "i", span=1),
                            ),
                        ),
                        Membar("VST_VLD"),
                        VFInst(
                            "VLDS",
                            ["tmp"],
                            ["result"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("tmp", "read", "i", span=1),
                            ),
                        ),
                    ],
                )
            ],
        )
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            vf_info
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)
        self.assertTrue(result["dynamic_instruction_order_match"])
        self.assertEqual(result["dynamic_instruction_count"], 12)

    def test_range_gate_releases_each_load_after_matching_store(self):
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info()
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)

            baseline_start = self._read_json_lines(
                Path(tmpdir) / "membar_global/start_by_cycle.json"
            )
            local_start = self._read_json_lines(
                Path(tmpdir) / "ub_local/start_by_cycle.json"
            )
            local_done = self._read_json_lines(
                Path(tmpdir) / "ub_local/done_by_cycle.json"
            )

        store_done = {
            item["iteration_path"][0]["induction_value"]: item["cy"]
            for item in local_done
            if item["static_instruction_id"] == "instruction.1"
        }
        load_start = {
            item["iteration_path"][0]["induction_value"]: item["cy"]
            for item in local_start
            if item["static_instruction_id"] == "instruction.2"
        }
        baseline_load_starts = [
            item["cy"]
            for item in baseline_start
            if item["static_instruction_id"] == "instruction.2"
        ]
        self.assertEqual(set(store_done), set(load_start))
        for index in store_done:
            self.assertEqual(load_start[index], store_done[index] + 1)
        self.assertLess(min(load_start.values()), max(store_done.values()) + 1)
        self.assertLessEqual(min(load_start.values()), min(baseline_load_starts))
        self.assertEqual(result["removed_membars"], 1)
        self.assertEqual(result["precise_access_ratio"], 1.0)
        self.assertGreater(result["local_dependency_edges"], 0)
        self.assertIn("membar_blocked_cycles", result)
        self.assertGreater(result["ub_dependency_blocked_cycles"], 0)
        self.assertLessEqual(
            result["ub_local"]["vf_end_cycle"],
            result["membar_global"]["vf_end_cycle"],
        )

    def test_partial_overlap_uses_half_open_byte_ranges(self):
        store = DynamicMemoryRange("tmp", 0, 8, "write")
        overlapping = DynamicMemoryRange("tmp", 4, 12, "read")
        adjacent = DynamicMemoryRange("tmp", 8, 12, "read")
        other = DynamicMemoryRange("other", 0, 8, "read")
        self.assertTrue(ranges_overlap(store, overlapping))
        self.assertFalse(ranges_overlap(store, adjacent))
        self.assertFalse(ranges_overlap(store, other))

    def test_access_span_comes_from_mode_width(self):
        self.assertEqual(
            span_bytes_for_access_mode("BRC_B32", element_size_bytes=2),
            4,
        )
        self.assertEqual(
            span_bytes_for_access_mode("ONEPT_B16", element_size_bytes=4),
            2,
        )
        self.assertEqual(
            span_bytes_for_access_mode("NORM_B32", element_size_bytes=2),
            256,
        )
        self.assertEqual(
            span_bytes_for_access_mode("NORM_B16", element_size_bytes=4),
            256,
        )
        self.assertEqual(
            span_bytes_for_access_mode("NORM", element_size_bytes=4),
            256,
        )
        self.assertEqual(
            span_bytes_for_access_mode("NORM", element_size_bytes=2),
            256,
        )

    def test_fp32_norm_offsets_expand_to_adjacent_256_byte_ranges(self):
        source = """
        void norm_ranges(__ubuf__ float *scores) {
          __VEC_SCOPE__ {
            vector_f32 value;
            for (uint16_t i = 0; i < 2; ++i) {
              vlds(value, scores, 64 * i, NORM);
            }
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "norm_ranges.cce"
            path.write_text(source, encoding="utf-8")
            canonical, metadata = parse_cce_canonical_with_ub_experiment_metadata(
                path, "norm_ranges"
            )
        payload = ExperimentalCanonicalCoreLowering().lower(canonical, metadata)
        ifu = IFUUnroll(
            Flattener({}).flatten(payload["program"]),
            {},
            structured_value_identity=True,
            ub_dependency_mode="range_overlap",
        )
        ranges = [
            ifu.next_inst()["memory_ranges"][0],
            ifu.next_inst()["memory_ranges"][0],
        ]
        self.assertEqual(
            [(item["byte_start"], item["byte_end"]) for item in ranges],
            [(0, 256), (256, 512)],
        )
        self.assertFalse(
            ranges_overlap(
                DynamicMemoryRange("ub.scores", 0, 256, "read"),
                DynamicMemoryRange("ub.scores", 256, 512, "write"),
            )
        )

    def test_unknown_span_falls_back_to_same_base_global_ordering(self):
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info(span=None)
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)
            starts = self._read_json_lines(
                Path(tmpdir) / "ub_local/start_by_cycle.json"
            )
            done = self._read_json_lines(
                Path(tmpdir) / "ub_local/done_by_cycle.json"
            )
            warning_report = json.loads(
                (Path(tmpdir) / "ub_local/model_warnings.json").read_text(
                    encoding="utf-8"
                )
            )
            warnings = warning_report["instruction_fallback_warnings"]

        last_store_done = max(
            item["cy"]
            for item in done
            if item["static_instruction_id"] == "instruction.1"
        )
        first_load_start = min(
            item["cy"]
            for item in starts
            if item["static_instruction_id"] == "instruction.2"
        )
        self.assertEqual(first_load_start, last_store_done + 1)
        fallback = [
            item
            for item in warnings
            if item["kind"] == "ub_address_dependency_fallback"
        ]
        self.assertTrue(fallback)
        self.assertEqual(fallback[0]["fallback_scope"], "same_base")
        self.assertEqual(fallback[0]["reason"], "unknown_span")
        self.assertGreater(result["same_base_fallback_count"], 0)
        self.assertEqual(result["global_fallback_count"], 0)

    def test_missing_access_metadata_falls_back_to_global_ordering(self):
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info(count=2, target="other")
        )
        incomplete = PythonUbAddressExperimentMetadata(
            {
                instruction_id: accesses
                for instruction_id, accesses in metadata.accesses_by_instruction.items()
                if instruction_id != "instruction.1"
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, incomplete)
            warning_report = json.loads(
                (Path(tmpdir) / "ub_local/model_warnings.json").read_text(
                    encoding="utf-8"
                )
            )
        fallbacks = [
            item
            for item in warning_report["instruction_fallback_warnings"]
            if item["kind"] == "ub_address_dependency_fallback"
        ]
        self.assertTrue(fallbacks)
        self.assertEqual(fallbacks[0]["fallback_scope"], "global")
        self.assertEqual(fallbacks[0]["reason"], "missing_metadata")
        self.assertGreater(result["fallback_dependency_edges"], 0)
        self.assertGreater(result["global_fallback_count"], 0)

    def test_different_base_objects_do_not_create_dependency(self):
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info(count=8, target="other")
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)
            starts = self._read_json_lines(
                Path(tmpdir) / "ub_local/start_by_cycle.json"
            )
            done = self._read_json_lines(
                Path(tmpdir) / "ub_local/done_by_cycle.json"
            )

        first_load_start = min(
            item["cy"]
            for item in starts
            if item["static_instruction_id"] == "instruction.2"
        )
        last_store_done = max(
            item["cy"]
            for item in done
            if item["static_instruction_id"] == "instruction.1"
        )
        self.assertLess(first_load_start, last_store_done + 1)

    def test_different_unresolved_bases_do_not_warn_or_fallback(self):
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info(
                count=4,
                target="other",
                span=None,
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)
        self.assertEqual(result["same_base_fallback_count"], 0)
        self.assertEqual(result["global_fallback_count"], 0)
        self.assertEqual(result["fallback_dependency_edges"], 0)
        unresolved_store = DynamicMemoryRange(
            "tmp", None, None, "write", "unknown_span"
        )
        unresolved_load = DynamicMemoryRange(
            "other", None, None, "read", "unknown_span"
        )
        self.assertEqual(
            dependency_conflict(unresolved_store, unresolved_load),
            (False, None, None),
        )

    def test_lsu_history_keeps_done_cycle_then_prunes_next_cycle(self):
        core = OoOCore(
            {"ub_dependency_mode": "range_overlap"},
            ParamDB(base_dir=str(ROOT)),
        )

        def uop(inst_id: int, done_cycle: int | None) -> Uop:
            return Uop(
                inst_id=inst_id,
                op="VSTS",
                form="fp32",
                src=[],
                dst=[],
                preg_src=[],
                preg_dst=[],
                preg_old=[],
                done_cycle=done_cycle,
            )

        just_done = uop(0, 10)
        running = uop(1, None)
        core.ub_lsu_history[:] = [just_done, running]
        core.cycle = 10
        core._prune_ub_lsu_history()
        self.assertEqual(core.ub_lsu_history, [just_done, running])
        core.cycle = 11
        core._prune_ub_lsu_history()
        self.assertEqual(core.ub_lsu_history, [running])

    def test_two_lsu_arbitration_phases_log_one_ub_block_per_cycle(self):
        db = ParamDB(base_dir=str(ROOT))
        core = OoOCore(
            {**db.get_uarch(), "ub_dependency_mode": "range_overlap"},
            db,
            dtype="fp32",
        )
        blocker = Uop(
            inst_id=0,
            op="VSTS",
            form="fp32",
            src=[],
            dst=[],
            preg_src=[],
            preg_dst=[],
            preg_old=[],
            profile=db.resolve_inst("VSTS", "fp32", "fp32"),
            stream_seq=0,
            memory_ranges=[
                {
                    "base_object_id": "tmp",
                    "byte_start": 0,
                    "byte_end": 4,
                    "access_kind": "write",
                }
            ],
        )
        load = Uop(
            inst_id=1,
            op="VLDS",
            form="fp32",
            src=[],
            dst=[],
            preg_src=[],
            preg_dst=[],
            preg_old=[],
            profile=db.resolve_inst("VLDS", "fp32", "fp32"),
            stream_seq=1,
            memory_ranges=[
                {
                    "base_object_id": "tmp",
                    "byte_start": 0,
                    "byte_end": 4,
                    "access_kind": "read",
                }
            ],
        )
        core.ub_lsu_history[:] = [blocker, load]
        logged_ids: set[int] = set()

        self.assertTrue(core._blocked_by_ub_dependency(load, logged_ids))
        self.assertTrue(core._blocked_by_ub_dependency(load, logged_ids))

        self.assertEqual(core._ub_dependency_blocked_cycles, 1)
        self.assertEqual(logged_ids, {1})

    def test_same_base_non_overlapping_ranges_do_not_create_dependency(self):
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info(
                count=8,
                load_offset="i + 32",
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)
        self.assertEqual(result["local_dependency_edges"], 0)
        self.assertEqual(result["global_fallback_count"], 0)
        self.assertLess(
            result["local_dependency_cycle"],
            result["global_membar_cycle"],
        )

    def test_load_to_store_direction_releases_by_matching_address(self):
        values = {
            "tmp": ValueInfo("tmp", "UB", "fp32"),
            "lhs": ValueInfo("lhs", "Register", "fp32"),
            "rhs": ValueInfo("rhs", "Register", "fp32"),
            "store_value": ValueInfo("store_value", "Register", "fp32"),
            "loaded": ValueInfo("loaded", "Register", "fp32"),
        }
        vf_info = VFInfo(
            values=values,
            context=[
                VFInst("VADD", ["lhs", "rhs"], ["store_value"], "fp32"),
                VFLoop(
                    count=4,
                    induction_variable="i",
                    body=[
                        VFInst(
                            "VLDS",
                            ["tmp"],
                            ["loaded"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("tmp", "read", "i", span=1),
                            ),
                        )
                    ],
                ),
                Membar("VLD_VST"),
                VFLoop(
                    count=4,
                    induction_variable="i",
                    body=[
                        VFInst(
                            "VSTS",
                            ["store_value"],
                            ["tmp"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("tmp", "write", "i", span=1),
                            ),
                        )
                    ],
                ),
            ],
        )
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            vf_info
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)
            starts = self._read_json_lines(
                Path(tmpdir) / "ub_local/start_by_cycle.json"
            )
            done = self._read_json_lines(
                Path(tmpdir) / "ub_local/done_by_cycle.json"
            )
        load_done = {
            item["iteration_path"][0]["induction_value"]: item["cy"]
            for item in done
            if item["static_instruction_id"] == "instruction.1"
        }
        store_start = {
            item["iteration_path"][0]["induction_value"]: item["cy"]
            for item in starts
            if item["static_instruction_id"] == "instruction.2"
        }
        self.assertEqual(set(load_done), set(store_start))
        for index in load_done:
            self.assertEqual(store_start[index], load_done[index] + 1)
        self.assertLess(min(store_start.values()), max(load_done.values()) + 1)

    def test_unroll_uses_dynamic_induction_values_for_ranges(self):
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info(count=4, unroll=2)
        )
        local, _ = remove_directional_membars(canonical)
        payload = ExperimentalCanonicalCoreLowering().lower(local, metadata)
        linear = Flattener(dict(canonical.params)).flatten(payload["program"])
        ifu = IFUUnroll(
            linear,
            dict(canonical.params),
            structured_value_identity=True,
            ub_dependency_mode="range_overlap",
        )
        ranges = []
        while not ifu.done():
            inst = ifu.next_inst()
            if inst and inst.get("static_instruction_id") == "instruction.1":
                ranges.append(inst["memory_ranges"][0])
        self.assertEqual([item["byte_start"] for item in ranges], [0, 4, 8, 12])

    def test_unrolled_post_update_advances_once_per_dynamic_lane(self):
        source = """
        void post_update_loop(__ubuf__ float *scores) {
          __VEC_SCOPE__ {
            vector_f32 value;
            __ubuf__ float *p0 = scores;
            #pragma unroll(2)
            for (int i = 0; i < 4; ++i) {
              vlds(value, p0, 1, NORM, POST_UPDATE);
            }
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "post_update_loop.cce"
            path.write_text(source, encoding="utf-8")
            canonical, metadata = parse_cce_canonical_with_ub_experiment_metadata(
                path, "post_update_loop"
            )
        payload = ExperimentalCanonicalCoreLowering().lower(canonical, metadata)
        ifu = IFUUnroll(
            Flattener({}).flatten(payload["program"]),
            {},
            structured_value_identity=True,
            ub_dependency_mode="range_overlap",
        )
        starts = []
        while not ifu.done():
            inst = ifu.next_inst()
            if inst:
                starts.append(inst["memory_ranges"][0]["byte_start"])
        self.assertEqual(starts, [0, 4, 8, 12])

    def test_consumer_waits_for_latest_overlapping_producer(self):
        values = {
            "source": ValueInfo("source", "UB", "fp32"),
            "tmp": ValueInfo("tmp", "UB", "fp32"),
            "value": ValueInfo("value", "Register", "fp32"),
            "result": ValueInfo("result", "Register", "fp32"),
        }
        vf_info = VFInfo(
            values=values,
            context=[
                VFLoop(
                    count=2,
                    induction_variable="i",
                    body=[
                        VFInst(
                            "VLDS",
                            ["source"],
                            ["value"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("source", "read", "i", span=1),
                            ),
                        ),
                        VFInst(
                            "VSTS",
                            ["value"],
                            ["tmp"],
                            "fp32",
                            memory_accesses=(
                                VFMemoryAccess("tmp", "write", "i", span=2),
                            ),
                        ),
                    ],
                ),
                Membar("VST_VLD"),
                VFInst(
                    "VLDS",
                    ["tmp"],
                    ["result"],
                    "fp32",
                    memory_accesses=(
                        VFMemoryAccess("tmp", "read", 1, span=1),
                    ),
                ),
            ],
        )
        canonical, metadata = ValueVersioningPass().run_with_ub_experiment_metadata(
            vf_info
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            UbDependencyExperimentRunner(
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)
            ).run_pair(canonical, metadata)
            starts = self._read_json_lines(
                Path(tmpdir) / "ub_local/start_by_cycle.json"
            )
            done = self._read_json_lines(
                Path(tmpdir) / "ub_local/done_by_cycle.json"
            )
        store_done = [
            item["cy"]
            for item in done
            if item["static_instruction_id"] == "instruction.1"
        ]
        load_start = next(
            item["cy"]
            for item in starts
            if item["static_instruction_id"] == "instruction.2"
        )
        self.assertEqual(load_start, max(store_done) + 1)

    def test_independent_post_update_pointer_states_do_not_interfere(self):
        source = """
        void pointers(__ubuf__ float *scores) {
          __VEC_SCOPE__ {
            vector_f32 v0, v1;
            __ubuf__ float *p0 = scores;
            __ubuf__ float *p1 = scores;
            vlds(v0, p0, 1, NORM, POST_UPDATE);
            vlds(v1, p1, 1, NORM, POST_UPDATE);
            vlds(v0, p0, 1, NORM, POST_UPDATE);
            vlds(v1, p1, 1, NORM, POST_UPDATE);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pointers.cce"
            path.write_text(source, encoding="utf-8")
            canonical, metadata = parse_cce_canonical_with_ub_experiment_metadata(
                path, "pointers"
            )
        payload = ExperimentalCanonicalCoreLowering().lower(canonical, metadata)
        ifu = IFUUnroll(
            Flattener({}).flatten(payload["program"]),
            {},
            structured_value_identity=True,
            ub_dependency_mode="range_overlap",
        )
        starts = []
        while not ifu.done():
            inst = ifu.next_inst()
            if inst:
                starts.append(inst["memory_ranges"][0]["byte_start"])
        self.assertEqual(starts, [0, 0, 4, 4])

    def test_experiment_rejects_pointer_declaration_inside_loop(self):
        source = """
        void pointer_loop(__ubuf__ float *scores) {
          __VEC_SCOPE__ {
            vector_f32 value;
            for (int i = 0; i < 2; ++i) {
              __ubuf__ float *p = scores + i;
              vlds(value, p, 0, NORM);
            }
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pointer_loop.cce"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside dynamic loops"):
                parse_cce_canonical_with_ub_experiment_metadata(
                    path, "pointer_loop"
                )

    def test_experiment_rejects_alias_after_loop_updates_source_pointer(self):
        source = """
        void pointer_snapshot(__ubuf__ float *scores) {
          __VEC_SCOPE__ {
            vector_f32 value;
            __ubuf__ float *p0 = scores;
            for (int i = 0; i < 2; ++i) {
              vlds(value, p0, 1, NORM, POST_UPDATE);
            }
            __ubuf__ float *p1 = p0;
            vlds(value, p1, 0, NORM);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pointer_snapshot.cce"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already POST_UPDATE"):
                parse_cce_canonical_with_ub_experiment_metadata(
                    path, "pointer_snapshot"
                )

    def test_pointer_cast_alias_chain_keeps_base_and_byte_units(self):
        source = """
        void alias_chain(__ubuf__ float *scores) {
          __VEC_SCOPE__ {
            vector_f16 value;
            __ubuf__ float *p0 = scores + 4;
            __ubuf__ half *p1 = (__ubuf__ half *)p0;
            vlds(value, p1, 1, BRC_B16);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "alias_chain.cce"
            path.write_text(source, encoding="utf-8")
            canonical, metadata = parse_cce_canonical_with_ub_experiment_metadata(
                path, "alias_chain"
            )
        payload = ExperimentalCanonicalCoreLowering().lower(canonical, metadata)
        ifu = IFUUnroll(
            Flattener({}).flatten(payload["program"]),
            {},
            structured_value_identity=True,
            ub_dependency_mode="range_overlap",
        )
        memory_range = ifu.next_inst()["memory_ranges"][0]
        self.assertEqual(memory_range["base_object_id"], "ub.scores")
        self.assertEqual(memory_range["byte_start"], 18)
        self.assertEqual(memory_range["byte_end"], 20)

    def test_range_mode_requires_experimental_canonical_lowering(self):
        canonical, _ = ValueVersioningPass().run_with_ub_experiment_metadata(
            self._local_dependency_vf_info()
        )
        payload = CoreLoweringPass().lower(canonical)
        payload["uarch"] = {"ub_dependency_mode": "range_overlap"}
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(
                RuntimeError, "ExperimentalCanonicalCoreLowering"
            ):
                CoreVfCostModel(base_dir=ROOT, out_dir=tmpdir)._run_lowered_payload(
                    payload
                )

    def test_experiment_runner_rejects_legacy_vf_info(self):
        with self.assertRaisesRegex(TypeError, "CanonicalVfInfo only"):
            UbDependencyExperimentRunner().run_pair(  # type: ignore[arg-type]
                self._local_dependency_vf_info()
            )


if __name__ == "__main__":
    unittest.main()
