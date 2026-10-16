# ruff: noqa: E402, SLF001

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.sequencer.sequencer import SequencerProcess
from experiment_control.sequencer.var_resolution import evaluate_expressions, resolve_vars

SWEEP_YAML = """
version: 1
vars:
  freq_center_hz: 1000000000.0
  sweep_step_hz: 1000000.0
  n_steps: 30
  n_traces: 20
  sweep_start_hz: ${freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)}
  values: {gen: {values: [0, 200, 400]}}
steps:
  - sleep: 0.1
"""


def _build_proc() -> SequencerProcess:
    proc = object.__new__(SequencerProcess)
    proc._manager = None
    proc._sequence_library_path = None
    proc._sequence_library = None
    return proc


def _resolve(params: dict) -> dict:
    resp = _build_proc()._handle_rpc({"type": "sequencer.resolve_vars", "params": params})
    return resp


class ResolveVarsHelperTests(unittest.TestCase):
    def test_resolves_derived_vars_in_any_order(self) -> None:
        resolved, errors = resolve_vars({"b": "${a * 2}", "c": "${b + 1}", "a": 3})
        self.assertEqual(resolved, {"b": 6, "c": 7, "a": 3})
        self.assertEqual(errors, {})

    def test_reports_unresolvable_var(self) -> None:
        resolved, errors = resolve_vars({"a": "${missing + 1}"})
        self.assertEqual(resolved["a"], "${missing + 1}")
        self.assertIn("missing", errors["a"])

    def test_expressions_against_vars_and_runtime_names(self) -> None:
        out = evaluate_expressions(
            ["${a + 1}", "${vars.a}", "${freq_hz * a}", "${max(a, 5)}", "${a / 0}"],
            {"a": 2},
        )
        self.assertEqual(out["${a + 1}"], {"ok": True, "value": 3})
        self.assertEqual(out["${vars.a}"], {"ok": True, "value": 2})
        self.assertEqual(
            out["${freq_hz * a}"], {"ok": False, "runtime": True, "names": ["freq_hz"]}
        )
        self.assertEqual(out["${max(a, 5)}"], {"ok": True, "value": 5})
        self.assertFalse(out["${a / 0}"]["ok"])
        self.assertIn("division", out["${a / 0}"]["error"])


class ResolveVarsRpcTests(unittest.TestCase):
    def test_resolves_text_with_override(self) -> None:
        resp = _resolve(
            {
                "text": SWEEP_YAML,
                "vars_override": {"n_steps": 11, "nope": 1},
                "expressions": ["${sweep_start_hz}", "${n_traces}"],
            }
        )
        self.assertTrue(resp.get("ok"), resp)
        result = resp["result"]
        self.assertEqual(result["vars"]["sweep_start_hz"], 1e9 - 0.5 * 1e6 * 10)
        self.assertEqual(result["vars"]["values"], {"gen": {"values": [0, 200, 400]}})
        self.assertEqual(result["derived"], ["sweep_start_hz"])
        self.assertEqual(result["overridden"], ["n_steps"])
        self.assertEqual(
            result["base_vars"],
            {"n_steps": 30, "sweep_start_hz": 1e9 - 0.5 * 1e6 * 29},
        )
        self.assertEqual(
            result["formulas"],
            {"sweep_start_hz": "${freq_center_hz - 0.5 * sweep_step_hz * (n_steps - 1)}"},
        )
        self.assertEqual(result["unknown_overrides"], ["nope"])
        self.assertEqual(result["errors"], {})
        self.assertEqual(
            result["expressions"]["${sweep_start_hz}"],
            {"ok": True, "value": 1e9 - 0.5 * 1e6 * 10},
        )
        self.assertEqual(result["expressions"]["${n_traces}"], {"ok": True, "value": 20})

    def test_invalid_yaml_is_an_error(self) -> None:
        resp = _resolve({"text": "vars: [unclosed"})
        self.assertFalse(resp.get("ok"))
        self.assertEqual(resp["error"]["code"], "invalid_yaml")

    def test_invalid_params(self) -> None:
        resp = _resolve({"text": SWEEP_YAML, "expressions": "nope"})
        self.assertFalse(resp.get("ok"))


if __name__ == "__main__":
    unittest.main()
