from __future__ import annotations

import time
import unittest
from typing import Any

from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.eval import (
    OperandError,
    TemplateError,
    eval_condition,
    render_templates,
)
from experiment_control.sequencer.runtime import SequencerRuntime


def _runtime(call=None, telemetry=None, call_process=None) -> SequencerRuntime:
    return SequencerRuntime(
        call_device=call or (lambda d, a, p: {"ok": True, "result": None}),
        call_process=call_process,
        get_telemetry=telemetry or (lambda d, s: None),
        set_stream_context=lambda *_a, **_k: None,
    )


def _run(
    steps: list[Any],
    *,
    vars: dict[str, Any] | None = None,
    call=None,
    telemetry=None,
    call_process=None,
    budget_s: float = 2.0,
) -> SequencerRuntime:
    rt = _runtime(call, telemetry, call_process)
    doc: dict[str, Any] = {"version": 1, "steps": steps}
    if vars:
        doc["vars"] = vars
    rt.load(parse_sequence(doc))
    rt.start()
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        rt.tick()
        if rt.state != "RUNNING":
            break
        time.sleep(0.005)
    return rt


def _telemetry_wait(**kw: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "timeout_s": 0.1,
        "every_s": 0.01,
        "sample": {"telemetry": {"device": "d", "signal": "s"}},
        "condition": {"gt": ["${sample}", 6.0]},
    }
    cfg.update(kw)
    return {"wait_until": cfg}


class TemplateErrorTests(unittest.TestCase):
    def test_unknown_name_suggests_close_match(self) -> None:
        with self.assertRaises(TemplateError) as ctx:
            render_templates("${ff}", {"f": 1, "other": 2})
        self.assertIn("Unknown name 'ff' (did you mean 'f'?)", str(ctx.exception))

    def test_unknown_name_without_close_match(self) -> None:
        with self.assertRaises(TemplateError) as ctx:
            render_templates("${zzz}", {"alpha": 1})
        self.assertEqual(str(ctx.exception), "Unknown name 'zzz'")

    def test_syntax_error_is_template_error(self) -> None:
        with self.assertRaises(TemplateError) as ctx:
            render_templates("${1 +}", {})
        self.assertIn("Invalid expression", str(ctx.exception))

    def test_string_operand_is_named(self) -> None:
        with self.assertRaises(OperandError) as ctx:
            render_templates("${f / 2}", {"f": "4.0e6"})
        msg = str(ctx.exception)
        self.assertIn("${f / 2}", msg)
        self.assertIn("f is the string '4.0e6'", msg)
        self.assertFalse(ctx.exception.none_involved)

    def test_none_operand_flagged(self) -> None:
        with self.assertRaises(OperandError) as ctx:
            render_templates("${s * 2}", {"s": None})
        self.assertTrue(ctx.exception.none_involved)
        with self.assertRaises(OperandError) as ctx2:
            eval_condition({"gt": ["${s}", 6.0]}, {"s": None})
        self.assertTrue(ctx2.exception.none_involved)

    def test_division_by_zero_is_operand_error(self) -> None:
        with self.assertRaises(OperandError) as ctx:
            render_templates("${1 / z}", {"z": 0})
        self.assertIn("division by zero", str(ctx.exception))


class ConditionFailureTests(unittest.TestCase):
    def test_if_with_unknown_name_fails_step(self) -> None:
        rt = _run(
            [{"if": {"condition": {"eq": ["${missing}", 1]}, "then": [{"sleep": 0}]}}],
            vars={"missing_x": 1},
        )
        self.assertEqual(rt.state, "ERROR")
        self.assertIn("if condition could not be evaluated", rt._last_error)
        self.assertIn(
            "Unknown name 'missing' (did you mean 'missing_x'?)", rt._last_error
        )

    def test_while_with_unknown_name_fails_step(self) -> None:
        rt = _run(
            [{"while": {"condition": {"lt": ["${nope}", 3]}, "body": [{"sleep": 0}]}}]
        )
        self.assertEqual(rt.state, "ERROR")
        self.assertIn("while condition could not be evaluated", rt._last_error)

    def test_valid_if_still_works(self) -> None:
        rt = _run(
            [
                {"assign": {"x": 3}},
                {
                    "if": {
                        "condition": {"gt": ["${x}", 1]},
                        "then": [{"assign": {"y": 1}}],
                        "else": [{"assign": {"y": 2}}],
                    }
                },
            ]
        )
        self.assertEqual(rt.state, "STOPPED")

    def test_wait_until_unknown_name_fails_immediately(self) -> None:
        rt = _run(
            [
                _telemetry_wait(
                    timeout_s=5, condition={"gt": ["${smaple}", 1]}
                )
            ],
            telemetry=lambda d, s: {"value": 1.0, "t_mono": time.monotonic()},
        )
        self.assertEqual(rt.state, "ERROR")
        self.assertIn("wait_until condition could not be evaluated", rt._last_error)
        self.assertIn("did you mean 'sample'", rt._last_error)

    def test_wait_until_missing_sample_keeps_polling_until_timeout(self) -> None:
        rt = _run(
            [
                _telemetry_wait(
                    timeout_s=0.15,
                    sample={
                        "telemetry": {
                            "device": "freq1",
                            "signal": "nope",
                            "max_age_s": 2.0,
                        }
                    },
                )
            ]
        )
        self.assertEqual(rt.state, "ERROR")
        self.assertIn("wait_until timed out after 0.15s", rt._last_error)
        self.assertIn("no telemetry for freq1.nope", rt._last_error)
        self.assertIn("max_age_s=2", rt._last_error)
        self.assertNotIn("could not be evaluated", rt._last_error)

    def test_wait_until_expression_on_missing_sample_keeps_polling(self) -> None:
        rt = _run([_telemetry_wait(condition="${sample * 2 > 3}")])
        self.assertIn("timed out", rt._last_error)

    def test_wait_until_succeeds_when_sample_arrives(self) -> None:
        rt = _run(
            [_telemetry_wait(timeout_s=1)],
            telemetry=lambda d, s: {"value": 7.0, "t_mono": time.monotonic()},
        )
        self.assertEqual(rt.state, "STOPPED")

    def test_wait_until_timeout_reports_last_sample(self) -> None:
        rt = _run(
            [_telemetry_wait()],
            telemetry=lambda d, s: {"value": 1.5, "t_mono": time.monotonic()},
        )
        self.assertEqual(rt.state, "ERROR")
        self.assertIn("last sample 1.5", rt._last_error)
        self.assertIn("s old)", rt._last_error)
        self.assertIn('"gt"', rt._last_error)

    def test_wait_until_stale_telemetry_reports_age(self) -> None:
        rt = _run(
            [
                _telemetry_wait(
                    sample={
                        "telemetry": {"device": "d", "signal": "s", "max_age_s": 1.0}
                    }
                )
            ],
            telemetry=lambda d, s: {"value": 9.0, "t_mono": time.monotonic() - 30.0},
        )
        self.assertIn("latest telemetry for d.s is", rt._last_error)
        self.assertIn("max_age_s=1", rt._last_error)


class FieldAwareConversionTests(unittest.TestCase):
    def test_sleep_not_a_number(self) -> None:
        rt = _run([{"sleep": "soon"}])
        self.assertEqual(rt.state, "ERROR")
        self.assertIn("sleep: expected seconds as a number, got 'soon'", rt._last_error)

    def test_repeat_times_not_an_int(self) -> None:
        rt = _run([{"repeat": {"times": "lots", "body": [{"sleep": 0}]}}])
        self.assertIn("repeat.times must be an integer, got 'lots'", rt._last_error)

    def test_wait_until_timeout_not_a_number(self) -> None:
        rt = _run([{"wait_until": {"timeout_s": "ages", "condition": True}}])
        self.assertIn(
            "wait_until.timeout_s must be a number, got 'ages'", rt._last_error
        )

    @staticmethod
    def _call_step(**extra: Any) -> dict[str, Any]:
        return {"call": {"device": "d", "action": "a"}, **extra}

    def test_assign_key_on_non_mapping(self) -> None:
        rt = _run(
            [self._call_step(assign={"f": {"kind": "key", "ref": "missing"}})],
            call=lambda d, a, p: {"ok": True, "result": 1.5},
        )
        self.assertIn(
            "assign f: call result is a float, not a mapping with key 'missing'",
            rt._last_error,
        )

    def test_assign_missing_key_lists_keys(self) -> None:
        rt = _run(
            [self._call_step(assign={"f": {"kind": "key", "ref": "missing"}})],
            call=lambda d, a, p: {"ok": True, "result": {"a": 1, "b": 2}},
        )
        self.assertIn(
            "assign f: result has no key 'missing' (keys: a, b)", rt._last_error
        )

    def test_extract_index_errors(self) -> None:
        rt = _run(
            [self._call_step(extract={"kind": "index", "ref": 5}, save_as="v")],
            call=lambda d, a, p: {"ok": True, "result": [1, 2]},
        )
        self.assertIn(
            "extract v: cannot take index 5 of a result with 2 items", rt._last_error
        )

    def test_extract_key_success(self) -> None:
        rt = _run(
            [self._call_step(assign={"f": {"kind": "key", "ref": "a"}})],
            call=lambda d, a, p: {"ok": True, "result": {"a": 1}},
        )
        self.assertEqual(rt.state, "STOPPED")


class ProcessErrorTextTests(unittest.TestCase):
    def test_bare_unknown_process_code_names_process(self) -> None:
        text = SequencerRuntime._response_error_text(
            {"ok": False, "error": {"code": "unknown_process"}}, "analysis"
        )
        self.assertIn("'analysis'", text)
        self.assertIn("not registered", text)

    def test_call_step_reports_process_name(self) -> None:
        rt = _run(
            [{"call": {"process": "ghost", "action": "x"}}],
            call_process=lambda proc, a, p: {
                "ok": False,
                "error": {"code": "unknown_process"},
            },
        )
        self.assertIn("process 'ghost' is not registered", rt._last_error or "")


if __name__ == "__main__":
    unittest.main()
