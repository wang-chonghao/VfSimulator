import json
import unittest
from pathlib import Path

from api.frontend import (
    DEFAULT_INSTRUCTION_CATALOG,
    FormRule,
    InstructionClass,
    OperandRole,
    StorageKind,
)


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
        self.assertTrue(difference.is_empty, difference)

    def test_timing_difference_reports_each_drift_category(self):
        payload = json.loads((ROOT / "configs/isa.json").read_text())
        instructions = payload["instructions"]
        instructions.pop("VADD")
        instructions["VFUTURE"] = {"op_class": "COMPUTE", "forms": {}}
        instructions["VPACK"]["forms"].pop("b32")

        difference = DEFAULT_INSTRUCTION_CATALOG.compare_timing_config(payload)
        self.assertEqual(difference.catalog_without_timing, {"VADD"})
        self.assertEqual(difference.timing_without_catalog, {"VFUTURE"})
        self.assertEqual(difference.forms_without_timing["VPACK"], {"b32"})


if __name__ == "__main__":
    unittest.main()
