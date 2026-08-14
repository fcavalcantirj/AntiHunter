#!/usr/bin/env bash
set -u
set -o pipefail

repo_root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
base_url="http://192.168.4.1"
wait_seconds=180
output_dir=""

usage() {
  cat <<'EOF'
Usage: scripts/verify_n16r8_board.sh [options]

Run read-only checks against an AntiHunter N16R8 board's local SoftAP.
Start the script while still online, then join the Antihunter Wi-Fi network
when prompted. Internet loss while joined to the board is expected.

Options:
  --wait SECONDS       Time to wait for the board (default: 180)
  --base-url URL       Board URL (default: http://192.168.4.1)
  --output-dir DIR     Report directory (default: hardware-test-results/<time>)
  -h, --help           Show this help

This script performs HTTP GET requests only. It does not flash, erase, reboot,
or change any board setting. It requires curl and Python 3.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait)
      if [[ $# -lt 2 ]]; then
        echo "--wait requires a number of seconds" >&2
        exit 2
      fi
      wait_seconds="$2"
      shift 2
      ;;
    --base-url)
      if [[ $# -lt 2 ]]; then
        echo "--base-url requires a URL" >&2
        exit 2
      fi
      base_url="$2"
      shift 2
      ;;
    --output-dir)
      if [[ $# -lt 2 ]]; then
        echo "--output-dir requires a directory" >&2
        exit 2
      fi
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

if ! [[ "${wait_seconds}" =~ ^[0-9]+$ ]]; then
  echo "--wait must be a non-negative integer" >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required for JSON and PSRAM validation" >&2
  exit 2
fi

base_url="${base_url%/}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "${output_dir}" ]]; then
  output_dir="${repo_root}/hardware-test-results/n16r8-${timestamp}-$$"
elif [[ "${output_dir}" != /* ]]; then
  output_dir="${repo_root}/${output_dir}"
fi

if [[ -e "${output_dir}" ]]; then
  echo "Refusing to overwrite existing report directory: ${output_dir}" >&2
  exit 2
fi
mkdir -p "${output_dir}"

report="${output_dir}/report.txt"
passes=0
failures=0

log() {
  printf '%s\n' "$*" | tee -a "${report}"
}

pass() {
  passes=$((passes + 1))
  log "PASS: $*"
}

fail() {
  failures=$((failures + 1))
  log "FAIL: $*"
}

http_get() {
  local name="$1"
  local path="$2"
  local body="${output_dir}/${name}"
  local headers="${output_dir}/${name}.headers"
  local error_file="${output_dir}/${name}.curl-error"
  local http_code
  local curl_status
  local byte_count

  http_code="$(curl --silent --show-error --noproxy '*' \
    --connect-timeout 3 --max-time 20 \
    --dump-header "${headers}" --output "${body}" \
    --write-out '%{http_code}' "${base_url}${path}" 2>"${error_file}")"
  curl_status=$?

  if [[ ${curl_status} -ne 0 ]]; then
    fail "GET ${path} failed (curl exit ${curl_status}; see $(basename "${error_file}"))"
    return 1
  fi
  if [[ "${http_code}" != "200" ]]; then
    fail "GET ${path} returned HTTP ${http_code}"
    return 1
  fi

  byte_count="$(wc -c < "${body}")"
  byte_count="${byte_count//[[:space:]]/}"
  pass "GET ${path} returned HTTP 200 (${byte_count} bytes)"
  return 0
}

validate_json() {
  local file="$1"
  local label="$2"

  if python3 -m json.tool "${file}" >/dev/null 2>&1; then
    pass "${label} is valid JSON"
  else
    fail "${label} is not valid JSON"
    return 1
  fi
}

{
  printf 'AntiHunter N16R8 physical-board check\n'
  printf 'Started (UTC): %s\n' "${timestamp}"
  printf 'Base URL: %s\n' "${base_url}"
  printf 'Safety: read-only HTTP GET requests; no device mutations\n'
  printf 'Report directory: %s\n\n' "${output_dir}"
} | tee "${report}"

log "Join the board's Wi-Fi now: Antihunter (default password: antihunt3r123)."
log "The Mac will lose internet and Codex connectivity while joined; that is normal."
log "Waiting up to ${wait_seconds} seconds for ${base_url} ..."

now="$(date +%s)"
deadline=$(( now + wait_seconds ))
board_reachable=false
while (( now <= deadline )); do
  if curl --silent --fail --noproxy '*' --connect-timeout 1 --max-time 2 \
      "${base_url}/diag" >/dev/null 2>&1; then
    board_reachable=true
    break
  fi
  if [[ "${wait_seconds}" -eq 0 ]]; then
    break
  fi
  sleep 2
  now="$(date +%s)"
done

if [[ "${board_reachable}" != true ]]; then
  fail "Board did not become reachable at ${base_url} within ${wait_seconds} seconds"
  log ""
  log "Check that the Mac is joined to Antihunter and has a 192.168.4.x address, then rerun."
  log "Report: ${report}"
  exit 1
fi
pass "board is reachable at ${base_url}"

{
  printf '\n--- Host network state after association ---\n'
  date
  if command -v route >/dev/null 2>&1; then
    route -n get 192.168.4.1 2>&1 || true
  fi
} >>"${report}"

if http_get "web-ui.html" "/"; then
  if grep -q '<title>AntiHunter</title>' "${output_dir}/web-ui.html"; then
    pass "web UI identifies itself as AntiHunter"
  else
    fail "web UI response is missing the AntiHunter title"
  fi
fi

if http_get "diag.txt" "/diag"; then
  if grep -q '^AP IP: 192\.168\.4\.1$' "${output_dir}/diag.txt"; then
    pass "diagnostics report the expected SoftAP address"
  else
    fail "diagnostics do not report AP IP 192.168.4.1"
  fi
  if grep -q '^Up:[0-9][0-9]*:[0-9][0-9]:[0-9][0-9]$' "${output_dir}/diag.txt"; then
    pass "diagnostics report application uptime"
  else
    fail "diagnostics do not contain a valid uptime line"
  fi
  if grep -Eq '^Free int heap: [1-9][0-9]*$' "${output_dir}/diag.txt"; then
    pass "diagnostics report free internal heap"
  else
    fail "diagnostics do not report positive free internal heap"
  fi
fi

if http_get "config.json" "/config"; then
  validate_json "${output_dir}/config.json" "/config response"
fi

if http_get "detect-health.json" "/api/detect/health"; then
  if validate_json "${output_dir}/detect-health.json" "/api/detect/health response"; then
    health_summary="$(python3 - "${output_dir}/detect-health.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)

psram = data.get("psram_free")
heap = data.get("heap_free")
frame_queue = data.get("queues", {}).get("frame")
if not isinstance(psram, int) or psram <= 0:
    raise SystemExit(10)
if not isinstance(heap, int) or heap <= 0:
    raise SystemExit(11)
if not isinstance(frame_queue, int) or frame_queue < 0:
    raise SystemExit(12)
print(f"psram_free={psram} heap_free={heap} frame_queue={frame_queue}")
PY
)"
    health_status=$?
    if [[ ${health_status} -eq 0 ]]; then
      pass "live memory health is valid (${health_summary})"
    elif [[ ${health_status} -eq 10 ]]; then
      fail "live detector health reports no usable PSRAM"
    elif [[ ${health_status} -eq 11 ]]; then
      fail "live detector health reports no free heap"
    else
      fail "live detector health is missing the frame queue metric"
    fi
  fi
fi

if http_get "detect-config.json" "/api/detect/config"; then
  validate_json "${output_dir}/detect-config.json" "/api/detect/config response"
fi

log ""
log "SUMMARY: ${passes} passed, ${failures} failed"
log "Raw responses and the full report are in: ${output_dir}"
if [[ ${failures} -eq 0 ]]; then
  log "RESULT: PASS"
  log "Reconnect to your normal Wi-Fi, then tell Codex: board check finished."
  exit 0
fi

log "RESULT: FAIL"
log "Reconnect to your normal Wi-Fi, then give Codex the report path above."
exit 1
