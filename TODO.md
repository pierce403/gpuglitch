# GA100 bench checklist

The Pico 2 W now runs the pinned upstream GA100 glitch firmware. USB serial and I2C communication have been observed on the GPU host, but a working Goldilocks waveform has not been established for this card. Update the checkboxes as evidence arrives. Summarize each day's work in [the daily logs](logs/) and the [current CMP handoff](runs/2026-11-18-cmp170hx-handoff.md).

## Pico on the USB bench

- [x] Operator confirmed the attached board is a Pico 2 W. Verified the `pico2_w` USB diagnostic UF2 against its manifest, current sources and recorded build hash. [Bench log](logs/2026-11-18.md#pico-2-w-flash-preparation)
- [x] Installed the selected Pico 2 W image. Flash event 2 returned `runtime_verified`, and a separate USB inspection confirmed firmware 1.0.0. [Installation record](logs/2026-11-18.md#pico-2-w-installation)
- [x] After a cold reconnect on the GPU host, the Pico enumerated as USB CDC with the same serial and responded to `v`; `@s` found the card's I2C devices. The installed glitch firmware is not the earlier `ga100ctl` diagnostic image.

## Glitch waveform measurement and safety

- [ ] Verify a safe NVVDD measurement point and card ground before attaching a probe or ADC wire. For this Pico 2 W's RP2350A build, connect the optional NVVDD sense wire to GP26 only after confirming the point and voltage range. Keep the existing GP0/GP1 I2C and GP22 PEX_RST connections identified separately. [Upstream pin map](https://github.com/dalzyu/pico-ga100vfi/blob/e1ebd972f28ebd621c483a6c8d9f0ab69b3ba73c/docs/BUILD.md)
- [ ] Build and flash the upstream `DEBUG=ON`, `BOARD_RP2350A=ON`, `PICO_BOARD=pico2_w` firmware. Confirm its 400 MHz banner and `v` output, then capture the NVVDD ADC trace with `y`. Check that the debug build's waveform actuation matches the normal build before using it for calibration. [Debug build](https://github.com/dalzyu/pico-ga100vfi/blob/e1ebd972f28ebd621c483a6c8d9f0ab69b3ba73c/docs/BUILD.md)
- [ ] On a clean cold boot, capture one guarded attempt with enough pre-trigger and post-trigger samples to measure all three NVVDD dips relative to the firmware's PEX_RST release time. Record minimum voltage, actual dip width, spacing and recovery, alongside the commanded `v_dip`, `pw`, `gap`, `delay`, `v_restore` and the post-attempt DSOV, CRC, privilege mask and live PCI ID.
- [ ] If an oscilloscope or logic analyzer is available, measure NVVDD and the physical PEX_RST_BUF* net together, triggered on reset release; add SDA/SCL to correlate PMBus writes with rail response. The Pico ADC trace alone cannot independently verify the reset wire or decode I2C.
- [ ] Before each attempt, require the CMP's exact live PCI ID, active runtime power state, readable BAR0, and completed GFW boot status. Stop after the first failed check; never count a non-`0xffffffff` PCI config value alone as a healthy endpoint. Confirm the voltage controller returns to PWM-VID after every attempt. Preserve the guard and raw observations with the campaign record.
- [ ] Diagnose why the CMP has entered an invalid live PCI state after a single dip even though a stock-voltage PEX_RST pulse preserves its PCI ID. Investigate the separate GSP initialization failure seen on one cold boot before expanding a campaign. Do not infer that a voltage profile is transferable from the upstream author's card.

## First CMP 170HX intake and validation

- [ ] On the GPU host, run `ga100ctl doctor` and `ga100ctl gpu list`. Select the card by its full PCI address, inspect it, and record the inventory ID and inspection event ID. If driver identifiers are unavailable, use the physical asset tag on each inspection. Record label information, cooling and known prior modifications. [Intake procedure](README.md#record-a-card)
- [ ] Run a bounded CUDA validation on that card. Start with `--memory-mib 64 --seconds 0`. Record the bytes exercised, driver-visible capacity, SM count, PCIe link state and any unavailable checks. Preserve the event even on failure. [Validation commands](docs/benchmarking.md#verify-a-baseline)
- [ ] With suitable cooling, run a timed stability test. Record duration, completed products, sampled temperature, power and clocks. Export the card's JSON report and check that it includes the original inspection and the validation outcomes. [Electrical safety](docs/safety.md)

## Card-specific FF evidence and review

- [ ] On the GPU host, save a normal locked `10de:20c2` baseline before a glitch. Record the BDF and driver-observed UUID association, then obtain two matching full 512-row passes and option/defect snapshots with the pinned FF toolkit. The driver must already be unbound and PCI memory decoding enabled for raw capture. Keep the capture private. [Capture requirements](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/README.md#capture)
- [ ] Generate a memory-only FF plan, simulate it, and review every proposed record against the captured defect masks, observed FF/IFF boundary and blank rows. Retain the baseline, plan, simulation and hashes together; an incomplete plan is a stop condition. [Planning method](docs/unlocking.md#experimental-permanent-ff-route)
- [ ] Independently establish and check the programming window and any required external supply. Pico `campaign` alone does not arm the controller for the FF burner. No automated arming or supply control is implemented here. [Burner prerequisites](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/BURNER.md#live-preflight)
- [ ] If a human operator elects to program a reviewed plan, use a new journal and preserve the result without retrying an uncertain row. After a full card power cycle, obtain a fresh locked-boot raw capture and compare FF rows and memory option shadows with the reviewed plan, then verify driver-visible capacity and tested HBM readback separately. The public driver tools still lack a tested 80 GiB geometry profile. [Burn and verification boundary](docs/unlocking.md#experimental-permanent-ff-route)

## Performance comparisons

- [ ] Run one local model on an idle card with either PyTorch or llama.cpp. Record the backend version, model hash, prompt and generation lengths, trial count and token rates. Retain load time and peak memory where the backend measures them. [Inference benchmarks](docs/benchmarking.md#measure-inference)
- [ ] Repeat the same workload on another card or recorded configuration. Use `ga100ctl compare` with the two event IDs and document differences in driver, cooling or workload settings.
- [ ] When two peer-capable GPUs are available, run the peer-copy benchmark in each direction. Record both event IDs, observed bandwidth and driver-reported link status. [Two-GPU benchmark](docs/benchmarking.md#measure-two-gpu-communication)

## Completed groundwork

- [x] Recorded upstream commits, licenses and the Pico board-definition discrepancy. [Source review](docs/upstream.md)
- [x] Built repeatable Pico 2 and Pico 2 W USB diagnostic images and detected a physical RP2350 bootloader. [Build evidence](docs/provisioning.md#verified-build-outputs)
- [x] Implemented local inventory, SQLite history, JSON export, comparisons, validation and benchmark adapters. Verified the host workflows with automated tests and simulation fixtures. [First daily log](logs/2026-11-18.md)
