#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cli="${repo_root}/scripts/antihunter"
base_url="http://192.168.4.1"
wait_seconds=180
output_dir=""

usage() {
  cat <<'EOF'
Usage: scripts/verify_n16r8_board.sh [options]

Compatibility wrapper for the personal CLI's read-only physical validator.
Start it while still online, then join the Antihunter Wi-Fi network when
prompted. Internet loss while joined to the board is expected.

Options:
  --wait SECONDS       Time to wait for the board (default: 180)
  --base-url URL       Board URL (default: http://192.168.4.1)
  --output-dir DIR     New report directory (default: hardware-test-results/<time>)
  -h, --help           Show this help

The validator performs HTTP GET requests only. It does not flash, erase,
reboot, clear logs, start scans, or change any board setting. Python 3 is the
only runtime dependency.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait)
      [[ $# -ge 2 ]] || { echo "--wait requires seconds" >&2; exit 2; }
      wait_seconds="$2"
      shift 2
      ;;
    --base-url)
      [[ $# -ge 2 ]] || { echo "--base-url requires a URL" >&2; exit 2; }
      base_url="$2"
      shift 2
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || { echo "--output-dir requires a directory" >&2; exit 2; }
      output_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "${wait_seconds}" =~ ^[0-9]+$ ]] || {
  echo "--wait must be a non-negative integer" >&2
  exit 2
}
[[ -x "${cli}" ]] || {
  echo "Missing executable CLI: ${cli}" >&2
  exit 2
}
command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required" >&2
  exit 2
}

echo "AntiHunter N16R8 physical-board check"
echo "Join the board's Wi-Fi now: Antihunter (default password: antihunt3r123)."
echo "The Mac will lose internet and Codex connectivity while joined; that is normal."

args=(--base-url "${base_url}" validate --wait "${wait_seconds}")
if [[ -n "${output_dir}" ]]; then
  args+=(--output-dir "${output_dir}")
fi

set +e
"${cli}" "${args[@]}"
status=$?
set -e

echo "Reconnect to your normal Wi-Fi, then give Codex the report directory above."
exit "${status}"
