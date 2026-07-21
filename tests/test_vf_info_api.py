import unittest
from pathlib import Path

from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel
from api.vf_info import ValueInfo, VFInfo, VFInst, VFLoop, canonicalize_vf_info
from api.vf_lowering import VFInfoLowerer


ROOT = Path(__file__).resolve().parents[1]


class VfInfoApiTest(unittest.TestCase):
    def test_json_and_cce_adapters_return_mixed_dtype_vf_info(self):
        json_info = InputAPI.load_json_trace(ROOT / "VFtest/tadd_tcvt_tadd.json")
        cce_info = InputAPI.load_cce_file(ROOT / "cce_code/tadd_tcvt_tadd.dsl")

        for vf_info in (json_info, cce_info):
            forms = [inst.form for inst in vf_info.context[0].body]
            self.assertIn("f32_to_f16", forms)
            self.assertIn("fp16", forms)
            self.assertEqual(vf_info.values[next(
                value_id
                for value_id, value in vf_info.values.items()
                if value.dtype == "fp16" and value.storage == "Register"
            )].dtype, "fp16")

    def test_handwritten_vf_info_is_the_core_input(self):
        lhs_source = ValueInfo("lhs_input", "UB", "fp32", [16, 64])
        rhs_source = ValueInfo("rhs_input", "UB", "fp32", [16, 64])
        lhs = ValueInfo("lhs", "Register", "fp32", [64])
        rhs = ValueInfo("rhs", "Register", "fp32", [64])
        total = ValueInfo("total", "Register", "fp32", [64])
        output = ValueInfo("output", "UB", "fp32", [16, 64])
        vf_info = VFInfo(
            context=[
                VFLoop(
                    16,
                    body=[
                        VFInst("VLDS", [lhs_source], [lhs]),
                        VFInst("VLDS", [rhs_source], [rhs]),
                        VFInst("VADD", [lhs, rhs], [total]),
                        VFInst("VSTS", [total], [output]),
                    ],
                )
            ]
        )

        canonical = canonicalize_vf_info(vf_info)
        self.assertEqual(canonical.context[0].body[0].src, ["lhs_input"])
        self.assertEqual(canonical.context[0].body[0].form, "fp32")
        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-python-handwritten",
        ).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_mixed_dtype_python_prediction_matches_reference(self):
        vf_info = InputAPI.load_json_trace(ROOT / "VFtest/tadd_tcvt_tadd.json")
        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-python-test",
        ).run_vf_info(vf_info)
        self.assertEqual(result["cycles_executed"], 72)
        self.assertEqual(result["vf_end_cycle"], 84)

    def test_lowering_preserves_core_symbols_with_semantic_prefixes(self):
        vf_info = InputAPI.load_json_trace(
            ROOT
            / "regression_suite/inputs/json/vadd_fusion_singlev1_tests/I128"
            / "VADD_singleV1_fusion_128loops_4vadds.json"
        )
        lowered = VFInfoLowerer().lower(vf_info)
        first_store = lowered["program"][0]["body"][-1]
        second_load = lowered["program"][1]["body"][0]

        self.assertEqual(first_store["dst"], ["mem_inter_0"])
        self.assertEqual(second_load["src"], ["mem_inter_0"])
        self.assertEqual(first_store["src"], ["V1"])


if __name__ == "__main__":
    unittest.main()
