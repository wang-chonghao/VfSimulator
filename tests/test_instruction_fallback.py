import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB


ROOT = Path(__file__).resolve().parents[1]


class InstructionFallbackTest(unittest.TestCase):
    def test_unknown_vector_op_uses_default_compute_params(self):
        db = ParamDB(base_dir=str(ROOT))

        params = db.get_inst_form("VUNKNOWN", form="fp32", dtype="fp32")

        self.assertEqual(params["op_class"], "COMPUTE")
        self.assertEqual(params["EXU"], "ALU")
        self.assertEqual(params["dispatch_exu"], "EXU01")
        self.assertEqual(params["latency"], 9)
        self.assertTrue(
            any(warning["kind"] == "unsupported_isa_op" for warning in db.get_warnings())
        )

    def test_unknown_load_store_prefixes_use_lsu_defaults(self):
        db = ParamDB(base_dir=str(ROOT))

        load_params = db.get_inst_form("VLDX", form="fp32", dtype="fp32")
        store_params = db.get_inst_form("VSTX", form="fp32", dtype="fp32")

        self.assertEqual(load_params["op_class"], "LOAD")
        self.assertEqual(load_params["latency"], 9)
        self.assertEqual(store_params["op_class"], "STORE")
        self.assertEqual(store_params["latency"], 9)

    def test_missing_forwarding_and_ii_defaults(self):
        db = ParamDB(base_dir=str(ROOT))

        self.assertEqual(
            db.get_forwarding_cycles(
                "VLDX",
                "VSTX",
                dtype="fp32",
                producer_form="fp32",
                consumer_form="fp32",
            ),
            6,
        )
        self.assertEqual(
            db.get_ii(
                "VUNKNOWN",
                "VUNKNOWN2",
                dtype="fp32",
                prev_form="fp32",
                cur_form="fp32",
            ),
            1,
        )

        kinds = {warning["kind"] for warning in db.get_warnings()}
        self.assertIn("missing_forwarding_pair", kinds)
        self.assertIn("missing_ii_pair", kinds)

    def test_missing_ii_uses_two_when_prev_latency_exceeds_cur_by_one(self):
        db = ParamDB(base_dir=str(ROOT))
        db._insts["VPREV_LAT10"] = {
            "op_class": "COMPUTE",
            "forms": {"fp32": {"latency": 10, "EXU": "ALU", "dispatch_exu": "EXU01"}},
        }
        db._insts["VCUR_LAT9"] = {
            "op_class": "COMPUTE",
            "forms": {"fp32": {"latency": 9, "EXU": "ALU", "dispatch_exu": "EXU01"}},
        }

        self.assertEqual(
            db.get_ii(
                "VPREV_LAT10",
                "VCUR_LAT9",
                dtype="fp32",
                prev_form="fp32",
                cur_form="fp32",
            ),
            2,
        )

    def test_load_store_done_latency_uses_instruction_latency(self):
        payload = {
            "dtype": "fp32",
            "params": {},
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memB": {"value_id": "memB", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                        {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v0"], "dst": ["memB"]},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir)._run_lowered_payload(payload)
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            dones = [
                json.loads(line)
                for line in (out_dir / "done_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]

        start_by_op = {item["op"]: item["cy"] for item in starts}
        done_by_op = {item["op"]: item["cy"] for item in dones}
        self.assertEqual(done_by_op["VLDS"] - start_by_op["VLDS"], 9)
        self.assertEqual(done_by_op["VSTS"] - start_by_op["VSTS"], 9)

    def test_vpack_and_vsstb_have_explicit_model_params(self):
        db = ParamDB(base_dir=str(ROOT))

        vpack = db.get_inst_form("VPACK", form="b32", dtype="bf16")
        vsstb = db.get_inst_form("VSSTB", form="b16", dtype="bf16")

        self.assertEqual(vpack["op_class"], "COMPUTE")
        self.assertEqual(vpack["latency"], 11)
        self.assertEqual(vpack["dispatch_exu"], "EXU0_ONLY")
        self.assertEqual(vsstb["op_class"], "STORE")
        self.assertEqual(vsstb["latency"], 9)
        self.assertEqual(vsstb["data_store_cost"], 9)
        self.assertEqual(
            db.get_forwarding_cycles(
                "VCVT_F32_TO_F16",
                "VPACK",
                dtype="bf16",
                producer_form="f32_to_f16",
                consumer_form="b32",
            ),
            4,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VPACK",
                "VADD",
                dtype="bf16",
                producer_form="b32",
                consumer_form="fp16",
            ),
            8,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VPACK",
                "VSSTB",
                dtype="bf16",
                producer_form="b32",
                consumer_form="b16",
            ),
            9,
        )

    def test_vexpdif_and_vmulscvt_have_explicit_model_params(self):
        db = ParamDB(base_dir=str(ROOT))

        vexpdif = db.get_inst_form("VEXPDIF", form="fp32", dtype="fp32")
        vmulscvt = db.get_inst_form("VMULSCVT", form="f32_to_f16", dtype="fp32")

        self.assertEqual(vexpdif["op_class"], "COMPUTE")
        self.assertEqual(vexpdif["pipeline_startup_cost"], 7)
        self.assertEqual(vexpdif["latency"], 18)
        self.assertEqual(vexpdif["pipeline_drain_cost"], 16)
        self.assertEqual(vexpdif["data_load_cost"], 9)
        self.assertEqual(vexpdif["data_store_cost"], 9)
        self.assertEqual(vexpdif["dispatch_exu"], "EXU01")
        self.assertEqual(vmulscvt["op_class"], "COMPUTE")
        self.assertEqual(vmulscvt["pipeline_startup_cost"], 6)
        self.assertEqual(vmulscvt["latency"], 8)
        self.assertEqual(vmulscvt["pipeline_drain_cost"], 6)
        self.assertEqual(vmulscvt["data_load_cost"], 9)
        self.assertEqual(vmulscvt["data_store_cost"], 9)
        self.assertEqual(vmulscvt["dispatch_exu"], "EXU01")
        self.assertEqual(
            db.get_forwarding_cycles(
                "VMULS",
                "VEXPDIF",
                dtype="fp32",
                producer_form="fp32",
                consumer_form="fp32",
            ),
            5,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VEXPDIF",
                "VMULSCVT",
                dtype="fp32",
                producer_form="fp32",
                consumer_form="f32_to_f16",
            ),
            15,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VMULSCVT",
                "VPACK",
                dtype="fp32",
                producer_form="f32_to_f16",
                consumer_form="b32",
            ),
            5,
        )

    def test_vpack_vsstb_forms_do_not_fallback_through_python_idu(self):
        payload = {
            "dtype": "fp32",
            "params": {},
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp16", "shape": [64]},
                "memB": {"value_id": "memB", "storage": "UB", "dtype": "bf16", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp16", "shape": [64]},
                "v1": {"value_id": "v1", "storage": "Register", "dtype": "bf16", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp16", "src": ["memA"], "dst": ["v0"]},
                        {"type": "inst", "op": "VPACK", "form": "b32", "src": ["v0"], "dst": ["v1"]},
                        {"type": "inst", "op": "VSSTB", "form": "b16", "src": ["v1"], "dst": ["memB"]},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir)._run_lowered_payload(payload)
            warnings = json.loads((out_dir / "model_warnings.json").read_text(encoding="utf-8"))
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]

        fallback_warnings = warnings["instruction_fallback_warnings"]
        self.assertFalse(
            any(
                item["kind"] == "unsupported_isa_form"
                and item.get("op") in {"VPACK", "VSSTB"}
                for item in fallback_warnings
            )
        )
        self.assertEqual([item["op"] for item in starts], ["VLDS", "VPACK", "VSSTB"])

    def test_compatible_form_fallback_uses_fp_params_for_b_forms(self):
        db = ParamDB(base_dir=str(ROOT))

        vadd = db.get_inst_form("VADD", form="b16", dtype="bf16")

        self.assertEqual(vadd["form"], "b16")
        self.assertEqual(vadd["resolved_form"], "fp16")
        self.assertEqual(vadd["latency"], db.get_inst_form("VADD", form="fp16")["latency"])
        self.assertTrue(db.has_inst("VADD", "b16"))
        self.assertEqual(
            db.get_forwarding_cycles(
                "VADD",
                "VMUL",
                dtype="bf16",
                producer_form="b16",
                consumer_form="b16",
            ),
            db.get_forwarding_cycles(
                "VADD",
                "VMUL",
                dtype="fp16",
                producer_form="fp16",
                consumer_form="fp16",
            ),
        )
        self.assertEqual(
            db.get_ii(
                "VADD",
                "VMUL",
                dtype="bf16",
                prev_form="b16",
                cur_form="b16",
            ),
            db.get_ii(
                "VADD",
                "VMUL",
                dtype="fp16",
                prev_form="fp16",
                cur_form="fp16",
            ),
        )

        kinds = {warning["kind"] for warning in db.get_warnings()}
        self.assertIn("compatible_isa_form_fallback", kinds)
        self.assertIn("compatible_forwarding_pair_fallback", kinds)
        self.assertIn("compatible_ii_pair_fallback", kinds)

    def test_partial_compatible_form_params_inherit_missing_fields(self):
        db = ParamDB(base_dir=str(ROOT))
        db._insts["VPARTIAL"] = {
            "op_class": "COMPUTE",
            "forms": {
                "fp16": {
                    "latency": 7,
                    "throughput": 2,
                    "data_load_cost": 9,
                    "data_store_cost": 9,
                    "EXU": "ALU",
                    "dispatch_exu": "EXU01",
                },
                "b16": {"latency": 123},
            },
        }

        params = db.get_inst_form("VPARTIAL", form="b16", dtype="bf16")

        self.assertEqual(params["form"], "b16")
        self.assertEqual(params["resolved_form"], "b16")
        self.assertEqual(params["latency"], 123)
        self.assertEqual(params["throughput"], 2)
        self.assertEqual(params["data_load_cost"], 9)
        self.assertEqual(params["data_store_cost"], 9)
        self.assertEqual(params["dispatch_exu"], "EXU01")

    def test_cli_writes_instruction_fallback_warnings_without_vreg_warning(self):
        trace = {
            "dtype": "fp32",
            "params": {},
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memB": {"value_id": "memB", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "v1": {"value_id": "v1", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                        {"type": "inst", "op": "VUNKNOWN", "form": "fp32", "src": ["v0"], "dst": ["v1"]},
                        {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v1"], "dst": ["memB"]},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trace_path = tmp / "unknown_trace.json"
            out_dir = tmp / "out"
            trace_path.write_text(json.dumps(trace), encoding="utf-8")
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "main.py"),
                    "--trace",
                    str(trace_path),
                    "--out_dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            warnings_path = out_dir / "model_warnings.json"
            payload = json.loads(warnings_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["vreg_capacity_warnings"], [])
        self.assertTrue(payload["instruction_fallback_warnings"])

    def _run_payload_logs(self, body, include_history=False):
        payload = {
            "dtype": "fp32",
            "params": {},
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memB": {"value_id": "memB", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memC": {"value_id": "memC", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "v1": {"value_id": "v1", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [{"type": "loop", "iters": 1, "body": body}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir)._run_lowered_payload(payload)
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            dones = [
                json.loads(line)
                for line in (out_dir / "done_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            history = json.loads((out_dir / "sim_history.json").read_text())
        if include_history:
            return starts, dones, history
        return starts, dones

    def test_vst_vld_membar_blocks_following_load_until_prior_store_done(self):
        starts, dones = self._run_payload_logs(
            [
                {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v0"], "dst": ["memB"]},
                {"type": "membar", "barrier": "VST_VLD"},
                {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memC"], "dst": ["v1"]},
            ]
        )

        store_done = next(item["cy"] for item in dones if item["op"] == "VSTS")
        post_barrier_load_start = [item["cy"] for item in starts if item["op"] == "VLDS"][-1]
        self.assertGreaterEqual(post_barrier_load_start, store_done)

    def test_vld_vst_membar_blocks_following_store_until_prior_load_done(self):
        starts, dones = self._run_payload_logs(
            [
                {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                {"type": "membar", "barrier": "VLD_VST"},
                {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v0"], "dst": ["memB"]},
            ]
        )

        load_done = next(item["cy"] for item in dones if item["op"] == "VLDS")
        store_start = next(item["cy"] for item in starts if item["op"] == "VSTS")
        self.assertGreaterEqual(store_start, load_done)

    def test_vst_vld_membar_does_not_directly_block_compute(self):
        starts, dones, history = self._run_payload_logs(
            [
                {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v0"], "dst": ["memB"]},
                {"type": "membar", "barrier": "MEMBAR.VST_VLD"},
                {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memC"], "dst": ["v1"]},
                {"type": "inst", "op": "VADDS", "form": "fp32", "src": ["v0"], "dst": ["v1"]},
            ],
            include_history=True,
        )

        store_done = next(item["cy"] for item in dones if item["op"] == "VSTS")
        compute_start = next(item["cy"] for item in starts if item["op"] == "VADDS")
        post_barrier_load_start = [item["cy"] for item in starts if item["op"] == "VLDS"][-1]
        self.assertGreaterEqual(post_barrier_load_start, store_done)
        self.assertLess(compute_start, store_done)
        self.assertTrue(
            any(
                item.get("event") == "blocked"
                and item.get("op") == "VLDS"
                and item.get("state") == "ready"
                and item.get("blocked_reason") == "membar"
                for item in history
            )
        )

    def test_loop_membar_uses_dynamic_stream_sequence_not_static_pc(self):
        payload = {
            "dtype": "fp32",
            "params": {},
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memB": {"value_id": "memB", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memC": {"value_id": "memC", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "v1": {"value_id": "v1", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 2,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                        {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v0"], "dst": ["memB"]},
                        {"type": "membar", "barrier": "VST_VLD"},
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memC"], "dst": ["v1"]},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir)._run_lowered_payload(payload)
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            dones = [
                json.loads(line)
                for line in (out_dir / "done_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]

        first_post_barrier_load_start = next(item["cy"] for item in starts if item["inst_id"] == 2)
        second_store_done = next(item["cy"] for item in dones if item["inst_id"] == 4)
        self.assertLess(first_post_barrier_load_start, second_store_done)

    def test_membar_disables_innermost_unroll_with_warning(self):
        trace = {
            "dtype": "fp32",
            "params": {},
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memB": {"value_id": "memB", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "memC": {"value_id": "memC", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "v1": {"value_id": "v1", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 4,
                    "unroll": 2,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                        {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v0"], "dst": ["memB"]},
                        {"type": "membar", "barrier": "VST_VLD"},
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memC"], "dst": ["v1"]},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trace_path = tmp / "membar_unroll.json"
            out_dir = tmp / "out"
            trace_path.write_text(json.dumps(trace), encoding="utf-8")
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "main.py"),
                    "--trace",
                    str(trace_path),
                    "--out_dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            warnings_payload = json.loads(
                (out_dir / "model_warnings.json").read_text(encoding="utf-8")
            )
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]

        warning = next(
            item
            for item in warnings_payload["instruction_fallback_warnings"]
            if item["kind"] == "membar_unroll_disabled"
        )
        self.assertEqual(warning["barrier"], "VST_VLD")
        self.assertEqual(warning["loop_id"], 0)
        self.assertEqual(warning["requested_unroll"], 2)
        self.assertEqual(warning["used_unroll"], 1)
        self.assertEqual(warning["reason"], "membar_in_unrolled_innermost_loop")
        touched_values = []
        for item in starts:
            touched_values.extend(item.get("src", []))
            touched_values.extend(item.get("dst", []))
        self.assertFalse(any("_lane" in value for value in touched_values))

    def test_unsupported_membar_type_writes_warning(self):
        trace = {
            "dtype": "fp32",
            "params": {},
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "membar", "barrier": "VV_ALL"},
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trace_path = tmp / "unsupported_membar.json"
            out_dir = tmp / "out"
            trace_path.write_text(json.dumps(trace), encoding="utf-8")
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "main.py"),
                    "--trace",
                    str(trace_path),
                    "--out_dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            payload = json.loads((out_dir / "model_warnings.json").read_text(encoding="utf-8"))

        kinds = {warning["kind"] for warning in payload["instruction_fallback_warnings"]}
        self.assertIn("unsupported_membar_type", kinds)


if __name__ == "__main__":
    unittest.main()
