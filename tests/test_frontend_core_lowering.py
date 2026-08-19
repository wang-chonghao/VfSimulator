import json
import tempfile
import unittest
from pathlib import Path

from api.frontend import (
    AccessKind,
    AffineExpression,
    CanonicalVfInfo,
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
from core.flatten import Flattener
from core.ifu import IFUUnroll
from core.ooo_mainline import OoOCoreMainline
from core.param_db import ParamDB


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

    def _loop_carried_with_exit_consumer(self, *, count=4):
        payload = json.loads(
            (self.fixtures / "v1_valid_loop_carried.json").read_text(
                encoding="utf-8"
            )
        )
        payload["context"][0]["count"] = count
        payload["values"]["out.0"] = {
            "definition_id": "out.0",
            "logical_id": "out",
            "storage": "Register",
            "dtype": "fp32",
            "producer_node_id": "inst.after",
        }
        payload["context"].append(
            {
                "kind": "instruction",
                "instruction_id": "inst.after",
                "opcode": "VEXP",
                "instruction_class": "compute",
                "form": "fp32",
                "inputs": [
                    {"value_id": "acc.exit", "role": "source", "dtype": "fp32"}
                ],
                "outputs": [
                    {"value_id": "out.0", "role": "destination", "dtype": "fp32"}
                ],
            }
        )
        return canonical_vf_info_from_dict(payload)

    def _lower_and_expand(self, vf_info, count):
        payload = CoreLoweringPass().lower(vf_info)
        dynamic = IFUUnroll(
            Flattener(dict(vf_info.params)).flatten(payload["program"]),
            dict(vf_info.params),
        ).take(count)
        return payload, dynamic

    def _rename_dynamic(self, payload, dynamic):
        db = ParamDB(base_dir=str(Path(__file__).parents[1]))
        core = OoOCoreMainline(
            dict(db.get_uarch()),
            db,
            dtype="fp32",
            values=payload["values"],
        )
        for inst in dynamic:
            core.accept(inst)
        return list(core.ROB)

    def test_unroll_one_loop_carried_uses_previous_dynamic_definition(self):
        vf_info = self._loop_carried_with_exit_consumer()

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

        dynamic = IFUUnroll(
            Flattener(dict(vf_info.params)).flatten(payload["program"]),
            dict(vf_info.params),
        ).take(5)
        updates = dynamic[:4]
        self.assertEqual(
            [item["src"][0] for item in updates],
            ["acc.entry", "acc.back", "acc.back", "acc.back"],
        )
        self.assertEqual(
            [item["iteration_path"] for item in updates],
            [
                [
                    {
                        "loop_id": "loop.acc",
                        "iteration": index,
                        "induction_variable": "i",
                        "induction_value": index,
                    }
                ]
                for index in range(4)
            ],
        )
        self.assertEqual(dynamic[4]["src"], ["acc.back"])
        self.assertEqual(dynamic[4]["iteration_path"], [])

        db = ParamDB(base_dir=str(Path(__file__).parents[1]))
        core = OoOCoreMainline(
            dict(db.get_uarch()),
            db,
            dtype="fp32",
            values=payload["values"],
        )
        for inst in dynamic:
            core.accept(inst)
        update_uops = [uop for uop in core.ROB if uop.op == "VADD"]
        self.assertEqual(
            [uop.static_instruction_id for uop in update_uops],
            ["inst.update"] * 4,
        )
        self.assertEqual(
            [uop.iteration_path for uop in update_uops],
            [
                [
                    {
                        "loop_id": "loop.acc",
                        "iteration": index,
                        "induction_variable": "i",
                        "induction_value": index,
                    }
                ]
                for index in range(4)
            ],
        )
        self.assertEqual(
            [uop.stream_seq for uop in update_uops],
            [0, 1, 2, 3],
        )
        for previous, current in zip(update_uops, update_uops[1:]):
            self.assertEqual(current.preg_src[0], previous.preg_dst[0])
        after = next(uop for uop in core.ROB if uop.op == "VEXP")
        self.assertEqual(after.preg_src[0], update_uops[-1].preg_dst[0])
        self.assertEqual(after.static_instruction_id, "inst.after")
        self.assertEqual(after.iteration_path, [])

        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_canonical_vf_info(vf_info)
            history = json.loads(
                (Path(out_dir) / "sim_history.json").read_text(encoding="utf-8")
            )
        self.assertGreater(result["vf_end_cycle"], 0)
        first_update_event = next(
            item
            for item in history
            if item["op"] == "VADD" and item["static_instruction_id"] == "inst.update"
        )
        self.assertEqual(
            first_update_event["iteration_path"],
            [
                {
                    "loop_id": "loop.acc",
                    "iteration": 0,
                    "induction_variable": "i",
                    "induction_value": 0,
                }
            ],
        )
        self.assertEqual(first_update_event["stream_seq"], 0)

    def test_non_default_induction_is_preserved_in_dynamic_identity(self):
        payload = json.loads(
            (self.fixtures / "v1_valid_loop_carried.json").read_text(
                encoding="utf-8"
            )
        )
        payload["context"][0]["induction"] = {
            "variable_id": "row",
            "start": 10,
            "step": -2,
        }
        vf_info = canonical_vf_info_from_dict(payload)

        lowered, dynamic = self._lower_and_expand(vf_info, 4)
        self.assertEqual(
            [item["iteration_path"] for item in dynamic],
            [
                [
                    {
                        "loop_id": "loop.acc",
                        "iteration": index,
                        "induction_variable": "row",
                        "induction_value": 10 - 2 * index,
                    }
                ]
                for index in range(4)
            ],
        )
        uops = self._rename_dynamic(lowered, dynamic)
        for previous, current in zip(uops, uops[1:]):
            self.assertEqual(current.preg_src[0], previous.preg_dst[0])

        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_canonical_vf_info(vf_info)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_zero_iteration_loop_exit_uses_entry_definition(self):
        vf_info = self._loop_carried_with_exit_consumer(count=0)
        payload = CoreLoweringPass().lower(vf_info)

        dynamic = IFUUnroll(
            Flattener(dict(vf_info.params)).flatten(payload["program"]),
            dict(vf_info.params),
        ).take(2)

        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0]["op"], "VEXP")
        self.assertEqual(dynamic[0]["src"], ["acc.entry"])

    def test_serial_loop_exit_becomes_next_loop_dynamic_entry(self):
        vf_info = canonical_vf_info_from_dict(
            {
                "schema_version": 1,
                "values": {
                    "acc.entry": {
                        "definition_id": "acc.entry",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                    },
                    "rhs.entry": {
                        "definition_id": "rhs.entry",
                        "logical_id": "rhs",
                        "storage": "Register",
                        "dtype": "fp32",
                    },
                    "l1.back": {
                        "definition_id": "l1.back",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                        "producer_node_id": "inst.l1",
                    },
                    "l1.exit": {
                        "definition_id": "l1.exit",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                        "producer_node_id": "loop.l1",
                    },
                    "l2.back": {
                        "definition_id": "l2.back",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                        "producer_node_id": "inst.l2",
                    },
                    "l2.exit": {
                        "definition_id": "l2.exit",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                        "producer_node_id": "loop.l2",
                    },
                },
                "context": [
                    self._accumulator_loop(
                        "loop.l1", "inst.l1", "i", "acc.entry", "l1.back", "l1.exit"
                    ),
                    self._accumulator_loop(
                        "loop.l2", "inst.l2", "j", "l1.exit", "l2.back", "l2.exit"
                    ),
                ],
            }
        )

        payload, dynamic = self._lower_and_expand(vf_info, 4)
        self.assertEqual(
            [inst["src"][0] for inst in dynamic],
            ["acc.entry", "l1.back", "l1.back", "l2.back"],
        )
        uops = self._rename_dynamic(payload, dynamic)
        self.assertEqual(uops[1].preg_src[0], uops[0].preg_dst[0])
        self.assertEqual(uops[2].preg_src[0], uops[1].preg_dst[0])
        self.assertEqual(uops[3].preg_src[0], uops[2].preg_dst[0])

    def test_nested_loop_exit_becomes_outer_loop_back_edge(self):
        inner_loop = self._accumulator_loop(
            "loop.inner",
            "inst.inner",
            "j",
            "outer.entry",
            "inner.back",
            "inner.exit",
            count=1,
        )
        vf_info = canonical_vf_info_from_dict(
            {
                "schema_version": 1,
                "values": {
                    "outer.entry": {
                        "definition_id": "outer.entry",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                    },
                    "rhs.entry": {
                        "definition_id": "rhs.entry",
                        "logical_id": "rhs",
                        "storage": "Register",
                        "dtype": "fp32",
                    },
                    "inner.back": {
                        "definition_id": "inner.back",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                        "producer_node_id": "inst.inner",
                    },
                    "inner.exit": {
                        "definition_id": "inner.exit",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                        "producer_node_id": "loop.inner",
                    },
                    "outer.exit": {
                        "definition_id": "outer.exit",
                        "logical_id": "acc",
                        "storage": "Register",
                        "dtype": "fp32",
                        "producer_node_id": "loop.outer",
                    },
                },
                "context": [
                    {
                        "kind": "loop",
                        "loop_id": "loop.outer",
                        "induction": {"variable_id": "i", "start": 0, "step": 1},
                        "count": 2,
                        "unroll": 1,
                        "carried_values": [
                            {
                                "logical_id": "acc",
                                "entry_value_id": "outer.entry",
                                "back_edge_value_id": "inner.exit",
                                "exit_value_id": "outer.exit",
                            }
                        ],
                        "body": [inner_loop],
                    }
                ],
            }
        )

        payload, dynamic = self._lower_and_expand(vf_info, 2)
        self.assertEqual(
            [inst["src"][0] for inst in dynamic],
            ["outer.entry", "inner.back"],
        )
        self.assertEqual(
            [inst["iteration_path"] for inst in dynamic],
            [
                [
                    {
                        "loop_id": "loop.outer",
                        "iteration": 0,
                        "induction_variable": "i",
                        "induction_value": 0,
                    },
                    {
                        "loop_id": "loop.inner",
                        "iteration": 0,
                        "induction_variable": "j",
                        "induction_value": 0,
                    },
                ],
                [
                    {
                        "loop_id": "loop.outer",
                        "iteration": 1,
                        "induction_variable": "i",
                        "induction_value": 1,
                    },
                    {
                        "loop_id": "loop.inner",
                        "iteration": 0,
                        "induction_variable": "j",
                        "induction_value": 0,
                    },
                ],
            ],
        )
        uops = self._rename_dynamic(payload, dynamic)
        self.assertEqual(uops[1].preg_src[0], uops[0].preg_dst[0])

    @staticmethod
    def _accumulator_loop(
        loop_id,
        instruction_id,
        induction,
        entry_value_id,
        back_edge_value_id,
        exit_value_id,
        *,
        count=2,
    ):
        return {
            "kind": "loop",
            "loop_id": loop_id,
            "induction": {"variable_id": induction, "start": 0, "step": 1},
            "count": count,
            "unroll": 1,
            "carried_values": [
                {
                    "logical_id": "acc",
                    "entry_value_id": entry_value_id,
                    "back_edge_value_id": back_edge_value_id,
                    "exit_value_id": exit_value_id,
                }
            ],
            "body": [
                {
                    "kind": "instruction",
                    "instruction_id": instruction_id,
                    "opcode": "VADD",
                    "instruction_class": "compute",
                    "form": "fp32",
                    "inputs": [
                        {"value_id": entry_value_id, "role": "source", "dtype": "fp32"},
                        {"value_id": "rhs.entry", "role": "source", "dtype": "fp32"},
                    ],
                    "outputs": [
                        {
                            "value_id": back_edge_value_id,
                            "role": "destination",
                            "dtype": "fp32",
                        }
                    ],
                }
            ],
        }

    def test_invalid_canonical_input_is_validated_before_compatibility_check(self):
        invalid = CanonicalVfInfo(context=(object(),), values={})  # type: ignore[arg-type]

        with self.assertRaises(VfInfoValidationError):
            CoreVfCostModel().run_canonical_vf_info(invalid)


if __name__ == "__main__":
    unittest.main()
