# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager import Manager


def _entry(
    *,
    topic: str,
    severity: str,
    t_mono: float,
    message: str = "",
    payload_json: str = "",
    source_kind: str = "manager",
    source_id: str = "manager",
    device_id: str | None = None,
    process_id: str | None = None,
) -> dict[str, object]:
    return {
        "topic": topic,
        "severity": severity,
        "message": message,
        "payload_json": payload_json,
        "source_kind": source_kind,
        "source_id": source_id,
        "device_id": device_id,
        "process_id": process_id,
        "ts": {"t_wall": 1.0, "t_mono": t_mono},
    }


class ManagerLogTailTests(unittest.TestCase):
    def _build_manager(self) -> Manager:
        mgr = object.__new__(Manager)
        mgr._log_history = []  # type: ignore[attr-defined]
        return mgr  # type: ignore[return-value]

    def test_log_tail_filters_by_since_and_severity(self) -> None:
        mgr = self._build_manager()
        mgr._log_history = [  # type: ignore[attr-defined]
            _entry(topic="manager.info", severity="info", t_mono=1.0),
            _entry(topic="manager.warn", severity="warning", t_mono=2.0),
            _entry(topic="manager.err", severity="error", t_mono=3.0),
        ]

        out = Manager._log_tail(  # type: ignore[arg-type]
            mgr,
            {"since_t_mono": 1.5, "severity_min": "warning"},
        )
        entries = out.get("entries", [])
        self.assertEqual(out.get("count"), 2)
        self.assertEqual(out.get("total_matched"), 2)
        self.assertEqual([e["topic"] for e in entries], ["manager.warn", "manager.err"])
        self.assertEqual(out.get("latest_t_mono"), 3.0)

    def test_log_tail_filters_topic_and_text_case_insensitive(self) -> None:
        mgr = self._build_manager()
        mgr._log_history = [  # type: ignore[attr-defined]
            _entry(
                topic="manager.driver.failed",
                severity="error",
                t_mono=5.0,
                message="Driver failed to connect",
            ),
            _entry(
                topic="manager.process.exited",
                severity="info",
                t_mono=6.0,
                payload_json='{"detail":"ok"}',
            ),
        ]

        out = Manager._log_tail(  # type: ignore[arg-type]
            mgr,
            {"topic_contains": "DRIVER", "text_contains": "FAILED"},
        )
        entries = out.get("entries", [])
        self.assertEqual(out.get("count"), 1)
        self.assertEqual(entries[0]["topic"], "manager.driver.failed")

    def test_log_tail_invalid_limit_raises(self) -> None:
        mgr = self._build_manager()
        with self.assertRaises(TypeError):
            Manager._log_tail(mgr, {"limit": "not-int"})  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
