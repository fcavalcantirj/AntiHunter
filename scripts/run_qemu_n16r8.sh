#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
build_dir="${repo_root}/.pio/build/AntiHunter-n16r8-qemu"
factory_image="${build_dir}/firmware.factory.bin"
qemu_bin="${AH_QEMU_BIN:-qemu-system-xtensa}"

if [[ ! -f "${factory_image}" ]]; then
  echo "Missing ${factory_image}" >&2
  echo "Build it first: pio run -e AntiHunter-n16r8-qemu" >&2
  exit 2
fi

if ! command -v "${qemu_bin}" >/dev/null 2>&1; then
  echo "Espressif qemu-system-xtensa was not found." >&2
  echo "Set AH_QEMU_BIN to the Espressif QEMU binary path." >&2
  exit 2
fi

if ! "${qemu_bin}" -machine help 2>&1 | grep -q '^esp32s3'; then
  echo "${qemu_bin} is not Espressif's QEMU fork (esp32s3 machine missing)." >&2
  exit 2
fi

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/antihunter-qemu.XXXXXX")"
flash_image="${temp_dir}/qemu_flash.bin"
cleanup() {
  rm -f "${flash_image}"
  rmdir "${temp_dir}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cp "${factory_image}" "${flash_image}"
if command -v truncate >/dev/null 2>&1; then
  truncate -s 16777216 "${flash_image}"
elif command -v gtruncate >/dev/null 2>&1; then
  gtruncate -s 16777216 "${flash_image}"
else
  dd if=/dev/zero of="${flash_image}" bs=1 count=1 seek=16777215 conv=notrunc 2>/dev/null
fi

"${qemu_bin}" \
  -machine esp32s3 \
  -nographic \
  -drive "file=${flash_image},if=mtd,format=raw" \
  -serial mon:stdio \
  -no-reboot
