# Pico 2 USB provisioning

The diagnostic image identifies its build target and version over USB. It initializes USB CDC, exposes a product string such as `ga100ctl/pico2_w/1.0.0` and sleeps. The host reads the string through Linux sysfs. No GPIO, wireless setup, GPU, serial session, or external power is required. Keep the Pico on USB alone throughout provisioning. [Firmware source](../firmware/main.c)

## Check the device

Run `ga100ctl --json pico list` or `ga100ctl doctor`. To enter BOOTSEL, disconnect USB, hold BOOTSEL, reconnect using a data-capable cable and release the button. Linux should enumerate `2e8a:000f`. Most Ubuntu desktops mount a volume named `RP2350`. The CLI correlates the VFAT mount with the USB device through its block device in sysfs. An unmounted device still appears in the list. [Raspberry Pi getting-started guide](https://datasheets.raspberrypi.com/pico/getting-started-with-pico.pdf)

| Mode | Meaning |
| --- | --- |
| `bootsel` | The RP2350 ROM is running. The listed serial identifies the chip. Check the printed model on the board to select `pico2_w` or `pico2`. The ROM provides a chip-family ID shared by both boards. |
| `diagnostic` | The USB product descriptor supplies the board target and diagnostic firmware version. `compatible: true` means the firmware version matches this host implementation. |
| `sdk-cdc` | A device uses the Pico SDK CDC ID `2e8a:0009` with an unrecognized product string. Its board and firmware version are unknown. |

A Pico running an arbitrary USB application can advertise a different VID/PID or expose no USB interface. Enter BOOTSEL to identify the chip in that case. If BOOTSEL appears without a mount, open the RP2350 volume in the desktop file manager. If the mount is read-only, discovery still works. A firmware transfer requires a writable mount on the host.

## Build on Ubuntu

The Python host uses the standard library. The firmware requires the Raspberry Pi Pico SDK, the Arm bare-metal GCC toolchain, CMake and picotool's UF2 conversion code. The build below uses picotool locally and does not need USB access. SDK 2.2.0 is pinned to `a1438dff1d38bd9c65dbd693f0e5db4b9ae91779`, TinyUSB to its SDK gitlink `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`, and picotool 2.2.0 to `a7eb3988f0645239185fadb4e25d8279478c2dbb`. [Pico SDK](https://github.com/raspberrypi/pico-sdk/tree/a1438dff1d38bd9c65dbd693f0e5db4b9ae91779) · [picotool](https://github.com/raspberrypi/picotool/tree/a7eb3988f0645239185fadb4e25d8279478c2dbb)

```bash
sudo apt install --no-install-recommends git cmake build-essential gcc-arm-none-eabi \
    libnewlib-arm-none-eabi libstdc++-arm-none-eabi-dev python3

mkdir -p build/deps
git clone --depth 1 --branch 2.2.0 https://github.com/raspberrypi/pico-sdk.git build/deps/pico-sdk
test "$(git -C build/deps/pico-sdk rev-parse HEAD)" = a1438dff1d38bd9c65dbd693f0e5db4b9ae91779
git -C build/deps/pico-sdk submodule update --init --depth 1 lib/tinyusb
export PICO_SDK_PATH="$PWD/build/deps/pico-sdk"

git clone --depth 1 --branch 2.2.0 https://github.com/raspberrypi/picotool.git build/deps/picotool
test "$(git -C build/deps/picotool rev-parse HEAD)" = a7eb3988f0645239185fadb4e25d8279478c2dbb
export CMAKE_PREFIX_PATH="$PWD/build/deps/picotool-install"
cmake -S build/deps/picotool -B build/deps/picotool-build \
    -DCMAKE_INSTALL_PREFIX="$CMAKE_PREFIX_PATH" -DPICOTOOL_NO_LIBUSB=1 -DCMAKE_BUILD_TYPE=Release
cmake --build build/deps/picotool-build --parallel 4
cmake --install build/deps/picotool-build

ga100ctl pico build --board pico2_w --sdk "$PICO_SDK_PATH" --build-dir build/pico2_w
ga100ctl pico build --board pico2 --sdk "$PICO_SDK_PATH" --build-dir build/pico2
"$CMAKE_PREFIX_PATH/bin/picotool" info build/pico2_w/ga100ctl_usb.uf2
```

Each successful build produces a UF2 and a JSON manifest in its requested directory. The manifest records the board target, diagnostic and SDK versions, source file digests and UF2 SHA-256. Treat the source, toolchain and local build directory as trusted inputs. The manifest checks consistency and detects accidental file substitution. Independent authentication requires trust in the build environment.

### Verified build outputs

Both targets produced 42,496-byte UF2 images under Ubuntu 26.04, Arm GCC 14.2.1, picotool 2.2.0, and CMake 4.2.3. Rebuilding in separate directories with the same inputs produced the same bytes. Picotool identified each file as an RP2350 Arm Secure image named `ga100ctl USB diagnostics`, version `1.0.0`. Other compiler versions may produce different hashes.

| Board build | UF2 SHA-256 |
| --- | --- |
| `pico2_w` | `51478e767dcc0956a6ebefcd88304dda9a55d01c030ffab9464cddf3daecb6b8` |
| `pico2` | `b69a7172973fd20b65cf71e2a02149d665e0566617db655d41852a9a46c29c20` |

## Install and verify

Flashing replaces the program on the Pico. Save the previous image and any application data you need before authorizing this step. Identify the printed board model and the exact BOOTSEL serial from the listing. Substitute that serial in the command below.

```bash
ga100ctl --json pico list
ga100ctl pico flash --board pico2_w --serial SERIAL_FROM_LIST --build-dir build/pico2_w --confirm
ga100ctl --json pico list
```

The transfer accepts the local build artifact for the selected board. It checks the manifest, descriptor and RP2350 UF2 blocks, verifies the mounted volume's device ID and ROM identity, then creates a new UF2 file on that volume. The Pico normally reboots and the volume disappears. The CLI waits up to eight seconds for the matching USB port, serial, diagnostic version and board target. The stored status reports `runtime_verified` only when that endpoint appears. A normal copy with no runtime observation yields `copied_runtime_unverified`. The host also checks for re-enumeration if the ROM disconnects during the write or flush. It retains the I/O error in the result and returns `transfer_error_runtime_unverified` if verification fails. Inspect the current USB listing before considering another transfer.

The standard RP2350 ROM prints the high and low chip-ID words in big-endian hexadecimal. SDK 2.2.0 reads the same chip ID through `SYS_INFO_CHIP_INFO` and formats the bytes in the same order. The verification check compares serials case-insensitively. [RP2350 ROM USB](https://github.com/raspberrypi/pico-bootrom-rp2350/blob/c6cdb1711f32c3e34faaebd58618a6d096dbd52e/src/nsboot/nsboot_usb_client.c#L647-L648) · [SDK unique ID](https://github.com/raspberrypi/pico-sdk/blob/a1438dff1d38bd9c65dbd693f0e5db4b9ae91779/src/rp2_common/pico_unique_id/unique_id.c#L40-L54)

To restore a previous Pico application, disconnect the Pico, re-enter BOOTSEL and copy your saved, board-compatible UF2 to the identified RP2350 volume. Restoring an application that used flash storage may also require restoring its saved data. On the Pico 2 W bench unit, flash event 2 returned `runtime_verified`. A separate discovery call found the same serial running compatible firmware 1.0.0. [Bench log](../logs/2026-11-18.md#pico-2-w-installation)

The firmware uses SDK and TinyUSB code. Preserve their notices when redistributing the image. [Pico SDK license](licenses/pico-sdk.txt) · [TinyUSB license](licenses/tinyusb.txt)
