# ruff: noqa: E402

import sys
import time
import unittest
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.command_journal import CommandJournal, CommandJournalSettings


class _SlowWriteCommandJournal(CommandJournal):
    def __init__(self, *, settings: CommandJournalSettings, instance_id: str, delay_s: float):
        super().__init__(settings=settings, instance_id=instance_id)
        self._delay_s = float(delay_s)

    def _write_batch(self, conn, batch):  # type: ignore[override]
        time.sleep(self._delay_s)
        super()._write_batch(conn, batch)


class CommandJournalTests(unittest.TestCase):
    def _make_tempfile_path(self) -> Path:
        root = ROOT / ".tmp_tests"
        root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="command_journal_", suffix=".sqlite3", dir=str(root))
        try:
            os.close(fd)
        except Exception:
            pass
        Path(name).unlink(missing_ok=True)
        path = Path(name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.addCleanup(lambda: Path(str(path) + "-wal").unlink(missing_ok=True))
        self.addCleanup(lambda: Path(str(path) + "-shm").unlink(missing_ok=True))
        return path

    @staticmethod
    def _wait_until(predicate, *, timeout_s: float = 3.0, sleep_s: float = 0.02) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(sleep_s)
        return bool(predicate())

    def test_command_journal_writes_and_tails(self) -> None:
        path = self._make_tempfile_path()
        settings = CommandJournalSettings(
            path=path,
            queue_max=100,
            batch_size=10,
            flush_interval_ms=20,
            retention_max_rows=None,
            retention_max_age_days=None,
            prune_interval_s=60.0,
        )
        journal = CommandJournal(settings=settings, instance_id="inst-a")
        journal.start()
        try:
            for idx in range(3):
                journal.append(
                    {
                        "t_wall": time.time(),
                        "t_mono": time.monotonic(),
                        "instance_id": "inst-a",
                        "device_id": "trace1",
                        "action": f"set_{idx}",
                        "params_json": "{\"x\": 1}",
                        "ok": True,
                        "status": "OK",
                        "error_json": "",
                        "result_json": "null",
                        "request_id": f"req-{idx}",
                        "caller_process_id": "sequencer",
                        "source_kind": "process",
                        "source_id": "sequencer",
                        "is_remote_target": False,
                    }
                )
            self.assertTrue(
                self._wait_until(lambda: int(journal.status().get("written", 0)) >= 3)
            )

            result = journal.tail({"limit": 10})
            self.assertEqual(result.get("count"), 3)
            entries = result.get("entries", [])
            self.assertEqual(entries[0]["action"], "set_0")
            self.assertEqual(entries[-1]["action"], "set_2")
            self.assertEqual(entries[-1]["source_kind"], "process")
        finally:
            journal.close()

    def test_command_journal_prunes_by_max_rows(self) -> None:
        path = self._make_tempfile_path()
        settings = CommandJournalSettings(
            path=path,
            queue_max=200,
            batch_size=20,
            flush_interval_ms=20,
            retention_max_rows=3,
            retention_max_age_days=None,
            prune_interval_s=0.5,
            prune_chunk_rows=10,
        )
        journal = CommandJournal(settings=settings, instance_id="inst-b")
        journal.start()
        try:
            for idx in range(10):
                journal.append(
                    {
                        "t_wall": time.time(),
                        "t_mono": time.monotonic(),
                        "instance_id": "inst-b",
                        "device_id": "trace1",
                        "action": f"cmd_{idx}",
                        "params_json": "{}",
                        "ok": True,
                        "status": "OK",
                        "error_json": "",
                        "result_json": "null",
                        "is_remote_target": False,
                    }
                )
            self.assertTrue(
                self._wait_until(lambda: int(journal.status().get("written", 0)) >= 10)
            )
            self.assertTrue(
                self._wait_until(lambda: int(journal.status().get("pruned_rows", 0)) > 0)
            )
            result = journal.tail({"limit": 20})
            self.assertLessEqual(int(result.get("count", 0)), 3)
        finally:
            journal.close()

    def test_command_journal_close_reports_incomplete_flush(self) -> None:
        path = self._make_tempfile_path()
        settings = CommandJournalSettings(
            path=path,
            queue_max=20,
            batch_size=1,
            flush_interval_ms=10,
            retention_max_rows=None,
            retention_max_age_days=None,
            prune_interval_s=60.0,
        )
        journal = _SlowWriteCommandJournal(
            settings=settings, instance_id="inst-slow", delay_s=0.25
        )
        journal.start()
        for idx in range(4):
            journal.append(
                {
                    "t_wall": time.time(),
                    "t_mono": time.monotonic(),
                    "instance_id": "inst-slow",
                    "device_id": "trace1",
                    "action": f"slow_{idx}",
                    "params_json": "{}",
                    "ok": True,
                    "status": "OK",
                    "error_json": "",
                    "result_json": "null",
                    "is_remote_target": False,
                }
            )

        # Intentionally too short: this should surface an incomplete close.
        journal.close(timeout_s=0.05)
        status = journal.status()
        self.assertGreaterEqual(int(status.get("close_incomplete_count", 0)), 1)
        self.assertIn("close timed out", str(status.get("last_error", "")))
        self.assertTrue(
            self._wait_until(lambda: not bool(journal.status().get("thread_alive")), timeout_s=3.0)
        )


if __name__ == "__main__":
    unittest.main()
