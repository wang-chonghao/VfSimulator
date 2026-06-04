from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vfsimulator import VfSimInst, VfSimLoop, VfSimProgram, predict_from_program
from vfsimulator.core.model_config import normalize_model_name


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


if __name__ == "__main__":
    unittest.main()
