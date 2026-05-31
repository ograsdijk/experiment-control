# ruff: noqa: E402
"""Regression tests for the Group F manager-hardening fixes.

Six independent fixes are pinned here:

F.20 - _error_counts is incremented under a lock (was: dict[get]+1 race).
F.21 - _supervisor_log_dropped is bumped under a lock and snapshot+reset
       atomically (was: `+=` lost counts under concurrent reader-thread
       bumps).
F.22 - enforce_managed_process_stop_timeout escalates a refused-to-die
       process to FAILED after _MAX_KILL_ATTEMPTS retries (was: spammed
       kill() forever).
F.24 - _lifecycle_event_queue is bounded with a drop counter (was:
       unbounded queue.Queue).
F.32 - stop_process_handle publishes manager.process.failed (not .exited)
       when polling reveals the process already crashed with non-zero
       exit (was: silently classified as a clean exit).
F.33 - route_manager_cleanup_orphans caps user-supplied timeout_s at
       _CLEANUP_ORPHANS_TIMEOUT_CAP_S (was: unbounded — could stall the
       manager loop for minutes).
F.34 - _publish_transition_event records publish failures into
       _last_error (was: bare `except: pass`).
"""

from __future__ import annotations

import queue
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager_process_logs import (
    drain_supervisor_logs,
    queue_supervisor_log,
)
from experiment_control.manager_process_supervision import (
    _MAX_KILL_ATTEMPTS,
    enforce_managed_process_stop_timeout,
    stop_process_handle,
)
from experiment_control.manager_pubsub import publish_manager_event
from experiment_control.manager_route_handlers import (
    _CLEANUP_ORPHANS_TIMEOUT_CAP_S,
    route_manager_cleanup_orphans,
)
from experiment_control.processes.state_machine_base import (
    StateMachineProcessBase,
)


# ---------------------------------------------------------------------------
# F.21 — supervisor log drop counter is thread-safe
# ---------------------------------------------------------------------------


def _make_log_manager() -> SimpleNamespace:
    """Minimal stub exposing the attributes record_supervisor_log_item +
    drain_supervisor_logs read."""
    emitted: list[dict[str, object]] = []

    def _emit(**kwargs):
        emitted.append(dict(kwargs))

    mgr = SimpleNamespace(
        _supervisor_log_queue=queue.Queue(maxsize=2),
        _supervisor_log_dropped=0,
        _supervisor_log_dropped_lock=threading.Lock(),
        _supervisor_pending_blocks={},
        _supervisor_log_threads={},
        _emit_log=_emit,
        _emitted=emitted,
    )
    # `record_supervisor_log_item` also calls `_record_supervisor_raw_log`
    # and a couple of other attrs guarded by try/except — leave them
    # absent so the try/except path runs.
    mgr._record_supervisor_raw_log = lambda *_a, **_k: None
    return mgr


class SupervisorLogDroppedCounterTests(unittest.TestCase):
    def test_concurrent_bumps_are_not_lost(self) -> None:
        mgr = _make_log_manager()
        # Fill the bounded queue (maxsize=2) so every subsequent put
        # hits the drop path. Each thread tries to push N items.
        for _ in range(2):
            mgr._supervisor_log_queue.put({"primer": True})

        N_THREADS = 8
        N_PER_THREAD = 200

        def _bump_many():
            item = {
                "source_kind": "process",
                "source_id": "p1",
                "stream": "stdout",
                "message": "x",
            }
            for _ in range(N_PER_THREAD):
                queue_supervisor_log(mgr, item)

        threads = [threading.Thread(target=_bump_many) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            self.assertFalse(t.is_alive())

        # Lossless counting: every bump must be reflected. Pre-fix the
        # non-atomic += would lose a significant fraction under load.
        self.assertEqual(
            mgr._supervisor_log_dropped,
            N_THREADS * N_PER_THREAD,
            f"expected {N_THREADS * N_PER_THREAD} drops, got "
            f"{mgr._supervisor_log_dropped}",
        )

    def test_drain_snapshots_and_resets_atomically(self) -> None:
        mgr = _make_log_manager()
        # Pre-load the dropped counter (as if reader threads had bumped).
        mgr._supervisor_log_dropped = 42
        drain_supervisor_logs(mgr)
        # After drain: counter reset to 0, one emit_log with dropped=42.
        self.assertEqual(mgr._supervisor_log_dropped, 0)
        drop_emits = [
            e for e in mgr._emitted if e.get("topic") == "manager.supervisor.drop"
        ]
        self.assertEqual(len(drop_emits), 1)
        self.assertEqual(drop_emits[0]["payload"], {"dropped": 42})

    def test_drain_does_nothing_when_zero(self) -> None:
        mgr = _make_log_manager()
        drain_supervisor_logs(mgr)
        drop_emits = [
            e for e in mgr._emitted if e.get("topic") == "manager.supervisor.drop"
        ]
        self.assertEqual(drop_emits, [])


# ---------------------------------------------------------------------------
# F.22 — enforce_managed_process_stop_timeout escalates after N kills
# ---------------------------------------------------------------------------


class _StaleFakePopen:
    """popen.poll() always returns None (process refuses to exit), and
    popen.kill() succeeds quietly. Used to drive
    enforce_managed_process_stop_timeout into its escalation branch."""

    def __init__(self) -> None:
        self.kill_calls = 0

    def poll(self) -> int | None:
        return None

    def kill(self) -> None:
        self.kill_calls += 1


def _make_kill_handle() -> SimpleNamespace:
    spec = SimpleNamespace(
        process_id="zombie_proc",
        shutdown_timeout_s=1.0,
    )
    return SimpleNamespace(
        spec=spec,
        state="STOPPING",
        stop_requested_t_mono=10.0,
        popen=_StaleFakePopen(),
        last_error=None,
        last_error_kind=None,
        kill_attempts=0,
    )


class EnforceStopTimeoutEscalationTests(unittest.TestCase):
    def test_kill_escalates_to_failed_after_max_attempts(self) -> None:
        handle = _make_kill_handle()
        events: list[tuple[str, object]] = []
        mgr = SimpleNamespace(
            _publish_process_event=lambda topic, h: events.append((topic, h)),
        )
        # Call past the shutdown_timeout_s window. Each call must bump
        # kill_attempts and call popen.kill once. After
        # _MAX_KILL_ATTEMPTS, the handle flips to FAILED and emits
        # manager.process.failed.
        for _ in range(_MAX_KILL_ATTEMPTS):
            enforce_managed_process_stop_timeout(mgr, handle, now_mono=100.0)

        self.assertEqual(handle.kill_attempts, _MAX_KILL_ATTEMPTS)
        self.assertEqual(handle.popen.kill_calls, _MAX_KILL_ATTEMPTS)
        # Final tick at _MAX_KILL_ATTEMPTS must have escalated.
        self.assertEqual(str(handle.state), "FAILED")
        self.assertEqual(handle.last_error_kind, "kill_escalated")
        self.assertIsNotNone(handle.last_error)
        self.assertIn("kill()", handle.last_error)
        failed_events = [e for e in events if e[0] == "manager.process.failed"]
        self.assertEqual(len(failed_events), 1)

    def test_kill_does_not_escalate_before_max_attempts(self) -> None:
        handle = _make_kill_handle()
        events: list[tuple[str, object]] = []
        mgr = SimpleNamespace(
            _publish_process_event=lambda topic, h: events.append((topic, h)),
        )
        # _MAX_KILL_ATTEMPTS - 1 calls: STOPPING should remain.
        for _ in range(_MAX_KILL_ATTEMPTS - 1):
            enforce_managed_process_stop_timeout(mgr, handle, now_mono=100.0)
        self.assertEqual(str(handle.state), "STOPPING")
        self.assertEqual(events, [])


# ---------------------------------------------------------------------------
# F.24 — bounded _lifecycle_event_queue with drop counter
# ---------------------------------------------------------------------------


class LifecycleEventQueueOverflowTests(unittest.TestCase):
    def test_overflow_drops_event_and_bumps_counter(self) -> None:
        main_id = threading.get_ident()
        # Pretend we're on a worker thread by making the manager's
        # `_main_thread_id` differ from this thread's id.
        mgr = SimpleNamespace(
            _main_thread_id=main_id + 999,  # mismatched on purpose
            _lifecycle_event_queue=queue.Queue(maxsize=2),
            _lifecycle_event_dropped=0,
            _lifecycle_event_dropped_lock=threading.Lock(),
        )
        # First two events fit.
        publish_manager_event(mgr, "manager.test", {"i": 0})
        publish_manager_event(mgr, "manager.test", {"i": 1})
        # Third event must be dropped; counter increments.
        publish_manager_event(mgr, "manager.test", {"i": 2})
        self.assertEqual(mgr._lifecycle_event_queue.qsize(), 2)
        self.assertEqual(mgr._lifecycle_event_dropped, 1)
        # Subsequent drops keep bumping.
        publish_manager_event(mgr, "manager.test", {"i": 3})
        self.assertEqual(mgr._lifecycle_event_dropped, 2)


# ---------------------------------------------------------------------------
# F.32 — stop_process_handle publishes .failed on non-zero exit
# ---------------------------------------------------------------------------


class _FakePopenExited:
    def __init__(self, rc: int) -> None:
        self._rc = rc

    def poll(self) -> int | None:
        return self._rc


def _make_stop_handle(popen) -> SimpleNamespace:
    spec = SimpleNamespace(process_id="p1")
    return SimpleNamespace(
        spec=spec,
        state="RUNNING",
        popen=popen,
        rpc_endpoint=None,
        last_exit_code=None,
        last_error=None,
        last_error_kind=None,
        last_signal_name=None,
    )


class StopProcessHandleNonZeroExitTests(unittest.TestCase):
    def test_zero_exit_publishes_exited(self) -> None:
        handle = _make_stop_handle(_FakePopenExited(0))
        events: list[tuple[str, object]] = []
        mgr = SimpleNamespace(
            _publish_process_event=lambda topic, h: events.append((topic, h)),
            _close_process_rpc=lambda h: None,
        )
        stop_process_handle(mgr, handle)
        self.assertEqual(str(handle.state), "EXITED")
        self.assertEqual([e[0] for e in events], ["manager.process.exited"])

    def test_nonzero_exit_publishes_failed_with_diagnostic(self) -> None:
        handle = _make_stop_handle(_FakePopenExited(137))  # SIGKILL
        events: list[tuple[str, object]] = []
        mgr = SimpleNamespace(
            _publish_process_event=lambda topic, h: events.append((topic, h)),
            _close_process_rpc=lambda h: None,
        )
        stop_process_handle(mgr, handle)
        self.assertEqual(str(handle.state), "FAILED")
        self.assertEqual([e[0] for e in events], ["manager.process.failed"])
        # Diagnostic is populated.
        self.assertEqual(handle.last_error_kind, "nonzero_exit")
        self.assertIsNotNone(handle.last_error)
        self.assertIn("exited", handle.last_error)


# ---------------------------------------------------------------------------
# F.33 — cleanup_orphans timeout_s is capped
# ---------------------------------------------------------------------------


class CleanupOrphansTimeoutCapTests(unittest.TestCase):
    def test_user_supplied_timeout_capped(self) -> None:
        called_with: dict[str, object] = {}

        def _summary(**kwargs):
            called_with.update(kwargs)
            return {"dry_run": False, "killed": [], "skipped": []}

        mgr = SimpleNamespace(
            _cleanup_orphans_summary=_summary,
            _record_orphan_cleanup=lambda *_a, **_kw: None,
            _publish_manager_event=lambda *_a, **_kw: None,
        )
        # Request a huge timeout; handler must clamp it.
        resp = route_manager_cleanup_orphans(
            mgr,
            {"params": {"timeout_s": 999999.0, "dry_run": True}},
        )
        self.assertTrue(resp.get("ok"))
        self.assertEqual(
            called_with["timeout_s"], _CLEANUP_ORPHANS_TIMEOUT_CAP_S
        )

    def test_reasonable_timeout_not_modified(self) -> None:
        called_with: dict[str, object] = {}

        def _summary(**kwargs):
            called_with.update(kwargs)
            return {"dry_run": False, "killed": [], "skipped": []}

        mgr = SimpleNamespace(
            _cleanup_orphans_summary=_summary,
            _record_orphan_cleanup=lambda *_a, **_kw: None,
            _publish_manager_event=lambda *_a, **_kw: None,
        )
        resp = route_manager_cleanup_orphans(
            mgr,
            {"params": {"timeout_s": 5.0, "dry_run": True}},
        )
        self.assertTrue(resp.get("ok"))
        self.assertEqual(called_with["timeout_s"], 5.0)

    def test_invalid_timeout_returns_error(self) -> None:
        mgr = SimpleNamespace(
            _cleanup_orphans_summary=lambda **_kw: {},
            _record_orphan_cleanup=lambda *_a, **_kw: None,
            _publish_manager_event=lambda *_a, **_kw: None,
        )
        resp = route_manager_cleanup_orphans(
            mgr, {"params": {"timeout_s": -1.0}}
        )
        self.assertFalse(resp.get("ok"))


# ---------------------------------------------------------------------------
# F.34 — _publish_transition_event surfaces failure into _last_error
# ---------------------------------------------------------------------------


class _StubManagerThatRaises:
    def publish_event(self, *, topic: str, payload: Any) -> None:
        raise RuntimeError("simulated publish failure")


class PublishTransitionEventFailureTests(unittest.TestCase):
    def _make_proc(self) -> StateMachineProcessBase:
        proc = object.__new__(StateMachineProcessBase)
        proc._manager = _StubManagerThatRaises()  # type: ignore[attr-defined]
        proc._process_id = "proc-1"
        proc._last_error = None
        return proc

    def test_publish_failure_recorded_in_last_error(self) -> None:
        proc = self._make_proc()
        proc._publish_transition_event(
            "READY", "RUNNING", reason="user", metadata=None
        )
        self.assertIsNotNone(proc._last_error)
        self.assertIn("READY", proc._last_error)
        self.assertIn("RUNNING", proc._last_error)
        self.assertIn("simulated publish failure", proc._last_error)

    def test_prior_last_error_not_overwritten(self) -> None:
        proc = self._make_proc()
        proc._last_error = "existing operational error"
        proc._publish_transition_event(
            "READY", "RUNNING", reason="user", metadata=None
        )
        # The pre-existing error stays — operational failures take
        # precedence over observability failures.
        self.assertEqual(proc._last_error, "existing operational error")


class ErrorCountsLockTests(unittest.TestCase):
    """F.20: _bump_error in hdf_writer (and tui_manager) takes a lock so
    concurrent bumps don't lose counts. Hardest to test deterministically
    without spawning threads — assert the lock attribute exists and the
    increment goes through it."""

    def test_hdf_writer_bump_error_takes_lock(self) -> None:
        from experiment_control.processes.hdf_writer import HdfWriter

        writer = object.__new__(HdfWriter)
        writer._error_counts = {}
        writer._error_counts_lock = threading.Lock()
        writer._bump_error("foo")
        writer._bump_error("foo")
        writer._bump_error("bar")
        self.assertEqual(writer._error_counts, {"foo": 2, "bar": 1})

    def test_tui_manager_bump_error_takes_lock(self) -> None:
        from experiment_control.tui_manager import ManagerTUI

        tui = object.__new__(ManagerTUI)
        tui._error_counts = {}
        tui._error_counts_lock = threading.Lock()
        tui._bump_error("a")
        tui._bump_error("b")
        tui._bump_error("a")
        self.assertEqual(tui._error_counts, {"a": 2, "b": 1})

    def test_concurrent_bumps_do_not_lose_counts(self) -> None:
        """Reproduces the race from F.20 with N threads bumping the same
        key. Without the lock, CPython's get+set decomposition would lose
        a measurable fraction of increments at this scale."""
        from experiment_control.processes.hdf_writer import HdfWriter

        writer = object.__new__(HdfWriter)
        writer._error_counts = {}
        writer._error_counts_lock = threading.Lock()

        N_THREADS = 8
        N_PER_THREAD = 1000
        barrier = threading.Barrier(N_THREADS)

        def _bump():
            barrier.wait()
            for _ in range(N_PER_THREAD):
                writer._bump_error("shared")

        threads = [threading.Thread(target=_bump) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            self.assertFalse(t.is_alive())

        self.assertEqual(
            writer._error_counts.get("shared"),
            N_THREADS * N_PER_THREAD,
            "concurrent _bump_error must not lose counts; "
            f"expected {N_THREADS * N_PER_THREAD}, got "
            f"{writer._error_counts.get('shared')}",
        )


if __name__ == "__main__":
    unittest.main()
