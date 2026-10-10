# ruff: noqa: E402

import sys
import time
import types
import unittest
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import experiment_control.sequencer.runtime as runtime_module
from experiment_control.sequencer.ast import (
    AssignStep,
    CallStep,
    ForStep,
    IfStep,
    RepeatStep,
    SequenceSpec,
    SleepStep,
    TryStep,
    UseStep,
    WhileStep,
)
from experiment_control.sequencer.runtime import SequencerRuntime


class _FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return 1_700_000_000.0 + self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _spec(steps: list[Any], vars: dict[str, Any] | None = None) -> SequenceSpec:
    return SequenceSpec(version=1, meta={}, vars=vars or {}, steps=steps)


class SequencerProgressEtaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FakeClock()
        self._real_time = runtime_module.time
        runtime_module.time = types.SimpleNamespace(  # type: ignore[assignment]
            monotonic=self.clock.monotonic,
            time=self.clock.time,
            sleep=self.clock.sleep,
            perf_counter=time.perf_counter,
        )
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.call_cost_s = 0.0

    def tearDown(self) -> None:
        runtime_module.time = self._real_time  # type: ignore[assignment]

    def _runtime(
        self, resolve_use: Callable[[str], SequenceSpec] | None = None
    ) -> SequencerRuntime:
        def call_device(
            device_id: str, action: str, params: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append((device_id, action, dict(params)))
            self.clock.t += self.call_cost_s
            return {"ok": True, "result": None}

        return SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda device, signal: None,
            set_stream_context=lambda device, stream, ctx, fields: None,
            resolve_use=resolve_use,
        )

    def _advance(self, runtime: SequencerRuntime) -> None:
        """One tick; jump the clock to the end of a pending sleep."""
        busy = runtime.tick()
        sleep_until = runtime._sleep_until
        if not busy and sleep_until is not None and sleep_until > self.clock.t:
            self.clock.t = sleep_until

    def _run(self, runtime: SequencerRuntime, max_ticks: int = 10_000) -> None:
        for _ in range(max_ticks):
            if runtime.state != "RUNNING":
                return
            self._advance(runtime)
        self.fail("sequence did not finish")

    def _progress(self, runtime: SequencerRuntime) -> dict[str, Any]:
        return runtime.status()["progress"]

    def test_while_total_is_approximate_and_finishes_at_100(self) -> None:
        runtime = self._runtime()
        runtime.load(_spec([WhileStep(condition=False, body=[])]))
        runtime.start()

        progress = self._progress(runtime)
        self.assertEqual(progress["total_steps"], 1)
        self.assertTrue(progress["approximate"])
        self.assertIn("while", str(progress["estimate_reason"]))

        self._run(runtime)
        progress = self._progress(runtime)
        self.assertEqual(progress["completed_steps"], 1)
        self.assertEqual(progress["percent"], 100.0)
        self.assertEqual(progress["eta_s"], 0.0)

    def test_while_looping_three_times_grows_total(self) -> None:
        runtime = self._runtime()
        runtime.load(
            _spec(
                [
                    AssignStep(values={"n": 0}),
                    WhileStep(
                        condition={"lt": ["${n}", 3]},
                        body=[
                            AssignStep(values={"n": "${n + 1}"}),
                            SleepStep(seconds=1.0),
                        ],
                    ),
                ]
            )
        )
        runtime.start()
        totals = []
        for _ in range(1000):
            if runtime.state != "RUNNING":
                break
            self._advance(runtime)
            self.clock.t += 1.5  # let the re-walk throttle expire
            totals.append(self._progress(runtime)["total_steps"])
        progress = self._progress(runtime)
        # assign + while + 3 x (assign + sleep)
        self.assertEqual(progress["completed_steps"], 8)
        self.assertEqual(progress["total_steps"], 8)
        self.assertEqual(progress["percent"], 100.0)
        self.assertLess(min(t for t in totals if t is not None), 8)

    def test_for_over_assigned_var_becomes_exact_after_assign(self) -> None:
        runtime = self._runtime()
        runtime.load(
            _spec(
                [
                    AssignStep(values={"targets": [1.0, 2.0, 3.0]}),
                    SleepStep(seconds=1.0),
                    ForStep(
                        bind={"value": "x"},
                        in_expr="${targets}",
                        body=[AssignStep(values={"y": "${x}"})],
                    ),
                ]
            )
        )
        runtime.start()
        progress = self._progress(runtime)
        self.assertIsNone(progress["total_steps"])
        self.assertIn("targets", str(progress["estimate_reason"]))

        runtime.tick()  # runs the assign, starts the sleep
        progress = self._progress(runtime)
        self.assertEqual(progress["total_steps"], 6)
        self.assertFalse(progress["approximate"])

    def test_stop_during_sleep_does_not_leak_into_cleanup(self) -> None:
        cleanup_assign = AssignStep(values={"done": 1})
        runtime = self._runtime()
        runtime.load(
            _spec(
                [
                    TryStep(
                        body=[SleepStep(seconds=10.0)],
                        finally_steps=[cleanup_assign, SleepStep(seconds=2.0)],
                    )
                ]
            )
        )
        runtime.start()
        runtime.tick()  # enters the 10 s sleep
        self.clock.t += 5.0
        before = self._progress(runtime)
        self.assertEqual(before["phase"], "run")

        runtime.request_stop()
        runtime.tick()  # begins the unwind
        progress = self._progress(runtime)
        self.assertEqual(progress["phase"], "cleanup")
        self.assertEqual(progress["cleanup_completed_steps"], 0)
        self.assertEqual(progress["cleanup_total_steps"], 2)
        runtime.tick()  # runs the cleanup assign, starts the 2 s sleep
        progress = self._progress(runtime)
        self.assertEqual(progress["phase"], "cleanup")
        self.assertEqual(progress["completed_steps"], before["completed_steps"])
        self.assertEqual(progress["total_steps"], before["total_steps"])
        self.assertEqual(progress["cleanup_completed_steps"], 1)
        self.assertEqual(progress["cleanup_total_steps"], 2)
        self.assertAlmostEqual(progress["eta_s"], 2.0)

        assign_s = runtime._step_durations.estimate(cleanup_assign, "assign")
        self.assertIsNotNone(assign_s)
        assert assign_s is not None
        self.assertLess(assign_s, 0.1)

        self._run(runtime)
        self.assertEqual(runtime.state, "STOPPED")
        progress = self._progress(runtime)
        self.assertEqual(progress["cleanup_completed_steps"], 2)
        self.assertEqual(progress["eta_s"], 0.0)

    def test_use_with_overridden_vars_counts_nested_and_outer_repeats(self) -> None:
        inner = _spec(
            [
                SleepStep(seconds=1.0),
                RepeatStep(times="${vars.n}", body=[AssignStep(values={"a": 1})]),
            ],
            vars={"n": 2},
        )
        runtime = self._runtime(resolve_use=lambda name: inner)
        runtime.load(
            _spec(
                [
                    UseStep(sequence_id="inner", args={"n": 4}),
                    RepeatStep(times="${vars.n}", body=[AssignStep(values={"b": 1})]),
                ],
                vars={"n": 3},
            )
        )
        runtime.start()
        # use + (sleep + repeat + 4) + (repeat + 3)
        self.assertEqual(self._progress(runtime)["total_steps"], 11)
        runtime.tick()  # now inside the nested sleep; outer frame below the use
        self.assertEqual(self._progress(runtime)["total_steps"], 11)
        self._run(runtime)
        self.assertEqual(self._progress(runtime)["completed_steps"], 11)

    def test_eta_counts_down_during_a_sleep(self) -> None:
        self.call_cost_s = 0.05
        runtime = self._runtime()
        runtime.load(
            _spec(
                [
                    RepeatStep(
                        times=5,
                        body=[
                            SleepStep(seconds=2.0),
                            RepeatStep(
                                times=20,
                                body=[CallStep(device="d", action="read", params={})],
                            ),
                        ],
                    )
                ]
            )
        )
        runtime.start()
        # Finish two iterations, then stop inside the third sleep.
        while runtime._completed_steps < 1 + 2 * 22:
            self._advance(runtime)
        runtime.tick()
        self.assertIsNotNone(runtime._sleep_until)
        eta_a = self._progress(runtime)["eta_s"]
        self.clock.t += 0.5
        eta_b = self._progress(runtime)["eta_s"]
        self.assertIsNotNone(eta_a)
        self.assertIsNotNone(eta_b)
        assert eta_a is not None and eta_b is not None
        self.assertAlmostEqual(eta_a - eta_b, 0.5, places=6)

    def test_eta_matches_remaining_time_with_fixed_costs(self) -> None:
        self.call_cost_s = 0.05
        runtime = self._runtime()
        runtime.load(
            _spec(
                [
                    RepeatStep(
                        times=6,
                        body=[
                            SleepStep(seconds=2.0),
                            RepeatStep(
                                times=20,
                                body=[CallStep(device="d", action="read", params={})],
                            ),
                        ],
                    )
                ]
            )
        )
        runtime.start()
        start_t = self.clock.t
        # Run two full iterations (2 s sleep + 20 x 0.05 s each).
        while runtime._completed_steps < 1 + 2 * 22:
            self._advance(runtime)
        elapsed = self.clock.t - start_t
        self.clock.t += 1.5  # let the re-walk throttle expire
        progress = self._progress(runtime)
        self.assertIsNotNone(progress["eta_s"])
        # No step is in flight, so idle time doesn't shrink the work left.
        true_remaining = 6 * 3.0 - elapsed
        self.assertAlmostEqual(
            progress["eta_s"], true_remaining, delta=0.05 * true_remaining
        )
        self.assertIsNotNone(progress["time_percent"])
        self.assertIsNotNone(progress["eta_wall_ts"])

    def test_sleep_lengths_from_the_loop_variable_are_each_counted(self) -> None:
        runtime = self._runtime()
        runtime.load(
            _spec(
                [
                    AssignStep(values={"a": 1}),
                    ForStep(
                        bind={"value": "t"},
                        in_expr=[1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0],
                        body=[SleepStep(seconds="${t}")],
                    ),
                ]
            )
        )
        runtime.start()
        # Finish the assign, the for step and the 1, 2, 4 s sleeps (the
        # helper jumps the clock to the end of each sleep), so the ETA gate
        # is open.
        while runtime._completed_steps < 5:
            self._advance(runtime)
        runtime.tick()  # finishes the 8 s sleep, starts the 16 s one
        self.clock.t += 4.0  # 4 s into the 16 s sleep; re-walk throttle
        progress = self._progress(runtime)
        # 12 s left of the 16 s sleep, then 32 + 64 + 128 s.
        self.assertAlmostEqual(progress["eta_s"], 12.0 + 224.0, delta=0.5)

    def test_repeat_count_projects_future_loops(self) -> None:
        runtime = self._runtime()
        runtime.load(_spec([SleepStep(seconds=1.0)]))
        runtime.start(repeat_count=3)
        progress = self._progress(runtime)
        self.assertEqual(progress["total_steps"], 3)
        self.assertEqual(progress["scope"], "run")
        self._run(runtime)
        progress = self._progress(runtime)
        self.assertEqual(progress["completed_steps"], 3)
        self.assertEqual(progress["percent"], 100.0)

    def test_continuous_run_reports_current_loop(self) -> None:
        runtime = self._runtime()
        runtime.load(_spec([SleepStep(seconds=1.0), AssignStep(values={"x": 1})]))
        runtime.start(continuous=True)
        for _ in range(7):
            self._advance(runtime)
        progress = self._progress(runtime)
        self.assertEqual(progress["scope"], "loop")
        self.assertEqual(progress["total_steps"], 2)
        self.assertLessEqual(progress["completed_steps"], 2)
        self.assertIsNotNone(progress["percent"])
        runtime.request_stop()

    def test_shuffled_loop_gets_fresh_draw_each_time_it_runs(self) -> None:
        runtime = self._runtime()
        runtime.load(
            _spec(
                [
                    ForStep(
                        bind={"value": "x"},
                        in_expr={"gen": {"values": list(range(20)), "shuffle": True}},
                        body=[
                            CallStep(device="d", action="visit", params={"x": "${x}"})
                        ],
                    )
                ]
            )
        )
        runtime.start(repeat_count=2)
        for _ in range(10_000):
            if runtime.state != "RUNNING":
                break
            self._advance(runtime)
            self._progress(runtime)  # exercise the estimator's records cache
        visits = [params["x"] for _, action, params in self.calls if action == "visit"]
        self.assertEqual(len(visits), 40)
        self.assertEqual(sorted(visits[:20]), list(range(20)))
        self.assertNotEqual(visits[:20], visits[20:])


class SequencerProgressWalkCostTests(unittest.TestCase):
    def test_status_on_large_nested_sequence_is_fast(self) -> None:
        runtime = SequencerRuntime(
            call_device=lambda device, action, params: {"ok": True, "result": None},
            get_telemetry=lambda device, signal: None,
            set_stream_context=lambda device, stream, ctx, fields: None,
        )
        spots = {
            "gen": {
                "scan2d": {
                    "center": {"x": 0, "y": 0},
                    "size": {"width": 1200, "height": 2000},
                    "pitch": 25,
                },
                "sample": {"count": 4, "replace": True},
            }
        }
        runtime.load(
            _spec(
                [
                    ForStep(
                        bind={"value": "f"},
                        in_expr={"gen": {"values": list(range(40)), "shuffle": True}},
                        body=[
                            CallStep(device="d", action="set", params={"f": "${f}"}),
                            ForStep(
                                bind={"x": "x", "y": "y"},
                                in_expr=spots,
                                body=[
                                    CallStep(device="d", action="move", params={}),
                                    RepeatStep(
                                        times=16,
                                        body=[
                                            CallStep(device="d", action="a", params={}),
                                            CallStep(device="d", action="b", params={}),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    )
                ]
            )
        )
        runtime.start()
        started = time.perf_counter()
        progress = runtime.status()["progress"]
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.assertGreater(progress["total_steps"], 5000)
        self.assertLess(elapsed_ms, 50.0)

    def _for_runtime(self, body: list[Any], n: int) -> SequencerRuntime:
        runtime = SequencerRuntime(
            call_device=lambda device, action, params: {"ok": True, "result": None},
            get_telemetry=lambda device, signal: None,
            set_stream_context=lambda device, stream, ctx, fields: None,
        )
        runtime.load(
            _spec(
                [
                    ForStep(
                        bind={"value": "x"},
                        in_expr={"gen": {"values": list(range(n))}},
                        body=body,
                    )
                ]
            )
        )
        runtime.start()
        return runtime

    def test_large_loop_independent_of_its_variable_is_counted_once(self) -> None:
        # A full scan2d grid without `sample:` gives thousands of records per
        # loop; walking each one on every status() blocked the run loop.
        runtime = self._for_runtime(
            [RepeatStep(times=15, body=[CallStep(device="d", action="a", params={})])],
            20_000,
        )
        started = time.perf_counter()
        progress = runtime.status()["progress"]
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.assertEqual(progress["total_steps"], 1 + 20_000 * 16)
        self.assertLess(elapsed_ms, 50.0)

    def test_loop_whose_counts_use_its_variable_is_counted_per_record(self) -> None:
        # Only record 57 takes the `then` branch: no sampling would catch it.
        runtime = self._for_runtime(
            [
                IfStep(
                    condition={"eq": ["${x}", 57]},
                    then_steps=[AssignStep(values={"hit": 1})],
                )
            ],
            100,
        )
        self.assertEqual(runtime.status()["progress"]["total_steps"], 1 + 100 + 1)

    def test_repeat_count_from_loop_variable_is_counted_per_record(self) -> None:
        runtime = self._for_runtime(
            [RepeatStep(times="${x}", body=[AssignStep(values={"a": 1})])],
            5,
        )
        # 1 for + 5 repeats + (0 + 1 + 2 + 3 + 4) assigns
        self.assertEqual(runtime.status()["progress"]["total_steps"], 1 + 5 + 10)


if __name__ == "__main__":
    unittest.main()


class SequencerProgressLiveConditionTests(unittest.TestCase):
    def test_if_on_telemetry_is_estimated_not_sampled(self) -> None:
        sampled: list[str] = []

        def get_telemetry(device: str, signal: str) -> dict[str, Any]:
            sampled.append(signal)
            return {"value": 1.0, "age_s": 0.0}

        runtime = SequencerRuntime(
            call_device=lambda device, action, params: {"ok": True, "result": None},
            get_telemetry=get_telemetry,
            set_stream_context=lambda device, stream, ctx, fields: None,
        )
        runtime.load(
            _spec(
                [
                    IfStep(
                        condition={
                            "gt": [{"telemetry": {"device": "d", "signal": "s"}}, 0]
                        },
                        then_steps=[
                            AssignStep(values={"a": 1}),
                            AssignStep(values={"b": 1}),
                        ],
                    )
                ]
            )
        )
        runtime.start()
        progress = runtime.status()["progress"]
        self.assertEqual(sampled, [])
        self.assertEqual(progress["total_steps"], 3)
        self.assertTrue(progress["approximate"])
