from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from unittest import mock  # noqa: E402

import experiment_control._manager.process_supervision as ps  # noqa: E402
from experiment_control._manager.process_supervision import (  # noqa: E402
    enforce_managed_process_heartbeat_timeout,
    enforce_managed_process_stop_timeout,
    maybe_restart_managed_process,
    maybe_schedule_restart,
    enforce_device_driver_heartbeat_timeout,
    enforce_device_driver_stop_timeout,
    driver_is_stopped,
    process_snapshot,
    start_process_handle,
    stop_process_handle,
    supervise_device_drivers,
    try_restart_process,
    update_managed_process_exit_state,
    update_device_driver_exit_state,
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
        self.killed = False
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


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

    def test_heartbeat_stale_deferred_during_startup_grace(self) -> None:
        # No recent loop stall, but startup_sequence is active: a slow first
        # heartbeat (heavy imports) must be deferred unconditionally, even past
        # what would otherwise be a terminating strike count, so the process
        # isn't killed while it is still booting.
        manager = SimpleNamespace(
            _last_loop_stall_mono=None,
            _last_loop_stall_duration_s=0.0,
            _manager_loop_stall_recent_s=10.0,
            _heartbeat_stale_strikes_to_fail=2,
            _heartbeat_hard_timeout_multiplier=3.0,
            _startup_sequence_active=True,
            _startup_sequence_complete_mono=None,
            _publish_manager_event=lambda *args, **kwargs: None,
            _publish_process_event=lambda *args, **kwargs: None,
        )
        handle = _make_handle()
        handle.last_hb_recv_mono = 6.0  # hb_age = 4 > timeout 3 at t=10
        handle.popen = _FakePopen()
        handle.heartbeat_stale_strikes = 5  # already well past the fail threshold
        handle.last_stale_detected_mono = 8.5

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(handle.state, "RUNNING")
        self.assertFalse(handle.popen.terminated)
        # strikes are capped below the fail threshold while in startup grace
        self.assertLess(handle.heartbeat_stale_strikes, 2)

    def test_heartbeat_stale_fails_after_startup_grace_window(self) -> None:
        # Startup completed long ago and there is no recent loop stall, so the
        # grace no longer applies: a genuinely stale process must still fail.
        events: list[tuple[str, object]] = []
        manager = SimpleNamespace(
            _last_loop_stall_mono=None,
            _last_loop_stall_duration_s=0.0,
            _manager_loop_stall_recent_s=10.0,
            _heartbeat_stale_strikes_to_fail=2,
            _heartbeat_hard_timeout_multiplier=3.0,
            _startup_sequence_active=False,
            _startup_sequence_complete_mono=0.0,  # > recent window before now=100
            _publish_manager_event=lambda topic, payload: events.append((topic, payload)),
            _publish_process_event=lambda topic, handle: events.append((topic, handle)),
        )
        handle = _make_handle()
        handle.last_hb_recv_mono = 96.0  # hb_age = 4 > timeout 3 at t=100
        handle.popen = _FakePopen()
        handle.heartbeat_stale_strikes = 1
        handle.last_stale_detected_mono = 98.0  # > one period ago -> 2nd strike

        enforce_managed_process_heartbeat_timeout(manager, handle, 100.0)

        self.assertEqual(handle.state, "FAILED")
        self.assertTrue(handle.popen.terminated)
        self.assertEqual(events[-1][0], "manager.process.failed")

    def test_startup_grace_is_bounded_by_hard_timeout(self) -> None:
        # Even DURING startup grace, a process whose heartbeat age exceeds
        # _startup_grace_hard_timeout_s is failed — a crashed-at-boot process
        # must not hide for the whole startup window.
        events: list[tuple[str, object]] = []
        manager = SimpleNamespace(
            _last_loop_stall_mono=None,
            _last_loop_stall_duration_s=0.0,
            _manager_loop_stall_recent_s=10.0,
            _heartbeat_stale_strikes_to_fail=2,
            _heartbeat_hard_timeout_multiplier=3.0,
            _startup_sequence_active=True,  # in startup grace
            _startup_sequence_complete_mono=None,
            _startup_grace_hard_timeout_s=5.0,
            _publish_manager_event=lambda topic, payload: events.append((topic, payload)),
            _publish_process_event=lambda topic, handle: events.append((topic, handle)),
        )
        handle = _make_handle()
        handle.last_hb_recv_mono = 4.0  # hb_age = 6 at t=10, > grace hard cap 5
        handle.popen = _FakePopen()
        handle.heartbeat_stale_strikes = 1
        handle.last_stale_detected_mono = 8.5

        enforce_managed_process_heartbeat_timeout(manager, handle, 10.0)

        self.assertEqual(handle.state, "FAILED")
        self.assertTrue(handle.popen.terminated)
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


class DeviceDriverHeartbeatDemotionTests(unittest.TestCase):
    """Driver RUNNING state is heartbeat-authoritative: a stale heartbeat demotes
    to FAILED even when the wrapper process is still alive (poll() can't see the
    real driver's death), and covers the process-is-None stale-registration case
    too. Subsumes the earlier proc.poll()-based reconcile."""

    @staticmethod
    def _handle(**overrides: object) -> SimpleNamespace:
        handle = SimpleNamespace(
            process=None,
            driver_pid=123,
            driver_process_state="RUNNING",
            driver_last_error=None,
            driver_last_error_kind=None,
            driver_last_failure_pid=None,
            driver_last_signal_name=None,
            driver_last_exit_code=None,
            driver_popen_pid=123,
            driver_stop_requested_t_mono=None,
            last_hb_recv_mono=None,
            driver_running_since_mono=None,
            spec=SimpleNamespace(
                driver_stop_timeout_s=3.0,
                driver_kill_timeout_s=3.0,
            ),
        )
        for key, value in overrides.items():
            setattr(handle, key, value)
        return handle

    @staticmethod
    def _manager() -> SimpleNamespace:
        return SimpleNamespace(
            _heartbeat_timeout_s=3.0,
            _heartbeat_hard_timeout_multiplier=3.0,
            _publish_driver_event=mock.Mock(),
        )

    def _no_defer(self):
        return (
            mock.patch.object(ps, "_in_startup_grace", lambda *a: False),
            mock.patch.object(ps, "_recent_manager_loop_stall", lambda *a: False),
        )

    def test_demotes_when_heartbeat_stale_even_if_process_alive(self) -> None:
        popen = _FakePopen()
        handle = self._handle(process=popen, last_hb_recv_mono=90.0)  # age 10 > 3
        manager = self._manager()
        g1, g2 = self._no_defer()
        with g1, g2:
            enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
        self.assertEqual(str(handle.driver_process_state), "FAILED")
        self.assertEqual(handle.driver_pid, 123)
        self.assertIs(handle.process, popen)
        self.assertTrue(popen.terminated)
        self.assertEqual(handle.driver_last_error_kind, "heartbeat_stale")
        self.assertFalse(driver_is_stopped(handle))
        manager._publish_driver_event.assert_called()

    def test_stale_driver_that_ignores_terminate_is_killed(self) -> None:
        popen = _FakePopen()
        handle = self._handle(process=popen, last_hb_recv_mono=90.0)
        manager = self._manager()
        g1, g2 = self._no_defer()
        with g1, g2:
            enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)

        enforce_device_driver_stop_timeout(manager, handle, 103.1)

        self.assertTrue(popen.killed)
        self.assertIs(handle.process, popen)

    def test_automatic_heartbeat_failure_remains_failed_after_clean_exit(self) -> None:
        popen = _FakePopen()
        handle = self._handle(process=popen, last_hb_recv_mono=90.0)
        manager = self._manager()
        g1, g2 = self._no_defer()
        with g1, g2:
            enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)

        update_device_driver_exit_state(manager, handle, 0)

        self.assertEqual(str(handle.driver_process_state), "FAILED")
        self.assertIsNone(handle.process)
        self.assertIsNone(handle.driver_pid)

    def test_ages_against_running_since_when_no_heartbeat(self) -> None:
        handle = self._handle(
            process=_FakePopen(), last_hb_recv_mono=None, driver_running_since_mono=80.0
        )  # age 20 > 3
        manager = self._manager()
        g1, g2 = self._no_defer()
        with g1, g2:
            enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
        self.assertEqual(str(handle.driver_process_state), "FAILED")

    def test_no_demote_when_heartbeat_fresh(self) -> None:
        handle = self._handle(process=_FakePopen(), last_hb_recv_mono=99.0)  # age 1 < 3
        manager = self._manager()
        enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
        self.assertEqual(str(handle.driver_process_state), "RUNNING")

    def test_no_demote_when_no_reference(self) -> None:
        handle = self._handle(last_hb_recv_mono=None, driver_running_since_mono=None)
        manager = self._manager()
        enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
        self.assertEqual(str(handle.driver_process_state), "RUNNING")

    def test_only_acts_on_running_state(self) -> None:
        handle = self._handle(
            driver_process_state="STARTING", last_hb_recv_mono=80.0
        )
        manager = self._manager()
        enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
        self.assertEqual(str(handle.driver_process_state), "STARTING")

    def test_defers_within_hard_timeout_during_startup_grace(self) -> None:
        handle = self._handle(process=_FakePopen(), last_hb_recv_mono=96.0)  # age 4: >3, <9
        manager = self._manager()
        with mock.patch.object(ps, "_in_startup_grace", lambda *a: True), \
             mock.patch.object(ps, "_recent_manager_loop_stall", lambda *a: False):
            enforce_device_driver_heartbeat_timeout(manager, handle, 100.0)
        self.assertEqual(str(handle.driver_process_state), "RUNNING")

    def test_supervise_invokes_heartbeat_enforcement(self) -> None:
        calls: list = []
        handle = self._handle(process=_FakePopen(), last_hb_recv_mono=90.0)
        manager = SimpleNamespace(
            _devices={"dev": handle},
            _update_device_driver_exit_state=lambda h, rc: None,
            _enforce_device_driver_heartbeat_timeout=lambda h, n: calls.append(
                ("hb", h)
            ),
            _enforce_device_driver_stop_timeout=lambda h, n: None,
            _maybe_restart_device_driver=lambda d, h, n: None,
        )
        with mock.patch.object(ps, "_maybe_auto_reconnect_device", lambda *a, **k: None):
            supervise_device_drivers(manager, 100.0)
        self.assertIn(("hb", handle), calls)


if __name__ == "__main__":
    unittest.main()

