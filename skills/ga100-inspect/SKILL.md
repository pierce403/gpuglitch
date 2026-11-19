---
name: ga100-inspect
description: Use ga100ctl to inspect a Linux GPU, check memory and links, review saved captures, and guide software or experimental FF memory planning.
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

The pinned [kprinssu/ga100-ff-tools](https://github.com/kprinssu/ga100-ff-tools/tree/04573c9dbf82997fb481f26769ec58dc625d8318) revision `04573c9dbf82997fb481f26769ec58dc625d8318` offers raw capture, per-card planning, replay simulation and an optional burner. Its [AGENTS.md](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/AGENTS.md) limits agents to capture, plan and simulate. **Never run `ga100ctl memory-ff burn`, upstream `ffburn.py3 --execute-plan`, or any live burner, and never supply or automate its confirmations.** The command may be documented for a human operator; successful offline review is not permission to program OTP.

For a memory-only proposal, select an identified card and use a normal `10de:20c2` baseline with matching option snapshots and two complete, matching 512-row reads. Preserve the original file and record its BDF and GPU UUID provenance. The source's raw capture requires the driver already unbound, PCI memory decoding enabled and BAR0 accessible; do not unbind a driver or change PCI configuration as part of a routine inspection. With a pinned checkout, run its `capture.py3` only when those prerequisites are already established and the card is explicitly selected. From that checkout, use `python3 ffplan.py3 plan --baseline "$capture" --changes memory --output "$plan"`, then `python3 ffplan.py3 simulate --baseline "$capture" --plan "$plan"`. Choosing `--changes memory` avoids the default experimental Gen3 pair. Review defect-mask preservation, FF/IFF boundary evidence, blank append rows, 32-bit records, space budget, missing evidence and the simulation's logical replay. Do not treat a plan as a measured memory configuration.

`ga100ctl memory-ff burn --source ../ga100-ff-tools --baseline card-a.json --plan card-a-plan.json --bdf 0000:01:00.0 --plan-id FULL_PLAN_ID --journal journals/card-a-program.jsonl --confirm-burn` is an **experimental human-run OTP request** for a reviewed public memory plan. The upstream burner requires an already armed Goldilocks window, open write privilege masks, and any qualified external programming supply. It does not establish those conditions or control the Pico. Its live identity check permits `20c2` or glitch identity `2080`, not `2082`; no real-GPU burn validation or 80 GiB driver geometry is published. Agents should stop after delivering the capture, plan, simulation, and unresolved prerequisites. After any human-reported change, record it as such and verify cold-boot capacity and readback separately.

## Review a saved Kate capture

Run `ga100ctl capture review "$capture"` or add `--baseline "$baseline"` if the user supplies a stable reference. Both arguments are local Kate version 4 capture files. The reference must match the recorded specimen claim, PCI device ID and PCI address. A card moved to another slot can therefore be rejected as a reference. A Pico host CLI baseline dump uses a different format. To install the Rust helper in the active Python virtual environment, run `cargo install --locked --path tools/capture-review --root "$VIRTUAL_ENV"` from the checkout.

Read the JSON relationship even when the process exits successfully. `incomplete_preserve` calls for reporting the gap. `uncompared_preserve` describes a capture without a baseline comparison. For `baseline_match` and `changed_preserve`, report the comparison against the specific baseline supplied. These findings describe the saved observations. They do not establish factory history or usable memory. Keep both source files intact.

For a hardware-free demonstration, use `ga100ctl --simulate gpu list` or run `ga100ctl capture review tools/capture-review/examples/partial.json` from the checkout. Label fixture or example results as synthetic.
