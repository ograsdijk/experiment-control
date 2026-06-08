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
    WatchdogEntry,
    WatchdogProcess,
    WatchdogRule,
    _parse_ruleset,
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


def _ok_sample(value: float = 1.0) -> dict[str, object]:
    return {"value": value, "quality": "OK", "age_s": 0.1}


class WatchdogRuleEvalTests(unittest.TestCase):
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
        self.assertIn("last_evaluated_mono", rule)
        self.assertIsNone(rule.get("arm"))
        self.assertTrue(rule.get("armed"))
        self.assertFalse(rule.get("alarm"))
        self.assertFalse(rule.get("unknown"))
        self.assertEqual(rule.get("last_evaluated_mono"), 12.0)
        self.assertEqual(rule.get("snapshot"), {"sys_p": {"value": 1e-6, "ok": True}})
        self.assertEqual(rule.get("condition"), {"gt": ["${sys_p.value}", 1e-5]})


if __name__ == "__main__":
    unittest.main()
