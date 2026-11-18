# GA100 bench checklist

The next bench session has two independent tasks. Verify that the USB diagnostic image boots on the identified Pico, and capture the first CMP 170HX baseline on a GPU host. Update the checkboxes as evidence arrives. Summarize each day's work in [the daily logs](logs/).

## Pico on the USB bench

- [ ] Read the board marking and record whether it is a Pico 2 W or Pico 2. The RP2350 BOOTSEL descriptor is common to both. Match the selected build and manifest to the board. [Hardware setup](docs/hardware.md)
- [ ] On a host with a writable BOOTSEL volume, install the USB diagnostic image on the selected Pico. Keep the flash event ID, image SHA-256 and observed runtime version. Mark this complete when the CLI reports `runtime_verified`. [Provisioning and recovery](docs/provisioning.md)
- [ ] Disconnect and reconnect the Pico normally. Verify that `ga100ctl pico list` still shows the same serial, expected build target and compatible firmware. Record the outcome in that day's log.

## First CMP 170HX intake and validation

- [ ] On the GPU host, run `ga100ctl doctor` and `ga100ctl gpu list`. Select the card by its full PCI address, inspect it, and record the inventory ID and inspection event ID. If driver identifiers are unavailable, use the physical asset tag on each inspection. Record label information, cooling and known prior modifications. [Intake procedure](README.md#record-a-card)
- [ ] Run a bounded CUDA validation on that card. Start with `--memory-mib 64 --seconds 0`. Record the bytes exercised, driver-visible capacity, SM count, PCIe link state and any unavailable checks. Preserve the event even on failure. [Validation commands](docs/benchmarking.md#verify-a-baseline)
- [ ] With suitable cooling, run a timed stability test. Record duration, completed products, sampled temperature, power and clocks. Export the card's JSON report and check that it includes the original inspection and the validation outcomes. [Electrical safety](docs/safety.md)

## Performance comparisons

- [ ] Run one local model on an idle card with either PyTorch or llama.cpp. Record the backend version, model hash, prompt and generation lengths, trial count and token rates. Retain load time and peak memory where the backend measures them. [Inference benchmarks](docs/benchmarking.md#measure-inference)
- [ ] Repeat the same workload on another card or recorded configuration. Use `ga100ctl compare` with the two event IDs and document differences in driver, cooling or workload settings.
- [ ] When two peer-capable GPUs are available, run the peer-copy benchmark in each direction. Record both event IDs, observed bandwidth and driver-reported link status. [Two-GPU benchmark](docs/benchmarking.md#measure-two-gpu-communication)

## Completed groundwork

- [x] Recorded upstream commits, licenses and the Pico board-definition discrepancy. [Source review](docs/upstream.md)
- [x] Built repeatable Pico 2 and Pico 2 W USB diagnostic images and detected a physical RP2350 bootloader. [Build evidence](docs/provisioning.md#verified-build-outputs)
- [x] Implemented local inventory, SQLite history, JSON export, comparisons, validation and benchmark adapters. Verified the host workflows with automated tests and simulation fixtures. [First daily log](logs/2026-11-18.md)
