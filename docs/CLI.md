# AntiHunter interaction surfaces and personal CLI

AntiHunter is intentionally self-contained. The firmware exposes several control
surfaces, but they serve different jobs and are not interchangeable.

## Supported interaction methods

| Surface | Transport | What it is for | N16R8 status |
|---|---|---|---|
| Web UI | Browser over the board's SoftAP at `http://192.168.4.1` | Full configuration, scans, results, detector controls, logs and diagnostics | **Primary interface** |
| HTTP API | HTTP over the same SoftAP | Deterministic automation behind the Web UI | **Primary automation interface** |
| USB serial | 115200 baud | Boot/runtime logs, time setting and mesh-style text commands | Logs and command input work; structured command replies still target the disabled legacy mesh UART |
| Meshtastic text | UART to a Meshtastic radio | Remote node commands, detections and fleet coordination | Disabled by the safe generic N16R8 profile because its carrier wiring is unknown |
| Meshtastic integrations | Phone app, TAK/ATAK or a Meshtastic MQTT gateway | Human/fleet control and bridging to MQTT, Home Assistant or Node-RED | Requires a separately wired and enabled Meshtastic radio |
| Command Center PRO | Meshtastic USB radio plus the companion server | Multi-node maps, history, TAK/MQTT/ADS-B and Sentinel operations | Available when a Meshtastic transport is added |
| SD/export files | SD card and HTTP download endpoints | Offline evidence, long-running logs and dataset export | External SD is disabled on the generic N16R8 profile |
| Provisioning tools | Web flasher, `Dist/flashAntihunter.sh`, PlatformIO and esptool | Install/update firmware and initial configuration | PlatformIO N16R8 target is supported; flashing resets and writes the board |

The Web UI is an HTTP client embedded in the firmware. Browser actions ultimately
call the same `/scan`, `/sniffer`, `/stop`, `/config`, `/api/detect/*` and log
endpoints available to other local clients.

## Personal operator CLI

This fork adds `scripts/antihunter`, a dependency-free Python CLI around the
stable web/API surface plus conservative USB serial support.

```bash
./scripts/antihunter
./scripts/antihunter doctor
./scripts/antihunter web
./scripts/antihunter status
./scripts/antihunter validate --wait 180
./scripts/antihunter get detect-health
./scripts/antihunter logs incidents
./scripts/antihunter serial ports
```

Set a non-default node URL either globally or through the environment:

```bash
ANTIHUNTER_URL=http://192.168.4.1 ./scripts/antihunter status
./scripts/antihunter --base-url http://192.168.4.1 status
```

Global flags may go before or after the command. `--json` produces stable
machine-readable output:

```bash
./scripts/antihunter --json status
./scripts/antihunter status --json
./scripts/antihunter get config --json
./scripts/antihunter validate --output-dir hardware-test-results/manual-check --json
```

### Read-only commands

- A bare `antihunter` invocation runs `doctor`.
- `doctor` reports HTTP reachability and matching USB serial ports.
- `web` prints the SoftAP name, default password and Web UI URL without making a
  network request.
- `status` normalizes `/diag`, `/config` and `/api/detect/health` into one view.
- `validate` runs the 14-check physical validator using GET requests only and
  stores the raw responses, headers, human report and `summary.json`.
- `get RESOURCE` reads a curated API resource. Run `get --help` for the catalog.
- `logs DATASET` reads a curated log/export endpoint.
- `sentinel status` reads the Sentinel engine state.
- `serial ports` discovers likely ESP32 USB serial devices without opening them.

### Runtime-control commands

Runtime changes are non-interactive and require `--yes`. Omitting it exits with
code 3 without sending a request.

```bash
./scripts/antihunter scan device --mode both --seconds 60 --capture-probes --yes
./scripts/antihunter scan target --mode wifi --channels 1,6,11 --seconds 300 --yes
./scripts/antihunter scan probe --mode wifi --seconds 120 --yes
./scripts/antihunter scan drone --mode both --forever --yes
./scripts/antihunter scan stop --yes

./scripts/antihunter sentinel start --yes
./scripts/antihunter sentinel stop --yes
```

The CLI deliberately does not expose raw HTTP POST/DELETE, factory reset,
secure erase, auto-erase or erase-PSK configuration. Those operations are too
consequential for a convenience abstraction.

### USB serial

Opening some USB-UART adapters toggles DTR/RTS and resets the ESP32. Monitoring
and serial commands therefore require `--yes` even when the command itself is
read-only.

```bash
./scripts/antihunter serial monitor --seconds 20 --yes
./scripts/antihunter serial command STATUS --read-seconds 3 --yes
./scripts/antihunter serial command 'DEVICE_SCAN_START:2:60' --read-seconds 60 --yes
```

Use `--port /dev/cu.usbmodem...` when more than one serial device is connected.
The CLI accepts printable ASCII up to the firmware's 200-character limit and
refuses the destructive serial command families entirely.

On `AntiHunter-n16r8-full`, USB receives boot/debug output and accepts commands,
but response helpers still write acknowledgements to `Serial1`. Since the safe
N16R8 profile disables that unverified UART, HTTP is the reliable status/control
surface until a USB-response fallback is added to the firmware.

## Home automation fit

The `home-automations` project uses a useful rule: the agent selects validated
arguments, while a deterministic tool owns transport, parsing, identity and
failure handling. This CLI follows that pattern:

- no credentials or personal configuration are embedded;
- reads are the default and have stable JSON output;
- writes require an explicit flag and destructive writes are absent;
- validators preserve raw evidence instead of returning only a green/red claim;
- non-zero exit codes distinguish device failure, invalid use and missing
  confirmation.

The official fleet/home-automation bridge remains **Meshtastic MQTT**. A gateway
radio can publish AntiHunter text traffic to a broker consumed by Home Assistant
or Node-RED. The CLI can also feed a command-line sensor or local collector with
`--json`, but only from a host that has a network route to the board's isolated
SoftAP. Do not expose the board's unauthenticated local HTTP API to an untrusted
LAN or the public internet.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Command or validation succeeded |
| `1` | Board/API/validation failure, or no matching device found |
| `2` | Invalid arguments or ambiguous device selection |
| `3` | Mutation was not confirmed, or the CLI refused a destructive operation |
| `130` | Interrupted by the operator |

## Tests

The contract suite runs against an offline fake board and never touches hardware:

```bash
python3 -m unittest -v scripts/test_antihunter_cli.py
```

It verifies the JSON status contract, all 14 read-only validation checks,
evidence persistence, mutation confirmation, scan form mapping and destructive
serial-command classification.
