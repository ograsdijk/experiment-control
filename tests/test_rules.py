# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.interlock import Rule as InterlockRule
from experiment_control.processes.interlock import _parse_ruleset as parse_interlock_ruleset
from experiment_control.processes.interlock import evaluate_interlock_rule
from experiment_control.rules.rules_common import TelemetryBinding
from experiment_control.processes.watchdog import (
    RuleState,
    WatchdogRule,
    evaluate_watchdog_rule,
)


class InterlockRuleEvalTests(unittest.TestCase):
    def test_interlock_parse_assigns_rule_id(self) -> None:
        ruleset = parse_interlock_ruleset(
            {
                "version": 1,
                "interceptor_id": "demo",
                "rules": [
                    {
                        "name": "always_allow",
                        "match": {"device_id": "dev", "action": "act"},
                        "condition": {"always": True},
                    }
                ],
            },
            source="test",
        )
        self.assertEqual(ruleset.rules[0].rule_id, "r0")

    def test_interlock_allows_with_ok_telemetry(self) -> None:
        rule = InterlockRule(
            rule_id="r0",
            name="ok_rule",
            device_id="dev",
            action="act",
            telemetry=[TelemetryBinding(alias="x", device_id="d1", signal="s1", max_age_s=2.0)],
            condition={"gt": ["${x.value}", 1]},
            on_block_message=None,
            on_block_code=None,
            allow_transform_params=None,
        )

        def getter(device_id: str, signal: str) -> dict | None:
            return {
                "value": 2.0,
                "units": None,
                "quality": "OK",
                "t_mono": 1.0,
                "t_wall": 1.0,
                "age_s": 0.1,
            }

        verdict, new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev", "action": "act", "params": {}},
            telemetry_getter=getter,
            now_mono=1.1,
        )
        self.assertEqual(verdict, "allow")
        self.assertIsNone(new_cmd)
        self.assertIsNone(err)

    def test_interlock_rejects_missing(self) -> None:
        rule = InterlockRule(
            rule_id="r0",
            name="missing_rule",
            device_id="dev",
            action="act",
            telemetry=[TelemetryBinding(alias="x", device_id="d1", signal="s1", max_age_s=2.0)],
            condition={"gt": ["${x.value}", 1]},
            on_block_message=None,
            on_block_code=None,
            allow_transform_params=None,
        )

        def getter(device_id: str, signal: str) -> dict | None:
            return None

        verdict, new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev", "action": "act", "params": {}},
            telemetry_getter=getter,
            now_mono=1.1,
        )
        self.assertEqual(verdict, "reject")
        self.assertIsNone(new_cmd)
        self.assertIsNotNone(err)
        self.assertEqual(err.get("code"), "TELEMETRY_MISSING")


class WatchdogRuleEvalTests(unittest.TestCase):
    def test_watchdog_triggers_after_stable(self) -> None:
        rule = WatchdogRule(
            name="stable_rule",
            severity="warn",
            message=None,
            telemetry=[TelemetryBinding(alias="x", device_id="d1", signal="s1", max_age_s=2.0)],
            condition={"gt": ["${x.value}", 1]},
            stable_for_s=1.0,
            cooldown_s=0.0,
            latch=False,
            on_unknown="ignore",
            actions=[],
        )
        state = RuleState()

        def getter(device_id: str, signal: str) -> dict | None:
            return {"value": 2.0, "quality": "OK", "t_mono": 1.0, "age_s": 0.0}

        triggered, _alarm, _unknown, _snapshot = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=0.0
        )
        self.assertFalse(triggered)
        self.assertIsNotNone(state.stable_since_mono)

        triggered, _alarm, _unknown, _snapshot = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=1.1
        )
        self.assertTrue(triggered)

    def test_watchdog_unknown_trigger(self) -> None:
        rule = WatchdogRule(
            name="unknown_rule",
            severity="critical",
            message=None,
            telemetry=[TelemetryBinding(alias="x", device_id="d1", signal="s1", max_age_s=2.0)],
            condition={"gt": ["${x.value}", 1]},
            stable_for_s=0.0,
            cooldown_s=0.0,
            latch=False,
            on_unknown="trigger",
            actions=[],
        )
        state = RuleState()

        def getter(device_id: str, signal: str) -> dict | None:
            return None

        triggered, _alarm, unknown, _snapshot = evaluate_watchdog_rule(
            rule=rule, state=state, telemetry_getter=getter, now_mono=0.0
        )
        self.assertTrue(unknown)
        self.assertTrue(triggered)


if __name__ == "__main__":
    unittest.main()
