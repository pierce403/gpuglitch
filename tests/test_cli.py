import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ga100ctl import cli
from ga100ctl.history import History


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "state" / "history.sqlite3"

    def invoke(self, *args, simulate=True, code=0, as_json=True):
        flags = ["--db", str(self.db)]
        if simulate:
            flags.append("--simulate")
        if as_json:
            flags.append("--json")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            observed = cli.main([*flags, *args])
        self.assertEqual(observed, code, stderr.getvalue())
        if code == 2:
            return stderr.getvalue()
        return json.loads(stdout.getvalue()) if as_json else stdout.getvalue()

    @mock.patch("ga100ctl.cli.gpu.discover", side_effect=AssertionError("hardware access"))
    @mock.patch("ga100ctl.cli.pico.discover", side_effect=AssertionError("hardware access"))
    def test_passive_simulation(self, _pico, _gpu):
        doctor = self.invoke("doctor")
        self.assertEqual(len(doctor["gpus"]), 2)
        self.assertEqual(self.invoke("pico", "list")["picos"], doctor["picos"])
        self.assertFalse(self.db.exists())

    def test_inventory_report_note_and_compare(self):
        self.assertIn("Multiple GPUs", self.invoke("gpu", "inspect", code=2))
        one = self.invoke("gpu", "inspect", "--gpu", "0000:41:00.0")
        two = self.invoke("gpu", "inspect", "--gpu", "0000:81:00.0")
        note = self.invoke("note", "--card", one["card_id"], "--topic", "physical", "Intake photo 1")
        report = self.invoke("report", "--card", one["card_id"])
        self.assertEqual(report["cards"][0]["baseline"], one["gpu"])
        self.assertEqual(report["events"][-1]["id"], note["event_id"])
        self.assertIn("Intake photo 1", self.invoke("report", as_json=False))
        result = self.invoke("compare", str(one["event_id"]), str(two["event_id"]))
        self.assertEqual(result["measurements"]["telemetry.memory_total_mib"], [8192, 10240])
        self.assertEqual(self.invoke("report", simulate=False)["events"], [])

    @mock.patch("ga100ctl.gpu.discover", side_effect=AssertionError("live device access"))
    @mock.patch("ga100ctl.gpu._smi", side_effect=AssertionError("live driver access"))
    def test_simulated_links_require_selected_card(self, _smi, _discover):
        self.assertIn("Multiple GPUs", self.invoke("gpu", "links", code=2))
        result = self.invoke("gpu", "links", "--gpu", "0000:41:00.0")
        self.assertEqual(result["state"], "simulated")
        self.assertEqual(result["pcie"]["current_link_width"], "4")
        self.assertEqual(result["nvlink"]["status"], "unavailable")
        self.assertFalse(self.db.exists())

    @mock.patch("ga100ctl.gpu.discover")
    @mock.patch("ga100ctl.gpu._smi")
    def test_live_links_distinguish_unavailable_and_inactive(self, smi, discover):
        card = {"bdf": "0000:41:00.0", "uuid": "GPU-TEST", "pcie_link": {}}
        discover.return_value = [card]
        smi.return_value = "GPU 0: Example GPU\n  Link 0: [Not Supported]\n"
        absent = self.invoke("gpu", "links", "--gpu", card["bdf"], simulate=False, code=1)
        self.assertEqual(absent["state"], "unavailable")
        smi.assert_called_once_with("nvlink", "-s", "-i", card["uuid"])
        smi.return_value = "GPU 0: Example GPU\n  Link 0: <inactive>\n"
        inactive = self.invoke("gpu", "links", "--gpu", card["uuid"], simulate=False)
        self.assertEqual(inactive["nvlink"]["status"], "observed")
        self.assertIn("<inactive>", inactive["nvlink"]["detail"])
        self.assertEqual(inactive["pcie"]["status"], "unavailable")
        self.assertFalse(self.db.exists())

    @mock.patch("ga100ctl.cli.workloads.validate", side_effect=AssertionError("live workload"))
    def test_simulated_workload_journal(self, _):
        result = self.invoke("validate", "--gpu", "0000:41:00.0")
        self.assertEqual(result["state"], "simulated")
        self.assertEqual(self.invoke("report")["events"][-1]["inputs"]["memory_mib"], 64)
        self.assertIn("distinct", self.invoke("benchmark", "--backend", "peer-copy", "--gpu",
                                               "0000:41:00.0", "--peer", "0000:41:00.0", code=2))

    def test_peer_run_appears_in_both_card_histories(self):
        result = self.invoke("benchmark", "--backend", "peer-copy", "--gpu", "0000:41:00.0",
                             "--peer", "0000:81:00.0")
        report = self.invoke("report")
        for card in report["cards"]:
            events = self.invoke("report", "--card", card["id"])["events"]
            self.assertEqual(events[-1]["id"], result["event_id"])
        self.assertEqual(len(report["cards"]), 2)

    def test_report_before_first_observation_is_readonly(self):
        self.assertEqual(self.invoke("report")["cards"], [])
        self.assertFalse(self.db.exists())

    def test_operator_label_when_driver_identity_is_missing(self):
        card = {"uuid": None, "serial": None, "bdf": "0000:41:00.0", "model": "CMP"}
        with mock.patch.object(cli.gpu, "discover", side_effect=[[card.copy()], [dict(card, bdf="0000:42:00.0")]]):
            first = self.invoke("gpu", "inspect", "--asset-tag", "LAB-017", simulate=False)
            second = self.invoke("gpu", "inspect", "--asset-tag", "LAB-017", simulate=False)
        self.assertEqual(first["card_id"], second["card_id"])
        self.assertEqual(second["gpu"]["bdf"], "0000:42:00.0")

    def test_live_workload_failure_retained(self):
        card = {"uuid": "test", "bdf": "0000:41:00.0", "model": "test"}
        with mock.patch.object(cli.gpu, "discover", return_value=[card]):
            result = self.invoke("validate", "--gpu", card["bdf"], simulate=False, code=1)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(self.invoke("report", simulate=False)["events"][-1]["state"], "unavailable")

    def test_flash_requires_confirmation_and_keeps_copy_outcome(self):
        args = ["pico", "flash", "--board", "pico2_w", "--serial", "BOOT", "--build-dir", "/tmp/unused"]
        with mock.patch.object(cli.pico, "flash", return_value={"status": "copied_runtime_unverified"}) as flash:
            self.assertIn("--confirm", self.invoke(*args, code=2, simulate=False))
            flash.assert_not_called()
            self.invoke(*args, "--confirm", simulate=False, code=1)
        event = self.invoke("report", simulate=False)["events"][-1]
        self.assertEqual(event["state"], "copied_runtime_unverified")
        self.assertEqual(event["inputs"]["serial"], "BOOT")

    def test_interruption_and_failure_journaled(self):
        with contextlib.closing(History(self.db, "live")) as history:
            for exc, state in [(KeyboardInterrupt(), "interrupted"), (ValueError("test"), "failed")]:
                with self.assertRaises(type(exc)):
                    cli.run_event(history, "test", {}, mock.Mock(side_effect=exc))
                self.assertEqual(history.report()["events"][-1]["state"], state)
