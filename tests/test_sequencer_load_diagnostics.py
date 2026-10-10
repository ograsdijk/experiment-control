from __future__ import annotations

import unittest

from experiment_control.sequencer.sequencer import SequencerProcess


def _diag(text: str) -> dict:
    process = object.__new__(SequencerProcess)
    ok, _spec, diagnostics = process._load_sequence_text(text=text, source="seq")
    assert not ok
    return diagnostics[0]


class LoadDiagnosticTests(unittest.TestCase):
    def test_unknown_step_kind_has_line_and_suggestion(self) -> None:
        d = _diag("version: 1\nsteps:\n  - sleep: 1\n  - wait:\n      condition: true\n")
        self.assertEqual(d["line"], 4)
        self.assertIn("did you mean 'wait_until'", d["message"])

    def test_missing_action_has_line(self) -> None:
        d = _diag("steps:\n  - sleep: 1\n  - call:\n      device: d\n")
        self.assertEqual(d["line"], 3)
        self.assertIn("call.action is required", d["message"])

    def test_nested_step_line(self) -> None:
        d = _diag(
            "steps:\n"
            "  - repeat:\n"
            "      times: 2\n"
            "      do:\n"
            "        - sleep: 1\n"
            "        - call: {device: d}\n"
        )
        self.assertEqual(d["line"], 6)

    def test_yaml_syntax_error_is_concise(self) -> None:
        d = _diag("steps:\n  - sleep: 1\n bad: [\n")
        self.assertEqual(d["source"], "yaml")
        self.assertIsInstance(d["line"], int)
        self.assertIsInstance(d["column"], int)
        msg = d["message"]
        self.assertNotIn("<unicode string>", msg)
        self.assertNotIn("\n", msg)
        self.assertNotIn("seq:", msg)


if __name__ == "__main__":
    unittest.main()
