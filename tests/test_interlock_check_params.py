# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.interlock import (
    InterlockProcess,
    RulesetEntry,
    _parse_ruleset,
)


def _make_proc() -> InterlockProcess:
    """InterlockProcess with one ruleset limiting dev1.set to 0 <= value <= 10."""
    ruleset = _parse_ruleset(
        {
            "interceptor_id": "limits",
            "rules": [
                {
                    "name": "value_window",
                    "match": {"device_id": "dev1", "action": "set"},
                    "condition": {
                        "and": [
                            {"ge": ["${params.value}", 0]},
                            {"le": ["${params.value}", 10]},
                        ]
                    },
                    "on_block": {"message": "value out of range", "code": "OUT_OF_RANGE"},
                }
            ],
        },
        source="inline",
    )
    proc = object.__new__(InterlockProcess)
    entry = RulesetEntry(ruleset=ruleset, enabled=True, source="inline")
    proc._iter_ruleset_entries = lambda *, enabled_only: [entry]  # type: ignore[method-assign]
    proc._rule_enabled_state = lambda _interceptor, _rule: True  # type: ignore[method-assign]
    proc._set_phase = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    manager = SimpleNamespace(
        get_latest=lambda _dev, _sig: None,
        get_latest_process=lambda _proc, _sig: None,
    )
    proc._require_manager = lambda: manager  # type: ignore[method-assign]
    proc._make_condition_error_callback = (  # type: ignore[method-assign]
        lambda **_kwargs: (lambda _exc: None)
    )
    return proc


class InterlockCheckCommandShapeTests(unittest.TestCase):
    def _check(self, req: dict) -> dict:
        return _make_proc()._rpc_interlock_check({"request_id": "r", **req})

    def test_top_level_command_as_sent_by_the_manager(self) -> None:
        resp = self._check(
            {"command": {"device_id": "dev1", "action": "set", "params": {"value": 20}}}
        )
        self.assertTrue(resp["ok"])
        self.assertFalse(resp["allow"])
        self.assertEqual(resp["rule"], "value_window")

    def test_command_inside_params_as_sent_by_a_process_call(self) -> None:
        blocked = self._check(
            {"params": {"command": {"device_id": "dev1", "action": "set", "params": {"value": 20}}}}
        )
        self.assertFalse(blocked["allow"])
        self.assertEqual(blocked["error"]["message"], "value out of range")
        allowed = self._check(
            {"params": {"command": {"device_id": "dev1", "action": "set", "params": {"value": 5}}}}
        )
        self.assertTrue(allowed["allow"])

    def test_missing_command_is_still_rejected(self) -> None:
        resp = self._check({"params": {}})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "invalid_command")


if __name__ == "__main__":
    unittest.main()
