import concurrent.futures
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ga100ctl.history import History


class HistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "inventory.sqlite3"
        self.history = History(self.path, "live")
        self.addCleanup(self.history.close)
        self.card = {"bdf": "0000:41:00.0", "model": "CMP", "uuid": "GPU-A", "serial": "A"}

    def test_preserves_first_observation_and_joins_after_slot_move(self):
        first, _ = self.history.inspect(self.card)
        changed = dict(self.card, bdf="0000:81:00.0", serial=None)
        second, _ = self.history.inspect(changed)
        self.assertEqual(first, second)
        report = self.history.report(first)
        self.assertEqual(report["cards"][0]["baseline"], self.card)
        self.assertEqual(report["events"][1]["result"], changed)

    def test_slot_reuse_and_missing_identity(self):
        a, _ = self.history.inspect(self.card)
        b, _ = self.history.inspect(dict(self.card, uuid="GPU-B", serial="B"))
        c, _ = self.history.inspect(dict(self.card, uuid=None, serial="N/A"))
        d, _ = self.history.inspect(dict(self.card, uuid=None, serial="[N/A]"))
        self.assertEqual(len({a, b, c, d}), 4)

    def test_adds_new_identifier_and_rejects_conflicts_atomically(self):
        a, _ = self.history.inspect(dict(self.card, uuid=None))
        self.assertEqual(self.history.inspect(self.card)[0], a)
        self.history.inspect(dict(self.card, uuid="GPU-B", serial="B"))
        with self.assertRaises(ValueError):
            self.history.inspect(dict(self.card, serial="B"))
        with self.assertRaises(ValueError):
            self.history.inspect(dict(self.card, uuid="GPU-C"))
        self.assertEqual(len(self.history.report()["events"]), 3)

    def test_open_event_keeps_state_until_completed(self):
        card, event = self.history.begin("validate", {"memory_mib": 1}, self.card)
        with closing(History(self.path, "live")) as reader:
            initial = reader.report(card)["events"][-1]
            self.assertEqual(initial["state"], "running")
            self.assertIsNone(initial["finished"])
        self.history.finish(event, "unavailable", {"status": "unavailable"})
        with self.assertRaises(ValueError):
            self.history.finish(event, "passed", {})
        self.assertEqual(self.history.report(card)["events"][-1]["state"], "unavailable")

    def test_concurrent_first_inspections(self):
        def record(_):
            with closing(History(self.path, "live")) as other:
                return other.inspect(self.card)[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = set(pool.map(record, range(8)))
        self.assertEqual(len(ids), 1)
        self.assertEqual(len(self.history.report()["events"]), 8)

    def test_fixture_history_isolated(self):
        card, _ = self.history.inspect(self.card)
        with closing(History(self.path, "fixture-v1")) as fixture:
            self.assertEqual(fixture.report()["events"], [])
            with self.assertRaises(ValueError):
                fixture.note(card, "stability", "fixture")
