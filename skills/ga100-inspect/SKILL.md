---
name: ga100-inspect
description: Use ga100ctl to inspect a Linux GPU, check reported PCIe and NVLink status, record or compare observations, and review saved Kate GA100 captures offline.
---

Run `ga100ctl` after installation, or `python3 -m ga100ctl` from the gpuglitch checkout. Global options such as `--db PATH`, `--json`, and `--simulate` go before the subcommand.

## Inspect a card

1. Run `ga100ctl gpu list`. Use the full PCI address or UUID of the requested device. If several devices match the user's description, ask which to inspect.
2. For a baseline, run `ga100ctl gpu inspect --gpu "$gpu"` with the selected address or UUID in `gpu`. Keep its card ID and event ID. Use an operator-provided `--asset-tag` when serial and UUID are unavailable. A PCI slot can be reused by a different card.
3. For interconnect status, run `ga100ctl gpu links --gpu "$gpu"`. Report the negotiated speed and width alongside the maximums. Preserve any unavailable readings. Driver link status and measured transfer throughput answer different questions.
4. For a recorded workload or capacity check, consult `ga100ctl validate --help` and report the tested allocation in MiB. A driver capacity reading alone does not establish how many bytes passed a write and readback test. Use `ga100ctl --json report --card "$card_id"` for saved evidence and `ga100ctl compare LEFT_EVENT RIGHT_EVENT` for events of the same kind.

## Review a saved Kate capture

Run `ga100ctl capture review "$capture"` or add `--baseline "$baseline"` if the user supplies a stable reference. Both arguments are local Kate version 4 capture files. The reference must match the recorded specimen claim, PCI device ID and PCI address. A card moved to another slot can therefore be rejected as a reference. A Pico host CLI baseline dump uses a different format. To install the Rust helper in the active Python virtual environment, run `cargo install --locked --path tools/capture-review --root "$VIRTUAL_ENV"` from the checkout.

Read the JSON relationship even when the process exits successfully. `incomplete_preserve` calls for reporting the gap. `uncompared_preserve` describes a capture without a baseline comparison. For `baseline_match` and `changed_preserve`, report the comparison against the specific baseline supplied. These findings describe the saved observations. They do not establish factory history or usable memory. Keep both source files intact.

For a hardware-free demonstration, use `ga100ctl --simulate gpu list` or run `ga100ctl capture review tools/capture-review/examples/partial.json` from the checkout. Label fixture or example results as synthetic.
