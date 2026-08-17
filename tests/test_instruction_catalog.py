import json
import copy
import unittest
from pathlib import Path

from api.frontend import (
    DEFAULT_INSTRUCTION_CATALOG,
    FormRule,
    InstructionClass,
    OperandRole,
    StorageKind,
    instruction_catalog_from_dict,
)
from tools.generate_instruction_catalog_cpp import render_catalog_cpp


ROOT = Path(__file__).resolve().parents[1]


class InstructionCatalogTest(unittest.TestCase):
    def test_alias_class_and_signature_are_declared_once(self):
        load = DEFAULT_INSTRUCTION_CATALOG.lookup("vld")
        store = DEFAULT_INSTRUCTION_CATALOG.lookup("vst")
        vpack = DEFAULT_INSTRUCTION_CATALOG.lookup("vpack")
        vsstb = DEFAULT_INSTRUCTION_CATALOG.lookup("vsstb")

        self.assertEqual(load.opcode, "VLDS")
        self.assertEqual(load.instruction_class, InstructionClass.LOAD)
        self.assertEqual(load.operands[1].role, OperandRole.MEMORY)
        self.assertEqual(load.operands[1].storage, StorageKind.UB)
        self.assertEqual(store.instruction_class, InstructionClass.STORE)
        self.assertEqual(vpack.fixed_form, "b32")
        self.assertEqual(vsstb.fixed_form, "b16")

    def test_virtual_conversion_specializes_known_forms_only(self):
        vcvt = DEFAULT_INSTRUCTION_CATALOG.lookup("VCVT")
        self.assertTrue(vcvt.virtual)
        self.assertEqual(vcvt.form_rule, FormRule.CONVERSION)
        self.assertEqual(
            DEFAULT_INSTRUCTION_CATALOG.specialize("vcvt", "f32_to_bf16"),
            "VCVT_F32_TO_BF16",
        )
        self.assertEqual(
            DEFAULT_INSTRUCTION_CATALOG.specialize("vcvt", "f16_to_s32"),
            "VCVT",
        )

    def test_unknown_opcode_remains_available_for_timing_fallback(self):
        self.assertEqual(
            DEFAULT_INSTRUCTION_CATALOG.canonical_opcode("vfuture"),
            "VFUTURE",
        )
        self.assertIsNone(DEFAULT_INSTRUCTION_CATALOG.lookup("vfuture"))

    def test_catalog_and_current_timing_config_do_not_drift(self):
        payload = json.loads((ROOT / "configs/isa.json").read_text())
        difference = DEFAULT_INSTRUCTION_CATALOG.compare_timing_config(payload)
        self.assertFalse(difference.has_semantic_conflicts, difference)
        self.assertIn("b16", difference.semantic_forms_without_timing["VADD"])
        self.assertIn("b32", difference.semantic_forms_without_timing["VADD"])

    def test_timing_difference_reports_each_drift_category(self):
        payload = json.loads((ROOT / "configs/isa.json").read_text())
        instructions = payload["instructions"]
        instructions.pop("VADD")
        instructions["VFUTURE"] = {"op_class": "COMPUTE", "forms": {}}
        instructions["VPACK"]["forms"]["fp64"] = {}
        instructions["VMUL"]["op_class"] = "STORE"

        difference = DEFAULT_INSTRUCTION_CATALOG.compare_timing_config(payload)
        self.assertEqual(difference.catalog_without_timing, {"VADD"})
        self.assertEqual(difference.timing_without_catalog, {"VFUTURE"})
        self.assertEqual(
            difference.timing_forms_without_semantics["VPACK"], {"fp64"}
        )

    def test_timing_difference_reports_instruction_class_mismatch(self):
        payload = json.loads((ROOT / "configs/isa.json").read_text())
        payload["instructions"]["VADD"]["op_class"] = "STORE"
        difference = DEFAULT_INSTRUCTION_CATALOG.compare_timing_config(payload)
        self.assertEqual(
            difference.instruction_class_mismatches["VADD"],
            ("compute", "store"),
        )

    def test_catalog_rejects_invalid_declarations(self):
        source = json.loads(
            (ROOT / "configs/instruction_catalog.json").read_text()
        )
        mutations = {
            "negative_index": lambda data: data["signatures"]["binary"][0].update(
                argument_index=-1
            ),
            "duplicate_index": lambda data: data["signatures"]["binary"][1].update(
                argument_index=0
            ),
            "output_role": lambda data: data["signatures"]["binary"][0].update(
                role="source"
            ),
            "missing_fixed_form": lambda data: data["instructions"]["VPACK"].pop(
                "fixed_form"
            ),
            "unknown_specialization": lambda data: data["instructions"]["VCVT"][
                "specializations"
            ].update(f32_to_f64="VCVT_F32_TO_F64"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                invalid = copy.deepcopy(source)
                mutate(invalid)
                with self.assertRaises(ValueError):
                    instruction_catalog_from_dict(invalid)

    def test_generated_cpp_catalog_is_up_to_date(self):
        payload = json.loads(
            (ROOT / "configs/instruction_catalog.json").read_text()
        )
        generated = (
            ROOT / "api/native/generated/InstructionCatalogData.inc"
        ).read_text()
        self.assertEqual(generated, render_catalog_cpp(payload))


if __name__ == "__main__":
    unittest.main()
