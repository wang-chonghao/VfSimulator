import json
import tempfile
import unittest
from pathlib import Path

from core.perfetto_trace import build_perfetto_trace, dump_perfetto_trace


class PerfettoTraceTest(unittest.TestCase):
    @staticmethod
    def _start_events():
        return [
            {
                "cy": 10,
                "inst_id": 0,
                "static_instruction_id": "load.0",
                "stream_seq": 0,
                "op": "VLDS",
                "form": "fp32",
                "op_class": "LOAD",
                "fu_type": None,
                "exu_port": None,
                "ready_cycle": 9,
                "src": ["ub.input"],
                "dst": ["value.0"],
                "preg_src": [],
                "preg_dst": ["p0"],
                "iteration_path": [],
            },
            {
                "cy": 20,
                "inst_id": 1,
                "static_instruction_id": "compute.0",
                "stream_seq": 1,
                "op": "VADD",
                "form": "fp32",
                "op_class": "COMPUTE",
                "fu_type": "ALU",
                "exu_port": 1,
                "ready_cycle": 19,
                "src": ["value.0", "value.1"],
                "dst": ["sum.0"],
                "preg_src": ["p0", "p1"],
                "preg_dst": ["p2"],
                "iteration_path": [{"loop_id": "loop.0", "iteration": 3}],
            },
            {
                "cy": 30,
                "inst_id": 2,
                "static_instruction_id": "store.0",
                "stream_seq": 2,
                "op": "VSTS",
                "form": "fp32",
                "op_class": "STORE",
                "fu_type": None,
                "exu_port": None,
                "ready_cycle": 29,
                "src": ["sum.0"],
                "dst": ["ub.output"],
                "preg_src": ["p2"],
                "preg_dst": [],
                "iteration_path": [],
            },
        ]

    @staticmethod
    def _done_events():
        return [
            {"cy": 19, "inst_id": 0},
            {"cy": 29, "inst_id": 1},
            {"cy": 39, "inst_id": 2},
        ]

    def test_builds_unit_tracks_and_complete_instruction_slices(self):
        payload = build_perfetto_trace(
            self._start_events(),
            self._done_events(),
            issue_ports=2,
            vf_end_cycle=45,
        )
        events = payload["traceEvents"]
        process_names = {
            event["args"]["name"]
            for event in events
            if event["ph"] == "M" and event["name"] == "process_name"
        }
        thread_names = {
            event["args"]["name"]
            for event in events
            if event["ph"] == "M" and event["name"] == "thread_name"
        }
        slices = [
            event
            for event in events
            if event["ph"] == "X" and event["cat"] != "vf"
        ]
        vf_slices = [
            event
            for event in events
            if event["ph"] == "X" and event["cat"] == "vf"
        ]

        self.assertEqual(process_names, {"VF", "Load Unit", "EXU Unit", "Store Unit"})
        self.assertEqual(
            thread_names,
            {"VF Lifetime", "Load Pipeline", "EXU0", "EXU1", "Store Pipeline"},
        )
        self.assertEqual([event["name"] for event in slices], [
            "VLDS.fp32",
            "VADD.fp32",
            "VSTS.fp32",
        ])
        self.assertEqual([event["dur"] for event in slices], [9, 9, 9])
        compute = slices[1]
        self.assertEqual((compute["pid"], compute["tid"]), (200, 1))
        self.assertEqual(compute["args"]["start_cycle"], 20)
        self.assertEqual(compute["args"]["done_cycle"], 29)
        self.assertEqual(compute["args"]["preg_src"], ["p0", "p1"])
        self.assertEqual(len(vf_slices), 1)
        self.assertEqual(
            (vf_slices[0]["ts"], vf_slices[0]["dur"]),
            (0, 45),
        )
        self.assertEqual(
            vf_slices[0]["args"],
            {"start_cycle": 0, "end_cycle": 45, "cycles": 45},
        )
        self.assertEqual(payload["displayTimeUnit"], "us")

    def test_dumped_trace_is_a_json_object_perfetto_can_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trace.json"
            dump_perfetto_trace(
                path,
                self._start_events(),
                self._done_events(),
                issue_ports=2,
                vf_end_cycle=45,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertIn("traceEvents", payload)
        self.assertTrue(any(event["ph"] == "X" for event in payload["traceEvents"]))
        self.assertTrue(
            any(
                event["ph"] == "X"
                and event["cat"] == "vf"
                and event["dur"] == 45
                for event in payload["traceEvents"]
            )
        )


if __name__ == "__main__":
    unittest.main()
