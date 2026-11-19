# CMP 170HX Goldilocks handoff (2026-11-18)

## Host and devices

- SSH target: see ignored `sshinfo.txt`; do not copy credentials into this note.
- Target CMP 170HX: `0000:0a:00.0` (`10de:20c2`), initially 8192 MiB in `nvidia-smi`.
- Separate RTX 3060: `0000:09:00.0`; keep it distinct from the CMP.
- Pico: `/dev/ttyACM0`, USB `2e8a:0009`, upstream GA100 glitch firmware. Serial `v` returned `clk=150 MHz`.
- Upstream host source on Ubuntu: `~/gpuglitch-tools/pico-ga100vfi-e1ebd972f28ebd621c483a6c8d9f0ab69b3ba73c/host`.
- Host source archive SHA-256: `fb903ed20f75316f38ecace2a8c9ed180c0d9c912144e9b447f33bc0e22cf0b9`.

## Completed

- Pico I2C scan responded with addresses `23 24 25 26 40`; `z` read the MP2888B status.
- GNOME display manager was disabled and default boot target set to `multi-user.target` at the user's request. NVIDIA persistence service was stopped for the attempt.
- After a reboot, target-specific NVIDIA driver unbind succeeded. The RTX 3060 stayed bound.
- Pre-glitch baseline saved remotely as `~/gpuglitch-tools/baseline-0a.json`. Initial classifier: DSOV 1, CRC `0x55`, privilege mask `0xffffffff`, so not Goldilocks.
- Ran `sudo env PYTHONPATH="$PYTHONPATH" python3 -m ga100vfi campaign --port /dev/ttyACM0 --bdf 0000:0a:00.0 --adaptive --budget 200 --log goldilocks-0a.jsonl`, with `PYTHONPATH` set to the upstream `host` directory above.
- Remote outputs: `~/gpuglitch-tools/campaign-0a.json` and `campaign-0a.stderr`. Result: exit 1, 200 attempts, 200 PCIe-valid, 0 lost, 0 NACK, 196 DSOV=0, no Goldilocks. Adaptive choices were 369 and 375 mV. Most later CRC reads were `0x10015`, not required `0x55`.
- After the run, a stock-voltage `r` reset and classifier gave inconsistent fuse readings. Do not treat the card as being in Goldilocks. A manual `w` returned `revert vout=1 mode=1 (PWM-VID restored)`; `z` read `e1=0xc2ea`, `vout=0x007e`.

## Stopping point

No FF, OTP, or permanent fuse operation was attempted. No further glitches should run against the current state. On user request, `sudo systemctl poweroff` was sent over SSH; subsequent ICMP probes received no replies. The user will remove PSU AC power for a full card power cycle. After they restore power, verify Pico USB and I2C, both PCI devices, the CMP's driver state, baseline DSOV/CRC/shadow values, and PWM-VID status before choosing a new bounded campaign profile. Do not assume another adaptive run with the same 300-380 mV range is useful; it pinned at 375 mV with bad CRC.

## After the first cold power cycle

- The user restored PSU power and the host rebooted. `baseline-0a-cold.json` exactly matched `baseline-0a.json`. Initial classifier was clean normal boot: DSOV 1, CRC `0x55`, all five shadow words unchanged. Pico `v`, `@s` and `z` were normal; I2C scan again found `23 24 25 26 40`.
- NVIDIA persistence service was stopped and the CMP alone detached cleanly; the RTX 3060 stayed bound.
- A second, bounded `campaign --adaptive --min-v 381 --max-v 420 --budget 20` wrote `campaign-0a-cold.json` and `campaign-0a-cold.stderr` remotely. It returned no Goldilocks; all 20 classified as under at 381, 388 or 394 mV (`dsov=1`, `crc=0x55`). No NACKs were reported.
- A subsequent `classify` returned inconsistent values (DSOV 0, CRC `0x10015`, bad DSOV writeback). Later direct BAR0 reads were all `0xffffffff`. Live PCI config read using `setpci -s 0a:00.0 0.l` returned `b76000a1` instead of the expected `20c210de`, while sysfs/lspci still identified the device from enumeration. PCI COMMAND had memory decode disabled. Pico `z` showed `e1=0xc2ea`, `vout=0x007e`, so the controller reported stock PWM-VID path.
- **Correction:** The first campaign's `valid=200` and second campaign's `valid=20` are the upstream tool's counts, not proof of a healthy PCIe endpoint. `ga100vfi.bar0.restore()` accepts any config dword other than `0xffffffff` and does not verify the expected vendor/device ID. The later `b76000a1` shows this check is insufficient for this card. Do not interpret the counts as true PCIe validity.
- No further reset or glitch was sent after discovering the invalid config ID. At the user's request, `sudo systemctl poweroff` was sent again for a second full card power removal; subsequent ICMP probes received no replies. Wait for the user to remove PSU AC power and restore it before reconnecting. Future attempts need a live PCI ID check before classification and a way to observe the GFW boot-status safety condition in upstream `AGENTS.md`; do not guess the register address or reset while the card is in its current state.

## After the second cold power cycle

- User restored power; new host boot ID `79e2d74b-bde5-41f4-b862-da34654b282b`. Pico enumerated. Live PCI ID initially `20c210de`. NVIDIA 595.99.02 listed only the RTX 3060 in `nvidia-smi`; for the CMP, kernel logs reported `unexpected WPR2 already up` and `RmInitAdapter failed (0x62:0x40:2168)` during GSP startup. This is a new normal-boot failure; do not report driver-visible CMP memory as verified on this boot.
- Stopped NVIDIA persistence, detached CMP only, and saved `baseline-0a-cold2.json`; it exactly matched the original baseline. Linux then runtime-suspended the unbound card (`power/runtime_status=suspended`), making BAR0 reads all `0xffffffff` despite a valid PCI ID. Set `/sys/bus/pci/devices/0000:0a:00.0/power/control` to `on`; runtime status became `active` and BAR0 returned normal DSOV 1, CRC `0x55`, privilege `0xffffffff`.
- Linux Nova register definition identifies GFW_BOOT as BAR0 offset `0x00118234`, with low byte `0xff` meaning complete: `https://github.com/torvalds/linux/blob/master/drivers/gpu/nova-core/regs.rs`. The CMP then read `0x3ff` (complete); PMC_BOOT read `0x170000a1`.
- Added a private host-side wrapper `~/gpuglitch-tools/safe_campaign.py` around pinned upstream CLI, without editing upstream source. It checks runtime power active, exact live PCI ID `20c210de`, readable BAR0 and completed GFW before each attempt and after PCI restore. A guarded baseline passed.
- Ran one guarded campaign profile: fixed 381 mV, pulse width 220 us, budget 10. It stopped with exit 3 **after the first attempt** because live PCI ID became `b76000a1`; no further pulses were sent. Pico `z` showed `e1=0xc2ea`, `vout=0x007e` (stock path), `cml=0x28`. Subsequent read-only BAR0 values were PMC_BOOT `0xb76000a1`, GFW_BOOT `0x3ff`, DSOV 0, CRC `0x55`, privilege mask `0x105`. That is not Goldilocks because privilege is closed and the live PCI header is invalid.
- Current state: host running via SSH, GNOME disabled, RTX 3060 bound, CMP detached, CMP runtime power control `on`, Pico attached, no OTP/FF action. **Do not run another reset or glitch in this state.** Need investigation of why the first PEX_RST/glitch changes PCI config identification and why the CMP driver failed GSP init after a full cold cycle. A further cold cycle alone may restore baseline, but repeated attempts are unlikely to be productive until these issues are understood.

## Subsequent read-only Pico check

- User reported SSH back up; host boot ID `f29a3891-00aa-469b-a263-caacc06c1bae`. `nvidia-smi` again listed the RTX 3060 (12288 MiB) and CMP at `0000:0a:00.0` (8192 MiB). Both were bound to NVIDIA, CMP runtime state `active`, live PCI ID `20c210de`. No `unexpected WPR2` or `RmInitAdapter failed` lines appeared in this boot's filtered kernel log.
- Pico USB `2e8a:0009`, `/dev/ttyACM0`, same serial `E1B78C780C59FAB1`. Read-only `v`: `clk=150 MHz`, reset cause power-on/external. Read-only `@s`: `23 24 25 26 40`.
- First read-only `z` returned `status ERR`. Repeating the three underlying PMBus reads individually succeeded: `@r 25 7e 01` -> `02`, `@r 25 e1 02` -> `ea c2`, `@r 25 21 02` -> `7e 00`; a second `z` succeeded: `cml=0x02 pwd=0 e1=0xc2ea vout=0x007e`. Treat the first error as a transient observation, not proof all communication is reliable.
- No reset, glitch, VRM write, driver detach, or fuse action was performed in this check. USB serial and I2C communication work; GP22 reset wiring and the NVVDD dip remain unverified by these read-only tests.

## Controlled reset-path and 208 us attempt

- On the same clean boot (`f29a3891-00aa-469b-a263-caacc06c1bae`), user authorized resuming Goldilocks work. Both GPUs were again visible in `nvidia-smi`; CMP live PCI ID `20c210de`. Stopped NVIDIA persistence, detached only the CMP, kept its runtime power control `on`, and verified original shadow baseline plus GFW_BOOT `0x3ff`, PMC_BOOT `0x170000a1`, Pico `z` stock path.
- Sent one **stock-voltage** Pico `r` PEX_RST pulse, with no dip. After two seconds the live PCI ID remained `20c210de`; guarded `classify` returned normal DSOV 1, CRC `0x55`, privilege `0xffffffff`, original shadows. This separates the reset path from the glitch outcome: PEX_RST alone did not produce the invalid PCI header in this test.
- Sent one guarded `campaign --v-dip 381 --pw 208 --budget 1`. The guard stopped immediately after the first pulse: live PCI ID `b76000a1` instead of `20c210de`. Read-only BAR0 after the attempt: PMC_BOOT `0xb76000a1`, GFW_BOOT `0x3ff`, DSOV 0, CRC `0x10015` (invalid), privilege `0xffffffff`. Pico `z`: `e1=0xc2ea`, `vout=0x007e` (stock path), `cml=0x2a`. No Goldilocks.
- Because GFW_BOOT was complete and the controller was back on its stock path, sent one stock-voltage `r` to test recovery. The live PCI ID **remained `b76000a1`** after two seconds; this reset did not restore a clean endpoint. No further glitches or resets were sent. Host remains running; RTX remains bound, CMP detached. A complete card power removal is needed to restore baseline. Do not repeat pulses on the current state.

## Next controlled test

- A subsequent read-only check confirmed the same boot ID and live PCI ID `b76000a1`; the card has not recovered. Do not pulse again until full card power removal and a fresh baseline.
- Updated private `~/gpuglitch-tools/safe_campaign.py` to print the Pico's raw `glitch` response before any post-attempt PCI guard abort. Python compile check passed.
- After the next cold boot, first run a single guarded control attempt at **381 mV, 198 us pulse width**, the setting previously classified as under. Verify exact live PCI ID and raw Pico response. This tests whether the previous 20-shot under classification was trustworthy. Only if the endpoint remains healthy should a later attempt explore a pulse width between 198 and 208 us. Stop on the first invalid ID, unreadable BAR0, incomplete GFW, PMBus error, or unexpected fuse state.
