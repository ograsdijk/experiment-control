# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.interlock import (
    InterlockProcess,
    Rule,
    _parse_ruleset,
    evaluate_interlock_rule,
)
from experiment_control.rules.rules_common import TelemetryBinding


class InterlockRuleEvalTests(unittest.TestCase):
    def test_parse_ruleset_includes_source_on_error(self) -> None:
        raw = {
            "interceptor_id": "i1",
            "rules": [
                {
                    "name": "r1",
                    "match": {"device_id": "dev1", "action": "set"},
                    "condition": True,
                    "allow_transform": {"device_id": "rewrites_not_allowed"},
                }
            ],
        }
        with self.assertRaises(ValueError) as ctx:
            _parse_ruleset(raw, source="inline-rules")
        self.assertIn("inline-rules", str(ctx.exception))
        self.assertIn("allow_transform", str(ctx.exception))

    def test_evaluate_rejects_missing_telemetry(self) -> None:
        rule = Rule(
            rule_id="r1",
            name="rule-1",
            device_id="dev1",
            action="set",
            telemetry=[
                TelemetryBinding(alias="t", device_id="dev1", signal="temp", max_age_s=1.0)
            ],
            condition=True,
            on_block_message=None,
            on_block_code=None,
            allow_transform_params=None,
        )
        verdict, new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev1", "action": "set", "params": {}},
            telemetry_getter=lambda _dev, _sig: None,
            now_mono=10.0,
        )
        self.assertEqual(verdict, "reject")
        self.assertIsNone(new_cmd)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "TELEMETRY_MISSING")

    def test_evaluate_can_transform_params(self) -> None:
        rule = Rule(
            rule_id="r2",
            name="rule-2",
            device_id="dev1",
            action="set",
            telemetry=[],
            condition=True,
            on_block_message=None,
            on_block_code=None,
            allow_transform_params={"gain": "${params.gain + 1}"},
        )
        verdict, new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev1", "action": "set", "params": {"gain": 2}},
            telemetry_getter=lambda _dev, _sig: None,
            now_mono=20.0,
        )
        self.assertEqual(verdict, "transform")
        self.assertIsNone(err)
        self.assertEqual(
            new_cmd,
            {"device_id": "dev1", "action": "set", "params": {"gain": 3}},
        )

    def test_evaluate_uses_custom_block_error(self) -> None:
        rule = Rule(
            rule_id="r3",
            name="rule-3",
            device_id="dev1",
            action="set",
            telemetry=[],
            condition=False,
            on_block_message="custom message",
            on_block_code="CUSTOM_CODE",
            allow_transform_params=None,
        )
        verdict, _new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev1", "action": "set", "params": {}},
            telemetry_getter=lambda _dev, _sig: None,
            now_mono=30.0,
        )
        self.assertEqual(verdict, "reject")
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "CUSTOM_CODE")
        self.assertEqual(err.get("message"), "custom message")

    def test_rule_status_payload_includes_condition(self) -> None:
        proc = object.__new__(InterlockProcess)
        proc._rule_enabled = {}
        condition = {
            "and": [
                {"ge": ["${params.value}", 1.0]},
                {"le": ["${params.value}", 3.0]},
            ]
        }
        rule = Rule(
            rule_id="r4",
            name="rule-4",
            device_id="dev1",
            action="set",
            telemetry=[],
            condition=condition,
            on_block_message=None,
            on_block_code=None,
            allow_transform_params=None,
        )
        payload = proc._rule_status_payload("i1", rule)
        self.assertIn("condition", payload)
        self.assertEqual(payload.get("condition"), condition)


if __name__ == "__main__":
    unittest.main()
