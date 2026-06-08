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


if __name__ == "__main__":
    unittest.main()
