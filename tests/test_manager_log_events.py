# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control import manager_log_events


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
