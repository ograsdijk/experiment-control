from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PATH = ROOT / "examples" / "linien_cli" / "processes" / "laser_lock_freq_nltl_power.py"

spec = importlib.util.spec_from_file_location("linien_nltl_follower_example", EXAMPLE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
LaserLockFreqNltlPowerFollower = module.LaserLockFreqNltlPowerFollower


class FakeManager:
    def __init__(
        self,
        *,
        command_response: dict[str, Any] | None = None,
        command_exception: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.telemetry: dict[tuple[str, str], dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.command_response = command_response or {"ok": True}
        self.command_exception = command_exception

    def call(self, payload: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
        del timeout_ms
        self.calls.append(payload)
        if payload.get("type") == "command" and self.command_exception is not None:
            raise self.command_exception
        return dict(self.command_response)

    def get_latest(self, device_id: str, signal: str) -> dict[str, Any] | None:
        return self.telemetry.get((device_id, signal))

    def publish_event(self, *, topic: str, payload: dict[str, Any], **kwargs: Any) -> None:
        self.events.append({"topic": topic, "payload": dict(payload), **kwargs})


class SynthHDFollowerExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix="synthhd-follower-test-"))
        self.addCleanup(shutil.rmtree, tmp_path, True)
        csv_path = tmp_path / "cal.csv"
        np.savetxt(csv_path, np.array([[100.0, -10.0], [200.0, -20.0]]), delimiter=",")
        self.csv_path = csv_path

    def _rules(self) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for channel in (0, 1):
            rules.extend(
                [
                    {
                        "name": f"guard_ch{channel + 1}",
                        "device_id": "SynthHD",
                        "trigger_action": "set_frequency",
                        "trigger_param": "freq_hz",
                        "channel": channel,
                        "max_step_hz": 25.0,
                        "current_freq_signal": f"ch{channel + 1}_frequency_hz",
                        "telemetry_max_age_s": 2.0,
                        "csv_path": str(self.csv_path),
                        "freq_col": 0,
                        "power_col": 1,
                        "effects": [],
                    },
                    {
                        "name": f"power_ch{channel + 1}",
                        "device_id": "SynthHD",
                        "trigger_action": "set_frequency",
                        "trigger_param": "freq_hz",
                        "channel": channel,
                        "csv_path": str(self.csv_path),
                        "freq_col": 0,
                        "power_col": 1,
                        "effects": [
                            {
                                "action": "set_power",
                                "param": "dbm",
                                "channel": channel,
                            }
                        ],
                    },
                ]
            )
        return rules

    def _make(self) -> Any:
        process = LaserLockFreqNltlPowerFollower(
            manager_rpc="tcp://127.0.0.1:1",
            manager_pub="tcp://127.0.0.1:2",
            rules=self._rules(),
        )
        process._rules = process._parse_rules()
        process._rule_enabled = {rule.rule_id: True for rule in process._rules}
        return process

    @staticmethod
    def _check(process: Any, params: dict[str, Any]) -> dict[str, Any]:
        return process._handle_rpc(
            {
                "type": "command_interceptor.check",
                "request_id": "r",
                "command": {
                    "device_id": "SynthHD",
                    "action": "set_frequency",
                    "params": params,
                },
            }
        )

    def test_route_registration_uses_current_manager_api(self) -> None:
        process = self._make()
        manager = FakeManager()
        process._register_routes(manager)
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(manager.calls[0]["type"], "manager.interceptors.register")
        self.assertEqual(
            manager.calls[0]["routes"],
            [{"device_id": "SynthHD", "action": "set_frequency"}],
        )

    def test_interceptor_selects_only_requested_channel(self) -> None:
        process = self._make()
        manager = FakeManager()
        manager.telemetry[("SynthHD", "ch1_frequency_hz")] = {
            "quality": "OK",
            "value": 120.0,
            "age_s": 0.0,
        }
        manager.telemetry[("SynthHD", "ch2_frequency_hz")] = {
            "quality": "OK",
            "value": 190.0,
            "age_s": 0.0,
        }
        process._manager = manager

        allowed = self._check(process, {"channel": 0, "freq_hz": 130.0})
        self.assertTrue(allowed["allow"])

        rejected = self._check(process, {"channel": 1, "freq_hz": 150.0})
        self.assertFalse(rejected["allow"])
        self.assertEqual(rejected["rule"], "guard_ch2")
        self.assertEqual(rejected["error"]["code"], "FREQ_STEP_TOO_LARGE")

    def test_invalid_or_missing_channel_is_rejected(self) -> None:
        process = self._make()
        for params in (
            {"freq_hz": 150.0},
            {"channel": "0", "freq_hz": 150.0},
            {"channel": True, "freq_hz": 150.0},
            {"channel": 2, "freq_hz": 150.0},
        ):
            with self.subTest(params=params):
                response = self._check(process, params)
                self.assertFalse(response["allow"])
                self.assertEqual(response["error"]["code"], "INVALID_CHANNEL")

    def test_nonfinite_frequency_is_rejected(self) -> None:
        process = self._make()
        for raw in ("nan", "inf", "-inf", math.nan, math.inf, -math.inf):
            with self.subTest(raw=raw):
                response = self._check(process, {"channel": 0, "freq_hz": raw})
                self.assertFalse(response["allow"])
                self.assertEqual(response["error"]["code"], "INVALID_FREQUENCY")

    def test_nonfinite_config_limit_is_rejected(self) -> None:
        rules = self._rules()
        rules[0] = dict(rules[0], max_step_hz="nan")
        process = LaserLockFreqNltlPowerFollower(
            manager_rpc="tcp://127.0.0.1:1",
            manager_pub="tcp://127.0.0.1:2",
            rules=rules,
        )
        with self.assertRaisesRegex(ValueError, "finite and > 0"):
            process._parse_rules()

    def test_follower_emits_generic_set_power_with_matching_driver_channel(self) -> None:
        process = self._make()
        manager = FakeManager()

        process._handle_command(
            {
                "ok": True,
                "device_id": "SynthHD",
                "action": "set_frequency",
                "params_json": json.dumps({"channel": 1, "freq_hz": 150.0}),
            },
            manager,
        )

        self.assertEqual(len(manager.calls), 1)
        command = manager.calls[0]
        self.assertEqual(command["action"], "set_power")
        self.assertEqual(command["device_id"], "SynthHD")
        self.assertEqual(command["params"]["channel"], 1)
        self.assertAlmostEqual(command["params"]["dbm"], -15.0)
        self.assertEqual(command["source_kind"], "process")
        self.assertEqual(command["source_id"], process._process_id)
        self.assertEqual(manager.events, [])

    def test_follower_reports_rejected_power_command(self) -> None:
        process = self._make()
        manager = FakeManager(
            command_response={
                "ok": False,
                "error": {"code": "power_rejected", "message": "no"},
            }
        )
        process._handle_command(
            {
                "ok": True,
                "device_id": "SynthHD",
                "action": "set_frequency",
                "params_json": json.dumps({"channel": 0, "freq_hz": 150.0}),
            },
            manager,
        )
        self.assertEqual(len(manager.events), 1)
        event = manager.events[0]
        self.assertEqual(event["topic"], "manager.log")
        self.assertEqual(event["severity"], "warning")
        self.assertIn("not applied", event["payload"]["message"])
        self.assertEqual(event["payload"]["error"]["code"], "power_rejected")

    def test_follower_reports_power_command_exception(self) -> None:
        process = self._make()
        manager = FakeManager(command_exception=RuntimeError("transport down"))
        process._handle_command(
            {
                "ok": True,
                "device_id": "SynthHD",
                "action": "set_frequency",
                "params_json": json.dumps({"channel": 0, "freq_hz": 150.0}),
            },
            manager,
        )
        self.assertEqual(len(manager.events), 1)
        event = manager.events[0]
        self.assertEqual(event["severity"], "error")
        self.assertIn("transport down", event["payload"]["message"])


if __name__ == "__main__":
    unittest.main()
