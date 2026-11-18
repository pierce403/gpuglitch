import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ga100ctl import gpu


class InventoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def device(self, bdf, pci_id, vendor="10de", cls="030200"):
        path = self.root / bdf
        path.mkdir()
        attrs = {"vendor": vendor, "device": pci_id, "class": cls,
                 "subsystem_vendor": vendor, "subsystem_device": "1234", "revision": "a1"}
        for key, val in attrs.items():
            (path / key).write_text("0x" + val + "\n")
        (path / "current_link_width").write_text("4\n")

    @patch("ga100ctl.gpu._smi")
    def test_inventory_joins_by_full_bdf(self, smi):
        self.device("0000:02:00.0", "2082")
        self.device("0001:02:00.0", "20c2", cls="120000")
        self.device("0000:03:00.0", "20b0")
        self.device("0000:02:00.1", "abcd", cls="040300")
        self.device("0000:04:00.0", "1234", vendor="8086")
        smi.return_value = """
        <nvidia_smi_log><driver_version>555</driver_version><cuda_version>12.5</cuda_version>
        <gpu id="00000001:02:00.0"><serial>board-b</serial><product_name>B</product_name></gpu>
        <gpu id="00000000:02:00.0"><serial>N/A</serial><uuid>GPU-A</uuid><product_name>A</product_name>
        <fb_memory_usage><total>8192 MiB</total></fb_memory_usage>
        <gpu_power_readings><instant_power_draw>100.25 W</instant_power_draw></gpu_power_readings>
        </gpu></nvidia_smi_log>"""
        first, third, second = gpu.discover(self.root)
        smi.assert_called_once_with("-q", "-x")
        self.assertEqual(first["uuid"], "GPU-A")
        self.assertIsNone(first["serial"])
        self.assertEqual(first["device_id"], "2082")
        self.assertEqual(first["telemetry"]["memory_total_mib"], 8192)
        self.assertEqual(first["telemetry"]["power_w"], 100.25)
        self.assertEqual(first["pcie_link"]["current_link_width"], "4")
        self.assertTrue(first["cmp_170hx"] and second["cmp_170hx"])
        self.assertEqual(second["serial"], "board-b")
        self.assertFalse(third["cmp_170hx"])
        self.assertIn("driver_warning", third)

    @patch("ga100ctl.gpu._smi")
    def test_driver_errors_preserve_pci_evidence(self, smi):
        self.device("0000:01:00.0", "20c2")
        for err in (FileNotFoundError(), subprocess.TimeoutExpired("nvidia-smi", 10), None):
            with self.subTest(error=err):
                smi.side_effect = err
                smi.return_value = "truncated XML"
                card, = gpu.discover(self.root)
                self.assertIsNone(card["uuid"])
                self.assertIn("driver_warning", card)

    @patch("ga100ctl.gpu._smi")
    def test_empty_sysfs(self, smi):
        self.assertEqual(gpu.discover(self.root), [])
        smi.assert_not_called()
