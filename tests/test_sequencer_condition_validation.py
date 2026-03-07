# ruff: noqa: E402, SLF001

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.condition_validation import (
    has_error_diagnostics,
    validate_sequence_conditions,
)
from experiment_control.sequencer.sequencer import SequencerProcess


class SequencerConditionValidationTests(unittest.TestCase):
    def test_validate_reports_missing_wait_until_condition(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "wait_until": {
                            "timeout_s": 2.0,
                        }
                    }
                ],
            }
        )
        diagnostics = validate_sequence_conditions(spec)
        self.assertTrue(has_error_diagnostics(diagnostics))
        self.assertTrue(
            any(
                "steps[0].wait_until.condition" in str(diag.get("message", ""))
                for diag in diagnostics
            )
        )

    def test_validate_warns_for_single_and_clause(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "if": {
                            "condition": {"and": [{"gt": [1, 0]}]},
                            "then": [],
                        }
                    }
                ],
            }
        )
        diagnostics = validate_sequence_conditions(spec)
        self.assertFalse(has_error_diagnostics(diagnostics))
        warnings = [diag for diag in diagnostics if diag.get("severity") == "warning"]
        self.assertTrue(warnings)
        self.assertTrue(
            any(
                "only one clause" in str(diag.get("message", "")).lower()
                for diag in warnings
            )
        )

    def test_validate_rejects_compare_arity(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {
                        "if": {
                            "condition": {"gt": [1]},
                            "then": [],
                        }
                    }
                ],
            }
        )
        diagnostics = validate_sequence_conditions(spec)
        self.assertTrue(has_error_diagnostics(diagnostics))
        self.assertTrue(
            any(
                "expects exactly two arguments" in str(diag.get("message", ""))
                for diag in diagnostics
            )
        )

    def test_load_sequence_text_returns_warning_diagnostics(self) -> None:
        yaml_text = """
version: 1
steps:
  - if:
      condition:
        and:
          - {gt: [1, 0]}
      then: []
"""
        proc = object.__new__(SequencerProcess)
        ok, spec, diagnostics = SequencerProcess._load_sequence_text(
            proc,
            text=yaml_text,
            source="sequence_yaml",
        )
        self.assertTrue(ok)
        self.assertIsNotNone(spec)
        self.assertTrue(
            any(
                diag.get("severity") == "warning"
                and diag.get("source") == "sequencer.condition"
                for diag in diagnostics
            )
        )

    def test_load_sequence_text_fails_on_condition_error(self) -> None:
        yaml_text = """
version: 1
steps:
  - wait_until:
      timeout_s: 1.0
"""
        proc = object.__new__(SequencerProcess)
        ok, spec, diagnostics = SequencerProcess._load_sequence_text(
            proc,
            text=yaml_text,
            source="sequence_yaml",
        )
        self.assertFalse(ok)
        self.assertIsNone(spec)
        self.assertTrue(
            any(diag.get("source") == "sequencer.condition" for diag in diagnostics)
        )


if __name__ == "__main__":
    unittest.main()
