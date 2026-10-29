from __future__ import annotations

import unittest

from experiment_control.sequencer.ast import load_sequence_yaml

_BASE = """\
version: 1
vars:
  passes: 3
  enabled: true
  channels: [1, 2]
  channel_options: [{value: 1, label: A}, {value: 2, label: B}]
  targets: [{id: t, kind: generic}]
  target: {id: t, kind: generic}
  relative: true
  lo: -1.0
  hi: 1.0
  points: 5
form:
{form}
steps:
  - sleep: 0
"""

_VALID_FORM = """\
  title: Scan
  presets:
    - {label: Off, set: {enabled: false}}
  summary:
    - {label: Total, product: [points, passes]}
  sections:
    - title: Target
      toggle: enabled
      fields:
        - {type: target, var: target, catalog: targets, other_exclude: [mirror], other_defaults: {settle_s: 1.0}}
        - {type: range, target: target, relative: relative, from: lo, to: hi, points: points}
        - {type: integer, var: passes, label: Passes, min: 1, enabled_by: enabled}
        - {type: chips, var: channels, options: channel_options}
        - {type: select, var: passes, options_call: {device: dev, action: list}}
"""


def _load(form: str):
    return load_sequence_yaml(_BASE.replace("{form}\n", form))


class RunFormTests(unittest.TestCase):
    def test_valid_form_is_kept_on_the_spec(self) -> None:
        spec = _load(_VALID_FORM)
        assert spec.form is not None
        self.assertEqual(spec.form["title"], "Scan")
        self.assertEqual(len(spec.form["sections"][0]["fields"]), 5)

    def test_sequence_without_form_has_none(self) -> None:
        spec = load_sequence_yaml("version: 1\nsteps:\n  - sleep: 0\n")
        self.assertIsNone(spec.form)

    def test_unknown_var_is_rejected(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            _load("  sections:\n    - fields:\n        - {type: checkbox, var: nope}\n")
        self.assertIn("'nope', which is not declared in vars", str(ctx.exception))

    def test_unknown_field_type_and_key_are_rejected(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            _load("  sections:\n    - fields:\n        - {type: slider, var: passes}\n")
        self.assertIn("type must be one of", str(ctx.exception))
        with self.assertRaises(TypeError) as ctx:
            _load("  sections:\n    - fields:\n        - {type: checkbox, var: enabled, colour: red}\n")
        self.assertIn("unknown key(s): colour", str(ctx.exception))

    def test_options_must_be_value_label_list(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            _load("  sections:\n    - fields:\n        - {type: chips, var: channels, options: passes}\n")
        self.assertIn("must be a list of {value, label}", str(ctx.exception))

    def test_preset_and_summary_vars_are_checked(self) -> None:
        with self.assertRaises(TypeError):
            _load(
                "  presets: [{label: x, set: {missing: 1}}]\n"
                "  sections:\n    - fields:\n        - {type: checkbox, var: enabled}\n"
            )
        with self.assertRaises(TypeError):
            _load(
                "  summary: [{label: x, product: [missing]}]\n"
                "  sections:\n    - fields:\n        - {type: checkbox, var: enabled}\n"
            )

    def test_catalog_must_be_a_list(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            _load("  sections:\n    - fields:\n        - {type: target, var: target, catalog: passes}\n")
        self.assertIn("must be a list of targets", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
