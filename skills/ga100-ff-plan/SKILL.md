---
name: ga100-ff-plan
description: Capture and review card-specific GA100/CMP 170HX memory FF plans, then compare post-cold-boot evidence; never perform live OTP programming.
---

# GA100 memory FF planning

Use this skill for a proposed permanent memory floorsweep change. For ordinary driver-visible capacity and readback checks, use `ga100-inspect`. The published [ga100-ff-tools](https://github.com/kprinssu/ga100-ff-tools/tree/04573c9dbf82997fb481f26769ec58dc625d8318) checkout is pinned to `04573c9dbf82997fb481f26769ec58dc625d8318`. Read its `README.md`, `SOURCES.md`, and `AGENTS.md` before working with a card. Its reviewed tree has no license file; use an external checkout and do not copy its code into gpuglitch.

## Boundary

Agents may capture, plan, simulate, compare saved post-cold-boot captures, and inspect evidence. **Never invoke `ffburn.py3`, `ga100ctl memory-ff burn`, another live burner, or a custom replacement against hardware. Never provide or automate the burner's confirmations.** Do not issue `CMD_WRITE`, `CMD_SENSE`, write FUSEWDATA, arm override/security/PLM registers, change voltage or GPIO, reset or power-cycle the card or host, operate PSU/PDU controls, unbind drivers, or alter PCI configuration as part of this workflow. The upstream [agent instructions](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/AGENTS.md) reserve programming for a human operator. Synthetic I/O tests and offline planning are allowed.

## Select and capture one card

Require the card's full BDF and preserve its full GPU UUID association. The later cold-boot comparison requires matching UUIDs in both raw captures; stop that workflow if the UUID is unavailable. A normal locked `10de:20c2` capture with clean CRC is required for planning. The `2082` variant is outside this toolkit's claimed live support; a glitched `2080` identity can be captured but is not a normal planning baseline. Never infer a physical identity from BDF alone.

On the Linux GPU host, live `capture.py3 --raw` is appropriate only when the selected card is **already** unbound from its driver, PCI memory decoding is enabled, and BAR0 is accessible. If these conditions are absent, stop live capture and work from existing evidence. Save the BDF/UUID mapping while the driver is available; if supplying it after unbind, use `--gpu-uuid BDF=GPU-UUID` and label it user-supplied. Do not invent a UUID.

Use a fresh private run directory outside Git. If the FF-tools checkout is absent, clone it outside gpuglitch and check out the pinned commit. From the gpuglitch checkout:

```sh
FF_TOOLS="$HOME/ga100-ff-tools"
CARD_BDF=0000:01:00.0  # replace with the selected card
GPU_UUID=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # replace with its saved UUID
git -C "$FF_TOOLS" rev-parse HEAD
git -C "$FF_TOOLS" status --short
RUN_DIR=$(mktemp -d "$HOME/ga100-ff-run.XXXXXXXX")
printf 'RUN_DIR=%s\n' "$RUN_DIR"
```

Require the exact pinned commit, a clean source tree and verified BDF/UUID mapping before capture. Obtain the full UUID from the driver before unbinding, for example with `nvidia-smi --query-gpu=pci.bus_id,uuid --format=csv,noheader`; replace the placeholder above. Do not invent a UUID.

```sh
sudo python3 "$FF_TOOLS/capture.py3" --bdf "$CARD_BDF" --raw \
  --gpu-uuid "$CARD_BDF=$GPU_UUID" \
  --output "$RUN_DIR/capture.json"
```

Stop on a nonzero exit or incomplete capture. `--raw` selects physical rows and issues `CMD_READ`; it does not program. Inspect `ok`, `complete`, `read_issues`, two matching option snapshots, two matching 512-row passes, clean CRC, FF/IFF boundary observation, and `raw_address_restored`. A failed or partial capture remains evidence of failure, not planner input. Keep the original file intact. Only after a complete capture, transfer ownership to the regular user:

```sh
sudo chown "$(id -u):$(id -g)" "$RUN_DIR/capture.json"
```

## Plan and replay memory only

Use the gpuglitch offline wrappers from this checkout. They fix upstream planning to `--changes memory --route cmp`; upstream defaults also select compute and an experimental PCIe Gen3 pair. They require the pinned clean source checkout, an existing baseline and new output paths. From the same run directory and capture:

```sh
.venv/bin/ga100ctl memory-ff plan --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/capture.json" --output "$RUN_DIR/plan.json"
```

Stop if the wrapper exits nonzero or the plan is incomplete. Review the card identity and proposed records before logical replay:

```sh
.venv/bin/ga100ctl memory-ff simulate --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/capture.json" --plan "$RUN_DIR/plan.json" \
  --output "$RUN_DIR/simulation.json"
```

Inspect each exit status and wrapper JSON summary. Incomplete evidence produces a saved `status: incomplete` plan without executable records and wrapper exit 1. Other failures are validation or I/O errors. A complete plan may legitimately have no changed records. Do not turn missing defect values into zero or edit a generated plan by hand; recapture and regenerate when evidence is missing or stale.

For a proposed memory plan, require `status: logical-plan-complete`, route `cmp`, `public_sources_only: true`, feature `memory`, matching input hashes and simulation `plan_id`. Review `field_edits`, `register_effects`, each encoded word and append row, `ff_boundary_evidence`, `space_budget`, and `missing_evidence`. Memory targets for FBIO, FBPA, FBP and ROP/L2 must retain the captured `_DEFECTIVE` masks and preserve unrelated block bits. The first observed bit-5 IFF record and repeated `IFF_RECORD` register must agree; blank rows alone do not establish the boundary. Do not copy another card's masks, row numbers, or plan.

## Compare a later cold-boot capture

Only after a human-operated attempt and the operator's [cold card power cycle](../../docs/safety.md#cold-card-power-cycle), compare a **new** normal-boot raw capture with the saved baseline and exact reviewed plan. An Ubuntu `reboot` or changed host boot ID does not prove the card lost power. The operator records clean shutdown and removal of all PSU, auxiliary GPU, Pico USB and external bench power feeds; agents do not cause or control this step, driver unbind, PCI setup or programming. Live post-capture is permitted only if the operator has already left the card unbound, PCI memory decoding enabled and BAR0 accessible. Otherwise work from a supplied post-capture or stop.

Confirm the same physical card, same full BDF and saved full UUID as the baseline. Use a separate private output directory and pass the same UUID association to upstream capture:

```sh
POST_DIR=$(mktemp -d "$HOME/ga100-ff-post.XXXXXXXX")
printf 'POST_DIR=%s\n' "$POST_DIR"
```

```sh
sudo python3 "$FF_TOOLS/capture.py3" --bdf "$CARD_BDF" --raw \
  --gpu-uuid "$CARD_BDF=$GPU_UUID" --output "$POST_DIR/card-post.json"
```

Stop on a failed or partial capture. Require normal `10de:20c2`, DSOV status 1, software override 0, debug 0, idle fuse controller, clean CRC, both matching 512-row and option passes, observed IFF boundary, restored read address, consistent UUID provenance and a post timestamp after the baseline. Preserve failures under a unique path. After a complete capture, transfer file ownership and run the offline comparison:

```sh
sudo chown "$(id -u):$(id -g)" "$POST_DIR/card-post.json"
```

```sh
.venv/bin/ga100ctl memory-ff verify --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/capture.json" --plan "$RUN_DIR/plan.json" \
  --post "$POST_DIR/card-post.json"
```

Inspect its exit status and JSON report. `state: ff-replay-observed` means planned FF words, unchanged other rows and memory option shadows matched logical replay; `ff-replay-mismatch` is a stop condition with specific differences. Identity or incomplete-input failures also stop the workflow. Preserve evidence and do not infer that another burn is safe. Report `host_boot_id_changed` if available, while retaining `cold_boot_verified: false`: file evidence cannot prove physical power removal. This verifies only observed FF replay. For driver-visible capacity and CUDA readback after the operator restores the driver, use `ga100-inspect` separately.

## Deliver the evidence

Report the selected BDF, UUID provenance, source commit, baseline and post-capture paths and hashes, capture quality, selected changes, plan status and full `plan_id`, record count and row range, simulation and verification results, and any unresolved evidence. Keep captures, plans and card identifiers out of commits; use private directories outside Git.

State that **the agent did not burn fuses**. A plan or logical replay does not establish a programming window, electrical supply or cold-boot FF state. Even matching post-boot FF observations do not establish working memory or 80 GiB driver geometry. The external burner has synthetic I/O tests but no published real-GPU validation. The human-operated command and its separate prerequisites are documented in [docs/unlocking.md](../../docs/unlocking.md#experimental-permanent-ff-route); agents stop before that command.
