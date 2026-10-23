# ruff: noqa: E402

import sys
import time
import types
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import experiment_control.sequencer.runtime as runtime_module
from experiment_control.sequencer.ast import (
    AdaptiveStep,
    AssignStep,
    AtomicStep,
    CallStep,
    ForStep,
    IfStep,
    ParallelStep,
    RepeatStep,
    SequenceSpec,
    SleepStep,
    Step,
    TryStep,
    UseStep,
    WaitUntilStep,
    WhileStep,
    load_sequence_yaml,
)
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.sequencer.sequencer import SequencerProcess
from experiment_control.sequencer.source_info import build_step_source_info


class _FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return 1_700_000_000.0 + self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _spec(steps: list[Step], vars: dict[str, Any] | None = None) -> SequenceSpec:
    return SequenceSpec(version=1, meta={}, vars=vars or {}, steps=steps)


def _by_path(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in plan["steps"]}


class SequencerPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FakeClock()
        self._real_time = runtime_module.time
        runtime_module.time = types.SimpleNamespace(  # type: ignore[assignment]
            monotonic=self.clock.monotonic,
            time=self.clock.time,
            sleep=self.clock.sleep,
            perf_counter=time.perf_counter,
        )
        self.call_results: dict[str, Any] = {}

    def tearDown(self) -> None:
        runtime_module.time = self._real_time  # type: ignore[assignment]

    def _runtime(self, uses: dict[str, SequenceSpec] | None = None) -> SequencerRuntime:
        def call_device(device_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
            self.clock.t += 0.5
            return {"ok": True, "result": self.call_results.get(action)}

        return SequencerRuntime(
            call_device=call_device,
            get_telemetry=lambda device, signal: None,
            set_stream_context=lambda device, stream, ctx, fields: None,
            resolve_use=(lambda name: (uses or {})[name]),
        )

    def _load(self, runtime: SequencerRuntime, spec: SequenceSpec) -> None:
        runtime.load(spec, step_source_info=build_step_source_info(spec, source="t.yaml", line_map={}))

    def _advance(self, runtime: SequencerRuntime) -> None:
        busy = runtime.tick()
        sleep_until = runtime._sleep_until
        if not busy and sleep_until is not None and sleep_until > self.clock.t:
            self.clock.t = sleep_until

    def test_nested_loops_have_exact_counts_iterations_and_known_time(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    ForStep(
                        bind={"value": "f"},
                        in_expr={"gen": {"linspace": {"start": 0, "stop": 1, "num": "${n}"}}},
                        body=[
                            CallStep(device="d", action="set", params={"f": "${f}"}),
                            SleepStep(seconds="${settle}"),
                            RepeatStep(
                                times=3,
                                body=[
                                    CallStep(device="d", action="read", params={}),
                                    WaitUntilStep(
                                        raw={"timeout_s": "${t}", "condition": True}
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
                vars={"n": 5, "settle": 0.25, "t": 2.0},
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        self.assertFalse(plan["live"])
        self.assertEqual(steps["steps[0]"]["count"], 1)
        self.assertEqual(steps["steps[0]"]["iterations"], 5)
        self.assertEqual(steps["steps[0].for.do[0]"]["count"], 5)
        self.assertEqual(steps["steps[0].for.do[1]"]["seconds"], 0.25)
        self.assertEqual(steps["steps[0].for.do[2]"]["iterations"], 15)
        self.assertEqual(steps["steps[0].for.do[2].repeat.do[0]"]["count"], 15)
        self.assertEqual(steps["steps[0].for.do[2].repeat.do[1]"]["timeout_s"], 2.0)
        self.assertTrue(all(e["certainty"] == "exact" for e in plan["steps"]))
        # for + 5 x (call + sleep + repeat + 3 x (call + wait))
        self.assertEqual(plan["total_runs"], 1 + 5 * (3 + 6))
        self.assertEqual(plan["certainty"], "exact")
        self.assertAlmostEqual(plan["sleep_s"], 1.25)
        self.assertAlmostEqual(plan["wait_timeout_s"], 30.0)
        self.assertIsNone(plan["estimated_s"])

    def test_vars_override_changes_counts_and_unknown_names_are_ignored(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [RepeatStep(times="${n}", body=[CallStep(device="d", action="a", params={})])],
                vars={"n": 2},
            ),
        )
        plan = runtime.plan(vars_override={"n": 7, "nope": 1})
        self.assertEqual(plan["vars_override"], {"n": 7})
        self.assertEqual(_by_path(plan)["steps[0].repeat.do[0]"]["count"], 7)
        self.assertEqual(_by_path(runtime.plan())["steps[0].repeat.do[0]"]["count"], 2)

    def test_loop_over_a_called_list_is_counted_from_the_list_it_was_given(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    AssignStep(values={"center": {"call": {"device": "s", "action": "get"}}}),
                    CallStep(
                        device="ramp",
                        action="prepare_targets",
                        params={"values": "${grid}", "center_hz": "${center}"},
                        assign={"targets": {"kind": "key", "ref": "targets_hz"}},
                    ),
                    ForStep(
                        bind={"value": "f", "index": "i"},
                        in_expr={"gen": {"values": "${targets}", "shuffle": True}},
                        body=[CallStep(device="ramp", action="start", params={"f": "${f}"})],
                    ),
                ],
                vars={"grid": [-2.0, -1.0, 0.0, 1.0, 2.0]},
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        loop = steps["steps[2]"]
        self.assertEqual(loop["iterations"], 5)
        self.assertEqual(loop["iterations_certainty"], "inferred")
        self.assertIn("ramp.prepare_targets", loop["iterations_note"])
        self.assertIn("grid", loop["iterations_note"])
        body = steps["steps[2].for.do[0]"]
        self.assertEqual(body["count"], 5)
        self.assertEqual(body["certainty"], "inferred")
        self.assertEqual(plan["certainty"], "inferred")
        self.assertEqual(plan["total_runs"], 3 + 5)
        self.assertIsNone(plan["approximate_reason"])

    def test_called_list_with_two_list_params_is_unknown(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(
                        device="d",
                        action="merge",
                        params={"a": "${xs}", "b": "${ys}"},
                        save_as="items",
                    ),
                    ForStep(bind={"value": "v"}, in_expr="${items}", body=[SleepStep(seconds=1)]),
                    CallStep(device="d", action="after", params={}),
                ],
                vars={"xs": [1, 2], "ys": [3]},
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        self.assertIsNone(steps["steps[1]"]["iterations"])
        self.assertEqual(steps["steps[1]"]["iterations_certainty"], "unknown")
        self.assertEqual(steps["steps[1].for.do[0]"]["count"], 1)
        self.assertEqual(steps["steps[1].for.do[0]"]["certainty"], "unknown")
        # Steps after the loop are still counted.
        self.assertEqual(steps["steps[2]"]["count"], 1)
        self.assertEqual(steps["steps[2]"]["certainty"], "exact")
        self.assertIsNone(plan["total_runs"])
        self.assertEqual(plan["certainty"], "unknown")

    def test_undecided_if_lists_both_branches_but_totals_count_one(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(device="d", action="ok", params={}, save_as="locked"),
                    IfStep(
                        condition={"eq": ["${locked}", True]},
                        then_steps=[
                            CallStep(device="d", action="a", params={}),
                            CallStep(device="d", action="b", params={}),
                        ],
                        else_steps=[CallStep(device="d", action="c", params={})],
                    ),
                ]
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        for path in ("steps[1].if.then[0]", "steps[1].if.then[1]", "steps[1].if.else[0]"):
            self.assertEqual(steps[path]["count"], 1, path)
            self.assertEqual(steps[path]["certainty"], "approximate", path)
        self.assertIn("does not hold", steps["steps[1].if.else[0]"]["note"])
        # call + if + the longer (then) branch
        self.assertEqual(plan["total_runs"], 4)
        self.assertEqual(plan["certainty"], "approximate")

    def test_decided_if_counts_the_other_branch_as_never(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    IfStep(
                        condition={"eq": ["${mode}", "fast"]},
                        then_steps=[CallStep(device="d", action="a", params={})],
                        else_steps=[CallStep(device="d", action="b", params={})],
                    )
                ],
                vars={"mode": "fast"},
            ),
        )
        steps = _by_path(runtime.plan())
        self.assertEqual(steps["steps[0].if.then[0]"]["count"], 1)
        self.assertEqual(steps["steps[0].if.else[0]"]["count"], 0)
        self.assertEqual(steps["steps[0].if.else[0]"]["certainty"], "exact")

    def test_adaptive_and_while_bodies_are_counted_once(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(device="d", action="state", params={}, save_as="done"),
                    WhileStep(
                        condition={"eq": ["${done}", False]},
                        body=[CallStep(device="d", action="retune", params={})],
                    ),
                    AdaptiveStep(
                        id="opt",
                        controller={"kind": "random"},
                        space={"p": {"low": 0, "high": 1}},
                        bind={"p": "p"},
                        state=None,
                        body=[CallStep(device="d", action="set", params={"p": "${p}"})],
                        observe={},
                    ),
                ]
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        self.assertEqual(steps["steps[1]"]["iterations"], 1)
        self.assertEqual(steps["steps[1]"]["iterations_certainty"], "approximate")
        self.assertEqual(steps["steps[1].while.do[0]"]["certainty"], "approximate")
        self.assertIsNone(steps["steps[2]"]["iterations"])
        self.assertEqual(steps["steps[2].adaptive.do[0]"]["count"], 1)
        self.assertEqual(steps["steps[2].adaptive.do[0]"]["certainty"], "unknown")
        self.assertIn("trials", str(plan["unknown_reason"]))
        self.assertIsNone(plan["total_runs"])

    def test_use_sub_sequence_steps_are_listed_under_the_use_step(self) -> None:
        prepare = _spec(
            [
                ForStep(
                    bind={"value": "ch"},
                    in_expr="${channels}",
                    body=[CallStep(device="hv", action="on", params={"ch": "${ch}"})],
                ),
                WaitUntilStep(raw={"timeout_s": "${ready_s}", "condition": True}),
            ],
            vars={"channels": [0, 1, 2], "ready_s": 300.0},
        )
        runtime = self._runtime(uses={"prepare": prepare})
        self._load(
            runtime,
            _spec(
                [
                    TryStep(
                        body=[UseStep(sequence_id="prepare"), SleepStep(seconds=2)],
                        finally_steps=[CallStep(device="hv", action="idle", params={})],
                    )
                ]
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        loop = steps["steps[0].try.do[0].use.steps[0]"]
        self.assertEqual(loop["use_id"], "prepare")
        self.assertIsNone(loop["line"])
        self.assertEqual(loop["iterations"], 3)
        self.assertEqual(steps["steps[0].try.do[0].use.steps[0].for.do[0]"]["count"], 3)
        self.assertEqual(steps["steps[0].try.do[0].use.steps[1]"]["timeout_s"], 300.0)
        self.assertEqual(steps["steps[0].try.finally[0]"]["count"], 1)
        self.assertEqual(plan["wait_timeout_s"], 300.0)

    def test_disabled_steps_count_zero(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(device="d", action="a", params={}),
                    CallStep(device="d", action="b", params={}, disabled=True),
                ]
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        self.assertEqual(steps["steps[1]"]["count"], 0)
        self.assertTrue(steps["steps[1]"]["disabled"])
        self.assertEqual(plan["total_runs"], 1)

    def test_literal_value_list_reading_a_called_value_has_an_exact_count(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    AssignStep(values={"center": {"call": {"device": "s", "action": "get"}}}),
                    ForStep(
                        bind={"value": "f"},
                        in_expr={"gen": {"values": ["${center}", "${center + 1}"]}},
                        body=[SleepStep(seconds=1)],
                    ),
                ]
            ),
        )
        plan = runtime.plan()
        self.assertEqual(_by_path(plan)["steps[1]"]["iterations"], 2)
        self.assertEqual(plan["certainty"], "exact")
        self.call_results["get"] = 10.0
        runtime.start()
        self.assertFalse(runtime.status()["progress"]["approximate"])

    def test_parallel_branch_operations_are_listed_outside_the_totals(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    ParallelStep(
                        body=[
                            AtomicStep(
                                name="a",
                                body=[CallStep(device="a", action="arm", params={})],
                            ),
                            RepeatStep(
                                times=4,
                                body=[CallStep(device="b", action="read", params={})],
                            ),
                        ]
                    )
                ]
            ),
        )
        plan = runtime.plan()
        steps = _by_path(plan)
        self.assertEqual(steps["steps[0].parallel.do[1].repeat.do[0]"]["count"], 4)
        self.assertEqual(steps["steps[0].parallel.do[0].atomic.do[0]"]["count"], 1)
        self.assertEqual(plan["total_runs"], 1)

    def test_live_plan_counts_runs_so_far_and_status_lists_active_loops(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(
                        device="ramp",
                        action="prepare_targets",
                        params={"values": "${grid}"},
                        assign={"targets": {"kind": "key", "ref": "targets_hz"}},
                    ),
                    ForStep(
                        bind={"value": "f", "index": "i"},
                        in_expr={"gen": {"values": "${targets}"}},
                        body=[
                            RepeatStep(
                                times=2,
                                body=[
                                    CallStep(device="d", action="read", params={}),
                                    SleepStep(seconds=1),
                                ],
                            )
                        ],
                    ),
                ],
                vars={"grid": [1.0, 2.0, 3.0, 4.0]},
            ),
        )
        # The call actually returns three targets, not one per grid entry.
        self.call_results["prepare_targets"] = {"targets_hz": [10.0, 30.0, 20.0]}
        self.assertEqual(_by_path(runtime.plan())["steps[1]"]["iterations"], 4)
        runtime.start()
        sleep_path = "steps[1].for.do[0].repeat.do[1]"
        # Run until the second pass of the loop is sleeping in its repeat.
        for _ in range(200):
            busy = runtime.tick()
            plan_now = _by_path(runtime.plan())
            if plan_now[sleep_path]["runs_done"] >= 3:
                break
            if not busy and runtime._sleep_until is not None:
                self.clock.t = runtime._sleep_until
        self.clock.t += 2.0  # past the re-walk throttle
        plan = runtime.plan()
        self.assertTrue(plan["live"])
        steps = _by_path(plan)
        self.assertEqual(steps["steps[0]"]["runs_done"], 1)
        self.assertAlmostEqual(steps["steps[0]"]["mean_s"], 0.5)
        # Exact once the call has run: 3 targets x 2 repeats.
        self.assertEqual(steps[sleep_path]["count"], 6)
        self.assertEqual(steps[sleep_path]["certainty"], "exact")
        self.assertEqual(plan["certainty"], "exact")

        loops = runtime.status()["loops"]
        self.assertEqual([loop["kind"] for loop in loops], ["for", "repeat"])
        outer, inner = loops
        self.assertEqual(outer["path"], "steps[1]")
        self.assertEqual(outer["count"], 3)
        self.assertEqual(outer["iteration"], 2)
        self.assertEqual(outer["vars"], ["f", "i"])
        self.assertEqual(
            outer["values"],
            [{"f": 10.0, "i": 0}, {"f": 30.0, "i": 1}, {"f": 20.0, "i": 2}],
        )
        self.assertEqual(inner["count"], 2)
        self.assertGreaterEqual(inner["iteration"], 1)

        for _ in range(500):
            if runtime.state != "RUNNING":
                break
            busy = runtime.tick()
            if not busy and runtime._sleep_until is not None:
                self.clock.t = runtime._sleep_until
        self.assertEqual(runtime.status()["loops"], [])
        done = runtime.plan()
        self.assertFalse(done["live"])
        self.assertEqual(_by_path(done)[sleep_path]["runs_done"], 6)
        self.assertIsNotNone(done["estimated_s"])

    def _run_until(self, runtime: SequencerRuntime, done: Any, limit: int = 500) -> None:
        for _ in range(limit):
            if done():
                return
            self._advance(runtime)
        self.fail("condition not reached")

    def test_live_total_counts_a_shared_sub_sequence_once(self) -> None:
        settle = _spec([CallStep(device="d", action="a", params={}), SleepStep(seconds=1)])
        runtime = self._runtime(uses={"settle": settle})
        self._load(
            runtime,
            _spec([UseStep(sequence_id="settle"), UseStep(sequence_id="settle")]),
        )
        call = settle.steps[0]
        runtime.start()
        # Into the second use's sleep: the shared call has run twice.
        self._run_until(
            runtime,
            lambda: (runtime._step_durations.measured(call) or (0,))[0] >= 2
            and runtime._active_step is settle.steps[1],
        )
        self.clock.t += 2.0  # past the re-walk throttle
        plan = runtime.plan()
        self.assertTrue(plan["live"])
        self.assertEqual(plan["certainty"], "exact")
        # 2 uses + 2 calls + 2 sleeps, whichever place lists the shared steps.
        self.assertEqual(plan["total_runs"], 6)

    def test_idle_plan_counts_are_not_raised_to_the_last_run(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [RepeatStep(times="${n}", body=[CallStep(device="d", action="a", params={})])],
                vars={"n": 4},
            ),
        )
        runtime.start()
        self._run_until(runtime, lambda: runtime.state != "RUNNING")
        body = _by_path(runtime.plan(vars_override={"n": 2}))["steps[0].repeat.do[0]"]
        self.assertEqual(body["runs_done"], 4)
        self.assertEqual(body["count"], 2)

    def test_live_counts_cover_the_current_loop_in_repeat_mode(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    RepeatStep(
                        times=3,
                        body=[CallStep(device="d", action="a", params={}), SleepStep(seconds=1)],
                    )
                ]
            ),
        )
        sleep = runtime._spec.steps[0].body[1]  # type: ignore[union-attr]
        runtime.start(repeat_count=3)
        # Second sleep of the second loop (five sleeps finished overall).
        self._run_until(
            runtime,
            lambda: runtime._loops_completed == 1
            and (runtime._step_durations.measured(sleep) or (0,))[0] == 4
            and runtime._active_step is sleep,
        )
        self.clock.t += 2.0
        steps = _by_path(runtime.plan())
        entry = steps["steps[0].repeat.do[1]"]
        self.assertEqual(entry["runs_done"], 1)
        self.assertEqual(entry["count"], 3)
        self.assertEqual(entry["certainty"], "exact")

    def test_loop_scaled_over_budget_scales_nested_iterations(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    ForStep(
                        bind={"value": "x"},
                        in_expr=[2] * 10,
                        body=[
                            RepeatStep(
                                times="${x}",
                                body=[CallStep(device="d", action="a", params={})],
                            )
                        ],
                    )
                ]
            ),
        )
        saved = runtime_module._PLAN_WALK_BUDGET_S
        runtime_module._PLAN_WALK_BUDGET_S = -1.0  # scale after the first record
        try:
            plan = runtime.plan()
        finally:
            runtime_module._PLAN_WALK_BUDGET_S = saved
        steps = _by_path(plan)
        inner = steps["steps[0].for.do[0]"]
        self.assertEqual(inner["count"], 10)
        self.assertEqual(inner["iterations"], 20)
        self.assertEqual(inner["iterations_certainty"], "approximate")
        self.assertEqual(steps["steps[0].for.do[0].repeat.do[0]"]["count"], 20)

    def test_unknown_count_inside_parallel_branches_keeps_the_total(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(device="d", action="n", params={}, save_as="n"),
                    ParallelStep(
                        body=[
                            RepeatStep(
                                times="${n}",
                                body=[CallStep(device="b", action="read", params={})],
                            )
                        ]
                    ),
                ]
            ),
        )
        plan = runtime.plan()
        self.assertEqual(plan["total_runs"], 2)
        self.assertIsNone(plan["unknown_reason"])
        steps = _by_path(plan)
        self.assertEqual(steps["steps[1].parallel.do[0].repeat.do[0]"]["certainty"], "unknown")

    def test_steps_after_a_failed_walk_are_unknown_not_zero(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(device="d", action="a", params={}),
                    ForStep(
                        bind={"missing": "m"},
                        in_expr=[1, 2, 3],
                        body=[CallStep(device="d", action="b", params={})],
                    ),
                    CallStep(device="d", action="c", params={}),
                ]
            ),
        )
        plan = runtime.plan()
        self.assertIsNone(plan["total_runs"])
        steps = _by_path(plan)
        self.assertEqual(steps["steps[0]"]["certainty"], "exact")
        self.assertEqual(steps["steps[0]"]["count"], 1)
        for path in ("steps[1].for.do[0]", "steps[2]"):
            self.assertEqual(steps[path]["certainty"], "unknown", path)
            self.assertIn("not counted", steps[path]["note"], path)

    def test_failure_mid_loop_marks_the_rest_of_the_loop_body(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    ForStep(
                        bind={"a": "x"},
                        # The second record lacks the bound field.
                        in_expr=[{"a": 2}, {"b": 1}],
                        body=[
                            RepeatStep(
                                times="${x}",
                                body=[CallStep(device="d", action="a", params={})],
                            )
                        ],
                    ),
                    CallStep(device="d", action="after", params={}),
                    CallStep(device="d", action="off", params={}, disabled=True),
                ]
            ),
        )
        plan = runtime.plan()
        self.assertIsNotNone(plan["unknown_reason"])
        steps = _by_path(plan)
        self.assertEqual(steps["steps[0]"]["certainty"], "exact")
        for path in ("steps[0].for.do[0]", "steps[0].for.do[0].repeat.do[0]", "steps[1]"):
            self.assertEqual(steps[path]["certainty"], "unknown", path)
            self.assertIn("not counted", steps[path]["note"], path)
        # A disabled step is known never to run, failure or not.
        self.assertEqual(steps["steps[2]"]["certainty"], "exact")
        self.assertEqual(steps["steps[2]"]["note"], "disabled")

    def test_failure_in_second_use_of_a_shared_sequence_is_located_there(self) -> None:
        shared = _spec(
            [
                ForStep(
                    bind={"a": "x"},
                    in_expr="${items}",
                    body=[CallStep(device="d", action="a", params={})],
                )
            ],
            vars={"items": [{"a": 1}]},
        )
        runtime = self._runtime(uses={"s": shared})
        self._load(
            runtime,
            _spec(
                [
                    UseStep(sequence_id="s"),
                    CallStep(device="d", action="between", params={}),
                    UseStep(sequence_id="s", args={"items": [{"b": 1}]}),
                ]
            ),
        )
        plan = runtime.plan()
        self.assertIsNotNone(plan["unknown_reason"])
        steps = _by_path(plan)
        self.assertEqual(steps["steps[1]"]["certainty"], "exact")
        self.assertEqual(steps["steps[1]"]["count"], 1)
        self.assertEqual(steps["steps[2].use.steps[0]"]["certainty"], "unknown")

    def test_live_exact_counts_drop_the_up_front_reasons(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(
                        device="d",
                        action="n",
                        params={},
                        assign={"n": {"kind": "key", "ref": "n"}},
                    ),
                    RepeatStep(
                        times="${n}",
                        body=[CallStep(device="d", action="b", params={}), SleepStep(seconds=1)],
                    ),
                ]
            ),
        )
        self.assertIsNone(runtime.plan()["total_runs"])
        self.call_results["n"] = {"n": 3}
        runtime.start()
        sleep = runtime._spec.steps[1].body[1]  # type: ignore[union-attr]
        self._run_until(runtime, lambda: runtime._active_step is sleep)
        self.clock.t += 2.0
        plan = runtime.plan()
        self.assertEqual(plan["certainty"], "exact")
        self.assertEqual(plan["total_runs"], 2 + 3 * 2)
        self.assertIsNone(plan["unknown_reason"])
        self.assertIsNone(plan["approximate_reason"])

    def test_live_exact_plan_takes_times_from_its_counts(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(
                        device="d",
                        action="n",
                        params={},
                        assign={"n": {"kind": "key", "ref": "n"}},
                    ),
                    RepeatStep(
                        times="${n}",
                        body=[
                            WaitUntilStep(raw={"timeout_s": 2.0, "condition": True}),
                            SleepStep(seconds=1),
                        ],
                    ),
                ]
            ),
        )
        # Up front the repeat count is unknown and its body is counted once.
        self.assertEqual(runtime.plan()["sleep_s"], 1.0)
        self.call_results["n"] = {"n": 3}
        runtime.start()
        sleep = runtime._spec.steps[1].body[1]  # type: ignore[union-attr]
        self._run_until(runtime, lambda: runtime._active_step is sleep)
        self.clock.t += 2.0
        plan = runtime.plan()
        self.assertEqual(plan["certainty"], "exact")
        self.assertEqual(plan["sleep_s"], 3.0)
        self.assertEqual(plan["wait_timeout_s"], 6.0)
        # call 0.5 s + three sleeps of 1 s (no overhead on the fake clock).
        self.assertAlmostEqual(plan["estimated_s"], 3.5, places=6)

    def test_nested_scaled_loops_keep_one_scale_note(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(device="d", action="state", params={}, save_as="done"),
                    ForStep(
                        bind={"value": "x"},
                        in_expr=[1] * 5,
                        body=[
                            ForStep(
                                bind={"value": "y"},
                                in_expr={"gen": {"values": ["${x}"] * 4}},
                                body=[
                                    RepeatStep(
                                        times="${y}",
                                        body=[
                                            WhileStep(
                                                condition={"eq": ["${done}", False]},
                                                body=[CallStep(device="d", action="a", params={})],
                                            )
                                        ],
                                    )
                                ],
                            )
                        ],
                    ),
                ]
            ),
        )
        saved = runtime_module._PLAN_WALK_BUDGET_S
        runtime_module._PLAN_WALK_BUDGET_S = -1.0
        try:
            plan = runtime.plan()
        finally:
            runtime_module._PLAN_WALK_BUDGET_S = saved
        path = "steps[1].for.do[0].for.do[0].repeat.do[0]"
        note = _by_path(plan)[path]["iterations_note"]
        self.assertIn("while loop", note)
        self.assertEqual(note.count("estimated from its first"), 1)
        self.assertIn("loop of 5 items", note)

    def test_scaled_loop_keeps_nested_loop_notes(self) -> None:
        runtime = self._runtime()
        self._load(
            runtime,
            _spec(
                [
                    CallStep(device="d", action="state", params={}, save_as="done"),
                    ForStep(
                        bind={"value": "x"},
                        in_expr=[1] * 5,
                        body=[
                            RepeatStep(
                                times="${x}",
                                body=[
                                    WhileStep(
                                        condition={"eq": ["${done}", False]},
                                        body=[CallStep(device="d", action="a", params={})],
                                    )
                                ],
                            )
                        ],
                    ),
                ]
            ),
        )
        saved = runtime_module._PLAN_WALK_BUDGET_S
        runtime_module._PLAN_WALK_BUDGET_S = -1.0
        try:
            plan = runtime.plan()
        finally:
            runtime_module._PLAN_WALK_BUDGET_S = saved
        note = _by_path(plan)["steps[1].for.do[0].repeat.do[0]"]["iterations_note"]
        self.assertIn("while loop", note)
        self.assertIn("estimated from its first", note)

    def test_status_loops_inside_a_nested_use_have_plan_paths(self) -> None:
        inner = _spec(
            [ForStep(bind={"value": "f"}, in_expr=[1.0, 2.0], body=[SleepStep(seconds=1)])]
        )
        mid = _spec([UseStep(sequence_id="inner")])
        runtime = self._runtime(uses={"inner": inner, "mid": mid})
        self._load(runtime, _spec([UseStep(sequence_id="mid")]))
        runtime.start()
        self._run_until(runtime, lambda: runtime._active_step is inner.steps[0].body[0])
        (loop,) = runtime.status()["loops"]
        self.assertEqual(loop["path"], "steps[0].use.steps[0].use.steps[0]")
        self.assertIn(loop["path"], _by_path(runtime.plan()))

    def test_disabled_block_is_not_a_call_source(self) -> None:
        runtime = self._runtime()
        prepare = {"device": "ramp", "action": "prepare_targets"}
        self._load(
            runtime,
            _spec(
                [
                    AtomicStep(
                        name="old",
                        body=[
                            CallStep(**prepare, params={"values": [1, 2]}, save_as="targets")
                        ],
                        disabled=True,
                    ),
                    CallStep(**prepare, params={"values": "${grid}"}, save_as="targets"),
                    ForStep(
                        bind={"value": "f"},
                        in_expr="${targets}",
                        body=[CallStep(device="ramp", action="start", params={})],
                    ),
                ],
                vars={"grid": [1.0, 2.0, 3.0, 4.0, 5.0]},
            ),
        )
        loop = _by_path(runtime.plan())["steps[2]"]
        self.assertEqual(loop["iterations"], 5)
        self.assertEqual(loop["iterations_certainty"], "inferred")

    def test_status_loops_inside_a_used_sequence_have_plan_paths(self) -> None:
        sweep = _spec(
            [
                ForStep(
                    bind={"value": "f"},
                    in_expr=[1.0, 2.0],
                    body=[SleepStep(seconds=1)],
                )
            ]
        )
        runtime = self._runtime(uses={"sweep": sweep})
        self._load(
            runtime,
            _spec([CallStep(device="d", action="a", params={}), UseStep(sequence_id="sweep")]),
        )
        runtime.start()
        self._run_until(runtime, lambda: runtime._active_step is sweep.steps[0].body[0])
        (loop,) = runtime.status()["loops"]
        self.assertEqual(loop["path"], "steps[1].use.steps[0]")
        self.assertIn(loop["path"], _by_path(runtime.plan()))

    def test_plan_without_a_sequence_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            self._runtime().plan()


_SCAN_YAML = """version: 1
vars:
  points: 3
steps:
  - for:
      bind: {value: f}
      in:
        gen:
          linspace: {start: 0, stop: 1, num: "${points}"}
      do:
        - sleep: 0.5
        - call: {device: d, action: read}
"""


def _sequencer_process() -> SequencerProcess:
    process = object.__new__(SequencerProcess)
    process._runtime = SequencerRuntime(
        call_device=lambda *a, **k: {"ok": True, "result": None},
        get_telemetry=lambda *a, **k: None,
        set_stream_context=lambda *a, **k: None,
    )
    process._sequence_library = None
    process._sequence_library_path = None
    process._autoload_error = None
    process._autoload_error_ts_wall = None
    process._autoload_error_source = None
    process._last_progress_event_signature = None
    process._last_progress_event_mono = 0.0
    process._loaded_revision = 0
    process._loaded_sequence_source = None
    return process


class SequencerPlanRpcTests(unittest.TestCase):
    def test_plan_rpc_gives_lines_and_applies_overrides(self) -> None:
        process = _sequencer_process()
        process._set_loaded_sequence(
            spec=load_sequence_yaml(_SCAN_YAML),
            text=_SCAN_YAML,
            source="scan.yaml",
            source_kind="rpc",
            active_sequence_id=None,
        )
        resp = process._handle_rpc(
            {"type": "sequencer.plan", "params": {"vars_override": {"points": 4}}}
        )
        self.assertTrue(resp["ok"], resp)
        result = resp["result"]
        self.assertEqual(result["loaded_source"], "scan.yaml")
        self.assertEqual(result["loaded_revision"], 1)
        steps = _by_path(result)
        self.assertEqual(steps["steps[0]"]["line"], 5)
        self.assertEqual(steps["steps[0]"]["source"], "scan.yaml")
        self.assertEqual(steps["steps[0].for.do[1]"]["line"], 12)
        self.assertEqual(steps["steps[0].for.do[1]"]["count"], 4)
        self.assertEqual(result["sleep_s"], 2.0)

    def test_plan_rpc_errors(self) -> None:
        process = _sequencer_process()
        resp = process._handle_rpc({"type": "sequencer.plan", "params": {}})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "not_loaded")
        resp = process._handle_rpc(
            {"type": "sequencer.plan", "params": {"vars_override": [1]}}
        )
        self.assertEqual(resp["error"]["code"], "invalid_params")


if __name__ == "__main__":
    unittest.main()
