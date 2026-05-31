# ruff: noqa: E402

"""Same contract as test_interlock_rules_arg.py but for WatchdogProcess.

The watchdog YAML schema requires a few more fields than interlock (a
watchdog_id, defaults block, and at least the schema-version key), so the
minimal ruleset fixture here is fatter than the interlock one.
"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.watchdog import (
    collect_rulesets,
    resolve_rule_paths,
)


def _write_watchdog_ruleset(dir_path: Path, name: str, watchdog_id: str) -> Path:
    path = dir_path / name
    path.write_text(
        textwrap.dedent(
            f"""\
            version: 1
            watchdog_id: {watchdog_id}
            defaults:
              tolerate_missing_telemetry_s: 5.0
            rules: []
            """
        ),
        encoding="utf-8",
    )
    return path


class ResolveRulePathsTests(unittest.TestCase):
    def test_single_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_watchdog_ruleset(Path(tmp), "w.yaml", "w1")
            self.assertEqual(
                resolve_rule_paths(rules=str(yaml_path)),
                [yaml_path.resolve()],
            )

    def test_rules_dir_globs_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_watchdog_ruleset(tmp_path, "b.yaml", "wb")
            _write_watchdog_ruleset(tmp_path, "a.yml", "wa")
            paths = resolve_rule_paths(rules_dir=tmp_path)
            self.assertEqual([p.name for p in paths], ["a.yml", "b.yaml"])

    def test_neither_returns_empty(self) -> None:
        self.assertEqual(resolve_rule_paths(), [])


class WatchdogNormalizeRulesetsArgTests(unittest.TestCase):
    def test_rules_kwarg_loads_same_entries_as_rulesets_kwarg(self) -> None:
        from experiment_control.processes.watchdog import _normalize_rulesets_arg

        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_watchdog_ruleset(Path(tmp), "w.yaml", "w1")

            via_rulesets = collect_rulesets([yaml_path])
            via_rules = _normalize_rulesets_arg(
                rulesets=None, rules=yaml_path, rules_dir=None
            )
            self.assertEqual(
                [r.watchdog_id for r in via_rules],
                [r.watchdog_id for r in via_rulesets],
            )

    def test_rejects_both_rulesets_and_rules(self) -> None:
        from experiment_control.processes.watchdog import _normalize_rulesets_arg

        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_watchdog_ruleset(Path(tmp), "w.yaml", "w1")
            rulesets = collect_rulesets([yaml_path])
            with self.assertRaises(ValueError) as ctx:
                _normalize_rulesets_arg(
                    rulesets=rulesets, rules=yaml_path, rules_dir=None
                )
            self.assertIn("either rulesets=", str(ctx.exception))

    def test_rejects_neither(self) -> None:
        from experiment_control.processes.watchdog import _normalize_rulesets_arg

        with self.assertRaises(ValueError) as ctx:
            _normalize_rulesets_arg(rulesets=None, rules=None, rules_dir=None)
        self.assertIn("no rules provided", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
