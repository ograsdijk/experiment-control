# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.watchdog import (
    CommandAction,
    RuleState,
    WatchdogArm,
    WatchdogConfirmation,
    WatchdogEntry,
    WatchdogProcess,
    WatchdogRule,
    _parse_ruleset,
    _resolve_watchdog_bindings,
    evaluate_watchdog_rule,
    mark_watchdog_triggered,
)
from experiment_control.rules.rules_common import TelemetryBinding


def _default_action() -> CommandAction:
    return CommandAction(
        device_id="dev1",
        action="set",
        params={},
        timeout_s=None,
        retries=0,
    )


def _ok_sample(value: float = 1.0, t_mono_recv: float | None = None) -> dict[str, object]:
    sample: dict[str, object] = {"value": value, "quality": "OK", "age_s": 0.1}
    if t_mono_recv is not None:
        sample["t_mono_recv"] = t_mono_recv
    return sample


def _confirmation_rule(
    *,
    telemetry: list[TelemetryBinding],
    condition: object,
    confirmation: WatchdogConfirmation,
    stable_for_s: float = 0.0,
) -> WatchdogRule:
    return WatchdogRule(
        name="confirmed",
        severity="critical",
        message=None,
        telemetry=telemetry,
        condition=condition,
        stable_for_s=stable_for_s,
        cooldown_s=0.0,
        latch=False,
        on_unknown="ignore",
        actions=[_default_action()],
        confirmation=confirmation,
    )


class WatchdogRuleEvalTests(unittest.TestCase):
    def test_optional_binding_exposes_ok_without_changing_required_unknown(self) -> None:
        optional_rule = _confirmation_rule(
            telemetry=[
                TelemetryBinding(
                    alias="p",
                    device_id="hornet",
                    signal="pressure",
                    max_age_s=1.0,
                    required=False,
                )
            ],
            condition=True,
            confirmation=WatchdogConfirmation(("p",), 3, {}),
        )
        required_rule = _confirmation_rule(
            telemetry=[
                TelemetryBinding(
                    alias="p",
                    device_id="hornet",
                    signal="pressure",
                    max_age_s=1.0,
                )
            ],
            condition=True,
            confirmation=WatchdogConfirmation(("p",), 3, {}),
        )

        unavailable_samples = (
            None,
            {"value": 2.0, "quality": "BAD", "age_s": 0.1},
            {"value": 2.0, "quality": "OK", "age_s": 2.0},
        )
        for sample in unavailable_samples:
            with self.subTest(sample=sample):
                def getter(_dev: str, _sig: str) -> dict[str, object] | None:
                    return sample

                optional_env, _snapshot, optional_unknown = _resolve_watchdog_bindings(
                    optional_rule,
                    telemetry_getter=getter,
                    now_mono=1.0,
                )
                required_env, _snapshot, required_unknown = _resolve_watchdog_bindings(
                    required_rule,
                    telemetry_getter=getter,
                    now_mono=1.0,
                )

                self.assertFalse(optional_unknown)
                self.assertFalse(optional_env["p"].ok)
                self.assertIsNone(optional_env["p"].value)
                self.assertTrue(required_unknown)
                self.assertNotIn("p", required_env)

        valid_env, _snapshot, valid_unknown = _resolve_watchdog_bindings(
            optional_rule,
            telemetry_getter=lambda _dev, _sig: _ok_sample(2.0, 3.0),
            now_mono=1.0,
        )
        self.assertFalse(valid_unknown)
        self.assertTrue(valid_env["p"].ok)
        self.assertEqual(valid_env["p"].value, 2.0)

    def test_confirmation_counts_only_distinct_manager_samples(self) -> None:
        rule = _parse_ruleset(
            {"watchdog_id": "wd", "rules": [{"name": "r", "inputs": {"telemetry": [{"as": "p", "device": "h", "signal": "p"}]}, "condition": {"gt": ["${p.value}", 1.0]}, "confirmation": {"sample_alias": "p", "consecutive_samples": 3}, "actions": [{"command": {"device_id": "d", "action": "stop"}}]}]}, source="test"
        ).rules[0]
        state = RuleState()
        sample = _ok_sample(2.0, 1.0)
        def getter(_dev: str, _sig: str) -> dict[str, object]:
            return sample
        self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=1.0)[0])
        self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=1.1)[0])
        for marker in (2.0, 3.0):
            sample = _ok_sample(2.0, marker)
            triggered, *_ = evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=marker)
        self.assertTrue(triggered, state.confirmation)
        self.assertEqual(state.confirmation["p"]["count"], 3)

    def test_multi_source_confirmation_does_not_combine_or_require_sources(self) -> None:
        rule = _parse_ruleset(
            {"watchdog_id": "wd", "rules": [{"name": "r", "inputs": {"telemetry": [{"as": "rc", "device": "rc", "signal": "p", "required": False}, {"as": "eql", "device": "eql", "signal": "p", "required": False}]}, "condition": True, "confirmation": {"consecutive_samples": 3, "any": [{"sample_alias": "rc", "condition": {"gt": ["${rc.value}", 1.0]}}, {"sample_alias": "eql", "condition": {"gt": ["${eql.value}", 1.0]}}]}, "actions": [{"command": {"device_id": "d", "action": "stop"}}]}]}, source="test"
        ).rules[0]
        state = RuleState()
        values = {("rc", "p"): _ok_sample(2.0, 1.0), ("eql", "p"): None}
        def getter(dev: str, sig: str) -> dict[str, object] | None:
            return values[(dev, sig)]
        for marker in (1.0, 2.0, 3.0):
            values[("rc", "p")] = _ok_sample(2.0, marker)
            triggered, *_ = evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=marker)
        self.assertTrue(triggered, repr(state.confirmation))
        self.assertEqual(state.confirmation["rc"]["count"], 3)
        self.assertEqual(state.confirmation["eql"]["count"], 0)

    def test_confirmation_low_bad_stale_and_missing_reset_streak(self) -> None:
        binding = TelemetryBinding(alias="p", device_id="h", signal="p", max_age_s=1.0)
        rule = _confirmation_rule(
            telemetry=[binding],
            condition={"gt": ["${p.value}", 1.0]},
            confirmation=WatchdogConfirmation(("p",), 3, {}),
        )
        state = RuleState()
        sample: dict[str, object] | None = _ok_sample(2.0, 1.0)

        def getter(_dev: str, _sig: str) -> dict[str, object] | None:
            return sample

        for marker in (1.0, 2.0):
            sample = _ok_sample(2.0, marker)
            self.assertFalse(
                evaluate_watchdog_rule(
                    rule=rule, state=state, telemetry_getter=getter, now_mono=marker
                )[0]
            )
        sample = _ok_sample(0.5, 3.0)
        self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=3.0)[0])
        self.assertEqual(state.confirmation["p"]["count"], 0)
        for bad_sample in (
            {"value": 2.0, "quality": "BAD", "age_s": 0.1, "t_mono_recv": 4.0},
            {"value": 2.0, "quality": "OK", "age_s": 2.0, "t_mono_recv": 5.0},
            None,
        ):
            sample = bad_sample
            self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=6.0)[0])
            self.assertEqual(state.confirmation["p"]["count"], 0)

    def test_any_confirmation_does_not_combine_alternating_highs(self) -> None:
        telemetry = [
            TelemetryBinding(alias="a", device_id="a", signal="p", max_age_s=1.0, required=False),
            TelemetryBinding(alias="b", device_id="b", signal="p", max_age_s=1.0, required=False),
        ]
        rule = _confirmation_rule(
            telemetry=telemetry,
            condition=True,
            confirmation=WatchdogConfirmation(
                ("a", "b"), 3,
                {"a": {"gt": ["${a.value}", 1.0]}, "b": {"gt": ["${b.value}", 1.0]}},
            ),
        )
        values = {("a", "p"): _ok_sample(0.5, 1.0), ("b", "p"): _ok_sample(0.5, 1.0)}

        def getter(device: str, signal: str) -> dict[str, object]:
            return values[(device, signal)]

        state = RuleState()
        for marker, alias in enumerate(("a", "b", "a", "b", "a"), start=1):
            values[(alias, "p")] = _ok_sample(2.0, float(marker))
            other = "b" if alias == "a" else "a"
            values[(other, "p")] = _ok_sample(0.5, float(marker))
            self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=float(marker))[0])

    def test_required_prerequisite_blocks_ready_confirmation(self) -> None:
        telemetry = [
            TelemetryBinding(alias="p", device_id="h", signal="p", max_age_s=1.0),
            TelemetryBinding(alias="on", device_id="t", signal="on", max_age_s=1.0),
        ]
        rule = _confirmation_rule(
            telemetry=telemetry,
            condition={"eq": ["${on.value}", True]},
            confirmation=WatchdogConfirmation(("p",), 3, {}),
        )
        values: dict[tuple[str, str], dict[str, object] | None] = {
            ("h", "p"): _ok_sample(2.0, 1.0), ("t", "on"): _ok_sample(False)
        }

        def getter(device: str, signal: str) -> dict[str, object] | None:
            return values[(device, signal)]

        state = RuleState()
        for marker in (1.0, 2.0, 3.0):
            values[("h", "p")] = _ok_sample(2.0, marker)
            self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=marker)[0])
        values[("t", "on")] = None
        values[("h", "p")] = _ok_sample(2.0, 4.0)
        self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=4.0)[0])
        self.assertEqual(state.confirmation["p"]["count"], 0)

    def test_confirmation_composes_with_stable_for(self) -> None:
        rule = _confirmation_rule(
            telemetry=[TelemetryBinding(alias="p", device_id="h", signal="p", max_age_s=1.0)],
            condition={"gt": ["${p.value}", 1.0]},
            confirmation=WatchdogConfirmation(("p",), 3, {}),
            stable_for_s=2.0,
        )
        sample = _ok_sample(2.0, 1.0)

        def getter(_device: str, _signal: str) -> dict[str, object]:
            return sample

        state = RuleState()
        for now, marker in ((0.0, 1.0), (0.5, 2.0), (1.0, 3.0)):
            sample = _ok_sample(2.0, marker)
            self.assertFalse(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=now)[0])
        self.assertTrue(evaluate_watchdog_rule(rule=rule, state=state, telemetry_getter=getter, now_mono=2.1)[0])

    def test_confirmation_parser_rejects_bad_count_and_unknown_alias(self) -> None:
        base = {"name": "r", "inputs": {"telemetry": [{"as": "p", "device": "d", "signal": "s"}]}, "condition": True, "actions": [{"command": {"device_id": "d", "action": "stop"}}]}
        for confirmation in ({"sample_alias": "p", "consecutive_samples": 0}, {"sample_alias": "missing", "consecutive_samples": 3}):
            raw = {"watchdog_id": "wd", "rules": [{**base, "confirmation": confirmation}]}
            with self.assertRaises(ValueError):
                _parse_ruleset(raw, source="test")

    def test_parse_ruleset_includes_source_on_error(self) -> None:
        raw = {
            "watchdog_id": "wd1",
            "rules": [
                {
                    "name": "r1",
                    "inputs": {"telemetry": []},
                    "condition": True,
                    "actions": [],
                }
            ],
        }
        with self.assertRaises(ValueError) as ctx:
            _parse_ruleset(raw, source="inline-watchdog")
        self.assertIn("inline-watchdog", str(ctx.exception))
        self.assertIn("telemetry", str(ctx.exception))

    def test_unknown_trigger_mode_triggers_when_telemetry_missing(self) -> None:
        rule = WatchdogRule(
            name="r1",
            severity="warn",
            message=None,
            telemetry=[TelemetryBinding(alias="t", device_id="dev1", signal="temp", max_age_s=1.0)],
            condition=True,
            stable_for_s=0.0,
            cooldown_s=0.0,
            latch=False,
            on_unknown="trigger",
            actions=[_default_action()],
        )
        state = RuleState()
        triggered, alarm, unknown, snapshot = evaluate_watchdog_rule(
            rule=rule,
            state=state,
            telemetry_getter=lambda _dev, _sig: None,
            now_mono=5.0,
        )
        self.assertTrue(triggered)
        self.assertTrue(alarm)
        self.assertTrue(unknown)
        self.assertEqual(state.last_evaluated_mono, 5.0)
        self.assertTrue(state.alarm)
        self.assertTrue(state.unknown)
        self.assertIsInstance(state.snapshot, dict)
        self.assertIn("t", snapshot)
        self.assertFalse(bool(snapshot["t"].get("ok")))
        self.assertEqual(
            state.condition_evaluation,
            {"kind": "value", "result": None, "resolved": True, "unknown": True},
        )

    def test_unknown_trigger_bypasses_valid_sample_confirmation(self) -> None:
        rule = WatchdogRule(
            name="confirmed_fail_safe",
            severity="critical",
            message=None,
            telemetry=[
                TelemetryBinding(
                    alias="t",
                    device_id="dev1",
                    signal="temp",
                    max_age_s=1.0,
                )
            ],
            condition={"gt": ["${t.value}", 10.0]},
            stable_for_s=0.0,
            cooldown_s=0.0,
            latch=False,
            on_unknown="trigger",
            actions=[_default_action()],
            confirmation=WatchdogConfirmation(("t",), 3, {}),
        )

        triggered, alarm, unknown, _snapshot = evaluate_watchdog_rule(
            rule=rule,
            state=RuleState(),
            telemetry_getter=lambda _dev, _sig: None,
            now_mono=5.0,
        )

        self.assertTrue(triggered)
        self.assertTrue(alarm)
        self.assertTrue(unknown)

    def test_clear_alarm_state_is_recorded_after_evaluation(self) -> None:
        rule = WatchdogRule(
            name="r_clear",
            severity="warn",
            message=None,
            telemetry=[TelemetryBinding(alias="t", device_id="dev1", signal="temp", max_age_s=1.0)],
            condition={"gt": ["${t.value}", 10.0]},
            stable_for_s=0.0,
            cooldown_s=0.0,
            latch=False,
            on_unknown="ignore",
            actions=[_default_action()],
        )
        state = RuleState()
        triggered, alarm, unknown, snapshot = evaluate_watchdog_rule(
            rule=rule,
            state=state,
            telemetry_getter=lambda _dev, _sig: _ok_sample(1.0),
            now_mono=6.0,
        )
        self.assertFalse(triggered)
        self.assertFalse(alarm)
        self.assertFalse(unknown)
        self.assertEqual(state.last_evaluated_mono, 6.0)
        self.assertFalse(state.alarm)
        self.assertFalse(state.unknown)
        self.assertEqual(state.snapshot, snapshot)
        self.assertEqual(
            state.condition_evaluation,
            {
                "kind": "comparison",
                "operator": "gt",
                "result": False,
                "left": 1.0,
                "right": 10.0,
            },
        )

    def test_cooldown_suppresses_repeated_triggers(self) -> None:
        rule = WatchdogRule(
            name="r2",
            severity="warn",
            message=None,
            telemetry=[TelemetryBinding(alias="t", device_id="dev1", signal="temp", max_age_s=1.0)],
            condition=True,
            stable_for_s=0.0,
            cooldown_s=2.0,
            latch=False,
            on_unknown="ignore",
            actions=[_default_action()],
        )
        state = RuleState()
        first = evaluate_watchdog_rule(
            rule=rule,
            state=state,
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=10.0,
        )
        # Caller is now responsible for marking the cooldown after it
        # commits to the action chain — previously this was implicit
        # inside evaluate_watchdog_rule, which incurred the cooldown
        # even for callers that never executed the actions and for
        # action chains that were never given a chance to run.
        # WatchdogProcess._evaluate_rules calls mark_watchdog_triggered
        # at action-submit time; this test mirrors that contract.
        mark_watchdog_triggered(state, 10.0)
        second = evaluate_watchdog_rule(
            rule=rule,
            state=state,
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=11.0,
        )
        third = evaluate_watchdog_rule(
            rule=rule,
            state=state,
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=13.1,
        )
        self.assertTrue(first[0])
        self.assertFalse(second[0])
        self.assertTrue(third[0])

    def test_evaluate_does_not_mark_cooldown_by_itself(self) -> None:
        # Regression test for the cooldown-gating split: a caller that
        # never invokes mark_watchdog_triggered must continue to see
        # triggered=True every tick (instead of being silently locked
        # out for cooldown_s as in the pre-fix behaviour).
        rule = WatchdogRule(
            name="r2_cd_split",
            severity="warn",
            message=None,
            telemetry=[TelemetryBinding(alias="t", device_id="dev1", signal="temp", max_age_s=1.0)],
            condition=True,
            stable_for_s=0.0,
            cooldown_s=2.0,
            latch=False,
            on_unknown="ignore",
            actions=[_default_action()],
        )
        state = RuleState()
        for now in (10.0, 11.0, 12.0):
            triggered, *_ = evaluate_watchdog_rule(
                rule=rule,
                state=state,
                telemetry_getter=lambda _dev, _sig: _ok_sample(),
                now_mono=now,
            )
            self.assertTrue(triggered, f"tick {now}: expected triggered=True")
        # The state's last_trigger_mono must remain unset — the function
        # didn't write it, only mark_watchdog_triggered does.
        self.assertIsNone(state.last_trigger_mono)

    def test_latch_prevents_retrigger_until_cleared(self) -> None:
        rule = WatchdogRule(
            name="r3",
            severity="warn",
            message=None,
            telemetry=[TelemetryBinding(alias="t", device_id="dev1", signal="temp", max_age_s=1.0)],
            condition=True,
            stable_for_s=0.0,
            cooldown_s=0.0,
            latch=True,
            on_unknown="ignore",
            actions=[_default_action()],
        )
        state = RuleState()
        first = evaluate_watchdog_rule(
            rule=rule,
            state=state,
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=20.0,
        )
        second = evaluate_watchdog_rule(
            rule=rule,
            state=state,
            telemetry_getter=lambda _dev, _sig: _ok_sample(),
            now_mono=21.0,
        )
        self.assertTrue(first[0])
        self.assertFalse(second[0])
        self.assertTrue(state.latched)

    def test_armed_rule_suppresses_startup_and_rearms_after_safe_pressure(self) -> None:
        rule = WatchdogRule(
            name="turbo_pressure_guard",
            severity="critical",
            message=None,
            telemetry=[
                TelemetryBinding(
                    alias="p", device_id="hornet", signal="pressure", max_age_s=1.0
                ),
                TelemetryBinding(
                    alias="pump_on", device_id="turbo", signal="pumpg_statn", max_age_s=1.0
                ),
            ],
            condition={
                "and": [{"eq": ["${pump_on.value}", True]}, {"gt": ["${p.value}", 1.0e-2]}]
            },
            stable_for_s=0.0,
            cooldown_s=0.0,
            latch=False,
            on_unknown="ignore",
            actions=[_default_action()],
            arm=WatchdogArm(
                condition={
                    "and": [
                        {"eq": ["${pump_on.value}", True]},
                        {"lt": ["${p.value}", 5.0e-3]},
                    ]
                },
                disarm_condition={"eq": ["${pump_on.value}", False]},
                disarm_on_trigger=True,
            ),
        )
        state = RuleState()
        values = {
            ("hornet", "pressure"): _ok_sample(2.0e-2),
            ("turbo", "pumpg_statn"): _ok_sample(True),
        }

        def getter(device_id: str, signal: str) -> dict[str, object] | None:
            return values.get((device_id, signal))

        startup_high = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=1.0
        )
        self.assertFalse(startup_high[0])
        self.assertFalse(state.armed)
        self.assertTrue(startup_high[1])

        values[("hornet", "pressure")] = _ok_sample(4.0e-3)
        safe = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=2.0
        )
        self.assertFalse(safe[0])
        self.assertTrue(state.armed)
        self.assertFalse(safe[1])

        values[("hornet", "pressure")] = _ok_sample(2.0e-2)
        high_after_arm = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=3.0
        )
        self.assertTrue(high_after_arm[0])
        self.assertFalse(state.armed)

        high_still_unarmed = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=4.0
        )
        self.assertFalse(high_still_unarmed[0])
        self.assertFalse(state.armed)

        values[("hornet", "pressure")] = _ok_sample(4.0e-3)
        rearmed = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=5.0
        )
        self.assertFalse(rearmed[0])
        self.assertTrue(state.armed)

    def test_parse_ruleset_includes_arm_configuration(self) -> None:
        ruleset = _parse_ruleset(
            {
                "watchdog_id": "wd_arm",
                "rules": [
                    {
                        "name": "armed_guard",
                        "severity": "critical",
                        "inputs": {
                            "telemetry": [{"as": "p", "device": "d", "signal": "s"}]
                        },
                        "arm": {
                            "condition": {"lt": ["${p.value}", 1.0]},
                            "disarm_condition": {"gt": ["${p.value}", 2.0]},
                            "disarm_on_trigger": True,
                        },
                        "condition": {"gt": ["${p.value}", 2.0]},
                        "actions": [
                            {"command": {"device_id": "d", "action": "stop", "params": {}}}
                        ],
                    }
                ],
            },
            source="test",
        )
        arm = ruleset.rules[0].arm
        self.assertIsNotNone(arm)
        assert arm is not None
        self.assertTrue(arm.disarm_on_trigger)
        self.assertEqual(arm.condition, {"lt": ["${p.value}", 1.0]})
        self.assertEqual(arm.disarm_condition, {"gt": ["${p.value}", 2.0]})

    def test_watchdog_status_includes_condition_telemetry_actions(self) -> None:
        ruleset = _parse_ruleset(
            {
                "watchdog_id": "wd_status",
                "rules": [
                    {
                        "name": "pressure_guard",
                        "severity": "warn",
                        "inputs": {
                            "telemetry": [
                                {
                                    "as": "sys_p",
                                    "device": "hornet_rc",
                                    "signal": "system_pressure_torr",
                                    "max_age_s": 2.0,
                                }
                            ]
                        },
                        "condition": {"gt": ["${sys_p.value}", 1e-5]},
                        "actions": [
                            {
                                "command": {
                                    "device_id": "ps_cell",
                                    "action": "set_output_enabled",
                                    "params": {"enabled": False},
                                    "timeout_s": 1.5,
                                    "retries": 2,
                                }
                            }
                        ],
                    }
                ],
            },
            source="test",
        )
        proc = object.__new__(WatchdogProcess)
        proc._ruleset_order = [ruleset.watchdog_id]
        proc._watchdog_entries = {
            ruleset.watchdog_id: WatchdogEntry(ruleset=ruleset, enabled=True)
        }
        proc._states = {
            (ruleset.watchdog_id, "pressure_guard"): RuleState(
                stable_since_mono=10.0,
                last_trigger_mono=11.0,
                latched=True,
                armed=True,
                last_evaluated_mono=12.0,
                alarm=False,
                unknown=False,
                snapshot={"sys_p": {"value": 1e-6, "ok": True}},
                condition_evaluation={
                    "kind": "comparison",
                    "operator": "gt",
                    "result": False,
                    "left": 1e-6,
                    "right": 1e-5,
                },
                active_trip_id="trip-status",
            )
        }

        resp = proc._rpc_watchdog_status({"request_id": "req-1"})
        self.assertTrue(resp.get("ok"))
        result = resp.get("result")
        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        watchdogs = result.get("watchdogs")
        self.assertIsInstance(watchdogs, list)
        assert isinstance(watchdogs, list)
        self.assertEqual(len(watchdogs), 1)
        rules = watchdogs[0].get("rules")
        self.assertIsInstance(rules, list)
        assert isinstance(rules, list)
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertIn("condition", rule)
        self.assertIn("arm", rule)
        self.assertIn("telemetry", rule)
        self.assertIn("actions", rule)
        self.assertIn("armed", rule)
        self.assertIn("alarm", rule)
        self.assertIn("unknown", rule)
        self.assertIn("snapshot", rule)
        self.assertIn("condition_evaluation", rule)
        self.assertIn("last_evaluated_mono", rule)
        self.assertIn("last_evaluated_age_s", rule)
        self.assertIn("stable_since_age_s", rule)
        self.assertIn("last_trigger_age_s", rule)
        self.assertIn("active_trip_id", rule)
        self.assertIsNone(rule.get("arm"))
        self.assertTrue(rule.get("armed"))
        self.assertFalse(rule.get("alarm"))
        self.assertFalse(rule.get("unknown"))
        self.assertEqual(rule.get("last_evaluated_mono"), 12.0)
        self.assertEqual(rule.get("active_trip_id"), "trip-status")
        self.assertEqual(rule.get("snapshot"), {"sys_p": {"value": 1e-6, "ok": True}})
        self.assertEqual(
            rule.get("condition_evaluation"),
            {
                "kind": "comparison",
                "operator": "gt",
                "result": False,
                "left": 1e-6,
                "right": 1e-5,
            },
        )
        self.assertEqual(rule.get("condition"), {"gt": ["${sys_p.value}", 1e-5]})


if __name__ == "__main__":
    unittest.main()
