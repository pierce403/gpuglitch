"""Passive RP2350 USB inventory and provisioning for the ga100ctl USB diagnostic."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import time

BOARDS = ("pico2", "pico2_w")
VERSION = "1.0.0"
UF2 = "ga100ctl_usb.uf2"
MANIFEST = "ga100ctl_usb.json"
USB = Path("/sys/bus/usb/devices")
BLOCK = Path("/sys/dev/block")
MOUNTS = Path("/proc/self/mountinfo")


def _read(path):
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return None


def _mounts(block, mountinfo):
    for line in (_read(mountinfo) or "").splitlines():
        prefix, _, suffix = line.partition(" - ")
        fields = prefix.split()
        if len(fields) < 6 or fields[3] != "/" or not suffix.startswith("vfat "):
            continue
        root = (block / fields[2]).resolve()
        point = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4]))
        yield root, point, "rw" in fields[5].split(","), fields[2]


def discover(usb=None, block=None, mountinfo=None):
    """List BOOTSEL and SDK CDC endpoints. Read descriptors through Linux sysfs."""
    usb = USB if usb is None else Path(usb)
    mounts = list(_mounts(block or BLOCK, mountinfo or MOUNTS))
    found = []
    for device in sorted(usb.glob("*")):
        if _read(device / "idVendor") != "2e8a":
            continue
        pid = _read(device / "idProduct")
        if pid not in ("0009", "000f"):
            continue
        product = _read(device / "product")
        item = dict(usb_path=device.name, serial=_read(device / "serial"), product=product,
                    usb_id=f"2e8a:{pid}", mount=None, writable=False, block_id=None,
                    board=None, firmware_version=None, compatible=None)
        if pid == "000f":
            item["mode"] = "bootsel"
            candidates = [(p, rw, dev) for root, p, rw, dev in mounts
                          if device.resolve() in root.parents]
            if len(candidates) == 1:
                point, writable, dev = candidates[0]
                item.update(mount=str(point), writable=writable, block_id=dev,
                            rom_info=_read(point / "INFO_UF2.TXT"))
        else:
            item["mode"] = "sdk-cdc"
            match = re.fullmatch(r"ga100ctl/(pico2_w|pico2)/(\d+\.\d+\.\d+)", product or "")
            if match:
                item.update(mode="diagnostic", board=match[1], firmware_version=match[2],
                            compatible=match[2] == VERSION)
        found.append(item)
    return found


def _sources():
    candidates = (Path(__file__).resolve().parent.parent / "firmware",
                  Path(sys.prefix) / "share/ga100ctl/firmware")
    for directory in candidates:
        if all((directory / f).is_file() for f in ("CMakeLists.txt", "main.c")):
            return directory
    raise ValueError("Cannot locate packaged ga100ctl diagnostic firmware sources")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _fingerprint():
    return {f: _sha((_sources() / f).read_bytes()) for f in ("CMakeLists.txt", "main.c")}


def _board(board):
    if board not in BOARDS:
        raise ValueError("Choose board pico2 or pico2_w")


def _image(data, board):
    # picotool 2.2 prepends an RP2350 errata block to ARM Secure UF2 images.
    if not data or len(data) % 512 or len(data) > 4 * 1024 * 1024:
        raise ValueError("Invalid diagnostic UF2 size")
    app = bytearray()
    blocks = len(data) // 512
    for i in range(blocks):
        off = i * 512
        magic, magic2, flags, addr, size, num, total, family = struct.unpack_from("<8I", data, off)
        end = struct.unpack_from("<I", data, off + 508)[0]
        if (magic, magic2, size, end) != (0x0A324655, 0x9E5D5157, 256, 0x0AB16F30):
            raise ValueError("Invalid diagnostic UF2 block")
        if i == 0:
            if (flags, addr, num, total, family) != (0xa000, 0x10ffff00, 0, 2, 0xe48bff57):
                raise ValueError("Unexpected diagnostic UF2 prefix")
        else:
            index = i - 1
            if (flags, addr, num, total, family) != (
                0x2000, 0x10000000 + index * 256, index, blocks - 1, 0xe48bff59
            ):
                raise ValueError("Unexpected RP2350 application block")
            app.extend(data[off + 32:off + 288])
    if f"ga100ctl/{board}/{VERSION}\0".encode() not in app:
        raise ValueError("UF2 diagnostic descriptor does not match requested board/version")


def build(board, sdk, build_dir):
    _board(board)
    sdk = Path(sdk).resolve()
    if not (sdk / "pico_sdk_init.cmake").is_file():
        raise ValueError("Pass the path to a Pico SDK 2.2.0 checkout with TinyUSB")
    dest = Path(build_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest, prefix=".compile-") as tmp:
        tmp = Path(tmp)
        try:
            for cmd in (["cmake", "-S", str(_sources()), "-B", str(tmp), f"-DPICO_SDK_PATH={sdk}",
                         f"-DPICO_BOARD={board}", "-DCMAKE_BUILD_TYPE=Release"],
                        ["cmake", "--build", str(tmp), "--parallel", "4"]):
                subprocess.run(cmd, check=True, stdout=sys.stderr)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Diagnostic firmware build failed (exit {exc.returncode})") from exc
        data = (tmp / UF2).read_bytes()
        _image(data, board)
        record = dict(board=board, firmware_version=VERSION, sdk_version="2.2.0",
                      image=UF2, sha256=_sha(data), source_sha256=_fingerprint())
        (tmp / MANIFEST).write_text(json.dumps(record, indent=2) + "\n")
        os.replace(tmp / UF2, dest / UF2)
        os.replace(tmp / MANIFEST, dest / MANIFEST)
    return {**record, "build_dir": str(dest)}


def _load_image(build_dir, board):
    directory = Path(build_dir)
    record = json.loads((directory / MANIFEST).read_text())
    data = (directory / UF2).read_bytes()
    expected = dict(board=board, firmware_version=VERSION, sdk_version="2.2.0",
                    image=UF2, sha256=_sha(data), source_sha256=_fingerprint())
    if record != expected:
        raise ValueError("Local diagnostic image and manifest must match this board and source version")
    _image(data, board)
    return data


def _copy_image(device, data):
    # Pin the mount directory so an unplug or path replacement cannot redirect the write.
    directory = os.open(device["mount"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st_dev = os.fstat(directory).st_dev
        if f"{os.major(st_dev)}:{os.minor(st_dev)}" != device["block_id"]:
            raise ValueError("BOOTSEL mount changed during provisioning")
        info = os.open("INFO_UF2.TXT", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(info, "rb") as stream:
            if b"Board-ID: RP2350" not in stream.read(4096).splitlines():
                raise ValueError("BOOTSEL ROM does not report Board-ID RP2350")
        fd = os.open("GA100CTL.UF2", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            # The ROM may disconnect before Linux acknowledges the final write.
            # Preserve transport failure evidence while checking for re-enumeration.
            return {"errno": exc.errno, "message": str(exc)}
        return None
    finally:
        os.close(directory)


def flash(board, serial, build_dir, confirm=False):
    _board(board)
    if not confirm or not serial:
        raise ValueError("Flashing requires a board, BOOTSEL serial and explicit confirmation")
    devices = [d for d in discover() if d["mode"] == "bootsel" and d["serial"] == serial]
    if len(devices) != 1:
        raise ValueError("Expected one RP2350 BOOTSEL device matching the exact serial")
    device = devices[0]
    if not device["mount"] or not device["writable"]:
        raise ValueError("Selected BOOTSEL device needs a writable mounted UF2 volume")
    data = _load_image(build_dir, board)
    error = _copy_image(device, data)
    result = dict(serial=serial, board=board, usb_path=device["usb_path"], sha256=_sha(data),
                  status="copied_runtime_unverified")
    if error:
        result.update(status="transfer_error_runtime_unverified", transfer_error=error)
    for _ in range(16):
        for endpoint in discover():
            if (endpoint["usb_path"] == device["usb_path"] and endpoint["mode"] == "diagnostic"
                    and (endpoint.get("serial") or "").casefold() == serial.casefold()
                    and endpoint["board"] == board and endpoint["compatible"]):
                return {**result, "status": "runtime_verified", "runtime": endpoint}
        time.sleep(0.5)
    return result
