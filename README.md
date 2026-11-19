# ga100ctl

An Ubuntu CLI for tracking CMP 170HX cards, provisioning a USB diagnostic Raspberry Pi Pico 2 or 2 W, and measuring driver-visible GPU capabilities. Inventory and reporting use Python 3.10+ and SQLite. CUDA workloads and firmware builds have separate optional dependencies.

## Quickstart

From the project directory, run a complete inspection and report without hardware or an install.

```bash
make test
python3 -m ga100ctl --simulate --db /tmp/ga100-demo.sqlite3 doctor
python3 -m ga100ctl --simulate --db /tmp/ga100-demo.sqlite3 gpu inspect --gpu 0000:41:00.0
python3 -m ga100ctl --simulate --db /tmp/ga100-demo.sqlite3 report
```

To install the command in an Ubuntu virtual environment, install the distribution's `python3-venv` package if needed, then run the following.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
ga100ctl doctor
ga100ctl pico list
ga100ctl gpu list
```

Discovery works with a stock Linux kernel even if the NVIDIA driver is absent. It reads the Linux USB and PCI device trees and queries `nvidia-smi` when GPUs are present. All commands are also available through `python3 -m ga100ctl` from the checkout. For an offline host, use the checkout directly or install a prebuilt wheel with `python -m pip install --no-index dist/ga100ctl-0.6.0-py3-none-any.whl`.

## Check usable memory

On the GPU host, with CUDA-enabled PyTorch installed in the CLI's Python environment, run one command from the checkout.

```sh
.venv/bin/ga100ctl memory-check
```

The command discovers CMP 170HX cards, checks them one at a time, and saves a baseline and result for each. The summary pairs driver-reported capacity with the size of the allocation that passed readback. It also shows changes in DRAM error counters and the negotiated PCIe link. An 80 GB claim needs evidence from that card. A passing test covers the bytes exercised, and the command leaves GPU memory headroom for the driver and verification work.

Add `--gpu BDF_OR_UUID` to select one device or `--memory-mib 1024` to request an exact test size. Use `ga100ctl --json memory-check` for the full per-card evidence. The check exercises the current configuration through CUDA. [Sizing and interpretation](docs/benchmarking.md#automatic-memory-check)

## Enable the published software memory configuration

For a host with exactly one NVIDIA GPU, the `memory-enable` command runs a pinned, clean checkout of [cmpunlocker](https://github.com/amoghmunikote/cmpunlocker) and records the operation. PCI ID `10de:20c2` targets 64 GiB on the nominal 8 GB variant; `10de:2082` targets 40 GiB on the nominal 10 GB variant. This does not program FF records or provide the die-specific 80 GiB route described by Kate. Take a `gpu inspect` and `memory-check` baseline first.

```sh
git clone https://github.com/amoghmunikote/cmpunlocker.git ../cmpunlocker
git -C ../cmpunlocker checkout 2c7e2ecd7944e791fec040d2680036beb4a9327b
.venv/bin/ga100ctl memory-enable --source ../cmpunlocker --confirm
```

The command prompts for `sudo` as needed. It passes cmpunlocker's `--no-iommu --no-gen2-service --no-passthrough` options, but the installer still replaces NVIDIA kernel modules, removes conflicting NVIDIA DKMS modules and writes a PCIe Gen2 modprobe setting. A successful exit means installation is pending a cold power cycle, not that capacity has been measured. Shut the host down, remove GPU power, restart, then run `ga100ctl gpu inspect` and `ga100ctl memory-check` to compare reported capacity and tested readback with the baseline. The installer may need network access to download matching NVIDIA source. Its `--profile` flag changes metadata; runtime geometry follows the card's PCI ID. [Method and limitations](docs/unlocking.md#published-software-memory-route)

## Plan memory FF records before the Pico campaign

The experimental [GA100 FF toolkit](https://github.com/kprinssu/ga100-ff-tools/tree/04573c9dbf82997fb481f26769ec58dc625d8318) publishes a per-card capture, planner and guarded OTP burner. gpuglitch uses a clean external checkout at that exact commit; it does not copy the upstream source. The toolkit's live path supports the nominal 8 GB `20c2` card and glitch identity `2080`, not the `2082` 10 GB variant.

Before glitching, identify the physical card and save a normal, locked `20c2` raw capture. The upstream capture requires the card to be already unbound from its driver, with PCI memory decoding enabled and BAR0 accessible. While the driver is still loaded, record the BDF and full GPU UUID association with `nvidia-smi --query-gpu=pci.bus_id,uuid --format=csv,noheader`. The later cold-boot comparison requires that UUID in both captures. Its `--raw` mode selects and reads physical rows, without programming. Follow the [upstream capture requirements](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/README.md#capture). From the gpuglitch checkout, prepare the pinned external source and a new private directory for this card:

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

Check that the checkout is at the pinned commit, `git status --short` is empty, and the saved BDF and UUID match the physical card. Stop if the full UUID is unavailable; the post-boot comparison will not accept an identity inferred from the BDF alone. Then capture:

```sh
sudo python3 "$FF_TOOLS/capture.py3" --bdf "$CARD_BDF" --raw \
  --gpu-uuid "$CARD_BDF=$GPU_UUID" \
  --output "$RUN_DIR/card-raw.json"
```

Stop if capture exits nonzero or reports a partial or inconsistent result. Preserve that file and use a new run directory on retry. Only after a complete capture, transfer file ownership:

```sh
sudo chown "$(id -u):$(id -g)" "$RUN_DIR/card-raw.json"
```

Make the offline memory-only plan:

```sh
.venv/bin/ga100ctl memory-ff plan --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" --output "$RUN_DIR/card-memory-plan.json"
```

Stop if the plan exits nonzero or lacks `status: logical-plan-complete`. Inspect its defect masks, FF blocks and blank append rows before running simulation:

```sh
.venv/bin/ga100ctl memory-ff simulate --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" --plan "$RUN_DIR/card-memory-plan.json" \
  --output "$RUN_DIR/card-memory-simulation.json"
```

The CLI fixes planning to the published memory-only CMP route. Both commands run offline, create new files, and print JSON summaries. Continue to the Pico campaign only with a complete capture, `logical-plan-complete`, matching simulation `plan_id` and a reviewed plan. An `incomplete` plan is saved for diagnosis and exits nonzero; it is not burn input. Keep the capture, plan and simulation out of Git. The [ga100-ff-plan agent skill](skills/ga100-ff-plan/SKILL.md) describes evidence review and stops before programming. [Detailed sequence](docs/unlocking.md#experimental-permanent-ff-route) · [Graphical walkthrough](https://gpuglitch.fun/#baseline)

## Human-only permanent programming

The Pico campaign does not by itself establish the burn window. Before programming, the human operator must establish and maintain the required `SENSE_CTRL`, override, security/PLM state and any qualified external programming supply. The burner checks those register values but never sets them. It has synthetic I/O tests but no published real-GPU validation. [Upstream burner requirements](https://github.com/kprinssu/ga100-ff-tools/blob/04573c9dbf82997fb481f26769ec58dc625d8318/BURNER.md)

After a successful live preflight and manual review of a complete card-specific plan, only the human operator may request irreversible FF programming. In a new shell, restore `FF_TOOLS`, `CARD_BDF` and `RUN_DIR` to the verified checkout, card and saved absolute run path first:

```sh
.venv/bin/ga100ctl memory-ff burn \
  --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" \
  --plan "$RUN_DIR/card-memory-plan.json" \
  --bdf "$CARD_BDF" --plan-id FULL_REVIEWED_PLAN_ID \
  --journal "$RUN_DIR/card-program.jsonl" --confirm-burn
```

Use the full `plan_id` from the reviewed plan, not the placeholder. The wrapper prompts for `sudo`; the upstream burner then requires three exact interactive confirmations. It stops on an uncertain result without retry. A successful same-session array readback still needs a cold-boot comparison and a separate `memory-check` to establish usable capacity. The published FF toolkit does not supply a tested 80 GiB driver geometry profile, so this command alone is not an 80 GiB unlock. No fuse burn has been run on this project's card.

## Verify FF replay after a cold boot

After any human-operated burn, fully remove GPU power and cold boot the same card. Before binding the NVIDIA driver, arrange the same upstream raw-capture prerequisites: the card already unbound, PCI memory decoding enabled and BAR0 accessible. Restore `FF_TOOLS`, `CARD_BDF`, `GPU_UUID` and the saved absolute `RUN_DIR` in a new shell. Confirm the same full BDF and GPU UUID as the baseline, then use a fresh private output directory for the post-boot evidence:

```sh
POST_DIR=$(mktemp -d "$HOME/ga100-ff-post.XXXXXXXX")
printf 'POST_DIR=%s\n' "$POST_DIR"
```

Capture into a new file:

```sh
sudo python3 "$FF_TOOLS/capture.py3" --bdf "$CARD_BDF" --raw \
  --gpu-uuid "$CARD_BDF=$GPU_UUID" --output "$POST_DIR/card-post.json"
```

Stop if the capture is incomplete; preserve the file and use a new path for another attempt. After a complete capture, make it readable:

```sh
sudo chown "$(id -u):$(id -g)" "$POST_DIR/card-post.json"
```

Compare the two files offline against the exact reviewed plan:

```sh
.venv/bin/ga100ctl memory-ff verify --source "$FF_TOOLS" \
  --baseline "$RUN_DIR/card-raw.json" --plan "$RUN_DIR/card-memory-plan.json" \
  --post "$POST_DIR/card-post.json"
```

The comparison checks planned FF words, unchanged other rows and memory option shadows. A successful report has `state: ff-replay-observed`; a mismatch reports `ff-replay-mismatch`. Invalid or incomplete input stops before comparison. Inspect the JSON and stop on any mismatch or identity failure. `cold_boot_verified` remains false because the file comparison cannot independently prove physical power removal. This is an offline FF-state check, not a test of working HBM. After the operator restores normal driver operation, run a separate inspection and CUDA readback test:

```sh
.venv/bin/ga100ctl gpu inspect --gpu "$CARD_BDF"
.venv/bin/ga100ctl memory-check --gpu "$CARD_BDF"
```

Compare reported capacity and bytes actually tested with the pre-change baseline. A passing FF comparison alone does not establish 80 GiB of usable memory, and a CUDA test covers only its exercised allocation. [Full interpretation](docs/unlocking.md#post-cold-boot-verification)

## PCIe, NVLink and saved captures

Run `ga100ctl gpu list` to find the card's PCI address. Use that address for `ga100ctl gpu links --gpu 0000:41:00.0` to see negotiated and maximum PCIe speed and width, plus NVLink status from the driver. Missing readings appear as unavailable. This command works without elevated privileges.

To review an existing Kate GA100 capture, install the Rust companion once alongside the CLI. From the checkout, with the Python virtual environment above active and Rust 1.97 or later installed, run these commands. The included file is a synthetic partial capture.

```sh
cargo install --locked --path tools/capture-review --root "$VIRTUAL_ENV"
ga100ctl capture review tools/capture-review/examples/partial.json
```

For a comparison, run `ga100ctl capture review current.json --baseline earlier.json` with two saved Kate version 4 captures. The JSON result gives the capture identity, the number of observations from each pass, and Kate's assessment of stability and changes. An incomplete capture is a valid input and can return a successful exit code. The review runs offline.

The [capture reviewer](tools/capture-review/) uses Kate's original Rust libraries at a pinned revision. The [ga100-inspect agent skill](skills/ga100-inspect/SKILL.md) documents device selection, capture comparison and evidence reporting for agents working in this checkout.

## Record a card

Select the full PCI address or GPU UUID shown by `gpu list`. Each inspection returns a persistent inventory ID and an event ID. Replace the example address and card ID below with those from your host.

```bash
ga100ctl gpu inspect --gpu 0000:41:00.0
ga100ctl note --card CARD_ID --topic physical "Intake photo 03, factory heatsink present"
ga100ctl validate --gpu 0000:41:00.0 --memory-mib 64
ga100ctl report --card CARD_ID
ga100ctl --json report --card CARD_ID > card-history.json
```

The first observation is the baseline. UUID and serial establish continuity across PCI slot changes. If both are unavailable, supply the same physical label with `gpu inspect --asset-tag LAB-017` on each inspection. Without a stable identifier, each inspection receives a new ID. The PCI slot stays in the record as location evidence. The CLI recognizes both `10de:2082` and `10de:20c2`. Verify the stock memory variant using the specimen's history and label. The current driver-reported capacity can change.

Notes can record physical modifications, firmware and software changes, externally observed experiment outcomes, fuse observations, memory failures and stability issues. Each note carries its own timestamp and operator-supplied text. Live operations journal their inputs before starting and retain failures. An event still marked `running` after a host crash has an unknown outcome. Check the device before repeating the operation.

The default database is `$XDG_STATE_HOME/ga100ctl/history.sqlite3`, falling back to `~/.local/state/ga100ctl/history.sqlite3`. Set `--db PATH` before the subcommand to choose another location. Simulated entries occupy a separate inventory in that database. In simulation, workloads exercise targeting and logging and return `simulated` with no fabricated measurements. `compare LEFT_EVENT RIGHT_EVENT` shows inputs, states and measurements side by side. A peer-copy benchmark appears in both participating cards’ reports. Reports and comparisons can run with the hardware unplugged.

## Implementation and evidence

| Workflow | Verified here | Bench acceptance still needed |
| --- | --- | --- |
| USB provisioning | Built SDK 2.2.0 diagnostic UF2s for Pico 2 W and Pico 2. Verified repeat-build hashes and image metadata. Flashed the Pico 2 W over BOOTSEL and verified the same board running diagnostic firmware 1.0.0 after reboot. | Physical unplug/reconnect check. Non-W transfer remains fixture-tested. |
| GPU identification and history | Tested PCI and `nvidia-smi` parsing, multiple cards, missing drivers, baseline preservation, concurrent logging, offline reports and JSON export. Live discovery found no NVIDIA GPU here. | Confirm subsystem and driver identity on each card. |
| CUDA validation | Implemented CUDA initialization, SM count, visible capacity, allocation integrity, timed compute stability, PCIe, NVLink and telemetry capture. | Real measurements on the selected GPU. |
| Benchmarks | Implemented local PyTorch/Transformers, llama.cpp and two-GPU peer copy. Tested result parsing and failure handling. | Run the required backend with local models or an eligible pair of GPUs. vLLM remains planned. |

The supplied firmware identifies itself over USB and leaves the GPIOs unused. Voltage-glitch control and deployment of unlock firmware remain outside the implemented command set. `memory-enable` wraps the cmpunlocker software installer; `memory-ff burn` invokes the separate experimental FF burner only after an externally prepared burn window and a card-specific plan. Neither command establishes a maximum configuration for any card.

## Project guide

Start with the [bench checklist](TODO.md). For each day worked, append tasks performed, verification results and the next open item to a single [daily log](logs/) named `YYYY-MM-DD.md`. Use the bench's local date and cite SQLite event IDs for hardware runs.

- [Architecture and milestone plan](docs/architecture.md) and [pinned upstream source review](docs/upstream.md)
- [Hardware connections and diagnostic checks](docs/hardware.md)
- [Pico SDK setup, verified builds, BOOTSEL installation and recovery](docs/provisioning.md)
- [Interpreting stock and modified configurations](docs/unlocking.md) and [electrical safety](docs/safety.md)
- [CUDA validation, benchmarking methodology and comparisons](docs/benchmarking.md)

`make doctor` checks live discovery. `make demo` records a simulated inspection in `build/demo.sqlite3` and prints its report. `make firmware` uses `PICO_SDK_PATH` and defaults to `BOARD=pico2_w`. The [MIT license](LICENSE) covers original project code. Dependency and reference project licenses apply to their respective code.

## Static bench guide

The buildless guide in [site/](site/index.html) walks through the upstream Pico GA100VFI firmware and host client, with GPU preflight checks, board wiring, command examples, expected output shapes and source graphics. The site links to Kate’s GA100 research for the later, card-specific fuse work.

Preview with `python3 -m http.server 8000 --bind 127.0.0.1 --directory site` and open `http://localhost:8000`. For Cloudflare Workers Static Assets, use Node 22 or newer, run `npm ci` and `npm run check:deploy`. The deployment configuration targets the Worker `gpuglitch` and custom domain `gpuglitch.fun`. Workers Builds settings are repository `pierce403/gpuglitch`, production branch `main`, root `/`, an empty build command and deploy command `npm run deploy`.
