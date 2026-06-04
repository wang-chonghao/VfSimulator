from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.program_api import predict_from_program
from core.flatten import Flattener
from core.model_config import normalize_model_name
from core.program_ir import VfSimInst, VfSimLoop, VfSimMembar, VfSimProgram


def _vadd_oneloop_program() -> VfSimProgram:
    return VfSimProgram(
        dtype="fp32",
        params={"I": 64, "U": 2},
        body=[
            VfSimLoop(
                count="I",
                unroll="U",
                body=[
                    VfSimInst(op="VLDS", dst=["V1"], src=["memA"]),
                    VfSimInst(op="VADDS", dst=["V2"], src=["V1"]),
                    VfSimInst(op="VSTS", dst=["memB"], src=["V2"]),
                ],
            )
        ],
    )


class ProgramApiTest(unittest.TestCase):
    def test_program_payload_shape(self) -> None:
        program = VfSimProgram(
            dtype="fp16",
            params={"N": 8},
            body=[
                VfSimLoop(
                    count="N",
                    name="inner",
                    body=[
                        VfSimInst(op="VLDS", dst=["V1"], src=["memA"]),
                        VfSimMembar(),
                        VfSimInst(op="VSTS", dst=["memB"], src=["V1"]),
                    ],
                )
            ],
        )

        self.assertEqual(
            program.to_payload(),
            {
                "dtype": "fp16",
                "params": {"N": 8},
                "program": [
                    {
                        "type": "loop",
                        "iters": "N",
                        "unroll": 1,
                        "body": [
                            {"type": "inst", "op": "VLDS", "src": ["memA"], "dst": ["V1"]},
                            {"type": "membar", "barrier": "VST_VLD"},
                            {"type": "inst", "op": "VSTS", "src": ["V1"], "dst": ["memB"]},
                        ],
                        "name": "inner",
                    }
                ],
            },
        )

    def test_flattener_program_matches_legacy_json_trace(self) -> None:
        trace_path = ROOT / "VFtest" / "VADD_oneloop.json"
        with trace_path.open("r", encoding="utf-8") as f:
            trace = json.load(f)

        json_linear = Flattener(trace["params"]).flatten(trace["program"])
        program = _vadd_oneloop_program()
        program_linear = Flattener(program.params).flatten(program)

        self.assertEqual(program_linear, json_linear)

    def test_flattener_nested_program_keeps_loop_structure(self) -> None:
        program = VfSimProgram(
            params={"I": 2, "J": 3},
            body=[
                VfSimLoop(
                    count="I",
                    body=[
                        VfSimLoop(
                            count="J",
                            body=[VfSimInst(op="VADDS", dst=["V2"], src=["V1"])],
                        )
                    ],
                )
            ],
        )

        linear = Flattener(program.params).flatten(program)

        self.assertEqual(
            [node["type"] for node in linear],
            ["loop_begin", "loop_begin", "inst", "loop_end", "loop_end"],
        )
        self.assertEqual(
            [node["op"] for node in linear],
            ["VLOOPv2", "VLOOPv2", "VADDS", "VLOOPv2", "VLOOPv2"],
        )

    def test_predict_from_program_mainline_matches_legacy_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vfsim_test_mainline_") as tmp:
            result = predict_from_program(
                _vadd_oneloop_program(),
                out_dir=Path(tmp) / "mainline",
                model="mainline",
            )

        self.assertEqual(result["model"], "mainline")
        self.assertEqual(result["cycles"], 118)

    def test_predict_from_program_model_dispatch(self) -> None:
        cases = [
            ("theory", "theory_direct_issue", 118),
            ("theory_vloop_only", "theory_vloop_only", 117),
            ("theory_direct_issue", "theory_direct_issue", 118),
        ]
        with tempfile.TemporaryDirectory(prefix="vfsim_test_models_") as tmp:
            for model, expected_model, expected_cycles in cases:
                with self.subTest(model=model):
                    result = predict_from_program(
                        _vadd_oneloop_program(),
                        out_dir=Path(tmp) / model,
                        model=model,
                    )
                    self.assertEqual(result["model"], expected_model)
                    self.assertEqual(result["cycles"], expected_cycles)

    def test_model_aliases_and_invalid_model(self) -> None:
        self.assertEqual(normalize_model_name("queue_level4"), "mainline")
        self.assertEqual(normalize_model_name("level4"), "mainline")
        self.assertEqual(normalize_model_name("theory"), "theory_direct_issue")
        self.assertEqual(normalize_model_name("theoretical-limit"), "theory_direct_issue")
        self.assertEqual(
            normalize_model_name("theoretical-limit-vloop-only"),
            "theory_vloop_only",
        )

        with self.assertRaisesRegex(ValueError, "Unsupported VfSimulator model"):
            normalize_model_name("not_a_model")


if __name__ == "__main__":
    unittest.main()
