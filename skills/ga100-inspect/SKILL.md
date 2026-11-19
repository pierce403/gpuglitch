---
name: ga100-inspect
description: Use ga100ctl to inspect a Linux GPU, check driver-visible memory and links, review saved Kate captures, and verify post-boot results.
---

Run `ga100ctl` after installation, or `python3 -m ga100ctl` from the gpuglitch checkout. Global options such as `--db PATH`, `--json`, and `--simulate` go before the subcommand.

## Inspect a card

For a memory survey, use `ga100ctl --json memory-check`. It discovers CMP 170HX cards, tests each sequentially, and records per-card results with event IDs. Use `--gpu` when the request identifies one card. Report driver capacity, verified MiB, per-card state and DRAM ECC deltas separately. Automatic sizing checks for active compute clients. Preserve skipped cards and the reason in the report. Use an explicit `--memory-mib` only when a bounded workload is intended. The test covers accessible memory and leaves headroom.

1. Run `ga100ctl gpu list`. Use the full PCI address or UUID of the requested device. If several devices match the user's description, ask which to inspect.
2. For a baseline, run `ga100ctl gpu inspect --gpu "$gpu"` with the selected address or UUID in `gpu`. Keep its card ID and event ID. Use an operator-provided `--asset-tag` when serial and UUID are unavailable. A PCI slot can be reused by a different card.
3. For interconnect status, run `ga100ctl gpu links --gpu "$gpu"`. Report the negotiated speed and width alongside the maximums. Preserve any unavailable readings. Driver link status and measured transfer throughput answer different questions.
4. For a recorded workload or capacity check, consult `ga100ctl validate --help` and report the tested allocation in MiB. A driver capacity reading alone does not establish how many bytes passed a write and readback test. Use `ga100ctl --json report --card "$card_id"` for saved evidence and `ga100ctl compare LEFT_EVENT RIGHT_EVENT` for events of the same kind.

## Published software memory profile

For a CMP 170HX host with exactly one NVIDIA GPU, `ga100ctl memory-enable --source ../cmpunlocker --confirm` invokes the published [cmpunlocker](https://github.com/amoghmunikote/cmpunlocker/tree/2c7e2ecd7944e791fec040d2680036beb4a9327b) installer from a clean checkout at commit `2c7e2ecd7944e791fec040d2680036beb4a9327b`. PCI ID `10de:20c2` selects the 64 GiB target (65536 MiB); `10de:2082` selects 40 GiB (40960 MiB). The installer selects geometry by PCI ID. Its `--profile` option changes metadata only. This route does not require a Pico glitch and does not program FF records or implement Kate's die-specific 80 GiB process.

1. Record a per-card baseline with `ga100ctl gpu inspect` and `ga100ctl memory-check`. Check the PCI ID, driver capacity, tested allocation and any ECC readings. Identify the target host and confirm the session authorizes changing its NVIDIA driver and PCIe configuration before using `--confirm`.
2. Clone the upstream repository outside this checkout and check out the pinned commit. Review the source and verify the checkout is clean. Use `ga100ctl memory-enable --help` to review local prerequisites and the exact command behavior. Never run the installer as a discovery or verification step.
3. When authorized, run `ga100ctl memory-enable --source ../cmpunlocker --confirm` on that GPU host. The wrapper passes `--no-iommu --no-gen2-service --no-passthrough`. These flags leave IOMMU kernel-command-line setup, the new Gen2 retrain service and VM passthrough setup out of this invocation. The installer still removes conflicting NVIDIA DKMS modules, builds and installs patched NVIDIA modules, writes `/etc/modprobe.d/cmp-pcie-gen2.conf` and removes legacy PCIe retrain helpers. It may download matching NVIDIA source. Review host impact before invocation.
4. Preserve the command result and event ID. A `pending_power_cycle` result records installer completion, not working memory. After an authorized shutdown and full card power removal, compare `gpu inspect` and `memory-check` with the baseline. Report driver-reported capacity and verified readback range separately. Do not claim 64 GiB or 40 GiB is usable based only on installation or a capacity label.

## Experimental FF memory planning

For a proposed permanent memory floorsweep change, switch to [ga100-ff-plan](../ga100-ff-plan/SKILL.md). That skill covers the pinned external toolkit, normal-boot raw capture, memory-only planning, simulation and evidence review. This inspection skill resumes after a human-reported cold boot: compare driver-visible capacity, verified allocation and ECC readings with the pre-change baseline. **Never run `ga100ctl memory-ff burn`, `ffburn.py3`, another live burner, or their confirmations as an agent.** A logical plan and same-session row readback do not establish working memory or 80 GiB driver geometry.

## Review a saved Kate capture

Run `ga100ctl capture review "$capture"` or add `--baseline "$baseline"` if the user supplies a stable reference. Both arguments are local Kate version 4 capture files. The reference must match the recorded specimen claim, PCI device ID and PCI address. A card moved to another slot can therefore be rejected as a reference. A Pico host CLI baseline dump uses a different format. To install the Rust helper in the active Python virtual environment, run `cargo install --locked --path tools/capture-review --root "$VIRTUAL_ENV"` from the checkout.

Read the JSON relationship even when the process exits successfully. `incomplete_preserve` calls for reporting the gap. `uncompared_preserve` describes a capture without a baseline comparison. For `baseline_match` and `changed_preserve`, report the comparison against the specific baseline supplied. These findings describe the saved observations. They do not establish factory history or usable memory. Keep both source files intact.

For a hardware-free demonstration, use `ga100ctl --simulate gpu list` or run `ga100ctl capture review tools/capture-review/examples/partial.json` from the checkout. Label fixture or example results as synthetic.
