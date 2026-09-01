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

    def _make_proc_with_responses(
        self, responses: list[dict[str, Any] | None]
    ) -> tuple[WatchdogProcess, list[tuple[str, dict[str, Any]]]]:
        proc = object.__new__(WatchdogProcess)
        proc._process_id = "watchdog-test"  # type: ignore[attr-defined]
        remaining = list(responses)
        events: list[tuple[str, dict[str, Any]]] = []

        class _FakeManager:
            def call(self, _req: dict, timeout_ms: int | None = None) -> dict | None:
                del timeout_ms
                return remaining.pop(0)

        proc._require_manager = lambda: _FakeManager()  # type: ignore[method-assign]
        proc._publish_event = (  # type: ignore[method-assign]
            lambda topic, payload: events.append((topic, dict(payload)))
        )
        return proc, events

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

    def test_successful_action_emits_correlated_lifecycle(self) -> None:
        proc, events = self._make_proc_with_responses([{"status": "OK"}])
        rule = _rule_with_actions(
            [CommandAction("yag", "close_shutter", {}, None, 0)]
        )

        summary = proc._execute_actions(
            watchdog_id="wd1", rule=rule, trip_id="trip-123"
        )

        self.assertEqual(
            [topic for topic, _payload in events],
            [
                "manager.watchdog.action_started",
                "manager.watchdog.action_sent",
                "manager.watchdog.action_succeeded",
                "manager.watchdog.action_chain_completed",
            ],
        )
        self.assertTrue(summary["success"])
        lifecycle_events = [
            payload
            for topic, payload in events
            if topic != "manager.watchdog.action_sent"
        ]
        self.assertTrue(
            all(payload["trip_id"] == "trip-123" for payload in lifecycle_events)
        )
        legacy_payload = next(
            payload
            for topic, payload in events
            if topic == "manager.watchdog.action_sent"
        )
        self.assertEqual(legacy_payload["attempt"], 1)
        self.assertEqual(legacy_payload["retries"], 0)
        self.assertNotIn("trip_id", legacy_payload)

    def test_retry_failure_is_warning_event_not_terminal_failure(self) -> None:
        proc, events = self._make_proc_with_responses(
            [{"ok": False, "error": {"code": "busy"}}, {"status": "OK"}]
        )
        rule = _rule_with_actions(
            [CommandAction("yag", "close_shutter", {}, None, 1)]
        )

        summary = proc._execute_actions(
            watchdog_id="wd1", rule=rule, trip_id="trip-retry"
        )

        topics = [topic for topic, _payload in events]
        self.assertIn("manager.watchdog.action_retry", topics)
        self.assertNotIn("manager.watchdog.action_failed", topics)
        self.assertTrue(summary["success"])
        retry_payload = next(
            payload
            for topic, payload in events
            if topic == "manager.watchdog.action_retry"
        )
        self.assertEqual(retry_payload["attempt"], 1)
        self.assertEqual(retry_payload["max_attempts"], 2)

    def test_exhausted_action_emits_failure_and_failed_chain_summary(self) -> None:
        proc, events = self._make_proc_with_responses([None, None])
        rule = _rule_with_actions(
            [CommandAction("yag", "close_shutter", {}, None, 1)]
        )

        summary = proc._execute_actions(
            watchdog_id="wd1", rule=rule, trip_id="trip-failed"
        )

        topics = [topic for topic, _payload in events]
        self.assertEqual(topics.count("manager.watchdog.action_retry"), 1)
        self.assertEqual(topics.count("manager.watchdog.action_failed"), 1)
        self.assertFalse(summary["success"])
        self.assertEqual(summary["failed_actions"], 1)

    def test_failed_action_does_not_block_later_shutdown_actions(self) -> None:
        proc, events = self._make_proc_with_responses(
            [
                {"status": "OK"},
                None,
                {"status": "OK"},
                {"status": "OK"},
            ]
        )
        rule = _rule_with_actions(
            [
                CommandAction("hipace_rc", "stop", {}, 1.5, 0),
                CommandAction("hipace_eql", "stop", {}, 1.5, 0),
                CommandAction("hipace_det", "stop", {}, 1.5, 0),
                CommandAction("hipace_spb", "stop", {}, 1.5, 0),
            ]
        )

        summary = proc._execute_actions(
            watchdog_id="vacuum-cryo_watchdog",
            rule=rule,
            trip_id="trip-partial",
        )

        started = [
            payload["command"]["device_id"]
            for topic, payload in events
            if topic == "manager.watchdog.action_started"
        ]
        self.assertEqual(
            started,
            ["hipace_rc", "hipace_eql", "hipace_det", "hipace_spb"],
        )
        self.assertFalse(summary["success"])
        self.assertEqual(summary["action_count"], 4)
        self.assertEqual(summary["succeeded_actions"], 3)
        self.assertEqual(summary["failed_actions"], 1)
        failed = [result for result in summary["actions"] if not result["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["command"]["device_id"], "hipace_eql")
        self.assertIn(
            "manager.watchdog.action_chain_completed",
            [topic for topic, _payload in events],
        )


if __name__ == "__main__":
    unittest.main()
