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

    def _run_payload_logs(self, body):
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
        starts, dones = self._run_payload_logs(
            [
                {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["v0"], "dst": ["memB"]},
                {"type": "membar", "barrier": "VST_VLD"},
                {"type": "inst", "op": "VADDS", "form": "fp32", "src": ["v0"], "dst": ["v1"]},
            ]
        )

        store_done = next(item["cy"] for item in dones if item["op"] == "VSTS")
        compute_start = next(item["cy"] for item in starts if item["op"] == "VADDS")
        self.assertLess(compute_start, store_done)

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
