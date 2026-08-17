import unittest

from api.frontend import (
    AccessKind,
    AffineExpression,
    CanonicalMembar,
    CanonicalOperand,
    InductionVariable,
    InstructionClass,
    MemoryAccess,
    OperandRole,
    SourceLocation,
    StorageKind,
    VfInfoBuilder,
    VfInfoValidationError,
)
from api.input_api import InputAPI


class VfInfoBuilderTest(unittest.TestCase):
    def test_builds_valid_canonical_load(self):
        builder = VfInfoBuilder(source={"adapter": "unit-test"})
        builder.register_storage_object(
            "ub.input", storage=StorageKind.UB, shape=(64,)
        )
        builder.register_value(
            "input.0",
            logical_id="input",
            storage=StorageKind.UB,
            dtype="fp32",
            shape=(64,),
            storage_object_id="ub.input",
        )
        builder.register_value(
            "value.0",
            logical_id="value",
            storage=StorageKind.REGISTER,
            dtype="fp32",
            shape=(64,),
            producer_node_id="inst.load",
        )
        builder.add_instruction(
            "inst.load",
            opcode="VLDS",
            instruction_class=InstructionClass.LOAD,
            form="fp32",
            inputs=(
                CanonicalOperand(
                    "input.0",
                    OperandRole.MEMORY,
                    "fp32",
                    MemoryAccess(
                        "ub.input",
                        AffineExpression(constant=0),
                        AccessKind.READ,
                        span=64,
                    ),
                ),
            ),
            outputs=(
                CanonicalOperand(
                    "value.0", OperandRole.DESTINATION, "fp32"
                ),
            ),
            source_location=SourceLocation("builder_test.cce", 7, 3),
        )

        vf_info = builder.build()

        self.assertEqual(vf_info.context[0].opcode, "VLDS")
        self.assertEqual(vf_info.values["value.0"].producer_node_id, "inst.load")
        self.assertEqual(vf_info.source["adapter"], "unit-test")
        self.assertEqual(builder.build(), vf_info)

    def test_context_manager_builds_nested_loop(self):
        builder = InputAPI.new_vf_info_builder(params={"N": 2})
        with builder.loop(
            "loop.outer",
            induction=InductionVariable("i"),
            count="N",
        ):
            with builder.loop(
                "loop.inner",
                induction=InductionVariable("j"),
                count=2,
            ):
                builder.add_membar("barrier.0", barrier="VST_VLD")

        vf_info = builder.build()

        outer = vf_info.context[0]
        self.assertEqual(outer.loop_id, "loop.outer")
        self.assertEqual(outer.body[0].loop_id, "loop.inner")
        self.assertEqual(outer.body[0].body[0].instruction_id, "barrier.0")

    def test_add_loop_accepts_prebuilt_nodes(self):
        builder = VfInfoBuilder()
        builder.add_loop(
            "loop.0",
            induction=InductionVariable("i"),
            count=1,
            body=(CanonicalMembar("barrier.0", "VLD_VST"),),
        )

        vf_info = builder.build()

        self.assertEqual(vf_info.context[0].body[0].barrier, "VLD_VST")

    def test_build_exposes_structured_validation_errors(self):
        builder = VfInfoBuilder()
        builder.register_value(
            "src.0",
            logical_id="src",
            storage=StorageKind.REGISTER,
            dtype="fp32",
        )
        builder.register_value(
            "dst.0",
            logical_id="dst",
            storage=StorageKind.REGISTER,
            dtype="fp32",
            producer_node_id="inst.bad",
        )
        builder.add_instruction(
            "inst.bad",
            opcode="VADD",
            instruction_class=InstructionClass.COMPUTE,
            form="fp32",
            inputs=(CanonicalOperand("src.0", OperandRole.SOURCE, "fp32"),),
            outputs=(
                CanonicalOperand("dst.0", OperandRole.DESTINATION, "fp32"),
            ),
        )

        with self.assertRaises(VfInfoValidationError) as raised:
            builder.build()

        self.assertIn(
            "catalog_operand_count_mismatch",
            {item.code for item in raised.exception.diagnostics},
        )

    def test_duplicate_ids_and_failed_loop_scope_do_not_corrupt_builder(self):
        builder = VfInfoBuilder()
        builder.add_membar("barrier.0", barrier="VST_VLD")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            builder.add_membar("barrier.0", barrier="VLD_VST")

        try:
            with builder.loop(
                "loop.failed",
                induction=InductionVariable("i"),
                count=1,
            ):
                builder.add_membar("barrier.temporary", barrier="VST_VLD")
                builder.register_value(
                    "temporary.0",
                    logical_id="temporary",
                    storage=StorageKind.REGISTER,
                    dtype="fp32",
                )
                raise RuntimeError("abort loop")
        except RuntimeError:
            pass

        builder.add_membar("barrier.temporary", barrier="VLD_VST")
        builder.register_value(
            "temporary.0",
            logical_id="temporary",
            storage=StorageKind.REGISTER,
            dtype="fp32",
        )
        with builder.loop(
            "loop.failed",
            induction=InductionVariable("i"),
            count=1,
        ):
            builder.add_membar("barrier.final", barrier="VST_VLD")
        self.assertEqual(len(builder.build().context), 3)

    def test_failed_argument_materialization_does_not_reserve_node_ids(self):
        builder = VfInfoBuilder()

        with self.assertRaises(TypeError):
            builder.add_instruction(
                "inst.retry",
                opcode="VADD",
                instruction_class=InstructionClass.COMPUTE,
                form="fp32",
                inputs=None,
            )
        builder.add_instruction(
            "inst.retry",
            opcode="VUNKNOWN",
            instruction_class=InstructionClass.COMPUTE,
            form="fp32",
        )

        def failing_dependencies():
            raise RuntimeError("dependency generator failed")
            yield

        with self.assertRaisesRegex(RuntimeError, "dependency generator failed"):
            builder.add_membar(
                "barrier.retry",
                barrier="VST_VLD",
                dependencies=failing_dependencies(),
            )
        builder.add_membar("barrier.retry", barrier="VST_VLD")

        def failing_carried_values():
            raise RuntimeError("carried value generator failed")
            yield

        with self.assertRaisesRegex(RuntimeError, "carried value generator failed"):
            builder.add_loop(
                "loop.retry",
                induction=InductionVariable("i"),
                count=1,
                carried_values=failing_carried_values(),
            )
        builder.add_loop(
            "loop.retry",
            induction=InductionVariable("i"),
            count=1,
        )

        vf_info = builder.build()
        self.assertEqual(
            [node.instruction_id for node in vf_info.context[:2]],
            ["inst.retry", "barrier.retry"],
        )
        self.assertEqual(vf_info.context[2].loop_id, "loop.retry")


if __name__ == "__main__":
    unittest.main()
