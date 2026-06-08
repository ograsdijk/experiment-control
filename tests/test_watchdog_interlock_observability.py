# ruff: noqa: E402
"""Regression tests for the watchdog + interlock observability fixes.

Three behavioural changes are pinned here:

1. evaluate_watchdog_rule + evaluate_interlock_rule accept a new
   keyword-only `on_condition_error=` callback. When the rule's
   condition expression raises, the callback is invoked with the
   exception. Default `None` preserves the pre-existing silent-as-
   reject behaviour so downstream callers that destructure the
   tuple but don't care about diagnostics keep working.

2. evaluate_interlock_rule still returns a 3-tuple (verdict, new_cmd,
   error). Downstream centrex tests destructure exactly three values:
       verdict, _new_cmd, err = evaluate_interlock_rule(...)
   so the signature change must remain additive.

3. WatchdogProcess submits action chains to a single-worker
   ThreadPoolExecutor instead of running them on the tick. The
   cooldown is now marked at action SUBMIT time via
   mark_watchdog_triggered (called by _evaluate_rules), not inside
   evaluate_watchdog_rule. A second tick must NOT re-submit while the
   first chain is still in flight, and a chain submission that fails
   (executor shut down) must clear the in-flight marker.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.interlock import (
    Rule,
    evaluate_interlock_rule,
)
from experiment_control.processes.watchdog import (
    CommandAction,
    RuleState,
    WatchdogEntry,
    WatchdogProcess,
    WatchdogRule,
    WatchdogRuleset,
    evaluate_watchdog_rule,
)
from experiment_control.rules.rules_common import TelemetryBinding


def _ok_sample(value: float = 1.0) -> dict[str, Any]:
    return {"value": value, "quality": "OK", "age_s": 0.1}


def _default_action() -> CommandAction:
    return CommandAction(
        device_id="dev1",
        action="set",
        params={},
        timeout_s=None,
        retries=0,
    )


def _raising_rule(condition: Any = None) -> WatchdogRule:
    """Build a watchdog rule whose condition definitely raises.

    `condition` is a dict-form op containing a templated divide-by-zero
    expression — `eval_condition` evaluates `>` between a stringified
    template (post-render) and 0, which raises TypeError.
    """
    return WatchdogRule(
        name="r_err",
        severity="warn",
        message=None,
        telemetry=[
            TelemetryBinding(
                alias="t", device_id="dev1", signal="temp", max_age_s=1.0
            )
        ],
        condition=condition or {"gt": ["{{ 1/0 }}", 0]},
        stable_for_s=0.0,
        cooldown_s=0.0,
        latch=False,
        on_unknown="ignore",
        actions=[_default_action()],
    )


class WatchdogConditionErrorCallbackTests(unittest.TestCase):
    def test_callback_fires_when_condition_raises(self) -> None:
        seen: list[BaseException] = []
        triggered, _alarm, _unknown, _snapshot = evaluate_watchdog_rule(
            rule=_raising_rule(),
            state=RuleState(),
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=10.0,
            on_condition_error=seen.append,
        )
        self.assertFalse(
            triggered,
            "a raised condition should still be treated as a non-alarm "
            "for backwards compatibility",
        )
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], BaseException)

    def test_callback_omitted_preserves_silent_behaviour(self) -> None:
        # No keyword passed -> must NOT raise even though the condition
        # is guaranteed to raise inside eval_condition. This is the
        # contract downstream tests rely on.
        triggered, _alarm, _unknown, _snapshot = evaluate_watchdog_rule(
            rule=_raising_rule(),
            state=RuleState(),
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=10.0,
        )
        self.assertFalse(triggered)

    def test_callback_exception_is_swallowed(self) -> None:
        # If the diagnostic callback itself raises, the rule eval must
        # continue cleanly (don't let observability break enforcement).
        def _bad_callback(_exc: BaseException) -> None:
            raise RuntimeError("callback exploded")

        triggered, *_ = evaluate_watchdog_rule(
            rule=_raising_rule(),
            state=RuleState(),
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=10.0,
            on_condition_error=_bad_callback,
        )
        self.assertFalse(triggered)


def _make_interlock_rule(condition: Any) -> Rule:
    return Rule(
        rule_id="r1",
        name="r1",
        device_id="dev1",
        action="set",
        telemetry=[],
        condition=condition,
        on_block_message="rejected",
        on_block_code="X",
        allow_transform_params=None,
    )


_RAISING_CONDITION = {"gt": ["{{ 1/0 }}", 0]}


class InterlockConditionErrorCallbackTests(unittest.TestCase):
    def test_callback_fires_when_condition_raises(self) -> None:
        rule = _make_interlock_rule(_RAISING_CONDITION)
        seen: list[BaseException] = []
        verdict, new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev1", "action": "set", "params": {}},
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=10.0,
            on_condition_error=seen.append,
        )
        self.assertEqual(verdict, "reject")
        self.assertIsNone(new_cmd)
        self.assertIsNotNone(err)
        self.assertEqual(len(seen), 1)

    def test_three_tuple_destructure_still_works_without_kwarg(self) -> None:
        # Centrex tests do: verdict, _new_cmd, err = evaluate_interlock_rule(...)
        # without on_condition_error= — this destructure must keep
        # working. If a future change accidentally adds a 4th element,
        # this test fails loudly.
        rule = _make_interlock_rule(True)
        verdict, new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev1", "action": "set", "params": {}},
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=10.0,
        )
        self.assertEqual(verdict, "allow")
        self.assertIsNone(new_cmd)
        self.assertIsNone(err)

    def test_callback_exception_is_swallowed(self) -> None:
        rule = _make_interlock_rule(_RAISING_CONDITION)

        def _bad_callback(_exc: BaseException) -> None:
            raise RuntimeError("callback exploded")

        verdict, _new_cmd, _err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev1", "action": "set", "params": {}},
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=10.0,
            on_condition_error=_bad_callback,
        )
        self.assertEqual(verdict, "reject")


class WatchdogActionSubmissionTests(unittest.TestCase):
    """Pin the two behaviours of the new tick-vs-action split:

    1. `_evaluate_rules` returns immediately after submitting the
       action chain to the executor (doesn't block the tick on a slow
       remediation).
    2. While a chain is still in flight, the same rule cannot be
       re-submitted on the next tick.
    """

    def _make_proc(self, rule_triggers: bool = True) -> WatchdogProcess:
        proc = object.__new__(WatchdogProcess)
        proc._process_id = "watchdog-test"
        proc._states = {}
        proc._inflight_action_keys = set()
        proc._inflight_lock = threading.Lock()
        proc._rule_error_last_mono = {}
        proc._rule_error_period_s = 30.0
        # Set up one ruleset with one rule.
        rule = WatchdogRule(
            name="r1",
            severity="warn",
            message=None,
            telemetry=[TelemetryBinding(alias="t", device_id="dev1", signal="temp", max_age_s=1.0)],
            condition=True if rule_triggers else False,
            stable_for_s=0.0,
            cooldown_s=2.0,
            latch=False,
            on_unknown="ignore",
            actions=[_default_action()],
        )
        ruleset = WatchdogRuleset(
            watchdog_id="wd1",
            rules=[rule],
        )
        proc._ruleset_order = [ruleset.watchdog_id]
        proc._watchdog_entries = {
            ruleset.watchdog_id: WatchdogEntry(ruleset=ruleset, enabled=True)
        }
        proc._states[(ruleset.watchdog_id, rule.name)] = RuleState()
        # Stub manager with a get_latest that returns OK telemetry.
        proc._manager = SimpleNamespace(
            get_latest=lambda _dev, _sig: _ok_sample(),
        )
        # Stub event publisher (so _publish_triggered / _publish_event
        # don't blow up).
        proc._manager_helper = SimpleNamespace(
            publish_event=lambda *_args, **_kwargs: None,
        )
        proc._publish_triggered = lambda **_kwargs: None  # type: ignore[method-assign]
        return proc

    def test_tick_does_not_block_on_slow_action(self) -> None:
        """If _execute_actions sleeps for several seconds, _evaluate_rules
        must still return promptly because the action runs on the worker."""
        from concurrent.futures import ThreadPoolExecutor

        proc = self._make_proc()
        proc._action_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="test-watchdog-actions"
        )
        try:
            action_started = threading.Event()
            action_finished = threading.Event()

            def _slow_actions(*, watchdog_id: str, rule: WatchdogRule) -> None:
                action_started.set()
                # Simulate a slow remediation. The tick MUST return
                # before this completes.
                time.sleep(0.5)
                action_finished.set()

            proc._execute_actions = _slow_actions  # type: ignore[method-assign]

            t_start = time.monotonic()
            proc._evaluate_rules()
            t_elapsed = time.monotonic() - t_start
            self.assertLess(
                t_elapsed,
                0.2,
                f"_evaluate_rules should return immediately after submitting "
                f"the action chain, but blocked for {t_elapsed:.3f}s",
            )
            # The worker should have started by now and will finish later.
            self.assertTrue(
                action_started.wait(timeout=2.0),
                "action chain never started on the worker thread",
            )
            self.assertTrue(
                action_finished.wait(timeout=2.0),
                "action chain never finished",
            )
        finally:
            proc._action_executor.shutdown(wait=True, cancel_futures=False)

    def test_inflight_marker_blocks_resubmission(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        proc = self._make_proc()
        proc._action_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="test-watchdog-actions-2"
        )
        try:
            release = threading.Event()
            call_count = {"n": 0}

            def _blocking_actions(
                *, watchdog_id: str, rule: WatchdogRule
            ) -> None:
                call_count["n"] += 1
                release.wait(timeout=5.0)

            proc._execute_actions = _blocking_actions  # type: ignore[method-assign]

            # First tick submits; second tick must NOT re-submit because
            # the first chain is still in flight.
            proc._evaluate_rules()
            proc._evaluate_rules()
            time.sleep(0.05)  # let the worker pick up the first task
            release.set()
            # Give the worker time to finish + the done callback to run.
            proc._action_executor.shutdown(wait=True, cancel_futures=False)
            self.assertEqual(
                call_count["n"],
                1,
                "second tick must NOT re-submit while the first action "
                "chain is still in flight on the worker",
            )
        finally:
            # In case shutdown above didn't fire (e.g. early failure).
            try:
                proc._action_executor.shutdown(
                    wait=False, cancel_futures=True
                )
            except Exception:
                pass

    def test_cooldown_is_marked_at_submit_time(self) -> None:
        """Verifies that mark_watchdog_triggered is called as part of
        the submit path, so the cooldown is engaged immediately and the
        rule doesn't re-trigger on every tick while the worker runs.
        """
        from concurrent.futures import ThreadPoolExecutor

        proc = self._make_proc()
        proc._action_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="test-watchdog-actions-3"
        )
        try:
            proc._execute_actions = lambda **_kw: None  # type: ignore[method-assign]
            proc._evaluate_rules()
            state = proc._states[("wd1", "r1")]
            self.assertIsNotNone(
                state.last_trigger_mono,
                "last_trigger_mono must be set at submit time so the "
                "cooldown gate is armed; was None which would re-trigger "
                "every tick",
            )
        finally:
            proc._action_executor.shutdown(wait=True, cancel_futures=False)


class WatchdogRuleErrorEventTests(unittest.TestCase):
    """The WatchdogProcess wires a per-rule condition-error callback
    that publishes manager.watchdog.rule_error events, rate-limited so
    a chronically-broken rule doesn't flood the bus."""

    def _make_proc_with_event_capture(self) -> tuple[WatchdogProcess, list[tuple[str, dict]]]:
        proc = object.__new__(WatchdogProcess)
        proc._process_id = "watchdog-test"
        proc._rule_error_last_mono = {}
        proc._rule_error_period_s = 1000.0  # generous so rate-limit is testable
        events: list[tuple[str, dict]] = []

        def _publish(topic: str, payload: dict[str, Any]) -> None:
            events.append((topic, dict(payload)))

        proc._publish_event = _publish  # type: ignore[method-assign]
        return proc, events

    def test_repeated_errors_logged_once_per_window(self) -> None:
        proc, events = self._make_proc_with_event_capture()
        cb = proc._make_condition_error_callback(
            watchdog_id="wd1", rule_name="r1"
        )
        for _ in range(5):
            cb(ValueError("boom"))
        topics = [topic for topic, _ in events]
        self.assertEqual(
            topics.count("manager.watchdog.rule_error"),
            1,
            f"rate-limit should collapse 5 identical errors into 1 event; "
            f"got {topics!r}",
        )
        # The payload should include enough to diagnose.
        _topic, payload = events[0]
        self.assertEqual(payload["watchdog_id"], "wd1")
        self.assertEqual(payload["rule"], "r1")
        self.assertIn("ValueError", payload["error"])
        self.assertIn("boom", payload["error"])

    def test_distinct_rules_logged_independently(self) -> None:
        proc, events = self._make_proc_with_event_capture()
        cb1 = proc._make_condition_error_callback(
            watchdog_id="wd1", rule_name="r1"
        )
        cb2 = proc._make_condition_error_callback(
            watchdog_id="wd1", rule_name="r2"
        )
        cb1(ValueError("a"))
        cb2(ValueError("b"))
        cb1(ValueError("a-again"))  # rate-limited
        self.assertEqual(
            len(events),
            2,
            "rate-limit must be per-rule, not global",
        )


if __name__ == "__main__":
    unittest.main()
