import unittest

from core.vreg_live_range_normalization import normalize_program_vreg_live_ranges


class VregLiveRangeNormalizationTest(unittest.TestCase):
    def test_fresh_register_updates_explicit_values(self):
        program = [
            {
                "type": "loop",
                "iters": 32,
                "body": [
                    {"type": "inst", "op": "VADD", "src": ["s0", "s0"], "dst": ["v1"]},
                    {"type": "inst", "op": "VEXP", "src": ["v1"], "dst": ["v0"]},
                    {"type": "inst", "op": "VADD", "src": ["s1", "s1"], "dst": ["v1"]},
                    {"type": "inst", "op": "VADD", "src": ["v0", "v1"], "dst": ["v4"]},
                ],
            }
        ]
        values = {
            "v1": {"value_id": "v1", "storage": "Register", "dtype": "fp32", "shape": [64]},
            "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
        }

        normalized, new_values, stats = normalize_program_vreg_live_ranges(program, values)
        body = normalized[0]["body"]
        fresh = body[2]["dst"][0]

        self.assertNotIn(fresh, {"v0", "v1"})
        self.assertIn(fresh, new_values)
        self.assertEqual(new_values[fresh]["value_id"], fresh)
        self.assertEqual(new_values[fresh]["storage"], "Register")
        self.assertEqual(new_values[fresh]["dtype"], "fp32")
        self.assertEqual(new_values[fresh]["shape"], [64])
        self.assertGreater(stats["changed_fields"], 0)


if __name__ == "__main__":
    unittest.main()
