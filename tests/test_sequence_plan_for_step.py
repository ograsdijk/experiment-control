# ruff: noqa: E402
"""Regression test for ForStep formatting in sequence_plan.

Prior to the fix, sequence_plan.py read `step.var`, which does not exist on
ForStep (which carries `bind: dict[str, str]`). Any sequence containing a
`for:` step crashed with AttributeError when run through `experiment-control
sequence_plan`.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.cli.sequence_plan import (
    _format_for_bind,
    _MermaidBuilder,
    _text_lines_for_steps,
)
from experiment_control.sequencer.ast import ForStep


class FormatForBindTests(unittest.TestCase):
    def test_single_value_bind_renders_as_bare_name(self) -> None:
        self.assertEqual(_format_for_bind({"value": "x"}), "x")

    def test_multi_key_bind_renders_as_key_assignment_list(self) -> None:
        rendered = _format_for_bind({"voltage": "v", "current": "i"})
        # Order is insertion-order (3.7+) so both expected substrings present.
        self.assertIn("voltage=v", rendered)
        self.assertIn("current=i", rendered)

    def test_empty_bind_falls_back_to_placeholder(self) -> None:
        self.assertEqual(_format_for_bind({}), "?")


class SequencePlanForStepTests(unittest.TestCase):
    def test_text_render_for_step_does_not_crash_and_includes_var_name(self) -> None:
        step = ForStep(
            bind={"value": "current_v"},
            in_expr=[1.0, 2.0, 3.0],
            body=[],
        )
        lines = _text_lines_for_steps([step], resolve=False, env={}, indent=0)
        self.assertTrue(
            any("for current_v in" in line for line in lines),
            f"expected 'for current_v in ...' in rendered text, got: {lines!r}",
        )

    def test_mermaid_render_for_step_does_not_crash(self) -> None:
        step = ForStep(
            bind={"voltage": "v"},
            in_expr=[1.0, 2.0],
            body=[],
        )
        builder = _MermaidBuilder(resolve=False, env={})
        # Should not raise AttributeError; if the regression returns, this
        # is the line that fails before any assertion.
        entry, exit_ = builder.build_steps([step])
        self.assertIsNotNone(entry)
        self.assertIsNotNone(exit_)


if __name__ == "__main__":
    unittest.main()
