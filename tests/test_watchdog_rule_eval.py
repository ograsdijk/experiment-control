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
    WatchdogEntry,
    WatchdogProcess,
    WatchdogRule,
    _parse_ruleset,
    evaluate_watchdog_rule,
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
        self.assertIn("t", snapshot)
        self.assertFalse(bool(snapshot["t"].get("ok")))

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
        self.assertIn("telemetry", rule)
        self.assertIn("actions", rule)
        self.assertEqual(rule.get("condition"), {"gt": ["${sys_p.value}", 1e-5]})


if __name__ == "__main__":
    unittest.main()
