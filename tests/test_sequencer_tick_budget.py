# ruff: noqa: E402
"""F5: tick() must not run an unbounded number of cheap (set/call/assign)
steps in a single call - it must return periodically so sequencer.py's
run() loop can drain the RPC ROUTER (pause/stop/status) between chunks.

See docs/performance_review_2026-07-09.md, finding F5.
"""

import sys
import time
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer import runtime as runtime_mod
from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.runtime import SequencerRuntime


def _build_runtime() -> SequencerRuntime:
    return SequencerRuntime(
        call_device=lambda d, a, p: {"ok": True, "result": None},
        get_telemetry=lambda d, s: None,
        set_stream_context=lambda *a: None,
    )


def _sleepless_sequence(n: int) -> dict:
    """A sequence made entirely of cheap set/call steps (no sleep/wait),
    matching the F5 scenario: a scan composed purely of sets and calls."""
    steps = []
    for i in range(n):
        steps.append({"assign": {"i": i}})
        steps.append({"call": {"device": "dev", "action": "step"}})
    return {"version": 1, "steps": steps}


class TickBudgetTests(unittest.TestCase):
    def test_tick_returns_before_running_entire_sleepless_scan(self) -> None:
        """(a) A long sleepless sequence must not complete inside a single
        tick() call - tick() should return control after its budget so the
        caller can service RPC between chunks."""
        runtime = _build_runtime()
        runtime.load(parse_sequence(_sleepless_sequence(5000)))
        runtime.start()

        runtime.tick()

        status = runtime.status()
        self.assertEqual(status["state"], "RUNNING")
        self.assertLess(
            status["env"].get("i"),
            4999,
            "a single tick() call ran the entire sleepless scan to "
            "completion instead of returning after a bounded chunk",
        )

    def test_tick_call_is_time_bounded(self) -> None:
        """(a) Wall-clock bound: a single tick() call over a huge sleepless
        sequence must return in roughly the configured budget, not run
        until the sequence ends."""
        runtime = _build_runtime()
        runtime.load(parse_sequence(_sleepless_sequence(200_000)))
        runtime.start()

        start = time.monotonic()
        runtime.tick()
        elapsed = time.monotonic() - start

        self.assertEqual(runtime.status()["state"], "RUNNING")
        # Generous upper bound (budget is ~10ms) to absorb CI slowness/GC
        # pauses while still proving tick() didn't run unbounded.
        self.assertLess(elapsed, 2.0)

    def test_stop_takes_effect_within_one_budget_window(self) -> None:
        """(b) Simulates run()'s interleaving of RPC drain and tick(): a
        stop request issued between tick() calls on a long sleepless scan
        must take effect within roughly one budget window, not only after
        the whole scan finishes."""
        runtime = _build_runtime()
        runtime.load(parse_sequence(_sleepless_sequence(200_000)))
        runtime.start()

        # First tick() chunk runs, then (as run() would do between ticks)
        # an RPC handler calls request_stop().
        runtime.tick()
        self.assertEqual(runtime.status()["state"], "RUNNING")
        runtime.request_stop()

        # One more tick() (as run() would do) should observe the stop
        # request and stop - it must not run the remaining ~200k steps.
        runtime.tick()
        self.assertNotEqual(runtime.status()["state"], "RUNNING")

    def test_pause_takes_effect_within_one_budget_window(self) -> None:
        runtime = _build_runtime()
        runtime.load(parse_sequence(_sleepless_sequence(200_000)))
        runtime.start()

        runtime.tick()
        self.assertEqual(runtime.status()["state"], "RUNNING")
        runtime.request_pause()

        runtime.tick()
        self.assertEqual(runtime.status()["state"], "PAUSED")

    def test_atomic_block_not_split_by_budget(self) -> None:
        """(c) An atomic block of several cheap steps must still execute
        as a single uninterruptible unit - the new step/time budget must
        not fire mid-atomic, exactly like stop/pause already don't."""
        calls: list[int] = []

        def call_device(device, action, params):
            calls.append(len(calls))
            return {"ok": True, "result": None}

        runtime = SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        atomic_body = [
            {"call": {"device": "dev", "action": f"step{i}"}} for i in range(25)
        ]
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [{"atomic": {"do": atomic_body}}],
                }
            )
        )
        runtime.start()
        runtime.tick()

        # All 25 atomic-body calls must have executed within the single
        # tick() call that entered the atomic block - the budget must not
        # have split it, regardless of the step-count/time budget.
        self.assertEqual(len(calls), 25)

    def test_atomic_block_ignores_step_budget_even_when_large(self) -> None:
        """(c) A large atomic block (bigger than the step budget) must
        still run to completion in one go."""
        calls: list[int] = []

        def call_device(device, action, params):
            calls.append(len(calls))
            return {"ok": True, "result": None}

        runtime = SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        big_n = runtime_mod._TICK_MAX_STEPS + 50
        atomic_body = [
            {"call": {"device": "dev", "action": f"step{i}"}} for i in range(big_n)
        ]
        runtime.load(
            parse_sequence(
                {
                    "version": 1,
                    "steps": [{"atomic": {"do": atomic_body}}],
                }
            )
        )
        runtime.start()
        runtime.tick()

        self.assertEqual(len(calls), big_n)

    def test_existing_short_sequences_still_complete_in_one_tick_loop(self) -> None:
        """(d) Sanity check that ordinary short sequences still drain to
        completion via the normal `while state == RUNNING: tick()` pattern
        used throughout the existing test suite."""
        runtime = _build_runtime()
        runtime.load(parse_sequence(_sleepless_sequence(3)))
        runtime.start()
        ticks = 0
        while runtime.state == "RUNNING":
            runtime.tick()
            ticks += 1
            self.assertLess(ticks, 10_000)
        self.assertEqual(runtime.status()["state"], "STOPPED")


if __name__ == "__main__":
    unittest.main()
