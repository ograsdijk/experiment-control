# ruff: noqa: E402
"""Tests for F15: dynamic sequencer poll timeout.

`SequencerProcess.run()` used to poll/drain with a hardcoded 50ms timeout on
every outer-loop iteration, which quantized `sleep` and `wait_until` timing
onto a 50ms grid. `SequencerRuntime.next_poll_timeout_ms()` now reports the
time until the next pending deadline (sleep-until / wait_until sample), so
the outer loop can wake up sooner while still capping at 50ms as a ceiling
for RPC/control-plane responsiveness.

These tests drive `SequencerRuntime` directly (no ZMQ), using a small loop
that mimics `SequencerProcess.run()`'s poll-then-tick shape:

    while runtime.state == "RUNNING":
        timeout_ms = runtime.next_poll_timeout_ms(ceiling_ms=50)
        time.sleep(timeout_ms / 1000.0)
        runtime.tick()

Following this suite's existing convention (see test_sequencer_progress.py),
real wall-clock time is used with tolerance margins rather than mocking
time.monotonic.
"""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.runtime import SequencerRuntime

_OLD_FIXED_POLL_MS = 50


def _build_runtime(*, call_device=None, get_telemetry=None) -> SequencerRuntime:
    def _default_call_device(
        device_id: str, action: str, params: dict[str, object]
    ) -> dict[str, object]:
        return {"ok": True, "result": None}

    def _default_get_telemetry(device_id: str, signal: str) -> dict[str, object] | None:
        return None

    return SequencerRuntime(
        call_device=call_device or _default_call_device,
        get_telemetry=get_telemetry or _default_get_telemetry,
        set_stream_context=lambda *_a, **_k: None,
    )


def _drive(runtime: SequencerRuntime, *, budget_s: float = 2.0) -> None:
    """Emulate SequencerProcess.run()'s poll-then-tick outer loop.

    Real production only blocks the full poll ceiling on iterations where
    nothing is pending yet (e.g. before the RPC that starts a run arrives,
    at which point the ZMQ poll returns early on the RPC event itself, not
    on the timeout). Callers of `_drive` are expected to have already
    performed the first `tick()` that kicks off the step under test, so
    this loop only ever waits on *real* pending deadlines.
    """
    deadline = time.monotonic() + budget_s
    while runtime.state == "RUNNING" and time.monotonic() < deadline:
        timeout_ms = runtime.next_poll_timeout_ms(ceiling_ms=_OLD_FIXED_POLL_MS)
        time.sleep(timeout_ms / 1000.0)
        runtime.tick()


class NextPollTimeoutUnitTests(unittest.TestCase):
    """Deterministic unit coverage of next_poll_timeout_ms itself."""

    def test_no_pending_sleep_or_wait_falls_back_to_ceiling(self) -> None:
        runtime = _build_runtime()
        runtime._state = "RUNNING"  # noqa: SLF001
        self.assertEqual(runtime.next_poll_timeout_ms(ceiling_ms=50), 50)

    def test_not_running_falls_back_to_ceiling_even_with_pending_sleep(self) -> None:
        runtime = _build_runtime()
        runtime._state = "PAUSED"  # noqa: SLF001
        runtime._sleep_until = time.monotonic() + 0.005  # noqa: SLF001
        self.assertEqual(runtime.next_poll_timeout_ms(ceiling_ms=50), 50)

    def test_pending_sleep_reports_remaining_time_not_ceiling(self) -> None:
        runtime = _build_runtime()
        runtime._state = "RUNNING"  # noqa: SLF001
        runtime._sleep_until = time.monotonic() + 0.005  # noqa: SLF001
        timeout_ms = runtime.next_poll_timeout_ms(ceiling_ms=50)
        self.assertLess(timeout_ms, 50)
        self.assertGreaterEqual(timeout_ms, 1)

    def test_pending_sleep_beyond_ceiling_is_clamped_to_ceiling(self) -> None:
        runtime = _build_runtime()
        runtime._state = "RUNNING"  # noqa: SLF001
        runtime._sleep_until = time.monotonic() + 5.0  # noqa: SLF001
        self.assertEqual(runtime.next_poll_timeout_ms(ceiling_ms=50), 50)

    def test_elapsed_sleep_deadline_clamps_to_floor_not_negative(self) -> None:
        runtime = _build_runtime()
        runtime._state = "RUNNING"  # noqa: SLF001
        runtime._sleep_until = time.monotonic() - 1.0  # noqa: SLF001
        timeout_ms = runtime.next_poll_timeout_ms(ceiling_ms=50, floor_ms=1)
        self.assertEqual(timeout_ms, 1)

    def test_pending_wait_state_reports_remaining_time_not_ceiling(self) -> None:
        from experiment_control.sequencer.runtime import _WaitState

        runtime = _build_runtime()
        runtime._state = "RUNNING"  # noqa: SLF001
        runtime._wait_state = _WaitState(  # noqa: SLF001
            start_t=time.monotonic(),
            timeout_s=5.0,
            every_s=0.02,
            next_sample_t=time.monotonic() + 0.02,
            stable_for_s=0.0,
            condition=None,
            sample_spec={},
            reduce_spec=None,
            samples=[],
            max_samples=10000,
        )
        timeout_ms = runtime.next_poll_timeout_ms(ceiling_ms=50)
        self.assertLess(timeout_ms, 50)
        self.assertGreaterEqual(timeout_ms, 1)

    def test_earliest_of_sleep_and_wait_deadlines_is_used(self) -> None:
        from experiment_control.sequencer.runtime import _WaitState

        runtime = _build_runtime()
        runtime._state = "RUNNING"  # noqa: SLF001
        now = time.monotonic()
        runtime._sleep_until = now + 0.04  # noqa: SLF001
        runtime._wait_state = _WaitState(  # noqa: SLF001
            start_t=now,
            timeout_s=5.0,
            every_s=0.01,
            next_sample_t=now + 0.01,
            stable_for_s=0.0,
            condition=None,
            sample_spec={},
            reduce_spec=None,
            samples=[],
            max_samples=10000,
        )
        timeout_ms = runtime.next_poll_timeout_ms(ceiling_ms=50)
        # Should track the sooner (wait) deadline, well under the sleep one.
        self.assertLess(timeout_ms, 30)


class SleepStepTimingTests(unittest.TestCase):
    """(a) A `sleep: 0.005` step should complete close to 5ms, not ~50ms."""

    def test_short_sleep_step_is_not_quantized_to_fixed_poll(self) -> None:
        runtime = _build_runtime()
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [{"sleep": 0.005}],
            }
        )
        runtime.load(spec)
        runtime.start()
        # First tick kicks off the sleep step (sets _sleep_until), mirroring
        # production where tick() runs right after the poll that delivered
        # the start RPC (i.e. before any poll-timeout wait is relevant).
        runtime.tick()
        start = time.monotonic()
        _drive(runtime, budget_s=2.0)
        elapsed = time.monotonic() - start

        self.assertEqual(runtime.state, "STOPPED")
        # Old fixed-50ms poll would land this at ~50ms+ (likely multiple
        # 50ms cycles); allow generous margin for OS scheduler/timer
        # granularity (Windows default timer resolution is ~15ms) but stay
        # well clear of the old fixed-poll floor.
        self.assertLess(elapsed, 0.04)
        self.assertGreaterEqual(elapsed, 0.003)


class WaitUntilSamplingTimingTests(unittest.TestCase):
    """(b) wait_until(every_s=0.02) should sample ~every 20ms, not 50ms."""

    def test_wait_until_samples_near_every_s_interval(self) -> None:
        sample_times: list[float] = []

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            sample_times.append(time.monotonic())
            # Require 4 samples before the condition is satisfied so we get
            # several inter-sample intervals to measure.
            return {"ok": True, "result": len(sample_times)}

        runtime = _build_runtime(call_device=call_device)
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "wait_until": {
                            "every_s": 0.02,
                            "sample": {"call": {"device": "d", "action": "sample"}},
                            "condition": {"ge": ["${sample}", 4]},
                        }
                    }
                ],
            }
        )
        runtime.load(spec)
        runtime.start()
        # First tick kicks off the wait_until step; its first sample is
        # taken immediately (next_sample_t starts at "now"), same as the
        # first tick after the start RPC in production.
        runtime.tick()
        _drive(runtime, budget_s=2.0)

        self.assertEqual(runtime.state, "STOPPED")
        self.assertGreaterEqual(len(sample_times), 4)
        intervals = [
            b - a for a, b in zip(sample_times[:-1], sample_times[1:])
        ]
        # Old fixed-50ms poll would land every interval at ~50ms+; the
        # dynamic timeout should track the requested 20ms cadence. Allow
        # generous margin for OS scheduler/timer granularity (Windows
        # default timer resolution is ~15ms).
        avg_interval = sum(intervals) / len(intervals)
        self.assertLess(avg_interval, 0.045)


class NoPendingSleepOrWaitTimingTests(unittest.TestCase):
    """(c) With no pending sleep/wait, poll timeout stays at the 50ms ceiling
    (RPC/control-plane responsiveness for pause/stop/status is unaffected)."""

    def test_plain_set_and_call_steps_keep_ceiling_poll_timeout(self) -> None:
        calls: list[str] = []

        def call_device(
            device_id: str, action: str, params: dict[str, object]
        ) -> dict[str, object]:
            calls.append(action)
            return {"ok": True, "result": None}

        runtime = _build_runtime(call_device=call_device)
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"device": "d", "action": "noop_a", "params": {}}},
                    {"call": {"device": "d", "action": "noop_b", "params": {}}},
                    {"call": {"device": "d", "action": "noop_c", "params": {}}},
                ],
            }
        )
        runtime.load(spec)
        runtime.start()

        observed_timeouts = []
        deadline = time.monotonic() + 2.0
        while runtime.state == "RUNNING" and time.monotonic() < deadline:
            timeout_ms = runtime.next_poll_timeout_ms(ceiling_ms=50)
            observed_timeouts.append(timeout_ms)
            runtime.tick()

        self.assertEqual(runtime.state, "STOPPED")
        self.assertEqual(calls, ["noop_a", "noop_b", "noop_c"])
        # No sleep/wait was ever pending, so every observed poll timeout
        # should be the unchanged 50ms ceiling (no regression vs. old
        # fixed-50ms behavior for pure set/call sequences).
        self.assertTrue(observed_timeouts)
        self.assertTrue(all(t == 50 for t in observed_timeouts))


if __name__ == "__main__":
    unittest.main()
