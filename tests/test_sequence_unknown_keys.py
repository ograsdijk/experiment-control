from __future__ import annotations

import unittest
from pathlib import Path

from experiment_control.sequencer.ast import (
    SequenceParseError,
    load_sequence_yaml,
)
from experiment_control.sequencer.ranges import generate_from_gen

STACK_INSTANCES = (
    Path(__file__).resolve().parents[2] / "centrex-experimental-stack" / "instances"
)


def _load(text: str):
    return load_sequence_yaml(text)


class UnknownStepKeyTests(unittest.TestCase):
    def test_assign_inside_call_is_rejected_with_hint(self) -> None:
        with self.assertRaises(SequenceParseError) as ctx:
            _load(
                "steps:\n"
                "  - call:\n"
                "      device: d\n"
                "      action: a\n"
                "      assign: {x: {kind: key, ref: y}}\n"
            )
        msg = str(ctx.exception)
        self.assertIn("steps[0]", msg)
        self.assertIn("unknown key 'assign'", msg)
        self.assertIn("belongs next to 'call'", msg)
        self.assertEqual(ctx.exception.path, "steps[0]")

    def test_typo_gets_suggestion(self) -> None:
        with self.assertRaisesRegex(TypeError, "did you mean 'params'"):
            _load(
                "steps:\n  - call: {device: d, action: a, parmas: {}}\n"
            )

    def test_extra_step_level_key_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "unknown key 'sleep'"):
            _load("steps:\n  - call: {device: d, action: a}\n    sleep: 1\n")

    def test_call_step_level_extras_and_disabled_allowed(self) -> None:
        _load(
            "steps:\n"
            "  - call: {device: d, action: a}\n"
            "    save_as: r\n"
            "    extract: {kind: key, ref: x}\n"
            "    disabled: true\n"
        )

    def test_wait_until_unknown_key(self) -> None:
        with self.assertRaisesRegex(TypeError, "did you mean 'timeout_s'"):
            _load("steps:\n  - wait_until: {timeout: 3, condition: true}\n")

    def test_unknown_top_level_key(self) -> None:
        with self.assertRaisesRegex(TypeError, "did you mean 'steps'"):
            _load("step: []\n")

    def test_nested_error_path(self) -> None:
        with self.assertRaises(SequenceParseError) as ctx:
            _load(
                "steps:\n"
                "  - repeat:\n"
                "      times: 2\n"
                "      do:\n"
                "        - sleep: 1\n"
                "        - call: {device: d}\n"
            )
        self.assertEqual(ctx.exception.path, "steps[0].repeat.do[1]")

    def test_unknown_step_kind_suggests(self) -> None:
        with self.assertRaisesRegex(TypeError, "did you mean 'wait_until'"):
            _load("steps:\n  - wait_unti: {condition: true}\n")


class GeneratorStrictnessTests(unittest.TestCase):
    def _gen(self, spec):
        return generate_from_gen(spec, env={})

    def test_linspace_without_num_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "linspace is missing required field.*num"):
            self._gen({"linspace": {"start": 0, "stop": 1}})

    def test_other_generators_require_fields(self) -> None:
        cases = {
            "range": {"start": 0},
            "logspace": {"start": 0, "stop": 1},
            "geomspace": {"start": 1, "stop": 10},
            "triangle": {"start": 0, "stop": 1},
            "centered_triangle": {"center": 0, "span": 1},
        }
        for kind, params in cases.items():
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "missing required"):
                    self._gen({kind: params})

    def test_unknown_generator_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "did you mean 'num'"):
            self._gen({"linspace": {"start": 0, "stop": 1, "nun": 3}})

    def test_unknown_gen_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown key 'shufle'"):
            self._gen({"linspace": {"start": 0, "stop": 1, "num": 3}, "shufle": True})

    def test_valid_generators_with_modifiers(self) -> None:
        recs = self._gen(
            {"linspace": {"start": 0, "stop": 1, "num": 3}, "offset": 1, "serpentine": True}
        )
        self.assertEqual([r["value"] for r in recs], [1.0, 1.5, 2.0])
        recs = self._gen({"range": {"start": 0, "stop": 3}})
        self.assertEqual(len(recs), 3)

    def test_static_check_at_load(self) -> None:
        with self.assertRaisesRegex(TypeError, "missing required field.*num"):
            _load(
                "steps:\n"
                "  - for:\n"
                "      bind: v\n"
                "      in: {gen: {linspace: {start: 0, stop: 1}}}\n"
                "      do: [{sleep: 0}]\n"
            )


@unittest.skipUnless(STACK_INSTANCES.is_dir(), "centrex-experimental-stack not present")
class RealSequencesTests(unittest.TestCase):
    def test_all_stack_sequences_parse(self) -> None:
        files = sorted(STACK_INSTANCES.glob("*/sequences/*.yaml"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                load_sequence_yaml(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
