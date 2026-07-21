from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vfsimulator import VfSimInst, VfSimLoop, VfSimProgram, predict_from_program
from vfsimulator.core.ifu import IFUUnroll
from vfsimulator.core.model_config import normalize_model_name
from vfsimulator.core.program_canonicalization import canonicalize_single_super_iteration_loops


class VfsimulatorPackageTest(unittest.TestCase):
    def test_namespaced_import_and_prediction(self) -> None:
        program = VfSimProgram(
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

        with tempfile.TemporaryDirectory(prefix="vfsim_pkg_test_") as tmp:
            result = predict_from_program(program, out_dir=Path(tmp) / "mainline")

        self.assertEqual(result["cycles"], 118)
        self.assertEqual(result["model"], "mainline")

    def test_theory_alias_in_namespaced_package(self) -> None:
        self.assertEqual(normalize_model_name("theory"), "theory_direct_issue")

    def test_namespaced_program_preserves_inst_form(self) -> None:
        program = VfSimProgram(
            dtype="fp32",
            params={"I": 16, "U": 1},
            body=[
                VfSimLoop(
                    count="I",
                    unroll="U",
                    body=[
                        VfSimInst(op="VLDS", dst=["V1"], src=["mem0"], form="fp32"),
                        VfSimInst(op="VCVT_F32_TO_F16", dst=["V2"], src=["V1"], form="f32_to_f16"),
                        VfSimInst(op="VSTS", dst=["mem1"], src=["V2"], form="fp16"),
                    ],
                )
            ],
        )

        body = program.to_payload()["program"][0]["body"]

        self.assertEqual(
            [(inst["op"], inst.get("form")) for inst in body],
            [
                ("VLDS", "fp32"),
                ("VCVT_F32_TO_F16", "f32_to_f16"),
                ("VSTS", "fp16"),
            ],
        )

    def test_namespaced_unroll_and_single_super_iteration_canonicalization(self) -> None:
        body = [
            {"type": "inst", "op": "VLDS", "dst": ["v0"], "src": ["mem0"]},
            {"type": "inst", "op": "VADD", "dst": ["v1"], "src": ["v0", "v2"]},
            {"type": "inst", "op": "VLDS", "dst": ["v3"], "src": ["mem1"]},
            {"type": "inst", "op": "VSUB", "dst": ["v4"], "src": ["v1", "v3"]},
            {"type": "inst", "op": "VSTS", "dst": ["mem2"], "src": ["v4"]},
        ]
        program = [{"type": "loop", "iters": 2, "unroll": 2, "body": body}]

        canonical, stats = canonicalize_single_super_iteration_loops(program)
        self.assertEqual(stats["expanded_loops"], 1)
        self.assertEqual(
            [inst["op"] for inst in canonical],
            ["VLDS", "VLDS", "VADD", "VADD", "VLDS", "VLDS", "VSUB", "VSUB", "VSTS", "VSTS"],
        )

        linear = [{"type": "loop_begin", "iters": 2, "unroll": 2}, *body, {"type": "loop_end"}]
        ifu = IFUUnroll(linear)
        emitted = []
        while not ifu.done():
            inst = ifu.next_inst()
            if inst is not None:
                emitted.append(inst)
        self.assertEqual([inst["op"] for inst in emitted], [inst["op"] for inst in canonical])


if __name__ == "__main__":
    unittest.main()
