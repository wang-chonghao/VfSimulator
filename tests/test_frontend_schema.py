import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from api.frontend import (
    AccessKind,
    AffineExpression,
    AffineTerm,
    CanonicalInstruction,
    CanonicalLoop,
    CanonicalMembar,
    CanonicalOperand,
    CanonicalValue,
    CanonicalVfInfo,
    DependencyKind,
    DependencyRef,
    InductionVariable,
    InstructionClass,
    MemoryAccess,
    OperandRole,
    SourceLocation,
    StorageKind,
    validate_canonical_vf_info,
    canonical_vf_info_from_dict,
)


class CanonicalVfInfoValidatorTest(unittest.TestCase):
    def test_shared_v1_serialization_fixture(self):
        fixture = Path(__file__).parent / "fixtures/canonical_vf_info/v1_valid_loop.json"
        vf_info = canonical_vf_info_from_dict(json.loads(fixture.read_text()))
        result = validate_canonical_vf_info(vf_info)
        self.assertTrue(result.ok, result.diagnostics)
        self.assertEqual(vf_info.context[0].induction.variable_id, "i")

    def _values(self):
        return {
            "input.0": CanonicalValue("input.0", "input", StorageKind.UB, "fp32", (64,)),
            "output.0": CanonicalValue(
                "output.0", "output", StorageKind.UB, "fp32", (64,), "inst.store"
            ),
            "acc.0": CanonicalValue(
                "acc.0", "acc", StorageKind.REGISTER, "fp32", (64,), "inst.load"
            ),
        }

    def _memory(self, value_id, kind):
        return MemoryAccess(
            base_value_id=value_id,
            offset=AffineExpression(0, (AffineTerm("i", 1),)),
            access_kind=kind,
            span=64,
        )

    def _valid_vf_info(self) -> CanonicalVfInfo:
        load = CanonicalInstruction(
            instruction_id="inst.load",
            opcode="VLDS",
            instruction_class=InstructionClass.LOAD,
            form="fp32",
            inputs=(
                CanonicalOperand(
                    "input.0", OperandRole.MEMORY, "fp32", self._memory("input.0", AccessKind.READ)
                ),
            ),
            outputs=(CanonicalOperand("acc.0", OperandRole.DESTINATION, "fp32"),),
            source_location=SourceLocation("fixture.cce", 12, 3),
        )
        store = CanonicalInstruction(
            instruction_id="inst.store",
            opcode="VSTS",
            instruction_class=InstructionClass.STORE,
            form="fp32",
            inputs=(CanonicalOperand("acc.0", OperandRole.SOURCE, "fp32"),),
            outputs=(
                CanonicalOperand(
                    "output.0", OperandRole.MEMORY, "fp32", self._memory("output.0", AccessKind.WRITE)
                ),
            ),
            dependencies=(DependencyRef("inst.load", DependencyKind.DATA, 0),),
        )
        return CanonicalVfInfo(
            context=(
                CanonicalLoop("loop.row", InductionVariable("i"), "ROWS", 1, (), (load, store)),
                CanonicalMembar(
                    "membar.0",
                    "VST_VLD",
                    (DependencyRef("inst.store", DependencyKind.CONTROL),),
                ),
            ),
            values=self._values(),
            params={"ROWS": 4},
        )

    def test_valid_contract_passes_without_mutating_input(self):
        vf_info = self._valid_vf_info()
        before = copy.deepcopy(vf_info)
        result = validate_canonical_vf_info(vf_info)
        self.assertTrue(result.ok)
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(vf_info, before)

    def test_unknown_opcode_is_allowed_with_explicit_instruction_class(self):
        unknown = CanonicalInstruction(
            "inst.unknown",
            "VUNKNOWN",
            InstructionClass.COMPUTE,
            "fp32",
            (CanonicalOperand("acc.0", OperandRole.SOURCE, "fp32"),),
            (CanonicalOperand("acc.1", OperandRole.DESTINATION, "fp32"),),
        )
        values = {
            key: replace(value, producer_instruction_id=None)
            for key, value in self._values().items()
        }
        values["acc.1"] = replace(
            values["acc.0"],
            definition_id="acc.1",
            producer_instruction_id="inst.unknown",
        )
        vf_info = CanonicalVfInfo((unknown,), values)
        self.assertTrue(validate_canonical_vf_info(vf_info).ok)

    def test_unknown_reference_and_source_location_are_diagnosed(self):
        invalid = CanonicalInstruction(
            "inst.bad",
            "VADD",
            InstructionClass.COMPUTE,
            "fp32",
            (CanonicalOperand("missing", OperandRole.SOURCE),),
            (CanonicalOperand("acc.0", OperandRole.DESTINATION),),
            source_location=SourceLocation("bad.cce", 7, 9),
        )
        result = validate_canonical_vf_info(CanonicalVfInfo((invalid,), self._values()))
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0].code, "unknown_value_reference")
        self.assertEqual(result.errors[0].location.line, 7)

    def test_generic_semantic_mismatches_are_rejected(self):
        invalid = CanonicalInstruction(
            "same",
            "VLDS",
            InstructionClass.LOAD,
            "fp32",
            (
                CanonicalOperand(
                    "input.0",
                    "anything",  # type: ignore[arg-type]
                    "fp16",
                    self._memory("output.0", AccessKind.READ),
                ),
            ),
            (CanonicalOperand("acc.0", OperandRole.DESTINATION),),
        )
        loop = CanonicalLoop(
            "same", InductionVariable("i"), 1, 1, (), (invalid,)
        )
        result = validate_canonical_vf_info(CanonicalVfInfo((loop,), self._values()))
        codes = {error.code for error in result.errors}
        self.assertIn("duplicate_node_id", codes)
        self.assertIn("unsupported_operand_role", codes)
        self.assertIn("operand_dtype_mismatch", codes)
        self.assertIn("memory_base_operand_mismatch", codes)

    def test_affine_variables_must_be_declared(self):
        memory = MemoryAccess(
            "input.0",
            AffineExpression(0, (AffineTerm("undeclared", 64),)),
            AccessKind.READ,
            64,
        )
        invalid = CanonicalInstruction(
            "inst.load",
            "VLDS",
            InstructionClass.LOAD,
            "fp32",
            (CanonicalOperand("input.0", OperandRole.MEMORY, "fp32", memory),),
            (CanonicalOperand("acc.0", OperandRole.DESTINATION),),
        )
        result = validate_canonical_vf_info(CanonicalVfInfo((invalid,), self._values()))
        self.assertIn("undeclared_affine_variable", {error.code for error in result.errors})

    def test_membar_and_loop_parameter_validation(self):
        invalid = CanonicalVfInfo(
            (
                CanonicalLoop("loop.bad", InductionVariable("i"), "UNKNOWN", 0),
                CanonicalMembar("membar.bad", "ALL"),
            ),
            self._values(),
        )
        codes = {error.code for error in validate_canonical_vf_info(invalid).errors}
        self.assertIn("unresolved_parameter", codes)
        self.assertIn("invalid_loop_unroll", codes)
        self.assertIn("unsupported_membar_type", codes)


if __name__ == "__main__":
    unittest.main()
