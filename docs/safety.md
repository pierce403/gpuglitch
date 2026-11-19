# Electrical safety and recovery

For ordinary diagnostics, use a USB-connected Pico and a normally installed GPU. Soldering and GPU signal probes are unnecessary. Some CMP 170HX cards use passive cooling and rely on forced chassis airflow. Supply cooling appropriate to the board before running sustained workloads.

Disconnect the host from mains and remove independent supplies before installing or removing a GPU. Follow the board and PSU manufacturers' discharge instructions. Use an ESD-safe work surface and handle boards by their edges. Check for damage and loose conductive material before power-up. Never use an ohmmeter or continuity mode on an energized board. A continuity beep across a low-impedance rail alone cannot identify a short.

## Cold card power cycle

An Ubuntu `reboot` restarts software and may reset PCIe, but does not establish that the GPU lost power. For fuse replay or a driver change that calls for a **full card power cycle**, the human operator should:

1. Finish the current operation and save its result. Bring any external fuse-programming supply or rig to its safe off state using that setup's own sequence; never interrupt an active program command.
2. Shut Ubuntu down cleanly with `sudo systemctl poweroff`. If connected by SSH, expect that session to end.
3. Once the host is off, turn off the PDU outlets or disconnect AC cords for **every** PSU that can feed the motherboard PCIe slot or the card's auxiliary power connectors. Include redundant server PSUs and any independent GPU supply. De-energize the Pico's USB source and other attached bench supplies according to their setup instructions so they cannot back-power the card.
4. Wait for the board and PSU manufacturers' specified discharge period. Do not use dark fans or LEDs alone as proof that every rail is off. Do not unplug the card, its PCIe power connectors, or soldered leads while energized.
5. Restore the supplies and boot the host. Record how and when card power was removed. Then perform the fresh locked-boot capture and separate driver/CUDA checks in [the unlock guide](unlocking.md#post-cold-boot-verification).

`systemctl poweroff` by itself cannot prove that slot, auxiliary, standby and bench power were removed. A remote operator needs physical access or out-of-band control of every relevant power feed to complete and attest this step. The post-boot verifier's `host_boot_id_changed` flag records a host boot change; it cannot prove that the card was unpowered.

The Pico uses 3.3 V I/O. A connector's mechanical fit, a signal name or a continuity reading cannot establish electrical compatibility with a GPU. Overvoltage, back-powering through signal pins and unintended shorts can permanently damage either board. Keep the Pico and GPU connected through their ordinary host interfaces during this workflow. [Raspberry Pi microcontroller documentation](https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html)

## What can be restored

| Change | Recovery |
| --- | --- |
| Python host environment | Deactivate the project virtual environment. Retain the inventory database and JSON exports before removing a local installation. |
| Pico application firmware | Before flashing, identify a known-good image for that exact board. To restore it, enter BOOTSEL and reinstall it using its vendor's procedure. The immutable USB boot ROM remains available after replacing an ordinary application image. A fresh image may erase application data. |
| GPU workload | Stop the benchmark or validation process and let the card cool. Preserve its error log and the last available telemetry. A crashed or disconnected GPU may need operator-managed host recovery. |
| Physical damage or programmed eFuse | Neither a database restore nor a firmware reinstallation repairs electrical damage or reverses a one-time fuse change. |

Reports contain local hardware identifiers. Keep exports with the experiment records and review them before sharing.
