# ruff: noqa: E402
"""Watchdog `process` action: parsing + dispatch (e.g. sequencer.pause).

The watchdog historically dispatched device commands only. A `process`
action lets a rule invoke a process RPC through the manager — used so the
neon-flow watchdog can pause the sequencer on flow loss.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.watchdog import (
    CommandAction,
    ProcessAction,
    WatchdogProcess,
    WatchdogRule,
    _parse_watchdog_actions,
)
from experiment_control.rules.rules_common import TelemetryBinding
from experiment_control.utils.config_parsing import ConfigError


def _rule_with_actions(actions: list[Any]) -> WatchdogRule:
    return WatchdogRule(
        name="r1",
        severity="critical",
        message=None,
        telemetry=[TelemetryBinding(alias="t", device_id="dev1", signal="x", max_age_s=1.0)],
        condition=True,
        stable_for_s=0.0,
        cooldown_s=0.0,
        latch=False,
        on_unknown="ignore",
        actions=actions,
    )


class WatchdogActionParsingTests(unittest.TestCase):
    def test_parse_process_action(self) -> None:
        actions = _parse_watchdog_actions(
            rule_raw={
                "actions": [
                    {
                        "process": {
                            "process_id": "sequencer",
                            "action": "sequencer.pause",
                            "params": {},
                            "retries": 2,
                        }
                    }
                ]
            },
            rule_index=0,
        )
        self.assertEqual(len(actions), 1)
        act = actions[0]
        self.assertIsInstance(act, ProcessAction)
        assert isinstance(act, ProcessAction)
        self.assertEqual(act.process_id, "sequencer")
        self.assertEqual(act.action, "sequencer.pause")
        self.assertEqual(act.params, {})
        self.assertEqual(act.retries, 2)

    def test_parse_command_action_still_supported(self) -> None:
        actions = _parse_watchdog_actions(
            rule_raw={
                "actions": [
                    {"command": {"device_id": "yag", "action": "close_shutter", "params": {}}}
                ]
            },
            rule_index=0,
        )
        self.assertIsInstance(actions[0], CommandAction)
        assert isinstance(actions[0], CommandAction)
        self.assertEqual(actions[0].device_id, "yag")

    def test_parse_mixed_command_and_process(self) -> None:
        actions = _parse_watchdog_actions(
            rule_raw={
                "actions": [
                    {"command": {"device_id": "yag", "action": "close_shutter"}},
                    {"process": {"process_id": "sequencer", "action": "sequencer.pause"}},
                ]
            },
            rule_index=0,
        )
        self.assertIsInstance(actions[0], CommandAction)
        self.assertIsInstance(actions[1], ProcessAction)

    def test_action_must_have_exactly_one_kind(self) -> None:
        for bad in ({}, {"command": {}, "process": {}}):
            with self.assertRaises(ConfigError):
                _parse_watchdog_actions(rule_raw={"actions": [bad]}, rule_index=0)


class WatchdogActionDispatchTests(unittest.TestCase):
    def _make_proc(self) -> tuple[WatchdogProcess, list[dict]]:
        proc = object.__new__(WatchdogProcess)
        proc._process_id = "watchdog-test"  # type: ignore[attr-defined]
        sent: list[dict] = []

        class _FakeManager:
            def call(self, req: dict, timeout_ms: int | None = None) -> dict:
                sent.append(req)
                return {"status": "OK"}

        proc._require_manager = lambda: _FakeManager()  # type: ignore[method-assign]
        proc._publish_event = lambda *_a, **_k: None  # type: ignore[method-assign]
        return proc, sent

    def test_process_action_dispatches_processes_rpc_envelope(self) -> None:
        proc, sent = self._make_proc()
        rule = _rule_with_actions(
            [
                ProcessAction(
                    process_id="sequencer",
                    action="sequencer.pause",
                    params={},
                    timeout_s=None,
                    retries=0,
                )
            ]
        )
        proc._execute_actions(watchdog_id="wd1", rule=rule)
        self.assertEqual(
            sent,
            [
                {
                    "type": "manager.processes.rpc",
                    "process_id": "sequencer",
                    "request": {"type": "sequencer.pause", "params": {}},
                    "caller_process_id": "watchdog-test",
                }
            ],
        )

    def test_command_action_dispatch_unchanged(self) -> None:
        proc, sent = self._make_proc()
        rule = _rule_with_actions(
            [
                CommandAction(
                    device_id="yag",
                    action="close_shutter",
                    params={},
                    timeout_s=None,
                    retries=0,
                )
            ]
        )
        proc._execute_actions(watchdog_id="wd1", rule=rule)
        self.assertEqual(
            sent,
            [
                {
                    "type": "command",
                    "device_id": "yag",
                    "action": "close_shutter",
                    "params": {},
                    "caller_process_id": "watchdog-test",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
