# ruff: noqa: E402

"""Tests for the ``rules=`` / ``rules_dir=`` constructor kwargs.

The legacy public API required callers to pre-load ruleset entries via
``collect_rulesets(paths)`` and pass them as ``rulesets=``. The new
constructor also accepts raw paths, so per-instance wrapper classes that
existed solely to do this translation can disappear. These tests pin the
new contract.
"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.interlock import (
    InterlockProcess,
    collect_rulesets,
    resolve_rule_paths,
)


def _write_ruleset(dir_path: Path, name: str, interceptor_id: str) -> Path:
    path = dir_path / name
    path.write_text(
        textwrap.dedent(
            f"""\
            version: 1
            interceptor_id: {interceptor_id}
            rules: []
            """
        ),
        encoding="utf-8",
    )
    return path


class ResolveRulePathsTests(unittest.TestCase):
    def test_single_path_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_ruleset(Path(tmp), "r.yaml", "i1")
            paths = resolve_rule_paths(rules=str(yaml_path))
            self.assertEqual(paths, [yaml_path.resolve()])

    def test_single_path_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_ruleset(Path(tmp), "r.yaml", "i1")
            paths = resolve_rule_paths(rules=yaml_path)
            self.assertEqual(paths, [yaml_path.resolve()])

    def test_list_of_paths_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = _write_ruleset(Path(tmp), "a.yaml", "i_a")
            b = _write_ruleset(Path(tmp), "b.yaml", "i_b")
            paths = resolve_rule_paths(rules=[b, a])
            self.assertEqual(paths, [b.resolve(), a.resolve()])

    def test_rules_dir_globs_yml_and_yaml_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_ruleset(tmp_path, "c.yaml", "i_c")
            _write_ruleset(tmp_path, "a.yml", "i_a")
            _write_ruleset(tmp_path, "b.yaml", "i_b")
            (tmp_path / "ignored.txt").write_text("nope", encoding="utf-8")
            paths = resolve_rule_paths(rules_dir=tmp_path)
            # yml first (sorted), then yaml (sorted)
            self.assertEqual([p.name for p in paths], ["a.yml", "b.yaml", "c.yaml"])

    def test_rules_and_rules_dir_combine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            explicit = _write_ruleset(tmp_path, "explicit.yaml", "i_explicit")
            other_dir = tmp_path / "more"
            other_dir.mkdir()
            globbed = _write_ruleset(other_dir, "globbed.yaml", "i_globbed")
            paths = resolve_rule_paths(rules=[explicit], rules_dir=other_dir)
            self.assertEqual(paths, [explicit.resolve(), globbed.resolve()])

    def test_neither_returns_empty(self) -> None:
        self.assertEqual(resolve_rule_paths(), [])


class InterlockProcessConstructorRulesArgTests(unittest.TestCase):
    """The constructor's rules= path must produce the same ruleset entries
    as the legacy rulesets= path, and must reject ambiguous calls.
    """

    def _ctor_kwargs(self) -> dict:
        # All required kwargs except the rules-source. Patching .__init__
        # of ManagedProcessBase / ManagerClientHelper / Poller etc. is more
        # invasive than necessary; instead we stop before any of those by
        # patching __init__ on the base class to a no-op.
        return dict(
            manager_rpc="tcp://127.0.0.1:0",
            manager_pub="tcp://127.0.0.1:0",
            process_id="test-interlock",
            rpc_timeout_ms=100,
            heartbeat_endpoint=None,
            heartbeat_period_s=1.0,
        )

    def test_rules_kwarg_loads_same_entries_as_rulesets_kwarg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_ruleset(Path(tmp), "r.yaml", "i1")

            via_rulesets = collect_rulesets([yaml_path])

            # We only need to exercise the rulesets normalisation logic —
            # short-circuit the manager / poller setup that follows it.
            with patch.object(InterlockProcess, "__init__", lambda *a, **kw: None):
                # nothing to assert; just ensures the no-op patch is in scope
                pass

            from experiment_control.processes.interlock import _normalize_rulesets_arg

            via_rules_kwarg = _normalize_rulesets_arg(
                rulesets=None, rules=yaml_path, rules_dir=None
            )
            self.assertEqual(
                [e.ruleset.interceptor_id for e in via_rules_kwarg],
                [e.ruleset.interceptor_id for e in via_rulesets],
            )
            self.assertEqual(
                [e.source for e in via_rules_kwarg],
                [e.source for e in via_rulesets],
            )

    def test_rejects_both_rulesets_and_rules(self) -> None:
        from experiment_control.processes.interlock import _normalize_rulesets_arg

        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = _write_ruleset(Path(tmp), "r.yaml", "i1")
            rulesets = collect_rulesets([yaml_path])
            with self.assertRaises(ValueError) as ctx:
                _normalize_rulesets_arg(
                    rulesets=rulesets, rules=yaml_path, rules_dir=None
                )
            self.assertIn("either rulesets=", str(ctx.exception))

    def test_rejects_neither(self) -> None:
        from experiment_control.processes.interlock import _normalize_rulesets_arg

        with self.assertRaises(ValueError) as ctx:
            _normalize_rulesets_arg(rulesets=None, rules=None, rules_dir=None)
        self.assertIn("no rules provided", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
