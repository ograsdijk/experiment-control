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
from experiment_control.rules.rules_common import (
    TelemetryBinding,
    parse_telemetry_bindings,
)
from experiment_control.utils.config_parsing import ConfigError


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

    def test_optional_telemetry_allows_missing_and_sets_alias(self) -> None:
        rule = Rule(
            rule_id="r1b",
            name="rule-optional",
            device_id="dev1",
            action="set",
            telemetry=[
                TelemetryBinding(
                    alias="hornet",
                    device_id="hornet_eql",
                    signal="ig_on",
                    max_age_s=1.0,
                    required=False,
                )
            ],
            condition={"or": [{"not": "${hornet.ok}"}, {"eq": ["${hornet.value}", False]}]},
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
        self.assertEqual(verdict, "allow")
        self.assertIsNone(new_cmd)
        self.assertIsNone(err)

    def test_optional_telemetry_can_still_block_when_condition_fails(self) -> None:
        rule = Rule(
            rule_id="r1c",
            name="rule-optional-block",
            device_id="dev1",
            action="set",
            telemetry=[
                TelemetryBinding(
                    alias="hornet",
                    device_id="hornet_eql",
                    signal="ig_on",
                    max_age_s=1.0,
                    required=False,
                )
            ],
            condition={"or": [{"not": "${hornet.ok}"}, {"eq": ["${hornet.value}", False]}]},
            on_block_message="ion gauge must be off",
            on_block_code="HORNET_IG_ON",
            allow_transform_params=None,
        )
        verdict, new_cmd, err = evaluate_interlock_rule(
            rule=rule,
            cmd={"device_id": "dev1", "action": "set", "params": {}},
            telemetry_getter=lambda _dev, _sig: {
                "value": True,
                "quality": "OK",
                "age_s": 0.0,
            },
            now_mono=10.0,
        )
        self.assertEqual(verdict, "reject")
        self.assertIsNone(new_cmd)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("message"), "ion gauge must be off")

    def test_parse_optional_telemetry_binding_required_false(self) -> None:
        ruleset = _parse_ruleset(
            {
                "interceptor_id": "i1",
                "rules": [
                    {
                        "name": "optional-hornet",
                        "match": {"device_id": "dev1", "action": "set"},
                        "inputs": {
                            "telemetry": [
                                {
                                    "as": "hornet",
                                    "device": "hornet_eql",
                                    "signal": "ig_on",
                                    "required": False,
                                }
                            ]
                        },
                        "condition": {"always": True},
                    }
                ],
            },
            source="inline-rules",
        )
        self.assertFalse(ruleset.rules[0].telemetry[0].required)

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

    def test_rule_status_payload_includes_condition_and_required_flag(self) -> None:
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
            telemetry=[
                TelemetryBinding(
                    alias="hornet",
                    device_id="hornet_eql",
                    signal="ig_on",
                    max_age_s=1.0,
                    required=False,
                )
            ],
            condition=condition,
            on_block_message=None,
            on_block_code=None,
            allow_transform_params=None,
        )
        payload = proc._rule_status_payload("i1", rule)
        self.assertIn("condition", payload)
        self.assertEqual(payload.get("condition"), condition)
        telemetry = payload.get("telemetry")
        self.assertIsInstance(telemetry, list)
        assert isinstance(telemetry, list)
        self.assertEqual(telemetry[0].get("required"), False)


class InterlockProcessTelemetryBindingTests(unittest.TestCase):
    """Process-telemetry bindings (e.g. hdf_writer.writing_active) gating
    a device reconfig RPC — the PXIe reconfig interlock pattern."""

    def _writing_rule(self) -> Rule:
        # Fail-open: allow when the writer telemetry is absent/stale (writer
        # stopped), block only on a fresh writing_active == true.
        return Rule(
            rule_id="w1",
            name="block-reconfig-while-writing",
            device_id="pxie5171",
            action="set_channel_range",
            telemetry=[
                TelemetryBinding(
                    alias="writing",
                    device_id="",
                    signal="writing_active",
                    max_age_s=5.0,
                    required=False,
                    process_id="hdf_writer",
                )
            ],
            condition={
                "or": [
                    {"not": "${writing.ok}"},
                    {"eq": ["${writing.value}", False]},
                ]
            },
            on_block_message="HDF writer is recording",
            on_block_code="HDF_WRITING_ACTIVE",
            allow_transform_params=None,
        )

    def _eval(self, *, process_sample):
        return evaluate_interlock_rule(
            rule=self._writing_rule(),
            cmd={"device_id": "pxie5171", "action": "set_channel_range", "params": {}},
            telemetry_getter=lambda _dev, _sig: None,
            process_telemetry_getter=lambda _proc, _sig: process_sample,
            now_mono=10.0,
        )

    def test_blocks_when_writing_active_true(self) -> None:
        verdict, _new_cmd, err = self._eval(
            process_sample={"value": True, "quality": "OK", "age_s": 0.0}
        )
        self.assertEqual(verdict, "reject")
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "HDF_WRITING_ACTIVE")

    def test_allows_when_writing_active_false(self) -> None:
        verdict, _new_cmd, err = self._eval(
            process_sample={"value": False, "quality": "OK", "age_s": 0.0}
        )
        self.assertEqual(verdict, "allow")
        self.assertIsNone(err)

    def test_allows_when_process_telemetry_missing(self) -> None:
        # Writer stopped -> no telemetry -> fail-open allow.
        verdict, _new_cmd, err = self._eval(process_sample=None)
        self.assertEqual(verdict, "allow")
        self.assertIsNone(err)

    def test_quality_check_is_case_insensitive(self) -> None:
        # publish_telemetry defaults quality to lowercase "ok"; a fresh
        # writing_active==true must still BLOCK (binding counted healthy).
        verdict, _new_cmd, err = self._eval(
            process_sample={"value": True, "quality": "ok", "age_s": 0.0}
        )
        self.assertEqual(verdict, "reject")
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "HDF_WRITING_ACTIVE")

    def test_no_process_getter_treats_process_binding_as_missing(self) -> None:
        # Default (no process_telemetry_getter) -> sample None -> fail-open.
        verdict, _new_cmd, err = evaluate_interlock_rule(
            rule=self._writing_rule(),
            cmd={"device_id": "pxie5171", "action": "set_channel_range", "params": {}},
            telemetry_getter=lambda _dev, _sig: None,
            now_mono=10.0,
        )
        self.assertEqual(verdict, "allow")
        self.assertIsNone(err)

    def test_interlock_parses_process_binding(self) -> None:
        # _parse_ruleset (interlock) passes allow_process=True.
        ruleset = _parse_ruleset(
            {
                "interceptor_id": "i1",
                "rules": [
                    {
                        "name": "block-reconfig",
                        "match": {"device_id": "pxie5171", "action": "set_trigger"},
                        "inputs": {
                            "telemetry": [
                                {
                                    "as": "writing",
                                    "process": "hdf_writer",
                                    "signal": "writing_active",
                                    "required": False,
                                }
                            ]
                        },
                        "condition": {"not": "${writing.ok}"},
                    }
                ],
            },
            source="inline-rules",
        )
        binding = ruleset.rules[0].telemetry[0]
        self.assertEqual(binding.process_id, "hdf_writer")
        self.assertEqual(binding.device_id, "")
        self.assertEqual(binding.source_kind, "process")
        self.assertEqual(binding.source_id, "hdf_writer")


class TelemetryBindingParseTests(unittest.TestCase):
    def _parse(self, binding: dict, *, allow_process: bool):
        return parse_telemetry_bindings(
            {"telemetry": [binding]},
            path=["inputs"],
            default_max_age_s=2.0,
            require_nonempty=True,
            allow_process=allow_process,
        )

    def test_process_binding_rejected_without_allow(self) -> None:
        with self.assertRaises(ConfigError):
            self._parse(
                {"as": "w", "process": "hdf_writer", "signal": "writing_active"},
                allow_process=False,
            )

    def test_process_binding_parsed_with_allow(self) -> None:
        out = self._parse(
            {"as": "w", "process": "hdf_writer", "signal": "writing_active"},
            allow_process=True,
        )
        self.assertEqual(out[0].process_id, "hdf_writer")
        self.assertEqual(out[0].device_id, "")

    def test_device_binding_still_works(self) -> None:
        out = self._parse(
            {"as": "t", "device": "dev1", "signal": "temp"},
            allow_process=True,
        )
        self.assertIsNone(out[0].process_id)
        self.assertEqual(out[0].device_id, "dev1")

    def test_both_device_and_process_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self._parse(
                {
                    "as": "x",
                    "device": "dev1",
                    "process": "hdf_writer",
                    "signal": "s",
                },
                allow_process=True,
            )

    def test_neither_device_nor_process_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self._parse({"as": "x", "signal": "s"}, allow_process=True)


if __name__ == "__main__":
    unittest.main()
