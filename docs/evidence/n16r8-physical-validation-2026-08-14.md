# N16R8 physical validation — 2026-08-14

This record captures the final read-only acceptance run for the generic
ESP32-S3-WROOM-1 N16R8 target. It contains no Wi-Fi credentials, host paths,
USB identifiers, or captured device data.

## Test subject

| Item | Observed value |
|---|---|
| Firmware | AntiHunter v1.0.2 Stable |
| Node | `AH40` |
| Target | `AntiHunter-n16r8-full` |
| Connection | USB power/data plus the board's `Antihunter` SoftAP |
| Board URL | `http://192.168.4.1` |
| Validation start | `2026-08-14T22:33:17Z` |
| Last reset | `POWERON` |
| Uptime before validation | `03:26:18` |

The Web UI was also opened manually at the board URL and showed the AntiHunter
scan dashboard for node `AH40`.

## Commands executed

```bash
./scripts/antihunter
./scripts/antihunter web
./scripts/antihunter status --json
./scripts/antihunter validate --wait 180
```

All four commands are read-only. The validator issued HTTP `GET` requests only;
it did not open serial, flash, erase, reboot, start a scan, or change settings.

## Live status before validation

| Metric | Observed value |
|---|---:|
| AP address | `192.168.4.1` |
| Wi-Fi channel | 6 |
| Scan state | Idle; no task active |
| Scan mode | Wi-Fi + BLE |
| Free internal heap | 65,972 bytes |
| Minimum internal heap | 41,860 bytes |
| Free PSRAM | 8,200,324 bytes |
| Frame queue | 0 |
| Wi-Fi drops | 0 |
| BLE drops | 0 |
| Mesh-gated drops | 0 |
| ESP32 temperature | 62.9 °C / 145.2 °F |

## Validator result

**Result: PASS — 14 passed, 0 failed.**

| Check | Result |
|---|---|
| Board reachable at `192.168.4.1` | PASS |
| `GET /` returned HTTP 200 | PASS — 272,894 bytes |
| Web UI identified itself as AntiHunter | PASS |
| `GET /diag` returned HTTP 200 | PASS — 736 bytes |
| Diagnostics reported the expected SoftAP address | PASS |
| Diagnostics reported application uptime | PASS |
| Diagnostics reported positive free internal heap | PASS |
| `GET /config` returned HTTP 200 | PASS — 85 bytes |
| `/config` parsed as JSON | PASS |
| `GET /api/detect/health` returned HTTP 200 | PASS — 468 bytes |
| `/api/detect/health` parsed as JSON | PASS |
| Live memory health | PASS — PSRAM 8,200,068 bytes; heap 65,972 bytes; frame queue 0 |
| `GET /api/detect/config` returned HTTP 200 | PASS — 837 bytes |
| `/api/detect/config` parsed as JSON | PASS |

## Acceptance boundary

This run proves that the tested physical board boots the N16R8 image, exposes
the full local Web UI and expected API, reports usable internal heap and octal
PSRAM, and remains healthy after more than three hours of uptime.

It does not certify every carrier sold with an N16R8 module, external peripheral
wiring, antenna performance, every detector signature, or the disabled generic
profile interfaces (SD, GPS, RTC, vibration/PPS, and Meshtastic UART).

## Independent build and emulator checks

The same source tree was checked through three paths after the physical run.

### Physical-target build

`AntiHunter-n16r8-full` built successfully in a clean temporary PlatformIO
environment using the repository's pinned pioarduino platform and Arduino 3.3.8.

| Metric | Build result |
|---|---:|
| Static RAM | 64,668 / 327,680 bytes (19.7%) |
| Application flash | 2,364,135 / 6,553,600 bytes (36.1%) |
| Combined factory image | Created successfully |

### Wokwi

Wokwi CLI 0.26.1 ran the physical-target factory image against the repository's
16 MB flash / 8 MB octal-PSRAM ESP32-S3 model. The test found the expected
`ANTIHUNTER DIGINODE v1.0.2 STABLE BOOT COMPLETE` marker and exited with
`TEST PASSED`.

Observed before the marker:

- 8 MB PSRAM initialized with 8,385,516 bytes free at early boot;
- probe-request, authentication-frame, and BLE-advertisement queues allocated
  in PSRAM;
- the SoftAP and embedded web server started;
- the detector task initialized;
- the generic external peripherals and mesh UART remained disabled.

Wokwi is a hosted simulation. It exercises the modeled ESP32-S3 boot, memory,
and basic Wi-Fi path; it does not replace physical RF or Bluetooth acceptance.

### Espressif QEMU

`AntiHunter-n16r8-qemu` built successfully and booted under Espressif's ARM64
QEMU 9.0.0 fork with machine `esp32s3`. The downloaded release archive matched
its published SHA-256:

```text
fb4ca6be7b1a4dbcf153879cf0582300f974371def0826c0c5b728f12812ad08
```

QEMU reached the same stable boot-complete marker and exited cleanly. As
designed, this emulator-only image reported that radio hardware was disabled
and that octal PSRAM was unavailable. This path independently checks CPU,
bootloader, partition, DIO flash, application startup, and internal-memory
fallback behavior; the image must never be uploaded to physical hardware.

### Repository contracts

- 10 offline operator-CLI contract tests passed;
- `cppcheck` passed for full and headless firmware;
- Black, Python byte-compilation, Bash syntax, ShellCheck, workflow YAML parsing,
  and Git diff hygiene passed.
