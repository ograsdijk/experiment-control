from __future__ import annotations

import json
import os
import time
import unittest
from unittest import mock

from experiment_control.utils.rotating_jsonl import RotatingJsonlSink
from tests._temp_utils import repo_temp_dir


class RotatingJsonlSinkTests(unittest.TestCase):
    def test_rotates_at_utc_day_boundary(self) -> None:
        with repo_temp_dir("rotating-jsonl-day") as tmp:
            with mock.patch.object(
                RotatingJsonlSink,
                "_current_utc_day",
                side_effect=["2026-08-30", "2026-08-30", "2026-08-31"],
            ):
                sink = RotatingJsonlSink(
                    directory=tmp,
                    max_age_days=None,
                    max_total_bytes=None,
                )
                sink.write({"message": "before"})
                sink.write({"message": "after"})
                sink.close()

            self.assertTrue((tmp / "manager-2026-08-30-000.jsonl").exists())
            self.assertTrue((tmp / "manager-2026-08-31-000.jsonl").exists())

    def test_writes_json_lines_and_rotates_by_size(self) -> None:
        with repo_temp_dir("rotating-jsonl") as tmp:
            sink = RotatingJsonlSink(
                directory=tmp,
                max_bytes=80,
                max_age_days=None,
                max_total_bytes=None,
            )
            sink.write({"message": "a" * 50})
            sink.write({"message": "b" * 50})
            sink.close()

            paths = sorted(tmp.glob("manager-*.jsonl"))
            self.assertEqual(len(paths), 2)
            records = [
                json.loads(line)
                for path in paths
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["message"] for record in records], ["a" * 50, "b" * 50])

    def test_removes_files_older_than_retention_window(self) -> None:
        with repo_temp_dir("rotating-jsonl-age") as tmp:
            expired = tmp / "manager-2000-01-01.jsonl"
            expired.write_text("{}\n", encoding="utf-8")
            old = time.time() - 3 * 86_400
            os.utime(expired, (old, old))

            sink = RotatingJsonlSink(
                directory=tmp,
                max_age_days=1,
                max_total_bytes=None,
            )
            sink.close()

            self.assertFalse(expired.exists())

    def test_removes_oldest_files_to_enforce_total_size(self) -> None:
        with repo_temp_dir("rotating-jsonl-size") as tmp:
            oldest = tmp / "manager-2000-01-01.jsonl"
            newer = tmp / "manager-2000-01-02.jsonl"
            oldest.write_bytes(b"a" * 80)
            newer.write_bytes(b"b" * 80)
            now = time.time()
            os.utime(oldest, (now - 20, now - 20))
            os.utime(newer, (now - 10, now - 10))

            sink = RotatingJsonlSink(
                directory=tmp,
                max_age_days=None,
                max_total_bytes=100,
            )
            sink.close()

            self.assertFalse(oldest.exists())
            self.assertTrue(newer.exists())


if __name__ == "__main__":
    unittest.main()
