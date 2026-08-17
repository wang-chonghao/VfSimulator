import copy
import json
import math
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
    CanonicalStorageObject,
    CanonicalValue,
    CanonicalVfInfo,
    DependencyKind,
    DependencyRef,
    InductionVariable,
    InstructionClass,
    LoopCarriedValue,
    MemoryAccess,
    OperandRole,
    SourceLocation,
    StorageKind,
    canonical_vf_info_from_dict,
    validate_canonical_vf_info,
)


class CanonicalVfInfoValidatorTest(unittest.TestCase):
    def _storage_objects(self):
        return {
            "ub.input": CanonicalStorageObject("ub.input", StorageKind.UB, (64,)),
            "ub.output": CanonicalStorageObject("ub.output", StorageKind.UB, (64,)),
        }

    def _values(self):
        return {
            "input.0": CanonicalValue(
                "input.0", "input", StorageKind.UB, "fp32", (64,),
                storage_object_id="ub.input",
            ),
            "output.0": CanonicalValue(
                "output.0", "output", StorageKind.UB, "fp32", (64,),
                producer_node_id="inst.store", storage_object_id="ub.output",
            ),
            "acc.0": CanonicalValue(
                "acc.0", "acc", StorageKind.REGISTER, "fp32", (64,),
                producer_node_id="inst.load",
            ),
        }

    def _memory(self, object_id, kind):
        return MemoryAccess(
            base_object_id=object_id,
            offset=AffineExpression(0, (AffineTerm("i", 1),)),
            access_kind=kind,
            span=64,
        )

    def _contract(self, context, values=None, *, params=None, uarch=None):
        return CanonicalVfInfo(
            context=tuple(context),
            values=self._values() if values is None else values,
            storage_objects=self._storage_objects(),
            params={} if params is None else params,
            uarch={} if uarch is None else uarch,
        )

    def _valid_vf_info(self):
        load = CanonicalInstruction(
            "inst.load", "VLDS", InstructionClass.LOAD, "fp32",
            inputs=(CanonicalOperand(
                "input.0", OperandRole.MEMORY, "fp32",
                self._memory("ub.input", AccessKind.READ),
            ),),
            outputs=(CanonicalOperand("acc.0", OperandRole.DESTINATION, "fp32"),),
            source_location=SourceLocation("fixture.cce", 12, 3),
        )
        store = CanonicalInstruction(
            "inst.store", "VSTS", InstructionClass.STORE, "fp32",
            inputs=(CanonicalOperand("acc.0", OperandRole.SOURCE, "fp32"),),
            outputs=(CanonicalOperand(
                "output.0", OperandRole.MEMORY, "fp32",
                self._memory("ub.output", AccessKind.WRITE),
            ),),
        )
        return self._contract(
            (
                CanonicalLoop(
                    "loop.row", InductionVariable("i"), "ROWS", 1, (), (load, store)
                ),
                CanonicalMembar("membar.0", "VST_VLD"),
            ),
            params={"ROWS": 4},
        )

    def test_shared_v1_serialization_fixture(self):
        fixture = Path(__file__).parent / "fixtures/canonical_vf_info/v1_valid_loop.json"
        vf_info = canonical_vf_info_from_dict(json.loads(fixture.read_text()))
        result = validate_canonical_vf_info(vf_info)
        self.assertTrue(result.ok, result.diagnostics)
        self.assertEqual(vf_info.context[0].induction.variable_id, "i")

        carried_fixture = (
            Path(__file__).parent
            / "fixtures/canonical_vf_info/v1_valid_loop_carried.json"
        )
        carried = canonical_vf_info_from_dict(json.loads(carried_fixture.read_text()))
        self.assertTrue(validate_canonical_vf_info(carried).ok)

        invalid_fixture = (
            Path(__file__).parent
            / "fixtures/canonical_vf_info/v1_invalid_loop_scope.json"
        )
        invalid = canonical_vf_info_from_dict(json.loads(invalid_fixture.read_text()))
        self.assertEqual(
            {item.code for item in validate_canonical_vf_info(invalid).errors},
            {"loop_back_edge_out_of_scope"},
        )

    def test_valid_contract_passes_without_mutating_input(self):
        vf_info = self._valid_vf_info()
        before = copy.deepcopy(vf_info)
        result = validate_canonical_vf_info(vf_info)
        self.assertTrue(result.ok, result.diagnostics)
        self.assertEqual(vf_info, before)

    def test_explicit_data_dependency_is_not_a_supported_kind(self):
        vf_info = self._valid_vf_info()
        loop = vf_info.context[0]
        store = replace(
            loop.body[1],
            dependencies=(DependencyRef("inst.load", "data", 0),),  # type: ignore[arg-type]
        )
        invalid = replace(vf_info, context=(replace(loop, body=(loop.body[0], store)),))
        codes = {item.code for item in validate_canonical_vf_info(invalid).errors}
        self.assertIn("unsupported_dependency_kind", codes)

    def test_plain_strings_are_not_accepted_as_enum_members(self):
        vf_info = self._valid_vf_info()
        loop = vf_info.context[0]
        load = loop.body[0]
        load_memory = load.inputs[0].memory_access
        self.assertIsNotNone(load_memory)
        load = replace(
            load,
            inputs=(replace(
                load.inputs[0],
                memory_access=replace(
                    load_memory,
                    access_kind="read",  # type: ignore[arg-type]
                ),
            ),),
        )
        store = replace(
            loop.body[1],
            dependencies=(
                DependencyRef(
                    "inst.load", "memory", 0  # type: ignore[arg-type]
                ),
            ),
        )
        invalid = replace(vf_info, context=(replace(loop, body=(load, store)),))
        codes = {item.code for item in validate_canonical_vf_info(invalid).errors}
        self.assertIn("unsupported_memory_access_kind", codes)
        self.assertIn("unsupported_dependency_kind", codes)

    def test_producer_must_actually_emit_definition(self):
        producer = CanonicalInstruction(
            "inst.producer", "VADD", InstructionClass.COMPUTE, "fp32"
        )
        consumer = CanonicalInstruction(
            "inst.consumer", "VADD", InstructionClass.COMPUTE, "fp32",
            (CanonicalOperand("ghost.0", OperandRole.SOURCE, "fp32"),),
        )
        barrier = CanonicalMembar("membar.0", "VST_VLD")
        barrier_consumer = CanonicalInstruction(
            "inst.after_barrier", "VADD", InstructionClass.COMPUTE, "fp32",
            (CanonicalOperand("barrier.0", OperandRole.SOURCE, "fp32"),),
        )
        duplicate_producer = CanonicalInstruction(
            "inst.duplicate", "VADD", InstructionClass.COMPUTE, "fp32", (),
            (
                CanonicalOperand("duplicate.0", OperandRole.DESTINATION, "fp32"),
                CanonicalOperand("duplicate.0", OperandRole.DESTINATION, "fp32"),
            ),
        )
        values = {
            "ghost.0": CanonicalValue(
                "ghost.0", "ghost", StorageKind.REGISTER, "fp32",
                producer_node_id="inst.producer",
            ),
            "barrier.0": CanonicalValue(
                "barrier.0", "barrier", StorageKind.REGISTER, "fp32",
                producer_node_id="membar.0",
            ),
            "duplicate.0": CanonicalValue(
                "duplicate.0", "duplicate", StorageKind.REGISTER, "fp32",
                producer_node_id="inst.duplicate",
            ),
        }
        codes = {
            item.code for item in validate_canonical_vf_info(
                CanonicalVfInfo(
                    (
                        producer,
                        consumer,
                        barrier,
                        barrier_consumer,
                        duplicate_producer,
                    ),
                    values,
                )
            ).errors
        }
        self.assertIn("producer_definition_not_emitted", codes)
        self.assertIn("invalid_value_producer_kind", codes)
        self.assertIn("definition_emitted_multiple_times", codes)

    def test_instruction_class_memory_access_matrix(self):
        vf_info = self._valid_vf_info()
        loop = vf_info.context[0]
        for instruction, invalid_class in (
            (loop.body[0], InstructionClass.COMPUTE),
            (loop.body[0], InstructionClass.STORE),
            (loop.body[1], InstructionClass.LOAD),
            (loop.body[1], InstructionClass.CONTROL),
        ):
            invalid_instruction = replace(
                instruction, instruction_class=invalid_class
            )
            invalid_loop = replace(loop, body=(invalid_instruction,))
            result = validate_canonical_vf_info(
                replace(vf_info, context=(invalid_loop,))
            )
            self.assertIn(
                "instruction_class_memory_access_mismatch",
                {item.code for item in result.errors},
            )

    def test_known_opcode_must_match_catalog_semantics(self):
        vf_info = self._valid_vf_info()
        loop = vf_info.context[0]
        invalid_class = replace(loop.body[0], opcode="VADD")
        invalid_form = replace(loop.body[0], opcode="VEXPDIF", form="fp16")

        class_codes = {
            item.code
            for item in validate_canonical_vf_info(
                replace(vf_info, context=(replace(loop, body=(invalid_class,)),))
            ).errors
        }
        form_codes = {
            item.code
            for item in validate_canonical_vf_info(
                replace(vf_info, context=(replace(loop, body=(invalid_form,)),))
            ).errors
        }
        self.assertIn("catalog_instruction_class_mismatch", class_codes)
        self.assertIn("catalog_operand_count_mismatch", class_codes)
        self.assertIn("catalog_instruction_class_mismatch", form_codes)
        self.assertIn("catalog_instruction_form_mismatch", form_codes)

    def test_unknown_opcode_is_allowed_with_explicit_instruction_class(self):
        unknown = CanonicalInstruction(
            "inst.unknown", "VUNKNOWN", InstructionClass.COMPUTE, "fp32",
            (CanonicalOperand("acc.0", OperandRole.SOURCE, "fp32"),),
            (CanonicalOperand("acc.1", OperandRole.DESTINATION, "fp32"),),
        )
        values = {
            "acc.0": CanonicalValue("acc.0", "acc", StorageKind.REGISTER, "fp32"),
            "acc.1": CanonicalValue(
                "acc.1", "acc", StorageKind.REGISTER, "fp32",
                producer_node_id="inst.unknown",
            ),
        }
        vf_info = CanonicalVfInfo((unknown,), values)
        self.assertTrue(validate_canonical_vf_info(vf_info).ok)

    def test_generic_semantic_mismatches_are_rejected(self):
        invalid = CanonicalInstruction(
            "same", "VLDS", InstructionClass.LOAD, "fp32",
            (CanonicalOperand(
                "input.0", "anything", "fp16",  # type: ignore[arg-type]
                self._memory("ub.output", AccessKind.READ),
            ),),
            (CanonicalOperand("acc.0", OperandRole.DESTINATION),),
        )
        loop = CanonicalLoop("same", InductionVariable("i"), 1, 1, (), (invalid,))
        codes = {
            item.code for item in validate_canonical_vf_info(
                self._contract((loop,))
            ).errors
        }
        self.assertIn("duplicate_node_id", codes)
        self.assertIn("unsupported_operand_role", codes)
        self.assertIn("operand_dtype_mismatch", codes)
        self.assertIn("memory_base_object_mismatch", codes)

    def test_affine_variables_must_be_declared(self):
        memory = MemoryAccess(
            "ub.input", AffineExpression(0, (AffineTerm("undeclared", 64),)),
            AccessKind.READ, 64,
        )
        invalid = CanonicalInstruction(
            "inst.load", "VLDS", InstructionClass.LOAD, "fp32",
            (CanonicalOperand("input.0", OperandRole.MEMORY, "fp32", memory),),
            (CanonicalOperand("acc.0", OperandRole.DESTINATION),),
        )
        codes = {
            item.code for item in validate_canonical_vf_info(
                self._contract((invalid,))
            ).errors
        }
        self.assertIn("undeclared_affine_variable", codes)

    def test_loop_carried_scope_and_metadata_are_validated(self):
        update = CanonicalInstruction(
            "inst.update", "VADD", InstructionClass.COMPUTE, "fp32",
            (CanonicalOperand("acc.entry", OperandRole.SOURCE, "fp32"),),
            (CanonicalOperand("acc.back", OperandRole.DESTINATION, "fp32"),),
        )
        loop = CanonicalLoop(
            "loop.acc", InductionVariable("i"), 4, 1,
            (LoopCarriedValue("acc", "acc.entry", "acc.back", "acc.exit"),),
            (update,),
        )
        values = {
            "acc.entry": CanonicalValue("acc.entry", "acc", StorageKind.REGISTER, "fp32"),
            "acc.back": CanonicalValue(
                "acc.back", "acc", StorageKind.REGISTER, "fp32",
                producer_node_id="inst.after",
            ),
            "acc.exit": CanonicalValue(
                "acc.exit", "acc", StorageKind.REGISTER, "fp16",
                producer_node_id="inst.after",
            ),
        }
        after = CanonicalInstruction(
            "inst.after", "VADD", InstructionClass.COMPUTE, "fp32", (),
            (CanonicalOperand("acc.back", OperandRole.DESTINATION, "fp32"),),
        )
        codes = {
            item.code for item in validate_canonical_vf_info(
                CanonicalVfInfo((loop, after), values)
            ).errors
        }
        self.assertIn("loop_back_edge_out_of_scope", codes)
        self.assertIn("loop_exit_producer_mismatch", codes)
        self.assertIn("loop_carried_type_mismatch", codes)

    def test_direct_python_values_must_fit_cross_language_types(self):
        invalid_memory = MemoryAccess(
            "ub.input",
            AffineExpression("bad", (AffineTerm("i", "bad"),)),  # type: ignore[arg-type]
            AccessKind.READ,
            True,
        )
        inst = CanonicalInstruction(
            "inst.load", "VLDS", InstructionClass.LOAD, "fp32",
            (CanonicalOperand("input.0", OperandRole.MEMORY, "fp32", invalid_memory),),
            (CanonicalOperand("acc.0", OperandRole.DESTINATION),),
            attributes={"huge": 2**100, "infinite": math.inf},
            dependencies=(DependencyRef("membar.before", DependencyKind.CONTROL, True),),
        )
        contract = self._contract(
            (CanonicalMembar("membar.before", "VST_VLD"), inst),
            params={"ROWS": 4},
        )
        codes = {item.code for item in validate_canonical_vf_info(contract).errors}
        self.assertIn("invalid_scalar_attribute", codes)
        self.assertIn("invalid_int64", codes)
        self.assertIn("invalid_memory_span", codes)
        self.assertIn("invalid_dependency_operand_index", codes)

    def test_membar_and_loop_parameter_validation(self):
        invalid = self._contract((
            CanonicalLoop("loop.bad", InductionVariable("i"), "UNKNOWN", 0),
            CanonicalMembar("membar.bad", "ALL"),
        ))
        codes = {item.code for item in validate_canonical_vf_info(invalid).errors}
        self.assertIn("unresolved_parameter", codes)
        self.assertIn("invalid_loop_unroll", codes)
        self.assertIn("unsupported_membar_type", codes)


if __name__ == "__main__":
    unittest.main()
