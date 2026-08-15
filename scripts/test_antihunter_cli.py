#!/usr/bin/env python3
"""Offline contract tests for scripts/antihunter_cli.py."""

from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs


HERE = Path(__file__).resolve().parent
CLI = HERE / "antihunter_cli.py"
VALIDATOR = HERE / "verify_n16r8_board.sh"

SPEC = importlib.util.spec_from_file_location("antihunter_cli", CLI)
assert SPEC is not None and SPEC.loader is not None
CLI_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLI_MODULE
SPEC.loader.exec_module(CLI_MODULE)


class FakeBoardHandler(BaseHTTPRequestHandler):
    posts: list[tuple[str, dict[str, list[str]]]] = []

    diag = """Scanning: no
Stopping: no
Task Type: none
Up:01:02:03
Last reset: POWERON
Free int heap: 141136
Scan Mode: WiFi+BLE
Active Radio: Idle
Current channel: 6
AP IP: 192.168.4.1
Mesh Node ID: AH40
ESP32 Temp: 55.0C / 131.0F
"""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_payload(self, payload: bytes, content_type: str = "text/plain") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_payload(b"<!doctype html><title>AntiHunter</title>", "text/html")
        elif path == "/diag":
            self.send_payload(self.diag.encode())
        elif path == "/config":
            self.send_payload(
                json.dumps({"nodeId": "AH40", "scanMode": 2}).encode(),
                "application/json",
            )
        elif path == "/api/detect/health":
            self.send_payload(
                json.dumps(
                    {
                        "uptime_ms": 3723000,
                        "heap_free": 141136,
                        "heap_min": 132616,
                        "psram_free": 8240532,
                        "queues": {"frame": 0},
                        "drops": {"wifi": 0, "ble": 0, "mesh_gated": 0},
                    }
                ).encode(),
                "application/json",
            )
        elif path == "/api/detect/config":
            self.send_payload(json.dumps({"pmkid": True}).encode(), "application/json")
        elif path == "/api/sentinel/status":
            self.send_payload(
                json.dumps({"running": False}).encode(), "application/json"
            )
        elif path == "/stop":
            self.send_payload(b"Stopped")
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        fields = parse_qs(body, keep_blank_values=True)
        type(self).posts.append((self.path, fields))
        if self.path == "/api/sentinel/start":
            self.send_payload(b"Sentinel started")
        elif self.path == "/api/sentinel/stop":
            self.send_payload(b"Sentinel stopped")
        elif self.path in {"/scan", "/sniffer", "/drone"}:
            self.send_payload(b"Scan starting")
        else:
            self.send_error(404)


class CliContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBoardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        FakeBoardHandler.posts.clear()

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(CLI),
            "--base-url",
            self.base_url,
            *arguments,
        ]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def test_status_has_stable_json_shape(self) -> None:
        result = self.run_cli("status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["node_id"], "AH40")
        self.assertEqual(payload["uptime"], "01:02:03")
        self.assertEqual(payload["psram_free"], 8240532)
        self.assertEqual(payload["drops"]["wifi"], 0)

    def test_value_global_options_work_after_command(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "status",
                "--base-url",
                self.base_url,
                "--timeout",
                "2",
                "--json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["node_id"], "AH40")

    def test_bare_invocation_runs_doctor(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HTTP: PASS", result.stdout)
        self.assertIn(self.base_url, result.stdout)

    def test_web_command_is_offline_and_machine_readable(self) -> None:
        result = self.run_cli("web", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["url"], f"{self.base_url}/")
        self.assertEqual(payload["ssid"], "Antihunter")
        self.assertEqual(payload["default_password"], "antihunt3r123")

    def test_validator_is_read_only_and_preserves_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_dir = Path(temporary) / "report"
            result = self.run_cli(
                "--json",
                "validate",
                "--wait",
                "0",
                "--output-dir",
                str(report_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["result"], "PASS")
            self.assertEqual(payload["passes"], 14)
            self.assertEqual(payload["failures"], 0)
            self.assertTrue((report_dir / "report.txt").is_file())
            self.assertTrue((report_dir / "summary.json").is_file())
            self.assertTrue((report_dir / "detect-health.json").is_file())
            self.assertEqual(FakeBoardHandler.posts, [])

    def test_shell_validator_delegates_to_the_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_dir = Path(temporary) / "shell-report"
            result = subprocess.run(
                [
                    str(VALIDATOR),
                    "--base-url",
                    self.base_url,
                    "--wait",
                    "0",
                    "--output-dir",
                    str(report_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads((report_dir / "summary.json").read_text())
            self.assertEqual(summary["passes"], 14)
            self.assertEqual(summary["failures"], 0)
            self.assertEqual(FakeBoardHandler.posts, [])

    def test_scan_refuses_to_mutate_without_confirmation(self) -> None:
        result = self.run_cli("scan", "device", "--seconds", "30")
        self.assertEqual(result.returncode, 3)
        self.assertIn("rerun with --yes", result.stderr)
        self.assertEqual(FakeBoardHandler.posts, [])

    def test_device_scan_maps_to_firmware_form_contract(self) -> None:
        result = self.run_cli(
            "--json",
            "scan",
            "device",
            "--mode",
            "both",
            "--seconds",
            "30",
            "--channels",
            "1,6,11",
            "--capture-probes",
            "--yes",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(FakeBoardHandler.posts), 1)
        path, form = FakeBoardHandler.posts[0]
        self.assertEqual(path, "/sniffer")
        self.assertEqual(form["detection"], ["device-scan"])
        self.assertEqual(form["deviceScanMode"], ["2"])
        self.assertEqual(form["secs"], ["30"])
        self.assertEqual(form["ch"], ["1,6,11"])
        self.assertEqual(form["captureProbes"], ["1"])

    def test_sentinel_status_is_read_only(self) -> None:
        result = self.run_cli("--json", "sentinel", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"running": False})
        self.assertEqual(FakeBoardHandler.posts, [])

    def test_destructive_serial_commands_are_classified(self) -> None:
        for command in (
            "ERASE_REQUEST",
            "ERASE_FORCE:token",
            "FACTORY_RESET:FULL:token",
            "AUTOERASE_ENABLE:1:2:3:4:5",
            "CONFIG_ERASE_PSK:secret",
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    CLI_MODULE.serial_command_class(command), "destructive"
                )
        self.assertEqual(CLI_MODULE.serial_command_class("STATUS"), "read-only")
        self.assertEqual(
            CLI_MODULE.serial_command_class("AUTOERASE_STATUS"), "read-only"
        )
        self.assertEqual(
            CLI_MODULE.serial_command_class("SCAN_START:2:60:1..11"), "mutating"
        )

    def test_channels_reject_non_ascii_digits_cleanly(self) -> None:
        # Regression: Python's str.isdigit() is True for chars like "²"/"①"
        # that int() then rejects, so the guard must not let them through to
        # int() and raise an uncaught ValueError instead of a clean exit-2.
        self.assertEqual(CLI_MODULE.validate_channels("1,6,11"), "1,6,11")
        self.assertIsNone(CLI_MODULE.validate_channels(None))
        for bad in ("0", "15", "1,,6", "²", "①"):
            with self.subTest(value=bad):
                with self.assertRaises(CLI_MODULE.CliError) as caught:
                    CLI_MODULE.validate_channels(bad)
                self.assertEqual(caught.exception.exit_code, 2)

    def test_validate_survives_null_queues_without_crashing(self) -> None:
        # Regression: a firmware reply of `"queues": null` must fail the memory
        # check cleanly, not crash the validator with an uncaught AttributeError.
        class NullQueueHandler(FakeBoardHandler):
            def do_GET(self) -> None:  # noqa: N802 - handler contract
                if self.path.split("?", 1)[0] == "/api/detect/health":
                    self.send_payload(
                        json.dumps(
                            {
                                "heap_free": 141136,
                                "psram_free": 8240532,
                                "queues": None,
                            }
                        ).encode(),
                        "application/json",
                    )
                    return
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), NullQueueHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            with tempfile.TemporaryDirectory() as temporary:
                report_dir = Path(temporary) / "null-queues"
                result = subprocess.run(
                    [
                        sys.executable,
                        str(CLI),
                        "--base-url",
                        base_url,
                        "--json",
                        "validate",
                        "--wait",
                        "0",
                        "--output-dir",
                        str(report_dir),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["result"], "FAIL")
            self.assertGreaterEqual(payload["failures"], 1)
            self.assertTrue(
                any(
                    check["status"] == "FAIL"
                    and "live memory health" in check["message"]
                    for check in payload["checks"]
                ),
                payload["checks"],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
