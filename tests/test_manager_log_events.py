# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._manager import log_events as manager_log_events


class ManagerLogEventsTests(unittest.TestCase):
    def test_transient_capabilities_failure_is_warning(self) -> None:
        severity = manager_log_events._event_log_severity(
            "manager.command",
            {
                "ok": False,
                "action": "capabilities",
                "error": {"code": "device_rpc_timeout", "message": "timed out"},
            },
        )
        self.assertEqual(severity, "warning")

    def test_non_transient_command_failure_is_error(self) -> None:
        severity = manager_log_events._event_log_severity(
            "manager.command",
            {
                "ok": False,
                "action": "set_frequency_hz",
                "error": {"code": "invalid_params", "message": "bad value"},
            },
        )
        self.assertEqual(severity, "error")

    def test_watchdog_trigger_uses_payload_severity(self) -> None:
        severity = manager_log_events._event_log_severity(
            "manager.watchdog.triggered",
            {
                "process_id": "watchdog",
                "severity": "critical",
                "message": "RC pressure > 1e-2 Torr, stopping RC turbo",
            },
        )
        self.assertEqual(severity, "critical")

    def test_watchdog_trigger_warn_alias_normalizes_to_warning(self) -> None:
        severity = manager_log_events._event_log_severity(
            "manager.watchdog.triggered",
            {
                "process_id": "watchdog",
                "severity": "warn",
                "message": "watchdog warning",
            },
        )
        self.assertEqual(severity, "warning")

    def test_watchdog_latch_cleared_is_info(self) -> None:
        severity = manager_log_events._event_log_severity(
            "manager.watchdog.latch_cleared",
            {"process_id": "watchdog"},
        )
        self.assertEqual(severity, "info")

    def test_routine_watchdog_events_are_not_logged(self) -> None:
        for topic in (
            "manager.watchdog.rules_loaded",
            "manager.watchdog.action_sent",
            "manager.watchdog.cleared",
        ):
            with self.subTest(topic=topic):
                severity = manager_log_events._event_log_severity(topic, {})
                self.assertIsNone(severity)

    def test_watchdog_lifecycle_severities(self) -> None:
        cases = {
            "manager.watchdog.action_started": "info",
            "manager.watchdog.action_succeeded": "info",
            "manager.watchdog.action_retry": "warning",
            "manager.watchdog.action_failed": "error",
            "manager.watchdog.latched": "warning",
            "manager.watchdog.recovered": "info",
            "manager.watchdog.latch_cleared": "info",
            "manager.watchdog.rule_error": "error",
        }
        for topic, expected in cases.items():
            with self.subTest(topic=topic):
                self.assertEqual(
                    manager_log_events._event_log_severity(topic, {}), expected
                )

    def test_watchdog_chain_completion_severity_reflects_summary(self) -> None:
        topic = "manager.watchdog.action_chain_completed"
        self.assertEqual(
            manager_log_events._event_log_severity(topic, {"success": True}), "info"
        )
        self.assertEqual(
            manager_log_events._event_log_severity(topic, {"success": False}), "error"
        )

    def test_process_failure_log_message_includes_stderr_and_heartbeat(self) -> None:
        manager = mock.Mock()
        manager_log_events.maybe_publish_log_event(
            manager,
            "manager.process.failed",
            {
                "process_id": "influx_writer",
                "error": "heartbeat stale (4.83s > 3.00s)",
                "tail_stderr": [{"message": "Traceback: database write hung"}],
                "last_heartbeat_payload": {"phase": "write_batch", "detail": "vacuum bucket"},
            },
        )

        call = manager._emit_log.call_args.kwargs
        self.assertEqual(call["severity"], "error")
        self.assertEqual(call["source_kind"], "process")
        self.assertEqual(call["source_id"], "influx_writer")
        self.assertIn("heartbeat stale", call["message"])
        self.assertIn("Traceback: database write hung", call["message"])
        self.assertIn("write_batch", call["message"])

    def test_failure_log_prefers_recent_log_when_stderr_absent(self) -> None:
        manager = mock.Mock()
        manager_log_events.maybe_publish_log_event(
            manager,
            "manager.process.failed",
            {
                "process_id": "watchdog",
                "error": "heartbeat stale",
                "tail_recent_logs": [{"message": "phase changed to evaluate_rules"}],
                "tail_logs": [{"message": "old unrelated error"}],
            },
        )

        call = manager._emit_log.call_args.kwargs
        self.assertIn("phase changed to evaluate_rules", call["message"])
        self.assertNotIn("old unrelated error", call["message"])

    def test_command_failure_prefers_explicit_webui_source(self) -> None:
        manager = mock.Mock()
        payload = {
            "device_id": "hipace_rc",
            "action": "start",
            "ok": False,
            "error": {"code": "CONDITION_FAILED"},
            "source_kind": "webui",
            "source_id": "beamline-vacuum",
        }
        manager_log_events.maybe_publish_log_event(manager, "manager.command", payload)

        call = manager._emit_log.call_args.kwargs
        self.assertEqual(call["severity"], "error")
        self.assertEqual(call["source_kind"], "webui")
        self.assertEqual(call["source_id"], "beamline-vacuum")
        self.assertEqual(call["device_id"], "hipace_rc")
        self.assertIn("hipace_rc.start", call["message"])

    def test_published_webui_issue_keeps_webui_source(self) -> None:
        manager = mock.Mock()
        payload = {
            "severity": "error",
            "message": "instance UI failed to load command capabilities",
            "source_kind": "webui",
            "source_id": "beamline-vacuum",
        }
        manager_log_events.maybe_publish_log_event(
            manager, "manager.instance_ui.error", payload
        )

        call = manager._emit_log.call_args.kwargs
        self.assertEqual(call["severity"], "error")
        self.assertEqual(call["source_kind"], "webui")
        self.assertEqual(call["source_id"], "beamline-vacuum")
        self.assertEqual(call["message"], "instance UI failed to load command capabilities")

    def test_process_failure_ignores_incidental_explicit_source(self) -> None:
        manager = mock.Mock()
        manager_log_events.maybe_publish_log_event(
            manager,
            "manager.process.failed",
            {
                "process_id": "watchdog",
                "error": "heartbeat stale",
                "source_kind": "webui",
                "source_id": "beamline-vacuum",
            },
        )

        call = manager._emit_log.call_args.kwargs
        self.assertEqual(call["source_kind"], "process")
        self.assertEqual(call["source_id"], "watchdog")

    def test_watchdog_trigger_emits_manager_log_entry(self) -> None:
        manager = mock.Mock()
        manager_log_events.maybe_publish_log_event(
            manager,
            "manager.watchdog.triggered",
            {
                "process_id": "watchdog",
                "watchdog_id": "vacuum-cryo_watchdog",
                "rule": "rc_pressure_turbo_off",
                "severity": "critical",
                "message": "RC pressure > 1e-2 Torr, stopping RC turbo",
            },
        )

        manager._emit_log.assert_called_once_with(
            severity="critical",
            topic="manager.watchdog.triggered",
            message="RC pressure > 1e-2 Torr, stopping RC turbo",
            source_kind="process",
            source_id="watchdog",
            device_id=None,
            process_id="watchdog",
            stream="event",
            payload={
                "process_id": "watchdog",
                "watchdog_id": "vacuum-cryo_watchdog",
                "rule": "rc_pressure_turbo_off",
                "severity": "critical",
                "message": "RC pressure > 1e-2 Torr, stopping RC turbo",
            },
        )

    def test_watchdog_latch_cleared_emits_manager_log_entry(self) -> None:
        manager = mock.Mock()
        payload = {
            "process_id": "watchdog",
            "watchdog_id": "vacuum",
            "rule": "pressure_high",
            "previous_latched": True,
            "previous_armed": True,
        }
        manager_log_events.maybe_publish_log_event(
            manager, "manager.watchdog.latch_cleared", payload
        )

        manager._emit_log.assert_called_once_with(
            severity="info",
            topic="manager.watchdog.latch_cleared",
            message="Watchdog vacuum:pressure_high latch cleared",
            source_kind="process",
            source_id="watchdog",
            device_id=None,
            process_id="watchdog",
            stream="event",
            payload=payload,
        )

    def test_watchdog_action_failure_gets_specific_message(self) -> None:
        manager = mock.Mock()
        payload = {
            "process_id": "watchdog",
            "trip_id": "trip-1",
            "watchdog_id": "vacuum",
            "rule": "pressure_high",
            "command": {"device_id": "turbo", "action": "stop"},
            "attempt": 2,
            "max_attempts": 2,
            "error": "timeout",
        }

        manager_log_events.maybe_publish_log_event(
            manager, "manager.watchdog.action_failed", payload
        )

        call = manager._emit_log.call_args.kwargs
        self.assertEqual(call["severity"], "error")
        self.assertEqual(
            call["message"],
            "Watchdog vacuum:pressure_high failed turbo.stop attempt 2/2: timeout",
        )
        self.assertEqual(call["payload"]["trip_id"], "trip-1")


if __name__ == "__main__":
    unittest.main()
