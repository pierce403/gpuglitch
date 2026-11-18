# Electrical safety and recovery

For ordinary diagnostics, use a USB-connected Pico and a normally installed GPU. Soldering and GPU signal probes are unnecessary. Some CMP 170HX cards use passive cooling and rely on forced chassis airflow. Supply cooling appropriate to the board before running sustained workloads.

Disconnect the host from mains and remove independent supplies before installing or removing a GPU. Follow the board and PSU manufacturers' discharge instructions. Use an ESD-safe work surface and handle boards by their edges. Check for damage and loose conductive material before power-up. Never use an ohmmeter or continuity mode on an energized board. A continuity beep across a low-impedance rail alone cannot identify a short.

The Pico uses 3.3 V I/O. A connector's mechanical fit, a signal name or a continuity reading cannot establish electrical compatibility with a GPU. Overvoltage, back-powering through signal pins and unintended shorts can permanently damage either board. Keep the Pico and GPU connected through their ordinary host interfaces during this workflow. [Raspberry Pi microcontroller documentation](https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html)

## What can be restored

| Change | Recovery |
| --- | --- |
| Python host environment | Deactivate the project virtual environment. Retain the inventory database and JSON exports before removing a local installation. |
| Pico application firmware | Before flashing, identify a known-good image for that exact board. To restore it, enter BOOTSEL and reinstall it using its vendor's procedure. The immutable USB boot ROM remains available after replacing an ordinary application image. A fresh image may erase application data. |
| GPU workload | Stop the benchmark or validation process and let the card cool. Preserve its error log and the last available telemetry. A crashed or disconnected GPU may need operator-managed host recovery. |
| Physical damage or programmed eFuse | Neither a database restore nor a firmware reinstallation repairs electrical damage or reverses a one-time fuse change. |

Reports contain local hardware identifiers. Keep exports with the experiment records and review them before sharing.
