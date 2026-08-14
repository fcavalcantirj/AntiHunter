# AntiHunter N16R8

### A CLI-first field fork for generic ESP32-S3 hardware

[![ESP32-S3 N16R8](https://img.shields.io/badge/ESP32--S3_N16R8-hardware_validated-2ea44f)](docs/N16R8.md)
[![Operator CLI](https://img.shields.io/badge/operator_CLI-Python_3-3776ab)](docs/CLI.md)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL_v3-663399)](LICENSE)

This fork turns AntiHunter into a practical, repeatable tool for a generic
**ESP32-S3-WROOM-1 N16R8** board: 16 MB flash, 8 MB octal PSRAM, the complete
local Web UI, and a small operator CLI that is safe to automate.

The board was built, emulated, flashed over USB, and validated through its live
SoftAP on physical hardware. The N16R8 profile deliberately leaves unknown
carrier-board pins alone. It does not pretend that matching the ESP32 module
also proves the wiring around it.

This is our working fork of [lukeswitz/AntiHunter](https://github.com/lukeswitz/AntiHunter),
not a replacement for its hardware ecosystem or community documentation.

## Start with the board already on your desk

The CLI has no package install step. It uses Python 3's standard library.

```bash
git clone https://github.com/fcavalcantirj/AntiHunter.git
cd AntiHunter
./scripts/antihunter
```

With no command, `antihunter` runs `doctor`. It looks for both supported paths:

- the local HTTP service at `http://192.168.4.1`;
- a likely ESP32 USB serial device, without opening it.

If the cable is found but HTTP is unavailable, join the board's Wi-Fi:

| Setting | Default |
|---|---|
| Network | `Antihunter` |
| Password | `antihunt3r123` |
| Web UI | `http://192.168.4.1` |

Then run:

```bash
./scripts/antihunter web
./scripts/antihunter status
./scripts/antihunter validate --wait 180
```

Change the default Wi-Fi credentials in the Web UI before regular use. On a Mac
with one Wi-Fi interface, joining the board usually drops normal Internet and
remote Codex connectivity. That is expected: the board is its own isolated
access point.

## The operator CLI

The CLI owns transport, validation, output shape, and failure handling. The
operator chooses the action and its bounded arguments.

Its rules are intentionally simple:

- inspection is the default;
- runtime changes require `--yes`;
- erase, factory-reset, auto-erase, and erase-PSK serial commands are refused;
- JSON output is stable enough for shell automation;
- validators keep the raw evidence and never overwrite a report directory;
- the board's HTTP API is accessed without host proxy settings.

### Command map

| Command | Purpose | Changes the board? |
|---|---|---|
| `antihunter` / `doctor` | Find HTTP and USB access | No |
| `web` | Print SoftAP and Web UI connection details | No |
| `status` | Combine diagnostics, configuration, memory, queues, and drops | No |
| `validate` | Run the 14-check physical-board contract and save evidence | No |
| `get RESOURCE` | Read a curated API resource | No |
| `logs DATASET` | Read a curated log or export | No |
| `scan ...` | Start or stop a runtime radio operation | Yes; needs `--yes` |
| `sentinel status` | Read Sentinel state | No |
| `sentinel start\|stop` | Change Sentinel runtime state | Yes; needs `--yes` |
| `serial ports` | List likely ESP32 serial devices without opening them | No |
| `serial monitor\|command` | Open the USB serial path | May reset; needs `--yes` |

Every level has built-in help:

```bash
./scripts/antihunter --help
./scripts/antihunter scan --help
./scripts/antihunter scan device --help
```

For shorter interactive commands, define a shell alias from the repository
root:

```bash
alias antihunter="$PWD/scripts/antihunter"
antihunter status
```

### Global options

Global options may appear before or after the command.

```bash
./scripts/antihunter --json status
./scripts/antihunter status --json
./scripts/antihunter status --timeout 3
./scripts/antihunter status --base-url http://192.168.4.1
ANTIHUNTER_URL=http://192.168.4.1 ./scripts/antihunter status
```

`--base-url` defaults to `http://192.168.4.1`; `ANTIHUNTER_URL` is useful when a
router or second network interface provides a stable route to the node.

### Read status and evidence

Human-readable status is useful at the bench:

```bash
./scripts/antihunter status
```

JSON is designed for scripts and local collectors:

```bash
./scripts/antihunter status --json
./scripts/antihunter get detect-health --json
./scripts/antihunter sentinel status --json
```

The validator performs HTTP `GET` requests only. It saves the Web UI response,
diagnostics, headers, configuration, detector health, detector configuration,
a human report, and `summary.json`:

```bash
./scripts/antihunter validate --wait 180
./scripts/antihunter validate --output-dir hardware-test-results/bench-a --json
```

The older entry point remains as a compatibility wrapper around the same
contract:

```bash
./scripts/verify_n16r8_board.sh
```

### Read API resources and logs

`get` exposes named resources instead of arbitrary URLs:

```text
baseline-stats       baseline-status      config
detect-config        detect-health        devices
diag                 drone-status         gps
incidents            probes               results
rf-config            sd-status             sentinel-status
triangulation-status
```

Examples:

```bash
./scripts/antihunter get diag
./scripts/antihunter get devices --json
./scripts/antihunter get incidents --json
```

Available log datasets are:

```text
deauth        drones         evil-twin      incidents
jamming       mesh-commands  pmkid          probes
sae-dos       system         vibrations
```

```bash
./scripts/antihunter logs system
./scripts/antihunter logs incidents --json
```

Some resources depend on firmware feature flags or attached peripherals. A
generic N16R8 build intentionally has no external SD, GPS, RTC, vibration input,
PPS input, or Meshtastic UART until their pins are explicitly mapped.

### Run scanners

Starting or stopping a scanner is a runtime mutation, so confirmation is part
of every command:

```bash
./scripts/antihunter scan device --mode both --seconds 60 --capture-probes --yes
./scripts/antihunter scan target --mode wifi --channels 1,6,11 --seconds 300 --yes
./scripts/antihunter scan probe --mode wifi --seconds 120 --broadcast-all --yes
./scripts/antihunter scan baseline --seconds 300 --yes
./scripts/antihunter scan randomization --mode both --seconds 300 --yes
./scripts/antihunter scan deauth --seconds 300 --yes
./scripts/antihunter scan drone --mode both --forever --yes
./scripts/antihunter scan stop --yes
```

Modes are `wifi`, `ble`, or `both` where supported. Durations are bounded to one
day; `--forever` asks the firmware to keep the operation running. Wi-Fi channels
must be comma-separated values from 1 through 14.

Omit `--yes` to preview whether an operation is considered mutating. The CLI
exits with code 3 and sends no request.

### Sentinel

```bash
./scripts/antihunter sentinel status
./scripts/antihunter sentinel start --yes
./scripts/antihunter sentinel stop --yes
```

Sentinel availability follows the firmware build channel and feature flags. The
CLI does not claim a disabled engine is active; it reports the node response.

### USB serial

USB serial is useful for boot logs and mesh-style text input:

```bash
./scripts/antihunter serial ports
./scripts/antihunter serial monitor --seconds 20 --yes
./scripts/antihunter serial command STATUS --read-seconds 3 --yes
./scripts/antihunter serial command 'DEVICE_SCAN_START:2:60' --read-seconds 60 --yes
```

Use `--port /dev/cu.usbmodem...` when more than one candidate exists. Opening a
USB-UART device can toggle DTR/RTS and reset an ESP32, which is why monitor and
command operations require confirmation even for read-only text commands.

There is one important N16R8 detail: USB receives boot/debug output and accepts
input, but the current structured response helper still targets the disabled
legacy `Serial1` mesh UART. Until a USB response fallback lands in the firmware,
HTTP is the reliable status and control path for this target.

The CLI accepts only printable ASCII up to the firmware's 200-character limit.
It never forwards destructive serial command families. This does not remove the
firmware's destructive capabilities from other interfaces; use the Web UI and
raw API with care.

### Exit codes

| Code | Meaning |
|---:|---|
| `0` | Command or validation succeeded |
| `1` | Board/API/validation failure, or no matching serial device |
| `2` | Invalid arguments or ambiguous serial selection |
| `3` | Confirmation missing, or a destructive operation was refused |
| `130` | Interrupted by the operator |

The detailed interface reference is in [docs/CLI.md](docs/CLI.md).

## How an operator reaches AntiHunter

```text
                                      generic N16R8 node
browser ─────────────── SoftAP HTTP ─┬─ Web UI
scripts/antihunter ───── SoftAP HTTP ├─ diagnostics and API
USB terminal / CLI ───── USB serial ─┴─ boot logs and text input

Meshtastic app / TAK / MQTT ─ radio gateway ─ UART ─ node
                                           (optional; not wired by default)
```

The supported surfaces serve different jobs:

| Surface | Best use on N16R8 |
|---|---|
| Web UI | Full local configuration, scans, results, logs, and diagnostics |
| HTTP API | Deterministic local automation; this is what the CLI wraps |
| USB serial | Boot/runtime logs and conservative command input |
| Meshtastic text | Remote and fleet operation after a radio is safely wired |
| Command Center PRO | Multi-node operation through a Meshtastic USB radio |
| SD/export files | Offline retention on hardware with a mapped SD interface |
| PlatformIO/esptool | Build, install, update, and recovery—not normal operation |

The board's local HTTP API has no authentication layer of its own. Treat the
SoftAP password and network boundary as security controls. Do not forward the
API to an untrusted LAN or the public Internet.

## Why this fork exists

Upstream targets known AntiHunter/XIAO hardware. A generic module may share the
same ESP32-S3, flash, and PSRAM while routing every external peripheral
differently. This fork adds a separate profile instead of borrowing pins from a
board it cannot prove.

Our N16R8 work includes:

- `AntiHunter-n16r8-full`: 16 MB QIO flash, 8 MB octal PSRAM, full SoftAP Web UI,
  and the existing detector engine;
- compile-time gates that leave SD, GPS, DS3231, vibration, PPS, and Meshtastic
  UART pins unclaimed;
- `AntiHunter-n16r8-qemu`, an emulator-only DIO image;
- Wokwi and Espressif QEMU boot harnesses;
- a read-only, evidence-preserving physical-board validator;
- the dependency-free operator CLI and offline contract suite;
- CI coverage for the CLI contracts and the physical N16R8 build.

The safe generic target is deliberately conservative. Add peripherals only
after documenting and checking the exact carrier schematic and GPIO map.

## Physical validation record

The full N16R8 image was validated on a cable-connected board on **2026-08-14**.
The board had remained up for 49 minutes after a normal power-on reset when the
read-only validation completed.

| Observation | Result |
|---|---|
| Validator | **14 passed, 0 warnings, 0 failed** |
| Web UI | HTTP 200; 272,894 bytes; AntiHunter title present |
| Live API | `/diag`, `/config`, `/api/detect/health`, and `/api/detect/config` returned HTTP 200 |
| Internal heap | 141,136 bytes free |
| PSRAM | 8,240,532 bytes free |
| Runtime queues | Frame queue at 0 |
| Runtime drops | Wi-Fi, BLE, and mesh-gated drops at 0 |
| Build matrix | Full, headless, N16R8 full, and N16R8 QEMU passed |
| Virtual boot | Wokwi and Espressif QEMU smoke tests passed their expected boot paths |

This record proves the image boots, serves its UI/API, exposes sane live memory
health, and survives the checked runtime window on the tested board. It is not
a blanket certification of antenna performance, every detector, every carrier
layout, or every ESP32-S3 N16R8 product sold under that module name.

See [docs/N16R8.md](docs/N16R8.md) for the exact build, emulation, and hardware
safety boundary. The sanitized final run is preserved in the
[physical-validation evidence](docs/evidence/n16r8-physical-validation-2026-08-14.md).

## Build and verify

PlatformIO is the firmware toolchain. Build first; upload only after confirming
the module marking, USB recovery path, power, flash geometry, and carrier wiring.

```bash
pio run -e AntiHunter-n16r8-full
```

Only then, for the verified physical target:

```bash
pio run -e AntiHunter-n16r8-full -t upload
```

Available environments:

| Environment | Purpose | Physical upload? |
|---|---|---|
| `AntiHunter-full` | Upstream full Web UI target for XIAO hardware | Only to its intended hardware |
| `AntiHunter-headless` | Upstream serial/mesh target for XIAO hardware | Only to its intended hardware |
| `AntiHunter-n16r8-full` | This fork's generic N16R8 hardware image | Yes, after exact-board verification |
| `AntiHunter-n16r8-qemu` | DIO boot image for Espressif QEMU | **Never** |

Run the repository checks locally:

```bash
python3 -m unittest -v scripts/test_antihunter_cli.py
bash -n scripts/antihunter scripts/verify_n16r8_board.sh
make lint
pio run -e AntiHunter-n16r8-full
pio run -e AntiHunter-n16r8-qemu
```

The CLI test suite uses a fake local board. It does not open USB, join Wi-Fi,
contact a physical node, or mutate firmware state.

## Home automation

There are two sensible integration patterns.

For a durable installation, use the project's radio path:

```text
AntiHunter node → Meshtastic radio → MQTT broker → Home Assistant / Node-RED
```

That requires a Meshtastic radio, a verified UART pin map, and a build that
enables the mesh UART. The safe generic N16R8 image ships with that UART disabled.

For a bench or routed local network, consume CLI JSON:

```bash
./scripts/antihunter status --json
./scripts/antihunter get incidents --json
./scripts/antihunter logs incidents --json
```

This second path works only while the automation host can route to the board's
SoftAP. Keep identity, retries, scheduling, secret management, and retention in
the automation layer; keep HTTP parsing and device-specific validation in this
CLI. Never solve routing by publishing the unauthenticated board API.

## Repository map

| Path | What lives there |
|---|---|
| `scripts/antihunter` | Executable CLI entry point |
| `scripts/antihunter_cli.py` | HTTP, validation, serial, and safety contracts |
| `scripts/test_antihunter_cli.py` | Offline fake-board contract tests |
| `scripts/verify_n16r8_board.sh` | Compatibility entry point for physical validation |
| `docs/CLI.md` | Detailed interaction and automation notes |
| `docs/N16R8.md` | Build, emulator, and hardware-safety guide |
| `Antihunter/full/src/` | Full firmware and embedded Web UI |
| `Antihunter/headless/src/` | Headless firmware |
| `platformio.ini` | Hardware and emulator build environments |
| `wokwi.toml`, `diagram.json` | N16R8 virtual hardware model |

For the wider upstream hardware and operating model, see the bundled
[AntiHunter Operator's Guide](docs/AntiHunter-Operators-Guide.pdf).

## Responsible operation

AntiHunter is for lawful, authorized defensive work on networks, devices, radio
environments, and data you own or have explicit permission to assess. Wireless
identifiers, probe requests, BLE advertisements, and Remote ID broadcasts may
be regulated or personal data. Apply local radio, privacy, interception,
retention, and computer-misuse law before collecting them.

Detection is advisory and can produce false positives and false negatives. This
project is not a certified alarm, safety, aviation, medical, or control system.
You are responsible for power, antennas, batteries, wiring, regional radio
settings, access control, deployment, and any modifications made in this fork.

## Upstream and license

AntiHunter's original concept and hardware design are credited to
[@TheRealSirHaXalot](https://github.com/TheRealSirHaXalot). The maintained
upstream firmware is [lukeswitz/AntiHunter](https://github.com/lukeswitz/AntiHunter),
and the optional multi-node companion is
[AntiHunter Command Center PRO](https://github.com/TheRealSirHaXalot/AntiHunter-Command-Control-PRO).

The firmware and source in this repository are distributed under the
[GNU Affero General Public License v3.0](LICENSE). Third-party components retain
their own licenses; this project includes Apache-2.0-licensed OpenDroneID code.
The software, hardware material, and documentation are provided without warranty.
