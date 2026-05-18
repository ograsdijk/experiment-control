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
        last_hb_recv_mono=None,
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
        # The HB was received (manager-side) at t=6; the check fires at
        # t=10, so hb_age=4 > timeout=3 → stale. With a recent stall
        # logged at t=9.5, the deferral kicks in.
        handle.last_hb_recv_mono = 6.0
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
        handle.last_hb_recv_mono = 6.0
        handle.popen = _FakePopen()
        handle.heartbeat_stale_strikes = 1
        # Pretend the first strike was recorded at t=8.5 — > one period
        # (1 s) ago — so the rate limit allows another strike at t=10.
        handle.last_stale_detected_mono = 8.5

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(handle.state, "FAILED")
        self.assertTrue(handle.popen.terminated)
        self.assertTrue(handle.terminated_by_manager)
        self.assertEqual(handle.termination_reason, "heartbeat_stale")
        self.assertEqual(handle.termination_method, "terminate")
        self.assertEqual(events[-1][0], "manager.process.failed")

    def test_strike_increments_rate_limited_to_one_per_period(self) -> None:
        """During a recent stall, a series of rapid stale checks within
        one heartbeat_period_s must only count as a single strike.
        Without this rate limit, two consecutive _check_timeouts ticks
        ~50 ms apart would race past strikes_to_fail=2 (because each
        check would have incremented strikes) even though the process
        is healthy and its HB is sitting in the SUB buffer about to be
        drained — that's the actual vacuum-cryo failure mode."""
        manager = SimpleNamespace(
            # Recent stall present, so the deferral can engage and the
            # strike-counter is what gates termination.
            _last_loop_stall_mono=9.9,
            _last_loop_stall_duration_s=2.0,
            _manager_loop_stall_recent_s=10.0,
            _heartbeat_stale_strikes_to_fail=2,
            _heartbeat_hard_timeout_multiplier=3.0,
            _publish_manager_event=lambda *args, **kwargs: None,
            _publish_process_event=lambda *args, **kwargs: None,
        )
        handle = _make_handle()
        # hb_age = 5 s at t=10 → stale (> timeout 3 s) but well under
        # the hard timeout (3 × 3 s = 9 s).
        handle.last_hb_recv_mono = 5.0
        handle.popen = _FakePopen()

        # Twenty rapid checks across 100 ms — well under one
        # heartbeat_period_s (1.0). Strikes should not exceed 1.
        t = 10.0
        for _ in range(20):
            enforce_managed_process_heartbeat_timeout(manager, handle, t)
            if handle.state == "FAILED":
                self.fail(f"process failed after {t-10.0:.3f}s of rapid checks")
            t += 0.005

        self.assertEqual(handle.heartbeat_stale_strikes, 1)
        self.assertFalse(handle.popen.terminated)

    def test_heartbeat_check_uses_recv_time_not_sender_t_mono(self) -> None:
        """If `last_hb_t_mono` is old (sender's clock at HB-generation)
        but `last_hb_recv_mono` is fresh (manager just drained it),
        the timeout check sees fresh — proving the check uses recv
        time, which is what tolerates manager-side drain delay."""
        manager = SimpleNamespace(
            _last_loop_stall_mono=None,
            _manager_loop_stall_recent_s=10.0,
            _heartbeat_stale_strikes_to_fail=2,
            _heartbeat_hard_timeout_multiplier=3.0,
            _publish_manager_event=lambda *args, **kwargs: None,
        )
        handle = _make_handle()
        handle.last_hb_t_mono = 0.0      # sender stamped it long ago
        handle.last_hb_recv_mono = 9.5   # but manager processed it just now
        handle.popen = _FakePopen()

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(handle.state, "RUNNING")
        self.assertEqual(handle.heartbeat_stale_strikes, 0)
        self.assertFalse(handle.popen.terminated)

    def test_snapshot_hb_age_reflects_recv_time(self) -> None:
        """process_snapshot.hb_age_s must reflect the manager-side
        receive time (what the timeout check uses) so subscribers see
        a consistent 'is the process alive' value."""
        manager = SimpleNamespace()
        handle = _make_handle()
        handle.last_hb_t_mono = 0.0       # old sender stamp
        handle.last_hb_recv_mono = None   # not yet received
        # No recv time → falls back to sender stamp.
        snap = process_snapshot(manager, handle)
        self.assertIn("last_hb_recv_mono", snap)
        self.assertIsNone(snap["last_hb_recv_mono"])
        # With recv time set, hb_age uses it (not the old sender stamp).
        import time as _t
        handle.last_hb_recv_mono = _t.monotonic()
        snap = process_snapshot(manager, handle)
        self.assertIsNotNone(snap["hb_age_s"])
        self.assertLess(float(snap["hb_age_s"]), 0.5)


if __name__ == "__main__":
    unittest.main()

