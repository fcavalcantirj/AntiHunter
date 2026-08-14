#!/usr/bin/env python3
"""Deterministic operator CLI for an AntiHunter node.

The default commands are read-only. Runtime changes require ``--yes`` and the
serial path refuses destructive erase/reset commands entirely. The HTTP client
uses only Python's standard library and deliberately ignores host proxy settings
because the default target is the board's isolated SoftAP.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import glob
import json
import os
from pathlib import Path
import platform
import re
import select
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterator
from urllib import error, parse, request

try:
    import termios
except ImportError:  # pragma: no cover - HTTP commands remain usable on Windows
    termios = None  # type: ignore[assignment]


VERSION = "0.1.0"
DEFAULT_BASE_URL = "http://192.168.4.1"
DEFAULT_TIMEOUT = 10.0
MODE_IDS = {"wifi": 0, "ble": 1, "both": 2}

READ_RESOURCES = {
    "diag": ("/diag", "text"),
    "config": ("/config", "json"),
    "detect-health": ("/api/detect/health", "json"),
    "detect-config": ("/api/detect/config", "json"),
    "sentinel-status": ("/api/sentinel/status", "json"),
    "results": ("/results", "text"),
    "gps": ("/gps", "text"),
    "sd-status": ("/sd-status", "text"),
    "rf-config": ("/rf-config", "json"),
    "baseline-status": ("/baseline/status", "json"),
    "baseline-stats": ("/baseline/stats", "json"),
    "devices": ("/api/devicedb", "json"),
    "probes": ("/api/probedb", "json"),
    "incidents": ("/api/incidents.json?limit=200", "json"),
    "drone-status": ("/drone/status", "json"),
    "triangulation-status": ("/triangulate/status", "json"),
}

LOG_RESOURCES = {
    "system": ("/api/antihunter.log", "text"),
    "incidents": ("/api/incidents.jsonl", "jsonl"),
    "mesh-commands": ("/api/mesh_cmd.jsonl", "jsonl"),
    "probes": ("/api/probes.jsonl", "jsonl"),
    "deauth": ("/api/deauth.jsonl", "jsonl"),
    "drones": ("/api/drones.jsonl", "jsonl"),
    "vibrations": ("/api/vibrations.jsonl", "jsonl"),
    "pmkid": ("/api/pmkid.jsonl", "jsonl"),
    "evil-twin": ("/api/eviltwin.jsonl", "jsonl"),
    "sae-dos": ("/api/sae_dos.jsonl", "jsonl"),
    "jamming": ("/api/jamming.jsonl", "jsonl"),
}

SERIAL_READ_ONLY = {
    "STATUS",
    "BASELINE_STATUS",
    "SENTINEL_STATUS",
    "ATTACKER_TRILAT_STATUS",
    "DETECT_CFG_GET",
    "AUTOERASE_STATUS",
    "VIBRATION_STATUS",
    "VIBSCAN_STATUS",
    "BATTERY_SAVER_STATUS",
}
SERIAL_DESTRUCTIVE_PREFIXES = (
    "ERASE_",
    "FACTORY_RESET",
    "AUTOERASE_",
    "CONFIG_ERASE_PSK",
)


class CliError(Exception):
    """A user-facing error with a stable process exit code."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


class BoardClient:
    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT):
        value = base_url.strip().rstrip("/")
        parsed = parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CliError(f"invalid board URL: {base_url!r}", 2)
        self.base_url = value
        self.timeout = timeout
        self.opener = request.build_opener(request.ProxyHandler({}))

    def request(
        self,
        method: str,
        path: str,
        *,
        form: dict[str, Any] | None = None,
        json_body: Any | None = None,
        require_success: bool = True,
        timeout: float | None = None,
    ) -> HttpResponse:
        if not path.startswith("/"):
            raise CliError(f"HTTP path must begin with '/': {path!r}", 2)
        data: bytes | None = None
        headers = {"User-Agent": f"antihunter-cli/{VERSION}"}
        if form is not None and json_body is not None:
            raise CliError("cannot send form and JSON in the same request", 2)
        if form is not None:
            data = parse.urlencode(form, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as raw:
                response = HttpResponse(
                    status=raw.status,
                    headers=dict(raw.headers.items()),
                    body=raw.read(),
                )
        except error.HTTPError as exc:
            response = HttpResponse(
                status=exc.code,
                headers=dict(exc.headers.items()),
                body=exc.read(),
            )
        except (error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise CliError(f"cannot reach {self.base_url}{path}: {reason}") from exc

        if require_success and not 200 <= response.status < 300:
            detail = response.text.strip()
            suffix = f": {detail}" if detail else ""
            raise CliError(
                f"{method.upper()} {path} returned HTTP {response.status}{suffix}"
            )
        return response

    def get(self, path: str, **kwargs: Any) -> HttpResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> HttpResponse:
        return self.request("POST", path, **kwargs)


def json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def parse_diag(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            result[key.strip()] = value.strip()
    return result


def serial_ports() -> list[str]:
    if sys.platform == "darwin":
        patterns = (
            "/dev/cu.usbmodem*",
            "/dev/cu.usbserial*",
            "/dev/cu.SLAB_USBtoUART*",
            "/dev/cu.wchusbserial*",
        )
    else:
        patterns = ("/dev/ttyACM*", "/dev/ttyUSB*")
    return sorted({path for pattern in patterns for path in glob.glob(pattern)})


def resolve_serial_port(value: str | None) -> str:
    if value:
        if not Path(value).exists():
            raise CliError(f"serial port does not exist: {value}", 2)
        return value
    ports = serial_ports()
    if not ports:
        raise CliError("no USB serial port found", 1)
    if len(ports) > 1:
        joined = "\n  ".join(ports)
        raise CliError(f"multiple serial ports found; choose --port:\n  {joined}", 2)
    return ports[0]


@contextlib.contextmanager
def open_serial(port: str, baud: int = 115200) -> Iterator[int]:
    if termios is None:
        raise CliError("USB serial support is available on macOS and Linux only", 2)
    if baud != 115200:
        raise CliError("only the firmware's supported 115200 baud rate is accepted", 2)
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as exc:
        raise CliError(f"cannot open serial port {port}: {exc}") from exc
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = termios.IGNPAR
        attrs[1] = 0
        attrs[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
        attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 1
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        yield fd
    finally:
        os.close(fd)


def read_serial(fd: int, seconds: float) -> bytes:
    deadline = time.monotonic() + seconds
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        wait = min(0.25, max(0.0, deadline - time.monotonic()))
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def serial_command_class(command: str) -> str:
    normalized = command.strip().upper()
    if normalized in SERIAL_READ_ONLY or normalized.startswith("INCIDENTS:"):
        return "read-only"
    if any(normalized.startswith(prefix) for prefix in SERIAL_DESTRUCTIVE_PREFIXES):
        return "destructive"
    return "mutating"


def require_yes(args: argparse.Namespace, action: str) -> None:
    if not getattr(args, "yes", False):
        raise CliError(f"{action} changes or may reset the board; rerun with --yes", 3)


def cmd_doctor(args: argparse.Namespace, client: BoardClient) -> int:
    ports = serial_ports()
    result: dict[str, Any] = {
        "cli_version": VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "base_url": client.base_url,
        "serial_ports": ports,
        "http": {"reachable": False},
    }
    try:
        response = client.get("/diag", timeout=min(client.timeout, 2.0))
        diag = parse_diag(response.text)
        result["http"] = {
            "reachable": True,
            "status": response.status,
            "node_id": diag.get("Mesh Node ID"),
            "uptime": diag.get("Up"),
            "ap_ip": diag.get("AP IP"),
        }
    except CliError as exc:
        result["http"]["error"] = str(exc)

    if args.json_output:
        json_print(result)
    else:
        print(f"AntiHunter CLI {VERSION}")
        print(f"Board URL: {client.base_url}")
        if result["http"]["reachable"]:
            print(
                "HTTP: PASS "
                f"(node={result['http'].get('node_id') or 'unknown'}, "
                f"uptime={result['http'].get('uptime') or 'unknown'})"
            )
        else:
            print(f"HTTP: unavailable ({result['http'].get('error')})")
        if ports:
            print("USB serial:")
            for port in ports:
                print(f"  {port}")
            if not result["http"]["reachable"]:
                print(
                    "Board is cabled. Join the 'Antihunter' Wi-Fi network "
                    "to reach its Web UI and HTTP API."
                )
        else:
            print("USB serial: none found")
        print("Safety: reads are default; runtime changes require --yes")
    return 0 if result["http"]["reachable"] or ports else 1


def cmd_web(args: argparse.Namespace, client: BoardClient) -> int:
    result = {
        "url": f"{client.base_url}/",
        "ssid": "Antihunter",
        "default_password": "antihunt3r123",
        "note": "Join the board's Wi-Fi first and change its default credentials.",
    }
    if args.json_output:
        json_print(result)
    else:
        print("AntiHunter Web UI")
        print(f"Wi-Fi: {result['ssid']}")
        print(f"Default password: {result['default_password']}")
        print(f"Open: {result['url']}")
        print("Change the default Wi-Fi credentials before regular use.")
    return 0


def fetch_json(client: BoardClient, path: str) -> Any:
    response = client.get(path)
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise CliError(f"GET {path} returned invalid JSON: {exc}") from exc


def cmd_status(args: argparse.Namespace, client: BoardClient) -> int:
    diag_text = client.get("/diag").text
    diag = parse_diag(diag_text)
    config = fetch_json(client, "/config")
    health = fetch_json(client, "/api/detect/health")
    result = {
        "base_url": client.base_url,
        "node_id": config.get("nodeId") if isinstance(config, dict) else None,
        "uptime": diag.get("Up"),
        "last_reset": diag.get("Last reset"),
        "scanning": diag.get("Scanning"),
        "task": diag.get("Task Type"),
        "scan_mode": diag.get("Scan Mode"),
        "radio": diag.get("Active Radio"),
        "channel": diag.get("Current channel"),
        "ap_ip": diag.get("AP IP"),
        "temperature": diag.get("ESP32 Temp"),
        "heap_free": health.get("heap_free") if isinstance(health, dict) else None,
        "heap_min": health.get("heap_min") if isinstance(health, dict) else None,
        "psram_free": health.get("psram_free") if isinstance(health, dict) else None,
        "queues": health.get("queues") if isinstance(health, dict) else None,
        "drops": health.get("drops") if isinstance(health, dict) else None,
    }
    if args.json_output:
        json_print(result)
    else:
        print(f"AntiHunter {result['node_id'] or 'unknown'} at {client.base_url}")
        print(
            f"Uptime {result['uptime'] or 'unknown'} | reset {result['last_reset'] or 'unknown'} "
            f"| scan {result['scanning'] or 'unknown'} ({result['task'] or 'none'})"
        )
        print(
            f"Radio {result['radio'] or 'unknown'} | mode {result['scan_mode'] or 'unknown'} "
            f"| channel {result['channel'] or 'unknown'}"
        )
        print(
            f"Heap {result['heap_free']} free / {result['heap_min']} minimum "
            f"| PSRAM {result['psram_free']} free"
        )
        print(f"Queues {result['queues']} | drops {result['drops']}")
        print(f"Temperature {result['temperature'] or 'unknown'}")
    return 0


class ValidationReport:
    def __init__(self, output_dir: Path, quiet: bool):
        self.output_dir = output_dir
        self.quiet = quiet
        self.checks: list[dict[str, Any]] = []
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        self.lines.append(text)
        if not self.quiet:
            print(text)

    def check(self, passed: bool, message: str) -> None:
        status = "PASS" if passed else "FAIL"
        self.checks.append({"status": status, "message": message})
        self.line(f"{status}: {message}")

    @property
    def passes(self) -> int:
        return sum(check["status"] == "PASS" for check in self.checks)

    @property
    def failures(self) -> int:
        return sum(check["status"] == "FAIL" for check in self.checks)

    def write(self) -> None:
        (self.output_dir / "report.txt").write_text(
            "\n".join(self.lines) + "\n", encoding="utf-8"
        )
        (self.output_dir / "summary.json").write_text(
            json.dumps(
                {
                    "passes": self.passes,
                    "failures": self.failures,
                    "result": "PASS" if self.failures == 0 else "FAIL",
                    "checks": self.checks,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


def response_headers_text(response: HttpResponse) -> str:
    lines = [f"HTTP {response.status}"]
    lines.extend(f"{key}: {value}" for key, value in response.headers.items())
    return "\n".join(lines) + "\n"


def cmd_validate(args: argparse.Namespace, client: BoardClient) -> int:
    if args.wait < 0:
        raise CliError("--wait must be non-negative", 2)
    repo_root = Path(__file__).resolve().parent.parent
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
        if not output_dir.is_absolute():
            output_dir = repo_root / output_dir
    else:
        output_dir = (
            repo_root / "hardware-test-results" / f"cli-{timestamp}-{os.getpid()}"
        )
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise CliError(
            f"refusing to overwrite report directory: {output_dir}", 2
        ) from exc

    report = ValidationReport(output_dir, quiet=args.json_output)
    report.line("AntiHunter physical-board validation")
    report.line(f"Started (UTC): {timestamp}")
    report.line(f"Base URL: {client.base_url}")
    report.line("Safety: read-only HTTP GET requests; no device mutations")
    report.line(f"Report directory: {output_dir}")
    report.line()
    report.line(f"Waiting up to {args.wait} seconds for {client.base_url} ...")

    deadline = time.monotonic() + args.wait
    reachable = False
    last_error = ""
    while True:
        try:
            probe = client.get("/diag", timeout=min(client.timeout, 2.0))
            reachable = probe.status == 200
        except CliError as exc:
            last_error = str(exc)
        if reachable or time.monotonic() >= deadline:
            break
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))

    if not reachable:
        report.check(False, f"board is unreachable ({last_error or 'timeout'})")
        report.line()
        report.line(f"SUMMARY: {report.passes} passed, {report.failures} failed")
        report.line("RESULT: FAIL")
        report.write()
        result = {
            "result": "FAIL",
            "passes": report.passes,
            "failures": report.failures,
            "report_dir": str(output_dir),
            "checks": report.checks,
        }
        if args.json_output:
            json_print(result)
        return 1
    report.check(True, f"board is reachable at {client.base_url}")

    def get_and_save(filename: str, path: str) -> HttpResponse | None:
        try:
            response = client.get(path, require_success=False)
        except CliError as exc:
            report.check(False, f"GET {path} failed ({exc})")
            return None
        (output_dir / filename).write_bytes(response.body)
        (output_dir / f"{filename}.headers").write_text(
            response_headers_text(response), encoding="utf-8"
        )
        if response.status != 200:
            report.check(False, f"GET {path} returned HTTP {response.status}")
            return None
        report.check(True, f"GET {path} returned HTTP 200 ({len(response.body)} bytes)")
        return response

    web = get_and_save("web-ui.html", "/")
    if web is not None:
        report.check(
            "<title>AntiHunter</title>" in web.text,
            "web UI identifies itself as AntiHunter",
        )

    diag_response = get_and_save("diag.txt", "/diag")
    if diag_response is not None:
        diag_text = diag_response.text
        report.check(
            bool(re.search(r"^AP IP: 192\.168\.4\.1$", diag_text, re.MULTILINE)),
            "diagnostics report the expected SoftAP address",
        )
        report.check(
            bool(re.search(r"^Up:\d+:\d{2}:\d{2}$", diag_text, re.MULTILINE)),
            "diagnostics report application uptime",
        )
        report.check(
            bool(re.search(r"^Free int heap: [1-9]\d*$", diag_text, re.MULTILINE)),
            "diagnostics report positive free internal heap",
        )

    config_response = get_and_save("config.json", "/config")
    if config_response is not None:
        try:
            config_response.json()
            report.check(True, "/config response is valid JSON")
        except json.JSONDecodeError:
            report.check(False, "/config response is valid JSON")

    health_response = get_and_save("detect-health.json", "/api/detect/health")
    if health_response is not None:
        health: Any = None
        try:
            health = health_response.json()
            report.check(True, "/api/detect/health response is valid JSON")
        except json.JSONDecodeError:
            report.check(False, "/api/detect/health response is valid JSON")
        if isinstance(health, dict):
            psram = health.get("psram_free")
            heap = health.get("heap_free")
            frame_queue = health.get("queues", {}).get("frame")
            valid = (
                isinstance(psram, int)
                and psram > 0
                and isinstance(heap, int)
                and heap > 0
                and isinstance(frame_queue, int)
                and frame_queue >= 0
            )
            report.check(
                valid,
                "live memory health is valid "
                f"(psram_free={psram} heap_free={heap} frame_queue={frame_queue})",
            )

    detect_config = get_and_save("detect-config.json", "/api/detect/config")
    if detect_config is not None:
        try:
            detect_config.json()
            report.check(True, "/api/detect/config response is valid JSON")
        except json.JSONDecodeError:
            report.check(False, "/api/detect/config response is valid JSON")

    report.line()
    report.line(f"SUMMARY: {report.passes} passed, {report.failures} failed")
    report.line(f"Raw responses and report: {output_dir}")
    report.line(f"RESULT: {'PASS' if report.failures == 0 else 'FAIL'}")
    report.write()

    result = {
        "result": "PASS" if report.failures == 0 else "FAIL",
        "passes": report.passes,
        "failures": report.failures,
        "report_dir": str(output_dir),
        "checks": report.checks,
    }
    if args.json_output:
        json_print(result)
    return 0 if report.failures == 0 else 1


def cmd_get(args: argparse.Namespace, client: BoardClient) -> int:
    path, kind = READ_RESOURCES[args.resource]
    response = client.get(path)
    if kind == "json":
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise CliError(f"GET {path} returned invalid JSON: {exc}") from exc
        json_print(value)
    elif args.json_output:
        json_print({"resource": args.resource, "path": path, "text": response.text})
    else:
        print(response.text, end="" if response.text.endswith("\n") else "\n")
    return 0


def parse_jsonl(text: str) -> list[Any]:
    rows: list[Any] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CliError(f"invalid JSONL at line {number}: {exc}") from exc
    return rows


def cmd_logs(args: argparse.Namespace, client: BoardClient) -> int:
    path, kind = LOG_RESOURCES[args.dataset]
    response = client.get(path)
    if args.json_output:
        value: Any = (
            parse_jsonl(response.text)
            if kind == "jsonl"
            else {
                "dataset": args.dataset,
                "path": path,
                "text": response.text,
            }
        )
        json_print(value)
    else:
        print(response.text, end="" if response.text.endswith("\n") else "\n")
    return 0


def validate_channels(value: str | None) -> str | None:
    if value is None:
        return None
    tokens = value.split(",")
    if not tokens or any(
        not token.isdigit() or not 1 <= int(token) <= 14 for token in tokens
    ):
        raise CliError("--channels must be comma-separated integers from 1 to 14", 2)
    return ",".join(str(int(token)) for token in tokens)


def scan_form(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if not 0 <= args.seconds <= 86400:
        raise CliError("--seconds must be between 0 and 86400", 2)
    mode = MODE_IDS[getattr(args, "mode", "both")]
    channels = validate_channels(args.channels)
    form: dict[str, Any] = {"secs": args.seconds}
    if args.forever:
        form["forever"] = 1
    if channels:
        form["ch"] = channels

    if args.scan_type == "target":
        form["mode"] = mode
        return "/scan", form
    if args.scan_type == "drone":
        form["droneScanMode"] = mode
        return "/drone", form

    detection_names = {
        "device": "device-scan",
        "baseline": "baseline",
        "randomization": "randomization-detection",
        "deauth": "deauth",
        "probe": "probe-scan",
    }
    form["detection"] = detection_names[args.scan_type]
    if args.scan_type == "device":
        form["deviceScanMode"] = mode
        if args.capture_probes:
            form["captureProbes"] = 1
    elif args.scan_type == "randomization":
        form["randomizationMode"] = mode
    elif args.scan_type == "probe":
        form["probeScanMode"] = mode
        if args.broadcast_all:
            form["broadcastAll"] = 1
    return "/sniffer", form


def cmd_scan(args: argparse.Namespace, client: BoardClient) -> int:
    if args.scan_type == "stop":
        require_yes(args, "stopping active operations")
        response = client.get("/stop")
        payload = {"action": "stop", "response": response.text.strip()}
    else:
        require_yes(args, f"starting a {args.scan_type} scan")
        path, form = scan_form(args)
        response = client.post(path, form=form)
        payload = {
            "action": "scan-start",
            "type": args.scan_type,
            "path": path,
            "parameters": form,
            "response": response.text.strip(),
        }
    if args.json_output:
        json_print(payload)
    else:
        print(payload["response"] or "OK")
    return 0


def cmd_sentinel(args: argparse.Namespace, client: BoardClient) -> int:
    if args.sentinel_action == "status":
        value = fetch_json(client, "/api/sentinel/status")
        if args.json_output:
            json_print(value)
        else:
            print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0
    require_yes(args, f"sentinel {args.sentinel_action}")
    response = client.post(f"/api/sentinel/{args.sentinel_action}")
    payload = {
        "action": f"sentinel-{args.sentinel_action}",
        "response": response.text.strip(),
    }
    if args.json_output:
        json_print(payload)
    else:
        print(payload["response"] or "OK")
    return 0


def cmd_serial(args: argparse.Namespace) -> int:
    if args.serial_action == "ports":
        ports = serial_ports()
        if args.json_output:
            json_print({"ports": ports})
        elif ports:
            print("\n".join(ports))
        else:
            print("No AntiHunter-compatible USB serial ports found.")
        return 0 if ports else 1

    if args.serial_action == "monitor":
        require_yes(args, "opening the USB serial port")
        port = resolve_serial_port(args.port)
        if not 0 < args.seconds <= 3600:
            raise CliError("--seconds must be greater than 0 and at most 3600", 2)
        with open_serial(port) as fd:
            output = read_serial(fd, args.seconds)
        text = output.decode("utf-8", errors="replace")
        if args.json_output:
            json_print({"port": port, "seconds": args.seconds, "output": text})
        else:
            print(text, end="" if text.endswith("\n") or not text else "\n")
        return 0

    command = args.serial_text.strip()
    if not command or len(command) > 200:
        raise CliError("serial command must contain 1 to 200 characters", 2)
    try:
        encoded = command.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CliError("serial commands must contain printable ASCII only", 2) from exc
    if any(byte < 32 or byte > 126 for byte in encoded):
        raise CliError("serial commands must contain printable ASCII only", 2)
    classification = serial_command_class(command)
    if classification == "destructive":
        raise CliError(
            "the personal CLI refuses erase, factory-reset, auto-erase, and erase-PSK commands",
            3,
        )
    require_yes(args, "opening the USB serial port and sending a command")
    port = resolve_serial_port(args.port)
    if not 0 <= args.read_seconds <= 60:
        raise CliError("--read-seconds must be between 0 and 60", 2)
    with open_serial(port) as fd:
        os.write(fd, encoded + b"\n")
        output = read_serial(fd, args.read_seconds)
    text = output.decode("utf-8", errors="replace")
    payload = {
        "port": port,
        "command": command,
        "classification": classification,
        "output": text,
    }
    if args.json_output:
        json_print(payload)
    else:
        print(text, end="" if text.endswith("\n") or not text else "\n")
    return 0


def add_scan_common(parser: argparse.ArgumentParser, *, supports_mode: bool) -> None:
    if supports_mode:
        parser.add_argument("--mode", choices=MODE_IDS, default="both")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--channels", help="comma-separated WiFi channels (1-14)")
    parser.add_argument("--yes", action="store_true", help="confirm the runtime change")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="antihunter",
        description="Safe operator CLI for AntiHunter web/API and USB interfaces.",
        epilog=(
            "examples: antihunter | antihunter status --json | "
            "antihunter validate --wait 180 | "
            "antihunter scan device --mode both --seconds 60 --yes"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ANTIHUNTER_URL", DEFAULT_BASE_URL),
        help=f"board URL (default: {DEFAULT_BASE_URL}; env: ANTIHUNTER_URL)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--json", dest="json_output", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.set_defaults(command="doctor", handler=cmd_doctor)

    commands = parser.add_subparsers(dest="command")

    doctor = commands.add_parser("doctor", help="discover USB and HTTP access")
    doctor.set_defaults(handler=cmd_doctor)

    status = commands.add_parser("status", help="show normalized live health")
    status.set_defaults(handler=cmd_status)

    web = commands.add_parser("web", help="show Web UI connection details")
    web.set_defaults(handler=cmd_web)

    validate = commands.add_parser(
        "validate", help="run the read-only physical validator"
    )
    validate.add_argument(
        "--wait", type=int, default=0, help="seconds to wait for the SoftAP"
    )
    validate.add_argument(
        "--output-dir", help="new report directory; never overwritten"
    )
    validate.set_defaults(handler=cmd_validate)

    get_parser = commands.add_parser("get", help="read one supported API resource")
    get_parser.add_argument("resource", choices=sorted(READ_RESOURCES))
    get_parser.set_defaults(handler=cmd_get)

    logs = commands.add_parser("logs", help="read a supported log dataset")
    logs.add_argument("dataset", choices=sorted(LOG_RESOURCES))
    logs.set_defaults(handler=cmd_logs)

    scan = commands.add_parser("scan", help="start or stop a radio operation")
    scan_commands = scan.add_subparsers(dest="scan_type", required=True)
    for name in (
        "target",
        "device",
        "baseline",
        "randomization",
        "deauth",
        "probe",
        "drone",
    ):
        item = scan_commands.add_parser(name)
        add_scan_common(item, supports_mode=name not in {"baseline", "deauth"})
        if name == "device":
            item.add_argument("--capture-probes", action="store_true")
        else:
            item.set_defaults(capture_probes=False)
        if name == "probe":
            item.add_argument("--broadcast-all", action="store_true")
        else:
            item.set_defaults(broadcast_all=False)
        item.set_defaults(handler=cmd_scan)
    stop = scan_commands.add_parser("stop")
    stop.add_argument(
        "--yes", action="store_true", help="confirm stopping active operations"
    )
    stop.set_defaults(handler=cmd_scan)

    sentinel = commands.add_parser("sentinel", help="inspect or control Sentinel")
    sentinel_commands = sentinel.add_subparsers(dest="sentinel_action", required=True)
    sentinel_status = sentinel_commands.add_parser("status")
    sentinel_status.set_defaults(handler=cmd_sentinel)
    for name in ("start", "stop"):
        item = sentinel_commands.add_parser(name)
        item.add_argument(
            "--yes", action="store_true", help="confirm the runtime change"
        )
        item.set_defaults(handler=cmd_sentinel)

    serial = commands.add_parser(
        "serial", help="discover, monitor, or command USB serial"
    )
    serial_commands = serial.add_subparsers(dest="serial_action", required=True)
    ports = serial_commands.add_parser("ports")
    ports.set_defaults(handler=cmd_serial)
    monitor = serial_commands.add_parser("monitor")
    monitor.add_argument("--port")
    monitor.add_argument("--seconds", type=float, default=15.0)
    monitor.add_argument(
        "--yes",
        action="store_true",
        help="accept that opening serial may reset some boards",
    )
    monitor.set_defaults(handler=cmd_serial)
    serial_command = serial_commands.add_parser("command")
    serial_command.add_argument("serial_text", metavar="COMMAND")
    serial_command.add_argument("--port")
    serial_command.add_argument("--read-seconds", type=float, default=3.0)
    serial_command.add_argument(
        "--yes",
        action="store_true",
        help="accept reset risk and confirm any non-read-only command",
    )
    serial_command.set_defaults(handler=cmd_serial)
    return parser


def normalize_global_options(argv: list[str]) -> list[str]:
    """Allow root options before or after a subcommand.

    Argparse normally stops recognizing root-parser options once it enters a
    subparser. Moving this small, known option set keeps the operator syntax
    forgiving without changing any command-specific arguments.
    """

    global_options: list[str] = []
    command_options: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            command_options.extend(argv[index:])
            break
        if token in {"--json", "--version"}:
            global_options.append(token)
            index += 1
            continue
        if token in {"--base-url", "--timeout"}:
            global_options.append(token)
            if index + 1 < len(argv):
                global_options.append(argv[index + 1])
                index += 2
            else:
                index += 1
            continue
        if token.startswith("--base-url=") or token.startswith("--timeout="):
            global_options.append(token)
            index += 1
            continue
        command_options.append(token)
        index += 1
    return global_options + command_options


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(normalize_global_options(raw_argv))
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout must be greater than 0 and at most 300")
    try:
        if args.command == "serial":
            return args.handler(args)
        client = BoardClient(args.base_url, args.timeout)
        return args.handler(args, client)
    except CliError as exc:
        if args.json_output:
            json_print({"error": str(exc), "exit_code": exc.exit_code})
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
