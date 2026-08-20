import unittest
from pathlib import Path

from core.ooo import Uop
from core.ooo_mainline import OoOCoreMainline
from core.param_db import ParamDB


ROOT = Path(__file__).resolve().parents[1]


class UbSharedIssueSlotsTest(unittest.TestCase):
    def setUp(self):
        self.db = ParamDB(base_dir=str(ROOT))
        uarch = dict(self.db.get_uarch())
        uarch.update(
            {
                "load_ports": 2,
                "store_ports": 1,
                "ub_slots": 2,
                "lsu_store_priority_preg_threshold": 1,
            }
        )
        self.core = OoOCoreMainline(uarch, self.db, dtype="fp32")
        self.load_profile = self.db.resolve_inst("VLDS", "fp32", "fp32")
        self.store_profile = self.db.resolve_inst("VSTS", "fp32", "fp32")

    def test_removed_policy_selector_is_rejected(self):
        uarch = dict(self.db.get_uarch())
        uarch["lsu_issue_policy"] = "oldest_ready"

        with self.assertRaisesRegex(ValueError, "has been removed"):
            OoOCoreMainline(uarch, self.db, dtype="fp32")

    def _uop(self, inst_id, op_class, *, state="ready"):
        is_load = op_class == "LOAD"
        uop = Uop(
            inst_id=inst_id,
            op="VLDS" if is_load else "VSTS",
            form="fp32",
            src=[],
            dst=[],
            preg_src=[],
            preg_dst=[],
            preg_old=[],
            profile=self.load_profile if is_load else self.store_profile,
            state=state,
            stream_seq=inst_id,
        )
        if not is_load:
            uop.producer_op_for_store = "VADD"
            uop.producer_form_for_store = "fp32"
            uop.producer_start_for_store = 0
        return uop

    def _issue(self, cycle=100):
        return self.core._issue_ready_lsu(cycle, 0, 0, 0)

    def test_two_loads_use_both_shared_slots(self):
        loads = [self._uop(0, "LOAD"), self._uop(1, "LOAD")]
        store = self._uop(2, "STORE")
        self.core.LSQ = [*loads, store]

        counts = self._issue()

        self.assertEqual(counts, (2, 0, 2))
        self.assertTrue(all(u.state == "running" for u in loads))
        self.assertEqual(store.state, "ready")

    def test_remaining_load_and_store_share_next_cycle(self):
        first_loads = [self._uop(0, "LOAD"), self._uop(1, "LOAD")]
        store = self._uop(2, "STORE")
        younger_load = self._uop(3, "LOAD")
        self.core.LSQ = [*first_loads, store, younger_load]
        self._issue(cycle=100)

        counts = self._issue(cycle=101)

        self.assertEqual(counts, (1, 1, 2))
        self.assertEqual(store.start_cycle, 101)
        self.assertEqual(younger_load.start_cycle, 101)

    def test_loads_have_priority_while_pregs_remain(self):
        store = self._uop(0, "STORE")
        loads = [self._uop(1, "LOAD"), self._uop(2, "LOAD")]
        self.core.LSQ = [*loads, store]

        counts = self._issue()

        self.assertEqual(counts, (2, 0, 2))
        self.assertIsNone(store.start_cycle)
        self.assertTrue(all(u.start_cycle == 100 for u in loads))

    def test_blocked_old_store_does_not_block_ready_loads(self):
        store = self._uop(0, "STORE", state="blocked")
        loads = [self._uop(1, "LOAD"), self._uop(2, "LOAD")]
        self.core.LSQ = [store, *loads]

        counts = self._issue()

        self.assertEqual(counts, (2, 0, 2))
        self.assertIsNone(store.start_cycle)
        self.assertTrue(all(u.start_cycle == 100 for u in loads))

    def test_load_priority_switches_to_store_only_when_no_preg_is_free(self):
        store = self._uop(0, "STORE")
        loads = [self._uop(1, "LOAD"), self._uop(2, "LOAD")]
        self.core.LSQ = [store, *loads]

        counts = self._issue(cycle=100)

        self.assertEqual(counts, (2, 0, 2))
        self.assertIsNone(store.start_cycle)

        store.state = "ready"
        self.core.LSQ = [store, self._uop(3, "LOAD"), self._uop(4, "LOAD")]
        self.core.freelist.clear()
        counts = self._issue(cycle=101)

        self.assertEqual(counts, (1, 1, 2))
        self.assertEqual(store.start_cycle, 101)

    def test_store_priority_preserves_age_within_store_class(self):
        older_store = self._uop(0, "STORE")
        younger_store = self._uop(1, "STORE")
        load = self._uop(2, "LOAD")
        self.core.LSQ = [younger_store, load, older_store]
        self.core.freelist.clear()

        counts = self._issue()

        self.assertEqual(counts, (1, 1, 2))
        self.assertEqual(older_store.start_cycle, 100)
        self.assertIsNone(younger_store.start_cycle)
        self.assertEqual(load.start_cycle, 100)

    def test_two_lsu_arbitration_phases_log_one_membar_block_per_cycle(self):
        class BlockingControlUnit:
            @staticmethod
            def blocks(_inst):
                return True

        load = self._uop(0, "LOAD")
        self.core.LSQ = [load]
        self.core.control_unit = BlockingControlUnit()
        logged = []
        self.core._log_membar_blocked = lambda u: logged.append(u.inst_id)
        blocked_ids = set()

        self.core._issue_ready_lsu(100, 0, 0, 0, blocked_ids)
        self.core._issue_ready_lsu(100, 0, 0, 0, blocked_ids)

        self.assertEqual(logged, [0])
        self.assertEqual(blocked_ids, {0})


if __name__ == "__main__":
    unittest.main()
