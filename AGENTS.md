# Repository Guidelines

## Project Structure & Module Organization

Firmware lives in `Antihunter/full/src/` and `Antihunter/headless/src/`. The full
variant includes the Web UI, device database, and mesh transport; shared detector,
scanner, and hardware changes may need coordinated edits in both trees.
`platformio.ini` defines the XIAO ESP32-S3, generic N16R8, and emulator targets.
Operator tooling and its Python contract tests live in `scripts/`. Documentation,
Web assets, and sanitized validation records belong in `docs/`; enclosure and BOM
artifacts are under `hw/`. Treat `Dist/` as release output, not a scratch directory.

## Build, Test, and Development Commands

- `make build` builds the full and headless XIAO images.
- `make build-n16r8-full` builds the physical 16 MB flash/8 MB PSRAM target.
- `make build-n16r8-qemu` creates the emulator-only image; never flash it.
- `make lint` runs C++17 `cppcheck` over full and headless sources.
- `python3 -m unittest -v scripts/test_antihunter_cli.py` runs the offline fake-board CLI contracts.
- `./scripts/antihunter validate --wait 180` performs the 14-check, read-only physical-board validation.

Use `pio run -e <environment>` when diagnosing one PlatformIO target. See
`docs/N16R8.md` for Wokwi and Espressif QEMU commands.

## Coding Style & Naming Conventions

Compile firmware as C++17 and follow the surrounding file's indentation; avoid
unrelated reformatting. Preserve existing `camelCase` functions, `PascalCase`
types, and uppercase constants/macros. Python uses four spaces, `snake_case`,
type hints, standard-library-first dependencies, and Black-compatible formatting.
Shell scripts use Bash with `set -euo pipefail` and quoted expansions.

## Testing Guidelines

Name Python tests `test_<behavior>` and keep them deterministic and hardware-free.
Every firmware change must compile for each affected environment and pass
`make lint`. Hardware-facing changes require the exact board profile, commands,
and observed results in the PR; publish only sanitized evidence.

## Commit & Pull Request Guidelines

Use short, imperative commits consistent with history, for example
`Add physical N16R8 validation` or `docs: clarify antenna setup`. Keep commits
focused. PRs must explain what and why, list affected hardware/targets, include
commands and results, link relevant issues, and add screenshots for Web UI changes.

## Security & Hardware Safety

Never commit credentials, captured identifiers, raw radio data, or local report
directories. Reads are the CLI default; runtime changes require `--yes`. Do not
flash, erase, reset, expose the local HTTP API, or upload artifacts to hosted
emulators without explicit authorization and a verified hardware target.
