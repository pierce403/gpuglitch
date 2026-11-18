import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from ga100ctl import pico


def image(board):
    prefix = bytearray(512)
    block = bytearray(512)
    for buf, args in ((prefix, (0xa000, 0x10ffff00, 256, 0, 2, 0xe48bff57)),
                      (block, (0x2000, 0x10000000, 256, 0, 1, 0xe48bff59))):
        struct.pack_into("<8I", buf, 0, 0x0a324655, 0x9e5d5157, *args)
        struct.pack_into("<I", buf, 508, 0x0ab16f30)
    name = f"ga100ctl/{board}/{pico.VERSION}".encode()
    block[32:32+len(name)] = name
    return bytes(prefix + block)


class PicoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.usb = self.root / "usb"
        self.block = self.root / "block"
        self.mounts = self.root / "mountinfo"
        self.block.mkdir()
        self.usb.mkdir()

    def endpoint(self, path, pid, product):
        dev = self.usb / path
        dev.mkdir()
        for field, text in dict(idVendor="2e8a", idProduct=pid, serial="SER", product=product).items():
            (dev / field).write_text(text)
        return dev

    def test_read_only_discovery_requires_usb_parent(self):
        device = self.endpoint("1-1", "000f", "RP2350 Boot")
        target = device / "1-1:1.0/block/sda/sda1"
        target.mkdir(parents=True)
        unrelated = self.root / "other block"
        unrelated.mkdir()
        (self.block / "8:1").symlink_to(target)
        (self.block / "8:2").symlink_to(unrelated)
        point = self.root / "Pico Drive"
        point.mkdir()
        (point / "INFO_UF2.TXT").write_text("Board-ID: RP2350\n")
        path = str(point).replace(" ", r"\040")
        self.mounts.write_text(f"10 9 8:2 / /foreign rw - vfat /dev/sdb1 rw\n"
                               f"11 9 8:1 / {path} ro - vfat /dev/sda1 rw\n")
        found, = pico.discover(self.usb, self.block, self.mounts)
        self.assertEqual(found["mount"], str(point))
        self.assertIsNone(found["board"])
        self.assertFalse(found["writable"])
        self.assertIn("Board-ID: RP2350", found["rom_info"])

    def test_compatible_incompatible_and_unknown_runtime(self):
        for index, name in enumerate(("ga100ctl/pico2_w/1.0.0", "ga100ctl/pico2/2.0.0", "Pico")):
            self.endpoint(f"2-{index}", "0009", name)
        found = pico.discover(self.usb, self.block, self.mounts)
        self.assertEqual([r["compatible"] for r in found], [True, False, None])
        self.assertEqual([r["mode"] for r in found], ["diagnostic", "diagnostic", "sdk-cdc"])

    def artifact(self):
        data = image("pico2")
        (self.root / pico.UF2).write_bytes(data)
        record = dict(board="pico2", firmware_version=pico.VERSION, sdk_version="2.2.0",
                      image=pico.UF2, sha256=hashlib.sha256(data).hexdigest(),
                      source_sha256=pico._fingerprint())
        (self.root / pico.MANIFEST).write_text(json.dumps(record))
        return data

    def test_integrity_and_board_checks(self):
        data = self.artifact()
        self.assertEqual(pico._load_image(self.root, "pico2"), data)
        with self.assertRaises(ValueError):
            pico._load_image(self.root, "pico2_w")
        (self.root / pico.UF2).write_bytes(b"x" * 1024)
        with self.assertRaises(ValueError):
            pico._load_image(self.root, "pico2")
        for malformed in (data[:512], image("pico2_w"), data[:-1], data[:-4] + bytes(4)):
            with self.subTest(length=len(malformed)), self.assertRaises(ValueError):
                pico._image(malformed, "pico2")

    def test_flash_refuses_missing_confirmation_duplicate_and_unwritable(self):
        dev = dict(serial="X", mode="bootsel", usb_path="2-1", mount="/any", writable=False)
        for records, serial, confirm in (([dev], "X", False), ([dev], "X", True),
                                         ([dev], "", True), ([dev], "Y", True),
                                         ([dev, dev], "X", True)):
            with patch.object(pico, "discover", return_value=records), patch.object(pico, "_copy_image") as cp:
                with self.assertRaises(ValueError):
                    pico.flash("pico2_w", serial, self.root, confirm)
                cp.assert_not_called()

    def test_copy_to_pinned_volume_and_verify_runtime(self):
        data = self.artifact()
        st_dev = self.root.stat().st_dev
        dev = dict(serial="X", mode="bootsel", mount=str(self.root), writable=True,
                   usb_path="2-1", block_id=f"{os.major(st_dev)}:{os.minor(st_dev)}")
        runtime = dict(serial="X", mode="diagnostic", usb_path="2-1", board="pico2", compatible=True)
        (self.root / "INFO_UF2.TXT").write_text("Board-ID: RP2350\n")
        with patch.object(pico, "discover", side_effect=[[dev], [runtime]]):
            report = pico.flash("pico2", "X", self.root, True)
        self.assertEqual(report["status"], "runtime_verified")
        self.assertEqual((self.root / "GA100CTL.UF2").read_bytes(), data)
        with self.assertRaises(FileExistsError):
            pico._copy_image(dev, data)

    def test_copy_rejects_wrong_mount_and_rom(self):
        dev = dict(mount=str(self.root), block_id="0:0")
        with self.assertRaisesRegex(ValueError, "mount changed"):
            pico._copy_image(dev, b"")
        st_dev = self.root.stat().st_dev
        dev["block_id"] = f"{os.major(st_dev)}:{os.minor(st_dev)}"
        (self.root / "INFO_UF2.TXT").write_text("Board-ID: RPI-RP2")
        with self.assertRaisesRegex(ValueError, "Board-ID"):
            pico._copy_image(dev, b"")
        self.assertFalse((self.root / "GA100CTL.UF2").exists())

    def test_post_copy_unverified_if_serial_changes(self):
        dev = dict(mode="bootsel", serial="X", usb_path="2-1", mount="ok", writable=True)
        runtime = dict(mode="diagnostic", serial="Z", usb_path="2-1", board="pico2", compatible=True)
        self.artifact()
        with patch.object(pico, "discover", side_effect=[[dev]] + [[runtime]] * 16), \
                patch.object(pico, "_copy_image", return_value=None), patch.object(pico.time, "sleep"):
            self.assertEqual(pico.flash("pico2", "X", self.root, True)["status"],
                             "copied_runtime_unverified")

    def test_disconnect_error_retained_with_runtime_check(self):
        self.artifact()
        dev = dict(mode="bootsel", serial="X", usb_path="2-1", mount="ok", writable=True)
        runtime = dict(mode="diagnostic", serial="X", usb_path="2-1", board="pico2", compatible=True)
        error = {"errno": 5, "message": "I/O error"}
        for second, expected in (([runtime], "runtime_verified"), ([], "transfer_error_runtime_unverified")):
            with patch.object(pico, "discover", side_effect=[[dev]] + [second] * 16), \
                    patch.object(pico, "_copy_image", return_value=error), patch.object(pico.time, "sleep"):
                report = pico.flash("pico2", "X", self.root, True)
            self.assertEqual(report["status"], expected)
            self.assertEqual(report["transfer_error"], error)

    def test_invalid_build_inputs(self):
        for board in ("pico2", "rp2040"):
            with self.assertRaises(ValueError):
                pico.build(board, self.root, self.root)
