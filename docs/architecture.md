# Architecture and implementation plan

## Upstream review

The dalzyu project pairs Pico SDK C firmware with Python host tools. Its purpose is fault injection. The repository's Pico 2 W board assumptions need independent verification against the official SDK. The kate-land project targets a different controller, an STM32G431, and includes hardware designs and GA100 research tools. Kate's article gives results for individual cards. Neither a PCI product ID nor another card's measurements determine which partitions on a particular die work. Revisions, licenses, and review notes are in [the source inventory](upstream.md).

## Components and acceptance checks

| Order | Component | Implementation | Acceptance check |
| --- | --- | --- | --- |
| 1 | Ubuntu host CLI | Python standard library, argparse, SQLite, optional external GPU tools | Run discovery and report tests offline with a clean database |
| 2 | Pico provisioning | Official SDK board definitions, independent USB-only identity firmware, BOOTSEL volume discovery | Match the selected boot device and local image, verify image digest, record copy and runtime identity separately |
| 3 | GPU inventory | Linux PCI sysfs first, NVIDIA driver observations when available | Detect multiple GPUs, preserve subsystem IDs, support a missing driver and avoid slot-based card identity |
| 4 | Experiment history | Append timestamped observations and manual notes in SQLite | Preserve the first observed baseline, export evidence to JSON, isolate simulations |
| 5 | Validation | Explicitly targeted CUDA workloads and driver telemetry | Report actual bytes exercised, observed capabilities, software versions and unavailable measurements |
| 6 | Benchmarks | Optional local inference backends, per-run inputs and measurements | Preserve comparable run settings and distinguish measurements from fixtures |

Hardware-independent tests precede lab acceptance. Physical acceptance requires successful UF2 enumeration, a runtime firmware identity readout, and real workloads on the selected GPU. The first observed baseline can come from a card already modified by its operator. A manual observation and a driver measurement have different evidence sources.

The optional `ga100-capture-review` executable links Kate's Rust capture types and analysis library. The Python CLI passes local capture paths to that executable and preserves its exit code. Hardware inventory and link status use the Linux PCI device tree and NVIDIA driver.
