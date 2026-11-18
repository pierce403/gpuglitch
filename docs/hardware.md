# Connecting the diagnostic Pico

The diagnostic firmware uses one USB data cable. Either official Raspberry Pi Pico 2 model can be checked on the bench with the GPU in its normal PCIe slot. Leave the Pico headers open.

~~~text
Ubuntu host
  USB port ───── data-capable USB cable ───── Pico 2 or Pico 2 W
  PCIe slot ──── motherboard connector ────── GPU
~~~

1. Identify the board from its silkscreen or purchase record. Use the official board name when selecting an image. A USB identifier shared across RP2350 boards cannot identify the W variant.
2. Unplug the Pico. Run `ga100ctl doctor` to capture the host state. Hold BOOTSEL while plugging in the Pico, then release it. Linux should enumerate a USB mass-storage device. The RP2350 boot volume normally appears as `RP2350` and includes `INFO_UF2.TXT`. BOOTSEL enumeration checks the cable and ROM USB path.
3. Run `ga100ctl doctor` again and compare the USB observations. If empty, try a known-good data cable and a direct host port. Check whether Linux can see the device before troubleshooting mount permissions. A desktop may mount the volume automatically. A headless server may only enumerate it.
4. Follow the [provisioning procedure](provisioning.md) for the USB diagnostic image. Record the firmware version and compiled board target advertised after reboot alongside the physical board label. Record the GPU independently using the [quickstart](../README.md).

The research port describes separate board-level signals for I²C data, I²C clock, PCIe reset synchronization, and ground. Those lines have no assigned pins in this diagnostic setup. USB discovery says nothing about their continuity or voltage. The documented board-family discrepancy matters when evaluating the original publication. [Upstream review](upstream.md)
