# ruff: noqa: E402

import queue
import sys
import threading
import time
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
        mgr._supervisor_log_queue = queue.Queue()  # type: ignore[attr-defined]
        mgr._supervisor_log_dropped = 0  # type: ignore[attr-defined]
        mgr._supervisor_pending_blocks = {}  # type: ignore[attr-defined]
        mgr._supervisor_log_threads = {}  # type: ignore[attr-defined]
        mgr._published_events = []  # type: ignore[attr-defined]
        # `_supervisor_handle_for` reads these; tests don't need real handles
        # but the dicts have to exist so the lookup doesn't AttributeError and
        # silently fall through the try/except in _failure_event_log_context.
        mgr._processes = {}  # type: ignore[attr-defined]
        mgr._devices = {}  # type: ignore[attr-defined]

        def publish_manager_event(topic: str, payload: dict[str, object]) -> None:
            mgr._published_events.append((topic, payload))  # type: ignore[attr-defined]

        mgr._publish_manager_event = publish_manager_event  # type: ignore[method-assign]
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

    def test_failure_tail_logs_include_late_reader_line_before_timeout(self) -> None:
        mgr = self._build_manager()

        def write_late_line() -> None:
            time.sleep(0.01)
            mgr._supervisor_log_queue.put_nowait(  # type: ignore[attr-defined]
                {
                    "source_kind": "process",
                    "source_id": "proc_a",
                    "stream": "stderr",
                    "pid": 123,
                    "process_id": "proc_a",
                    "message": "late crash line",
                }
            )

        thread = threading.Thread(target=write_late_line, daemon=True)
        mgr._supervisor_log_threads[  # type: ignore[attr-defined]
            ("process", "proc_a", 123, "stderr")
        ] = thread
        thread.start()

        entries = Manager._failure_event_tail_logs(  # type: ignore[arg-type]
            mgr,
            process_id="proc_a",
            pid=123,
        )

        thread.join(timeout=1.0)
        self.assertEqual([entry["message"] for entry in entries], ["late crash line"])
        self.assertEqual(entries[0]["severity"], "warning")
        self.assertEqual(entries[0]["stream"], "stderr")

    def test_failure_log_drain_wait_is_bounded_for_live_reader(self) -> None:
        mgr = self._build_manager()

        def stay_alive() -> None:
            time.sleep(0.2)

        thread = threading.Thread(target=stay_alive, daemon=True)
        mgr._supervisor_log_threads[  # type: ignore[attr-defined]
            ("driver", "dev_a", 456, "stdout")
        ] = thread
        thread.start()

        started = time.monotonic()
        Manager._drain_failure_event_supervisor_logs(  # type: ignore[arg-type]
            mgr,
            source_kind="driver",
            source_id="dev_a",
            pid=456,
            wait_timeout_s=0.01,
        )
        elapsed = time.monotonic() - started

        thread.join(timeout=1.0)
        self.assertLess(elapsed, 0.1)

    def test_failure_tail_logs_preserve_existing_shape_and_order(self) -> None:
        mgr = self._build_manager()
        mgr._log_history = [  # type: ignore[attr-defined]
            _entry(
                topic="manager.supervisor.process.stdout",
                severity="info",
                t_mono=1.0,
                message="first line",
                source_kind="process",
                source_id="proc_a",
                process_id="proc_a",
                payload_json='{"pid": 123}',
            ),
            _entry(
                topic="manager.supervisor.process.stderr",
                severity="error",
                t_mono=2.0,
                message="second line",
                source_kind="process",
                source_id="proc_a",
                process_id="proc_a",
                payload_json='{"pid": 123}',
            ),
        ]

        entries = Manager._failure_event_tail_logs(  # type: ignore[arg-type]
            mgr,
            process_id="proc_a",
            pid=123,
        )

        self.assertEqual(
            entries,
            [
                {
                    "severity": "info",
                    "stream": "event",
                    "message": "first line",
                    "t_wall": 1.0,
                    "t_mono": 1.0,
                },
                {
                    "severity": "error",
                    "stream": "event",
                    "message": "second line",
                    "t_wall": 1.0,
                    "t_mono": 2.0,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
