from __future__ import annotations

import unittest

from experiment_control.sequencer.ast import load_sequence_yaml
from experiment_control.utils.yaml_helpers import load_yaml_text


class SequenceYamlNumberTests(unittest.TestCase):
    def test_yaml12_float_forms_load_as_floats(self) -> None:
        spec = load_sequence_yaml(
            "vars:\n"
            "  a: 2.5e6\n"
            "  b: 1e-3\n"
            "  c: 1.0E9\n"
            "  d: -3.5e-2\n"
            "  e: 20.0e+6\n"
            "  f: .5e2\n"
            "  g: 40.0e6\n"
            "steps: []\n"
        )
        self.assertEqual(
            spec.vars,
            {"a": 2.5e6, "b": 1e-3, "c": 1.0e9, "d": -3.5e-2, "e": 20.0e6,
             "f": 50.0, "g": 40.0e6},
        )
        for value in spec.vars.values():
            self.assertIsInstance(value, float)

    def test_quoted_and_plain_values_are_unaffected(self) -> None:
        spec = load_sequence_yaml(
            'vars:\n  q: "1.0e9"\n  r: \'2.5e6\'\n  i: 5\n  s: abc\n'
            "  h: 1e3x\n  t: 1.5\n  inf: .inf\n  neg: -7\nsteps: []\n"
        )
        self.assertEqual(spec.vars["q"], "1.0e9")
        self.assertEqual(spec.vars["r"], "2.5e6")
        self.assertEqual(spec.vars["i"], 5)
        self.assertIsInstance(spec.vars["i"], int)
        self.assertEqual(spec.vars["s"], "abc")
        self.assertEqual(spec.vars["h"], "1e3x")
        self.assertEqual(spec.vars["t"], 1.5)
        self.assertEqual(spec.vars["inf"], float("inf"))
        self.assertEqual(spec.vars["neg"], -7)

    def test_default_loader_unchanged_for_config_files(self) -> None:
        self.assertEqual(load_yaml_text("a: 2.5e6", source="cfg"), {"a": "2.5e6"})
        self.assertEqual(
            load_yaml_text("a: 2.5e6", source="seq", yaml12_floats=True), {"a": 2.5e6}
        )


if __name__ == "__main__":
    unittest.main()
