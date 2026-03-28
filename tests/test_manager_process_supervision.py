from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager_process_supervision import process_snapshot  # noqa: E402


def _make_handle() -> SimpleNamespace:
    spec = SimpleNamespace(
        process_id="example_proc",
        argv=["python", "-m", "example"],
        cwd=".",
        env={},
        heartbeat_period_s=1.0,
        heartbeat_timeout_s=3.0,
        shutdown_timeout_s=5.0,
        restart_policy="ON_FAILURE",
        restart_backoff_s=1.0,
        max_restarts=3,
    )
    return SimpleNamespace(
        spec=spec,
        state="RUNNING",
        pid=12345,
        last_start_t_wall=0.0,
        last_start_t_mono=0.0,
        last_hb_t_wall=0.0,
        last_hb_t_mono=None,
        last_exit_code=None,
        restart_count=0,
        last_restart_t_mono=None,
        last_error=None,
        heartbeat_endpoint=None,
        process_data_endpoint=None,
        rpc_endpoint=None,
    )


class ProcessSnapshotMemoryTests(unittest.TestCase):
    def test_process_snapshot_includes_rss_bytes(self) -> None:
        manager = SimpleNamespace()
        handle = _make_handle()
        snapshot = process_snapshot(manager, handle)
        self.assertIn("rss_bytes", snapshot)
        rss = snapshot["rss_bytes"]
        self.assertTrue(rss is None or (isinstance(rss, int) and rss >= 0))


if __name__ == "__main__":
    unittest.main()

