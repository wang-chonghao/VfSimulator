import json
import tempfile
import unittest
from pathlib import Path

from api.frontend import (
    AccessKind,
    AffineExpression,
    CanonicalVfInfo,
    CanonicalCoreCompatibilityError,
    CanonicalOperand,
    CoreLoweringPass,
    InductionVariable,
    InstructionClass,
    MemoryAccess,
    OperandRole,
    StorageKind,
    VfInfoBuilder,
    VfInfoValidationError,
    canonical_vf_info_from_dict,
)
from api.simulator_costmodel import CoreVfCostModel


class CanonicalCoreLoweringTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).parent / "fixtures/canonical_vf_info"

    def _fixture(self, name):
        payload = json.loads((self.fixtures / name).read_text(encoding="utf-8"))
        return canonical_vf_info_from_dict(payload)

    def test_lowering_preserves_static_identity_and_memory_metadata(self):
        vf_info = self._fixture("v1_valid_loop.json")

        payload = CoreLoweringPass().lower(vf_info)

        loop = payload["program"][0]
        load = loop["body"][0]
        self.assertEqual(loop["name"], "loop.row")
        self.assertEqual(load["static_instruction_id"], "inst.load")
        self.assertEqual(load["src"], ["__canonical_ub__ub.input"])
        self.assertEqual(load["dst"], ["acc.0"])
        self.assertEqual(
            load["memory_accesses"][0]["offset"]["terms"],
            [{"variable_id": "i", "coefficient": 64}],
        )
        self.assertEqual(
            payload["values"]["__canonical_ub__ub.input"]["storage"],
            "UB",
        )

    def test_current_core_runs_unroll_one_canonical_program(self):
        vf_info = self._fixture("v1_valid_loop.json")
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_canonical_vf_info(vf_info)

        self.assertGreater(result["vf_end_cycle"], 0)
        self.assertEqual(result["normalization_stats"]["renamed_operands"], 0)
        self.assertEqual(result["canonicalization_stats"]["expanded_loops"], 0)

    def test_canonical_register_chain_and_membar_run_end_to_end(self):
        builder = VfInfoBuilder()
        for object_id in ("ub.input", "ub.output", "ub.after"):
            builder.register_storage_object(
                object_id,
                storage=StorageKind.UB,
                shape=(64,),
            )

        values = (
            ("input.0", "input", StorageKind.UB, None, "ub.input"),
            ("rhs.0", "rhs", StorageKind.REGISTER, None, None),
            ("loaded.0", "loaded", StorageKind.REGISTER, "inst.load", None),
            ("sum.0", "sum", StorageKind.REGISTER, "inst.add", None),
            ("output.1", "output", StorageKind.UB, "inst.store", "ub.output"),
            ("after.0", "after", StorageKind.UB, None, "ub.after"),
            ("reloaded.0", "reloaded", StorageKind.REGISTER, "inst.reload", None),
        )
        for definition_id, logical_id, storage, producer, storage_object in values:
            builder.register_value(
                definition_id,
                logical_id=logical_id,
                storage=storage,
                dtype="fp32",
                shape=(64,),
                producer_node_id=producer,
                storage_object_id=storage_object,
            )

        def memory_operand(value_id, object_id, access_kind):
            return CanonicalOperand(
                value_id,
                OperandRole.MEMORY,
                "fp32",
                MemoryAccess(
                    object_id,
                    AffineExpression(constant=0),
                    access_kind,
                    span=64,
                ),
            )

        with builder.loop(
            "loop.main",
            induction=InductionVariable("i"),
            count=1,
        ):
            builder.add_instruction(
                "inst.load",
                opcode="VLDS",
                instruction_class=InstructionClass.LOAD,
                form="fp32",
                inputs=(memory_operand("input.0", "ub.input", AccessKind.READ),),
                outputs=(CanonicalOperand("loaded.0", OperandRole.DESTINATION, "fp32"),),
            )
            builder.add_instruction(
                "inst.add",
                opcode="VADD",
                instruction_class=InstructionClass.COMPUTE,
                form="fp32",
                inputs=(
                    CanonicalOperand("loaded.0", OperandRole.SOURCE, "fp32"),
                    CanonicalOperand("rhs.0", OperandRole.SOURCE, "fp32"),
                ),
                outputs=(CanonicalOperand("sum.0", OperandRole.DESTINATION, "fp32"),),
            )
            builder.add_instruction(
                "inst.store",
                opcode="VSTS",
                instruction_class=InstructionClass.STORE,
                form="fp32",
                inputs=(CanonicalOperand("sum.0", OperandRole.SOURCE, "fp32"),),
                outputs=(memory_operand("output.1", "ub.output", AccessKind.WRITE),),
            )
            builder.add_membar("membar.store_load", barrier="VST_VLD")
            builder.add_instruction(
                "inst.reload",
                opcode="VLDS",
                instruction_class=InstructionClass.LOAD,
                form="fp32",
                inputs=(memory_operand("after.0", "ub.after", AccessKind.READ),),
                outputs=(CanonicalOperand("reloaded.0", OperandRole.DESTINATION, "fp32"),),
            )

        vf_info = builder.build()
        lowered = CoreLoweringPass().lower(vf_info)
        self.assertEqual(
            lowered["program"][0]["body"][3]["static_instruction_id"],
            "membar.store_load",
        )

        with tempfile.TemporaryDirectory() as out_dir:
            CoreVfCostModel(out_dir=out_dir).run_canonical_vf_info(vf_info)
            starts = [
                json.loads(line)
                for line in (Path(out_dir) / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            dones = [
                json.loads(line)
                for line in (Path(out_dir) / "done_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]

        load_starts = [item["cy"] for item in starts if item["op"] == "VLDS"]
        compute_start = next(item["cy"] for item in starts if item["op"] == "VADD")
        store_start = next(item["cy"] for item in starts if item["op"] == "VSTS")
        store_done = next(item["cy"] for item in dones if item["op"] == "VSTS")
        self.assertGreater(compute_start, load_starts[0])
        self.assertGreater(store_start, compute_start)
        self.assertGreaterEqual(load_starts[1], store_done)

    def test_loop_carried_program_is_rejected_until_dynamic_identity_lands(self):
        vf_info = self._fixture("v1_valid_loop_carried.json")

        payload = CoreLoweringPass().lower(vf_info)
        self.assertEqual(
            payload["program"][0]["carried_values"],
            [
                {
                    "logical_id": "acc",
                    "entry_value_id": "acc.entry",
                    "back_edge_value_id": "acc.back",
                    "exit_value_id": "acc.exit",
                }
            ],
        )

        with self.assertRaisesRegex(
            CanonicalCoreCompatibilityError,
            "loop-carried values",
        ):
            CoreVfCostModel().run_canonical_vf_info(vf_info)

    def test_invalid_canonical_input_is_validated_before_compatibility_check(self):
        invalid = CanonicalVfInfo(context=(object(),), values={})  # type: ignore[arg-type]

        with self.assertRaises(VfInfoValidationError):
            CoreVfCostModel().run_canonical_vf_info(invalid)


if __name__ == "__main__":
    unittest.main()
