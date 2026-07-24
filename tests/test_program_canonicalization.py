import unittest

from core.ifu import IFUUnroll
from core.program_canonicalization import canonicalize_single_super_iteration_loops


class ProgramCanonicalizationTest(unittest.TestCase):
    def test_expands_one_super_iteration_in_abcabc_order(self):
        program = [
            {
                "type": "loop",
                "iters": 32,
                "unroll": 1,
                "body": [
                    {
                        "type": "loop",
                        "iters": "C",
                        "unroll": "U",
                        "body": [
                            {"type": "inst", "op": "VLDS", "dst": ["v0"], "src": ["mem0"]},
                            {"type": "inst", "op": "VADD", "dst": ["v1"], "src": ["v0", "v2"]},
                            {"type": "inst", "op": "VLDS", "dst": ["v3"], "src": ["mem1"]},
                            {"type": "inst", "op": "VSUB", "dst": ["v4"], "src": ["v1", "v3"]},
                            {"type": "inst", "op": "VSTS", "dst": ["mem1"], "src": ["v1"]},
                        ],
                    }
                ],
            }
        ]

        result, stats = canonicalize_single_super_iteration_loops(
            program,
            {"C": 2, "U": 2},
        )

        body = result[0]["body"]
        self.assertEqual(stats["expanded_loops"], 1)
        self.assertEqual(
            [node["op"] for node in body],
            ["VLDS", "VADD", "VLDS", "VSUB", "VSTS", "VLDS", "VADD", "VLDS", "VSUB", "VSTS"],
        )
        self.assertEqual(body[1]["src"][0], "v0_lane0")
        self.assertEqual(body[6]["src"][0], "v0_lane1")

    def test_keeps_loop_with_multiple_super_iterations(self):
        program = [
            {
                "type": "loop",
                "iters": 4,
                "unroll": 2,
                "body": [{"type": "inst", "op": "VADD", "dst": ["v1"], "src": ["v0"]}],
            }
        ]

        result, stats = canonicalize_single_super_iteration_loops(program)

        self.assertEqual(stats["expanded_loops"], 0)
        self.assertEqual(result[0]["type"], "loop")

    def test_expands_single_iteration_loop_without_unroll(self):
        program = [
            {
                "type": "loop",
                "iters": 1,
                "body": [{"type": "inst", "op": "VADD", "dst": ["v1"], "src": ["v0"]}],
            }
        ]

        result, stats = canonicalize_single_super_iteration_loops(program)

        self.assertEqual(stats["expanded_loops"], 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "inst")
        self.assertEqual(result[0]["src"], ["v0_lane0"])

    def test_ifu_unroll_preserves_interleaved_static_order(self):
        linear = [
            {"type": "loop_begin", "iters": 2, "unroll": 2},
            {"type": "inst", "op": "VLDS", "dst": ["v0"], "src": ["mem0"]},
            {"type": "inst", "op": "VADD", "dst": ["v1"], "src": ["v0", "v2"]},
            {"type": "inst", "op": "VLDS", "dst": ["v3"], "src": ["mem1"]},
            {"type": "inst", "op": "VSUB", "dst": ["v4"], "src": ["v1", "v3"]},
            {"type": "inst", "op": "VSTS", "dst": ["mem2"], "src": ["v4"]},
            {"type": "loop_end"},
        ]

        ifu = IFUUnroll(linear)
        emitted = []
        while not ifu.done():
            inst = ifu.next_inst()
            if inst is not None:
                emitted.append(inst)

        self.assertEqual(
            [inst["op"] for inst in emitted],
            ["VLDS", "VADD", "VLDS", "VSUB", "VSTS", "VLDS", "VADD", "VLDS", "VSUB", "VSTS"],
        )
        self.assertEqual([inst["lane"] for inst in emitted], [0] * 5 + [1] * 5)


if __name__ == "__main__":
    unittest.main()
