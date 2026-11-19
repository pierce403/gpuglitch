# Interpreting a GA100 card's configuration

The CMP 170HX product name covers multiple configurations. A die can have hardware excluded after production testing, and a product configuration can disable additional hardware. Driver-visible HBM, SM count and link training report what the current system can use. Those measurements alone cannot identify the cause of every disabled partition or establish a safe target configuration for a card.

Kate reports both nominal 8 GB and 10 GB products. Among ten nominal 8 GB specimens, one exposed 64 GB of usable memory after her experiments and nine exposed 80 GB. The observations apply to that sample. Her discussion also explains how address aliasing can make apparent capacity exceed usable memory. [Author's results](https://blog.kate.land/posts/using-voltage-glitching-to-unlock-the-a100/)

## Published software memory route

The independent [cmpunlocker](https://github.com/amoghmunikote/cmpunlocker) installer patches NVIDIA driver modules and selects framebuffer geometry from the PCI device ID. On `10de:20c2` it targets 65536 MiB (64 GiB); on `10de:2082` it targets 40960 MiB (40 GiB). It does not burn fuse records or select an 80 GiB profile. The nominal `--profile` option changes installer metadata; the PCI ID still determines geometry. A target capacity is not a readback test.

From gpuglitch on a dedicated GPU host, take a baseline with `ga100ctl gpu inspect` and `ga100ctl memory-check`, then run:

```sh
git clone https://github.com/amoghmunikote/cmpunlocker.git ../cmpunlocker
git -C ../cmpunlocker checkout 2c7e2ecd7944e791fec040d2680036beb4a9327b
.venv/bin/ga100ctl memory-enable --source ../cmpunlocker --confirm
```

The wrapper requires exactly one visible NVIDIA GPU with a supported PCI ID and a clean checkout at that reviewed commit. It records an event, then runs the upstream installer through `sudo` with IOMMU, Gen2 boot service and passthrough setup disabled. The installer still removes conflicting NVIDIA DKMS modules, installs patched kernel modules and writes `/etc/modprobe.d/cmp-pcie-gen2.conf`; it can download matching driver sources on first install. A successful wrapper result is `pending_power_cycle`. A human operator must perform the [cold card power cycle](safety.md#cold-card-power-cycle), then rerun both inspection and memory readback before claiming capacity. An Ubuntu `reboot` alone does not establish card power loss. Consult the [upstream installer](https://github.com/amoghmunikote/cmpunlocker/blob/2c7e2ecd7944e791fec040d2680036beb4a9327b/install.sh) for its host requirements and removal path.

## Experimental permanent FF route

The separately published [GA100 FF toolkit](https://github.com/kprinssu/ga100-ff-tools/tree/04573c9dbf82997fb481f26769ec58dc625d8318) offers a full 512-row capture, per-card planner, logical replay and guarded burner. Its source does not carry a license file, so gpuglitch pins and invokes an external checkout rather than incorporating its code. Its 109 synthetic tests passed in our review, but the author reports no real-GPU burn validation. The live burner accepts `20c2` and glitch identity `2080`; it does not claim the `2082` variant.

### Before the Pico campaign: capture and plan

The planner needs a normal locked `20c2` capture with two matching full physical-row passes, observed IFF boundary, defect options and card identity. While the driver is still loaded, record the card's full PCI BDF and GPU UUID association with `nvidia-smi --query-gpu=pci.bus_id,uuid --format=csv,noheader`. A later cold-boot comparison requires the full matching UUID in both captures. The upstream capture requires the selected card to be **already unbound** from its driver, with PCI memory decoding enabled and BAR0 accessible. Follow its [capture requirements](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/README.md#capture); its `--raw` mode selects and reads rows but does not program fuses or re-sense the controller. From the gpuglitch checkout, prepare the external source at the pinned commit and a fresh private run directory:

```sh
git clone https://github.com/kprinssu/ga100-ff-tools.git "$HOME/ga100-ff-tools"
git -C "$HOME/ga100-ff-tools" checkout 04573c9dbf82997fb481f26769ec58dc625d8318
FF_TOOLS="$HOME/ga100-ff-tools"
git -C "$FF_TOOLS" rev-parse HEAD
git -C "$FF_TOOLS" status --short
CARD_BDF=0000:1a:00.0  # replace with this card's full PCI address
GPU_UUID=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # replace with its saved UUID
RUN_DIR=$(mktemp -d "$HOME/ga100-ff-run.XXXXXXXX")
printf 'RUN_DIR=%s\n' "$RUN_DIR"
```

Check the source commit and require an empty `git status --short`; verify the BDF and UUID against the physical card. Stop if the full UUID is unavailable for the intended post-boot comparison. A supplied UUID remains labeled as such in the capture:

```sh
sudo python3 "$FF_TOOLS/capture.py3" --bdf "$CARD_BDF" --raw \
  --gpu-uuid "$CARD_BDF=$GPU_UUID" \
  --output "$RUN_DIR/card-raw.json"
```

Stop on a nonzero capture exit, `ok: false`, `complete: false`, mismatching passes, missing IFF boundary or failed address restoration. Preserve any partial capture as failure evidence and use a new run directory for a retry. Only after a complete capture, transfer ownership so the regular user can run the offline planner:

```sh
sudo chown "$(id -u):$(id -g)" "$RUN_DIR/card-raw.json"
```

Generate the memory-only plan:

```sh
.venv/bin/ga100ctl memory-ff plan --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" --output "$RUN_DIR/card-memory-plan.json"
```

Stop if planning exits nonzero or the saved plan does not say `status: logical-plan-complete`. Review its proposed changes and card identity before logical replay:

```sh
.venv/bin/ga100ctl memory-ff simulate --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" --plan "$RUN_DIR/card-memory-plan.json" \
  --output "$RUN_DIR/card-memory-simulation.json"
```

The CLI requires a clean checkout at the pinned commit with reviewed script hashes. `plan` fixes the upstream choices to `--changes memory --route cmp`, avoiding its default compute and experimental PCIe targets. Both CLI commands run offline and require a new output path; they never overwrite an earlier plan or simulation. Their JSON summaries identify the source commit, planner hash, input/output paths and plan ID. A plan with missing evidence is saved as `status: incomplete`, has no executable records and returns a nonzero status. Preserve it for diagnosis, then recapture rather than filling in unknown defect masks.

Review `ok`, `complete`, `read_issues`, both 512-row passes, options, CRC, IFF boundary and `raw_address_restored` in the capture. Require a plan with `status: logical-plan-complete`, `features: ["memory"]`, `route: cmp`, `public_sources_only: true`, input hashes tied to this capture and a matching `plan_id` in the simulation. Inspect `field_edits`, register effects, each encoded word, FF block, blank append row, defect mask and space budget. Simulation must report `simulation_only: true` and the expected number of `records_applied`; it proves logical replay only. Keep all card-specific files under the private `RUN_DIR`, outside Git. The [ga100-ff-plan agent skill](../skills/ga100-ff-plan/SKILL.md) gives the evidence review sequence and stops before live programming.

After this offline review, follow the [Pico campaign walkthrough](https://gpuglitch.fun/#campaign) to seek a temporary Goldilocks boot. `campaign` does not program FF records or establish the entire burn window. The programming window must additionally have DSOV 0, CRC `0x55`, programming enable 1, secured-burn feature 0, software override 1, write security 0 and open FUSEWDATA/FUSECTRL privilege masks. A qualified external programming supply may also be needed. The burner checks those states but does not arm the controller or control the supply. [Upstream burn prerequisites](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/BURNER.md#live-preflight)

### Human-only programming after the live preflight

After a separate successful live preflight and manual review of the complete card-specific plan, the human operator may run the irreversible command from the gpuglitch checkout. In a new shell, restore `FF_TOOLS`, `CARD_BDF` and `RUN_DIR` to the verified checkout, card and saved absolute run path first:

```sh
.venv/bin/ga100ctl memory-ff burn --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" \
  --plan "$RUN_DIR/card-memory-plan.json" \
  --bdf "$CARD_BDF" --plan-id FULL_REVIEWED_PLAN_ID \
  --journal "$RUN_DIR/card-program.jsonl" --confirm-burn
```

Replace the placeholder with the full reviewed `plan_id`. Agents do not run this command or provide the confirmations. The wrapper checks the pinned source and public memory plan, records the attempt, and delegates to the upstream burner. The burner requires three interactive confirmations, rereads the physical array and guards, and stops on any uncertain programming result without retry. Its success establishes only same-session array readback. Cold-boot replay, driver-visible capacity and working HBM still need separate checks. No published tool here supplies the tested 80 GiB framebuffer geometry described conceptually by Kate. [Detailed sequence](https://gpuglitch.fun/#records)

### Post-cold-boot verification

After any human-operated burn, the human operator must perform the [cold card power cycle](safety.md#cold-card-power-cycle), not merely `reboot` Ubuntu. First finish the operation and safely disable the external programming rig, then power off the host cleanly, remove AC from every PSU feeding the PCIe slot or auxiliary GPU connectors, de-energize Pico USB and other attached supplies, wait for discharge, and restore power. Do not hot-unplug the GPU or its power connectors. SSH poweroff alone cannot confirm card power removal; use physical or out-of-band power control and record what was done. Arrange a new normal-boot raw capture **before binding the NVIDIA driver**. The upstream prerequisites still apply: the selected card must already be unbound, PCI memory decoding enabled and BAR0 accessible. In a new shell, restore `FF_TOOLS`, `CARD_BDF`, `GPU_UUID` and the original absolute `RUN_DIR` from the reviewed evidence. Confirm the same full BDF and saved full UUID as the baseline; do not infer identity from slot alone. Create a new private directory so a failed post-capture cannot overwrite the baseline:

```sh
POST_DIR=$(mktemp -d "$HOME/ga100-ff-post.XXXXXXXX")
printf 'POST_DIR=%s\n' "$POST_DIR"
```

Capture both 512-row passes and option snapshots again:

```sh
sudo python3 "$FF_TOOLS/capture.py3" --bdf "$CARD_BDF" --raw \
  --gpu-uuid "$CARD_BDF=$GPU_UUID" --output "$POST_DIR/card-post.json"
```

Stop on a nonzero exit or any incomplete, inconsistent or partial post-capture. Preserve that file; retry only to a new path. A complete capture must have clean CRC, two matching row and option passes, an observed IFF boundary and restored read address. Both captures must show the normal `10de:20c2` identity, DSOV status 1, software override 0, debug 0 and an idle fuse controller. Its timestamp must follow the baseline capture. The full GPU UUID and provenance must be consistent across both files. After checking the capture, transfer ownership:

```sh
sudo chown "$(id -u):$(id -g)" "$POST_DIR/card-post.json"
```

Run the offline comparison from the gpuglitch checkout with the original reviewed plan:

```sh
.venv/bin/ga100ctl memory-ff verify --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" \
  --plan "$RUN_DIR/card-memory-plan.json" \
  --post "$POST_DIR/card-post.json"
```

The command regenerates and replays the plan, then checks observed planned FF words, unchanged other rows and memory option shadows against that simulation. `state: ff-replay-observed` with exit 0 means these comparisons matched; `ff-replay-mismatch` exits 1 and reports the divergent rows or fields. Invalid or incomplete evidence exits 2. Inspect its JSON report and stop on any mismatch or identity failure. It reports `host_boot_id_changed` when both captures have boot IDs, but `cold_boot_verified` stays false because files cannot independently prove that GPU power was removed. Preserve the captures and report as evidence; do not infer that a retry of an uncertain burn is safe. `working_features_verified` remains false and `verified_memory_mib` null. This check observes fuse replay only. It does not test that the newly exposed HBM is addressable or that the driver recognizes an 80 GiB geometry.

After the operator restores the NVIDIA driver, run a separate inventory and CUDA readback test in a Python environment with CUDA-enabled PyTorch:

```sh
.venv/bin/ga100ctl gpu inspect --gpu "$CARD_BDF"
.venv/bin/ga100ctl memory-check --gpu "$CARD_BDF"
```

Compare driver-reported capacity and the allocation actually tested against the original baseline. A passing test covers only the bytes exercised. Check for initialization errors, address aliasing, DRAM errors and link changes before making a capacity claim. [Memory-check interpretation](benchmarking.md#automatic-memory-check)

## Evidence for a before-and-after comparison

1. Record the physical card label and any known prior modification. Capture a first inspection, including the PCI address and subsystem identifiers, available GPU UUID or serial, reported HBM, driver version, VBIOS version and current link state. Label the card stock only if its history supports that claim.
2. Record a dated observation of any independently performed change, including who observed it, its provenance and the exact card identity. Keep unknowns explicit. Externally supplied fuse observations are records about that specimen. A mask copied from a different specimen carries no information about this one's defective components.
3. Collect a fresh inspection and compare entries for the same physical card. Driver or VBIOS changes, PCIe power management, ECC settings and changed cooling can affect comparisons. Identify any operator-reported details separately from measurements.
4. Exercise a selected allocation and record the number of bytes tested, the pattern, and any errors. Keep the capacity claim within the tested coverage. Check SM count, negotiated PCIe state and peer availability under the relevant workload. Retain failed initializations, unsupported queries, and thermal or compute errors in the history.

This toolkit covers inventory, measurement, the bounded cmpunlocker software installation and delegation to the external experimental FF burner. Voltage fault injection, programming-window setup and 80 GiB driver geometry remain outside its command set. A successful workload validates only the configuration, memory range and conditions exercised during that run.
