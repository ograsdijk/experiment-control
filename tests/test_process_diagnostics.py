# ruff: noqa: E402

import json
import sys
import tempfile
import unittest
from pathlib import Path

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control import manager_process_logs
from experiment_control.processes.process_base import ManagedProcessBase
from experiment_control.utils.zmq_helpers import drain_multipart_nonblocking


class _FakeSocket:
    def __init__(self, messages: list[tuple[bytes, bytes]]) -> None:
        self.messages = list(messages)

    def recv_multipart(self, *, flags: int = 0) -> tuple[bytes, bytes]:
        _ = flags
        if not self.messages:
            raise zmq.Again()
        return self.messages.pop(0)


class _DummyLogManager:
    def __init__(self, directory: Path, *, max_bytes: int = 10_000, backups: int = 3) -> None:
        self._supervisor_log_dir = directory
        self._supervisor_log_max_bytes = max_bytes
        self._supervisor_log_backups = backups


class ProcessDiagnosticsTests(unittest.TestCase):
    def test_process_base_heartbeat_extra_fields_include_phase_progress_exception(self) -> None:
        proc = ManagedProcessBase(process_id="p", heartbeat_endpoint=None)
        proc._set_phase("drain_telemetry", "drained=1000 limited=true")
        proc._mark_progress("loop complete")
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            proc._record_exception(exc, phase="evaluate_rules")

        fields = proc._heartbeat_extra_fields()

        self.assertEqual(fields["phase"], "evaluate_rules")
        self.assertNotIn("detail", fields)
        self.assertIsInstance(fields["last_progress_wall"], float)
        self.assertIsInstance(fields["last_progress_mono"], float)
        self.assertIn("RuntimeError", fields["last_exception"])
        self.assertIn("boom", fields["last_traceback_summary"])

    def test_bounded_drain_stops_at_message_limit(self) -> None:
        sock = _FakeSocket([(b"t", b"1"), (b"t", b"2"), (b"t", b"3")])
        seen: list[bytes] = []

        result = drain_multipart_nonblocking(
            sock,  # type: ignore[arg-type]
            lambda _topic, payload: seen.append(payload) is None,
            max_messages=2,
            max_duration_s=None,
        )

        self.assertEqual(result.count, 2)
        self.assertTrue(result.limited)
        self.assertEqual(seen, [b"1", b"2"])

    def test_supervisor_jsonl_log_persists_raw_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = _DummyLogManager(Path(tmp))
            path = manager_process_logs.supervisor_log_path(
                manager,
                source_kind="process",
                source_id="influx/writer",
                pid=123,
                stream="stderr",
            )
            manager_process_logs.append_supervisor_jsonl(
                manager,
                {
                    "source_kind": "process",
                    "source_id": "influx/writer",
                    "process_id": "influx/writer",
                    "pid": 123,
                    "stream": "stderr",
                    "message": "hello",
                    "log_path": str(path),
                },
            )

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["message"], "hello")
            self.assertEqual(rows[0]["stream"], "stderr")
            self.assertIn("influx_writer", path.name)

    def test_supervisor_jsonl_log_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = _DummyLogManager(Path(tmp), max_bytes=10, backups=2)
            path = Path(tmp) / "process-p-1.stdout.jsonl"
            path.write_text("x" * 20, encoding="utf-8")
            manager_process_logs.append_supervisor_jsonl(
                manager,
                {
                    "source_kind": "process",
                    "source_id": "p",
                    "process_id": "p",
                    "pid": 1,
                    "stream": "stdout",
                    "message": "new",
                    "log_path": str(path),
                },
            )

            self.assertTrue(path.exists())
            self.assertTrue(path.with_name(f"{path.name}.1").exists())
            self.assertIn("new", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
