from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager_process_supervision import (  # noqa: E402
    enforce_managed_process_heartbeat_timeout,
    process_snapshot,
)


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
        popen_pid=12345,
        heartbeat_pid=12345,
        last_start_t_wall=0.0,
        last_start_t_mono=0.0,
        last_hb_t_wall=0.0,
        last_hb_t_mono=None,
        last_exit_code=None,
        restart_count=0,
        last_restart_t_mono=None,
        last_error=None,
        last_error_kind=None,
        last_signal_name=None,
        last_failure_pid=None,
        last_heartbeat_age_s=None,
        last_liveness_age_s=None,
        last_heartbeat_received=None,
        heartbeat_stale_strikes=0,
        last_stale_detected_mono=None,
        terminated_by_manager=False,
        termination_reason=None,
        termination_method=None,
        termination_error=None,
        recent_manager_loop_stall=False,
        last_manager_loop_stall_duration_s=None,
        last_heartbeat_payload=None,
        supervisor_stdout_tail=[],
        supervisor_stderr_tail=[],
        supervisor_log_tail=[],
        stdout_log_path=None,
        stderr_log_path=None,
        heartbeat_endpoint=None,
        process_data_endpoint=None,
        rpc_endpoint=None,
    )


class _FakePopen:
    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True


class ProcessSnapshotMemoryTests(unittest.TestCase):
    def test_process_snapshot_includes_rss_bytes(self) -> None:
        manager = SimpleNamespace()
        handle = _make_handle()
        snapshot = process_snapshot(manager, handle)
        self.assertIn("rss_bytes", snapshot)
        rss = snapshot["rss_bytes"]
        self.assertTrue(rss is None or (isinstance(rss, int) and rss >= 0))

    def test_heartbeat_stale_defers_after_recent_manager_stall(self) -> None:
        manager = SimpleNamespace(
            _last_loop_stall_mono=9.5,
            _last_loop_stall_duration_s=4.0,
            _manager_loop_stall_recent_s=10.0,
            _heartbeat_stale_strikes_to_fail=2,
            _heartbeat_hard_timeout_multiplier=3.0,
            _publish_manager_event=lambda *args, **kwargs: None,
        )
        handle = _make_handle()
        handle.last_hb_t_mono = 6.0
        handle.popen = _FakePopen()

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(handle.state, "RUNNING")
        self.assertEqual(handle.heartbeat_stale_strikes, 1)
        self.assertFalse(handle.popen.terminated)
        self.assertTrue(handle.recent_manager_loop_stall)

    def test_heartbeat_stale_terminates_on_second_strike(self) -> None:
        events: list[tuple[str, object]] = []
        manager = SimpleNamespace(
            _last_loop_stall_mono=9.5,
            _last_loop_stall_duration_s=4.0,
            _manager_loop_stall_recent_s=10.0,
            _heartbeat_stale_strikes_to_fail=2,
            _heartbeat_hard_timeout_multiplier=3.0,
            _publish_manager_event=lambda topic, payload: events.append((topic, payload)),
            _publish_process_event=lambda topic, handle: events.append((topic, handle)),
        )
        handle = _make_handle()
        handle.last_hb_t_mono = 6.0
        handle.popen = _FakePopen()
        handle.heartbeat_stale_strikes = 1

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(handle.state, "FAILED")
        self.assertTrue(handle.popen.terminated)
        self.assertTrue(handle.terminated_by_manager)
        self.assertEqual(handle.termination_reason, "heartbeat_stale")
        self.assertEqual(handle.termination_method, "terminate")
        self.assertEqual(events[-1][0], "manager.process.failed")


if __name__ == "__main__":
    unittest.main()

