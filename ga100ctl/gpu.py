"""Read PCI sysfs attributes and NVIDIA's read-only management interface."""

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

# PCI ID Repository, snapshot 2026-02-12. A PCI ID does not encode HBM health.
CMP_170HX_IDS = {"2082", "20c2"}
FULL_UUID = re.compile(r"GPU-[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}", re.I)


def _bdf(value):
    match = re.fullmatch(r"([\da-f]{4,8}):([\da-f]{2}:[\da-f]{2}\.[0-7])", value, re.I)
    if match:
        return f"{int(match[1], 16):04x}:{match[2].lower()}"
    return None


def _read(path):
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def _field(gpu, path):
    value = (gpu.findtext(path) or "").strip()
    return value if value and value.lower() not in {"n/a", "unknown", "not supported", "[not supported]"} else None


def _number(value):
    if value is None:
        return None
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:\s+(?:W|C|MHz|MiB))?", value)
    return float(match[1]) if match else None


def _smi(*args):
    result = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True,
                            timeout=10, check=True)
    return result.stdout


def _dram_ecc(item, observed_at):
    """DRAM-only counters. NVIDIA used device_memory in older XML schemas."""
    def counter(modern, legacy):
        for path in (modern, legacy):
            value = _field(item, "ecc_errors/volatile/" + path)
            if value and re.fullmatch(r"[0-9]+", value):
                return int(value)
        return None

    return {"scope": "volatile_dram", "observed_at": observed_at,
            "mode": _field(item, "ecc_mode/current_ecc"),
            "corrected": counter("dram_correctable", "single_bit/device_memory"),
            "uncorrected": counter("dram_uncorrectable", "double_bit/device_memory")}


def _driver_inventory(selector=None):
    try:
        selection = ("-i", selector) if selector else ()
        xml = ElementTree.fromstring(_smi("-q", "-x", *selection))
    except (OSError, subprocess.SubprocessError, ElementTree.ParseError) as exc:
        return {}, f"nvidia-smi inventory unavailable ({type(exc).__name__})"

    by_bdf = {}
    observed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    for item in xml.findall("gpu"):
        bdf = _bdf(item.get("id", "") or item.findtext("pci/pci_bus_id", ""))
        if not bdf:
            continue
        power = (_field(item, "gpu_power_readings/instant_power_draw")
                 or _field(item, "gpu_power_readings/power_draw") or _field(item, "power_readings/power_draw"))
        by_bdf[bdf] = {
            "uuid": _field(item, "uuid"), "serial": _field(item, "serial"),
            "driver_name": _field(item, "product_name"), "driver_version": _field(xml, "driver_version"),
            "driver_cuda_compatibility": _field(xml, "cuda_version"), "vbios_version": _field(item, "vbios_version"),
            "mig_mode": _field(item, "mig_mode/current_mig"),
            "ecc": _dram_ecc(item, observed_at),
            "telemetry": {
                "memory_total_mib": _number(_field(item, "fb_memory_usage/total")),
                "memory_used_mib": _number(_field(item, "fb_memory_usage/used")),
                "temperature_c": _number(_field(item, "temperature/gpu_temp")), "power_w": _number(power),
                "graphics_clock_mhz": _number(_field(item, "clocks/graphics_clock")),
                "memory_clock_mhz": _number(_field(item, "clocks/mem_clock")),
                "pcie_generation": _field(item, "pci/pci_gpu_link_info/pcie_gen/current_link_gen"),
                "pcie_max_generation": _field(item, "pci/pci_gpu_link_info/pcie_gen/max_link_gen"),
            },
        }
    return by_bdf, None


def ecc_after_check(card):
    """Compare the discovery-time DRAM counters with a fresh scoped driver reading."""
    before = card.get("ecc")
    fields = ("corrected", "uncorrected")
    result = {"status": "unavailable", "before": before, "after": None,
              "delta": dict.fromkeys(fields)}
    bdf = _bdf(card.get("bdf") or "")
    if not bdf:
        return dict(result, detail="Missing device PCI address")
    uuid = card.get("uuid")
    selector = uuid if uuid and FULL_UUID.fullmatch(uuid) else bdf
    inventory, warning = _driver_inventory(selector)
    latest = inventory.get(bdf)
    if not latest or (uuid and latest["uuid"] != uuid):
        return dict(result, detail=warning or "Selected GPU was missing or changed identity")
    after = latest["ecc"]
    result["after"] = after
    if not before or not all((sample.get("mode") or "").lower() == "enabled"
                             for sample in (before, after)):
        return dict(result, detail="ECC mode was unavailable or disabled at a sample")
    comparable = [k for k in fields if before.get(k) is not None and after.get(k) is not None]
    if any(after[k] < before[k] for k in comparable):
        return dict(result, status="counter_reset", detail="A volatile counter decreased")
    for k in comparable:
        result["delta"][k] = after[k] - before[k]
    if comparable:
        result["status"] = "observed" if len(comparable) == len(fields) else "partial"
    return result


def discover(sysfs=Path("/sys/bus/pci/devices")):
    """Inventory NVIDIA display and processing accelerator functions even when there is no driver."""
    cards = []
    for path in sorted(Path(sysfs).iterdir()):
        bdf = _bdf(path.name)
        pci_class = (_read(path / "class") or "").lower()
        if not bdf or _read(path / "vendor") != "0x10de" or not pci_class.startswith(("0x03", "0x12")):
            continue
        def pci_id(attr):
            return (_read(path / attr) or "").lower().removeprefix("0x") or None
        device = pci_id("device")
        is_cmp = device in CMP_170HX_IDS
        driver = path / "driver"
        cards.append({
            "bdf": bdf, "vendor_id": "10de", "device_id": device,
            "subsystem_vendor_id": pci_id("subsystem_vendor"), "subsystem_device_id": pci_id("subsystem_device"),
            "revision": pci_id("revision"), "pci_class": pci_class, "cmp_170hx": is_cmp,
            "model": "GA100 [CMP 170HX]" if is_cmp else "NVIDIA GPU",
            "driver": driver.resolve().name if driver.is_symlink() else None,
            "pcie_link": {attr: _read(path / attr) for attr in (
                "current_link_speed", "current_link_width", "max_link_speed", "max_link_width")},
            "uuid": None, "serial": None, "telemetry": {},
        })
    if cards:
        driver_cards, warning = _driver_inventory()
        for card in cards:
            info = driver_cards.get(card["bdf"])
            if info:
                card.update(info)
                if info["driver_name"] and not card["cmp_170hx"]:
                    card["model"] = info["driver_name"]
            else:
                card["driver_warning"] = warning or "GPU missing from NVIDIA driver inventory"
    return cards


def nvlink_status(uuid):
    """Return the driver's status text because link formats vary by driver."""
    try:
        output = _smi("nvlink", "-s", "-i", uuid).strip()
        if not output:
            return {"status": "unavailable", "detail": "empty response"}
        # A driver may return success with a header or an unsupported message.
        statuses = re.findall(r"^\s*Link +\d+ *: *([^\r\n]*)", output, re.I | re.M)
        unavailable = {"", "n/a", "not supported", "unsupported", "unknown"}
        if not any(status.strip().strip("[]<>").lower() not in unavailable for status in statuses):
            return {"status": "unavailable", "detail": output}
        return {"status": "observed", "detail": output}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "detail": str(exc)}


def link_report(card, *, simulate=False):
    """Present PCI link attributes and one GPU's driver-reported NVLink status."""
    sysfs = card.get("pcie_link", {})
    pcie = {key: sysfs.get(key) for key in (
        "current_link_speed", "current_link_width", "max_link_speed", "max_link_width")}
    telemetry = card.get("telemetry", {})
    pcie["current_generation"] = telemetry.get("pcie_generation")
    pcie["max_generation"] = telemetry.get("pcie_max_generation")
    available = any(value and "unknown" not in str(value).lower() for value in pcie.values())
    pcie["status"] = "observed" if available else "unavailable"
    if simulate:
        if available:
            pcie["status"] = "simulated"
        nvlink = {"status": "unavailable", "detail": "No NVLink observation in the fixture"}
        state = "simulated"
    else:
        if card.get("uuid"):
            nvlink = nvlink_status(card["uuid"])
        else:
            nvlink = {"status": "unavailable", "detail": card.get("driver_warning") or "No driver UUID"}
        state = "observed" if available or nvlink["status"] == "observed" else "unavailable"
    return {"state": state, "bdf": card["bdf"], "uuid": card.get("uuid"),
            "driver": card.get("driver"), "pcie": pcie, "nvlink": nvlink}
