from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._manager.process_supervision import (  # noqa: E402
    enforce_managed_process_heartbeat_timeout,
    enforce_managed_process_stop_timeout,
    maybe_restart_managed_process,
    maybe_schedule_restart,
    process_snapshot,
    start_process_handle,
    stop_process_handle,
    try_restart_process,
    update_managed_process_exit_state,
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


def _make_supervision_manager(**overrides: object) -> SimpleNamespace:
    """Default manager-like object for enforce_managed_process_heartbeat_timeout tests."""
    defaults: dict[str, object] = {
        "_process_hb_sub": None,
        "_handle_process_pub": lambda: None,
        "_last_loop_stall_mono": None,
        "_last_loop_stall_duration_s": 0.0,
        "_manager_loop_stall_recent_s": 10.0,
        "_heartbeat_stale_strikes_to_fail": 2,
        "_heartbeat_hard_timeout_multiplier": 3.0,
        "_publish_manager_event": lambda *args, **kwargs: None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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

    def test_stale_check_drains_queued_heartbeat_before_strike(self) -> None:
        handle = _make_handle()
        handle.last_hb_recv_mono = 1.0
        handle.popen = _FakePopen()

        class _Sock:
            def poll(self, _timeout: int) -> bool:
                return True

        def _drain_queued_hb() -> None:
            handle.last_hb_recv_mono = 9.8

        manager = _make_supervision_manager(
            _process_hb_sub=_Sock(),
            _handle_process_pub=_drain_queued_hb,
        )

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(handle.state, "RUNNING")
        self.assertEqual(handle.heartbeat_stale_strikes, 0)
        self.assertIsNone(handle.last_stale_detected_mono)
        self.assertFalse(handle.popen.terminated)
        self.assertLess(float(handle.last_heartbeat_age_s), 0.5)

    def test_stale_check_reports_queued_heartbeat_drain_failure(self) -> None:
        events: list[tuple[str, object]] = []
        handle = _make_handle()
        handle.last_hb_recv_mono = 2.0
        handle.popen = _FakePopen()

        class _Sock:
            def poll(self, _timeout: int) -> bool:
                return True

        def _drain_failure() -> None:
            raise RuntimeError("boom")

        manager = _make_supervision_manager(
            _process_hb_sub=_Sock(),
            _handle_process_pub=_drain_failure,
            _last_loop_stall_mono=9.5,
            _last_loop_stall_duration_s=4.0,
            _publish_manager_event=lambda topic, payload: events.append((topic, payload)),
        )

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(events[0][0], "manager.process.heartbeat_refresh_failed")
        self.assertEqual(handle.heartbeat_stale_strikes, 1)
        self.assertEqual(handle.state, "RUNNING")

    def test_heartbeat_refresh_failure_diagnostic_is_rate_limited(self) -> None:
        events: list[tuple[str, object]] = []
        handle = _make_handle()
        handle.last_hb_recv_mono = 2.0
        handle.popen = _FakePopen()

        class _Sock:
            def poll(self, _timeout: int) -> bool:
                return True

        def _drain_failure() -> None:
            raise RuntimeError("boom")

        manager = _make_supervision_manager(
            _process_hb_sub=_Sock(),
            _handle_process_pub=_drain_failure,
            _last_loop_stall_mono=9.5,
            _last_loop_stall_duration_s=4.0,
            _heartbeat_stale_strikes_to_fail=99,
            _heartbeat_hard_timeout_multiplier=10.0,
            _process_hb_refresh_error_period_s=10.0,
            _publish_manager_event=lambda topic, payload: events.append((topic, payload)),
        )

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)
        enforce_managed_process_heartbeat_timeout(manager, handle, 10.5)
        setattr(manager, "_last_process_hb_refresh_error_mono", 0.0)
        enforce_managed_process_heartbeat_timeout(manager, handle, 11.5)

        refresh_events = [item for item in events if item[0] == "manager.process.heartbeat_refresh_failed"]
        self.assertEqual(len(refresh_events), 2)
        self.assertEqual(refresh_events[1][1]["suppressed_count"], 1)

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


class _AlwaysAlivePopen:
    """A process that never exits (poll() stays None) and records kills.

    Models a managed process whose graceful shutdown is slow / ignored —
    the case that exposes the restart-vs-stop-escalation race.
    """

    def __init__(self) -> None:
        self.terminated = False
        self.kill_called = False
        self.pid = 4242

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.kill_called = True


def _make_restart_handle() -> SimpleNamespace:
    handle = _make_handle()
    # Match the b-detection sequencer/watchdog config that wedged:
    # restart_backoff_s (0.5) < shutdown_timeout_s (3.0).
    handle.spec.shutdown_timeout_s = 3.0
    handle.spec.restart_backoff_s = 0.5
    handle.spec.restart_policy = "NEVER"
    handle.spec.max_restarts = None
    handle.state = "RUNNING"
    handle.popen = _AlwaysAlivePopen()
    handle.rpc_endpoint = "inproc://fake-proc-rpc"  # so the graceful path is taken
    handle.next_restart_t_mono = None
    return handle


def _make_restart_manager(events: list) -> SimpleNamespace:
    manager = SimpleNamespace()
    manager._device_rpc_timeout_ms = 500
    # Graceful process.stop is acked OK (process says "ok" then is slow to exit).
    manager._call_process_rpc = lambda **_kw: {"ok": True}
    manager._close_process_rpc = lambda _h: None
    manager._publish_process_event = lambda topic, h: events.append(topic)
    manager._maybe_recover_process_start_collision = lambda _h: False
    # Wire the mixin-style indirections back to the real module functions
    # so we exercise the real stop/restart/escalation logic.
    manager._maybe_schedule_restart = lambda h, now: maybe_schedule_restart(manager, h, now)
    manager._try_restart_process = lambda h: try_restart_process(manager, h)
    manager._start_process_handle = lambda h: start_process_handle(manager, h)
    manager._update_managed_process_exit_state = (
        lambda h, rc: update_managed_process_exit_state(manager, h, rc)
    )
    return manager


class RestartStopTimeoutRaceTests(unittest.TestCase):
    """Regression: a UI restart of a process whose graceful shutdown takes
    longer than restart_backoff_s must not wedge the handle in STOPPING.

    Before the fix: restart_process schedules a respawn at +backoff (0.5s);
    try_restart_process clears stop_requested_t_mono and calls
    start_process_handle, which bails because the old process is still
    alive — leaving state=STOPPING with stop_requested_t_mono=None, which
    permanently disables the stop-timeout force-kill escalation.
    """

    def _drive_restart(self) -> tuple[SimpleNamespace, SimpleNamespace]:
        events: list = []
        manager = _make_restart_manager(events)
        handle = _make_restart_handle()

        # --- emulate manager.restart_process(): graceful stop + schedule ---
        stop_process_handle(manager, handle)
        self.assertEqual(handle.state, "STOPPING")
        t0 = handle.stop_requested_t_mono
        self.assertIsNotNone(t0)
        handle.next_restart_t_mono = t0 + handle.spec.restart_backoff_s

        # --- supervise ticks (minus heartbeat) while the process refuses
        #     to exit, from before the backoff to past the shutdown window ---
        now = t0
        while now < t0 + 6.0:
            now += 0.1
            rc = handle.popen.poll()
            if rc is not None:
                if manager._update_managed_process_exit_state(handle, int(rc)):
                    continue
            else:
                enforce_managed_process_stop_timeout(manager, handle, now)
                maybe_restart_managed_process(manager, handle, now)
        return manager, handle

    def test_restart_with_slow_exit_does_not_wedge_in_stopping(self) -> None:
        _manager, handle = self._drive_restart()
        # The non-exiting old process MUST be force-killed by the
        # stop-timeout escalation...
        self.assertTrue(
            handle.popen.kill_called,
            "stop-timeout escalation never force-killed the old process — "
            "handle is wedged in STOPPING",
        )
        # ...and the handle must reach a terminal state, not sit in STOPPING.
        self.assertNotEqual(handle.state, "STOPPING")


if __name__ == "__main__":
    unittest.main()

