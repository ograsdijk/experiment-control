# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
