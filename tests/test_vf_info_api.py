import unittest
import json
import tempfile
from pathlib import Path

from api.input_api import InputAPI
from api.input_symbols import (
    normalize_dtype,
    normalize_form,
    normalize_membar_type,
    normalize_opcode,
    specialize_opcode,
)
from api.simulator_costmodel import CoreVfCostModel
from api.vf_info import Membar, ValueInfo, VFInfo, VFInst, VFLoop, canonicalize_vf_info
from api.vf_lowering import VFInfoLowerer


ROOT = Path(__file__).resolve().parents[1]


class VfInfoApiTest(unittest.TestCase):
    def test_input_symbols_normalize_public_aliases(self):
        self.assertEqual(normalize_dtype("float32"), "fp32")
        self.assertEqual(normalize_dtype("fp64"), "fp64")
        self.assertEqual(normalize_form("fp32_to_s32"), "f32_to_s32")
        self.assertEqual(normalize_form("f16_to_s32"), "f16_to_s32")
        self.assertEqual(normalize_opcode("vld"), "VLDS")
        self.assertEqual(normalize_opcode("vunknown"), "VUNKNOWN")
        self.assertEqual(specialize_opcode("vcvt", "fp32_to_s32"), "VCVT_F32_TO_S32")
        self.assertEqual(specialize_opcode("vcvt", "f16_to_s32"), "VCVT")
        self.assertEqual(normalize_membar_type("SMEM_BAR.VLD_VST"), "VLD_VST")
        self.assertEqual(normalize_membar_type("SMEM_BAR.VV_ALL"), "VV_ALL")

    def test_canonicalize_vf_info_normalizes_external_symbols(self):
        vf_info = VFInfo(
            default_dtype="float32",
            values={
                "a": ValueInfo("a", "ub", "float32", [64]),
                "b": ValueInfo("b", "reg", "f32", [64]),
                "c": ValueInfo("c", "register", "s32", [64]),
            },
            context=[
                VFLoop(
                    1,
                    body=[
                        VFInst("vld", ["a"], ["b"], "float32"),
                        VFInst("vcvt", ["b"], ["c"]),
                        Membar("MEMBAR.VST_VLD"),
                    ],
                )
            ],
        )

        canonical = canonicalize_vf_info(vf_info)
        body = canonical.context[0].body
        self.assertEqual(canonical.default_dtype, "fp32")
        self.assertEqual(canonical.values["a"].storage, "UB")
        self.assertEqual(canonical.values["b"].storage, "Register")
        self.assertEqual(canonical.values["c"].dtype, "int32")
        self.assertEqual(body[0].name, "VLDS")
        self.assertEqual(body[0].form, "fp32")
        self.assertEqual(body[1].name, "VCVT_F32_TO_S32")
        self.assertEqual(body[1].form, "f32_to_s32")
        self.assertEqual(body[2].type, "VST_VLD")

    def test_canonicalize_keeps_uncovered_symbols_for_core_fallback(self):
        canonical = canonicalize_vf_info(
            VFInfo(
                default_dtype="fp64",
                values={
                    "a": ValueInfo("a", "Register", "fp16", [64]),
                    "b": ValueInfo("b", "Register", "int32", [64]),
                },
                context=[
                    VFInst("vcvt", ["a"], ["b"]),
                    VFInst("vunknown", ["a"], ["b"], "fp64"),
                    Membar("SMEM_BAR.VV_ALL"),
                ],
            )
        )
        body = canonical.context
        self.assertEqual(body[0].name, "VCVT")
        self.assertEqual(body[0].form, "f16_to_s32")
        self.assertEqual(body[1].name, "VUNKNOWN")
        self.assertEqual(body[1].form, "fp64")
        self.assertEqual(body[2].type, "VV_ALL")

    def test_legacy_core_payload_still_uses_paramdb_fallback(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
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
                    ],
                }
            ],
        }
        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-legacy-fallback",
        )._run_lowered_payload(payload)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_json_adapter_allows_legacy_symbols_for_core_fallback(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "a": {"value_id": "a", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "b": {"value_id": "b", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {"type": "inst", "op": "VUNKNOWN", "form": "fp32", "src": ["a"], "dst": ["b"]}
            ],
        }
        from api.json_adapter import JsonVfInfoAdapter

        vf_info = JsonVfInfoAdapter.from_payload(payload)
        self.assertEqual(vf_info.context[0].name, "VUNKNOWN")

    def test_run_payload_writes_model_warnings_for_fallback(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "a": {"value_id": "a", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "b": {"value_id": "b", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VUNKNOWN", "form": "fp32", "src": ["a"], "dst": ["b"]}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "out"
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir).run_payload(payload)
            warnings = json.loads((out_dir / "model_warnings.json").read_text(encoding="utf-8"))
        kinds = {item["kind"] for item in warnings["instruction_fallback_warnings"]}
        self.assertIn("unsupported_isa_op", kinds)

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

    def test_core_payload_uses_explicit_storage_not_name_prefix(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "input_a": {"value_id": "input_a", "storage": "UB", "dtype": "fp32", "shape": [1, 64]},
                "input_b": {"value_id": "input_b", "storage": "UB", "dtype": "fp32", "shape": [1, 64]},
                "lhs": {"value_id": "lhs", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "rhs": {"value_id": "rhs", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "sum": {"value_id": "sum", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "output": {"value_id": "output", "storage": "UB", "dtype": "fp32", "shape": [1, 64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["input_a"], "dst": ["lhs"]},
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["input_b"], "dst": ["rhs"]},
                        {"type": "inst", "op": "VADD", "form": "fp32", "src": ["lhs", "rhs"], "dst": ["sum"]},
                        {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["sum"], "dst": ["output"]},
                    ],
                }
            ],
        }

        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-explicit-storage-test",
        )._run_lowered_payload(payload)
        self.assertGreater(result["vf_end_cycle"], 0)

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
