# ruff: noqa: E402

"""Phase 10 deprecation-shim coverage.

Every underscored alias on ``ManagedProcessBase`` /
``StateMachineProcessBase`` that was renamed in Phase 10 must:

1. Continue to return the same value as the new public name.
2. Emit a ``DeprecationWarning`` so downstream callers see the
   migration signal during the one-release deprecation window
   (REFACTOR_PLAN.md §10.3, §10.12).
"""

import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.process_base import ManagedProcessBase
from experiment_control.processes.state_machine_base import (
    SequenceError,
    StateMachineProcessBase,
)


class _FakeManager:
    def __init__(self, response: dict | None = None) -> None:
        self._response = response if response is not None else {"ok": True}
        self.publish_calls: list[dict] = []
        self.calls: list[dict] = []

    def call(self, req: dict, *, timeout_ms: int | None = None) -> dict | None:
        self.calls.append(dict(req))
        return self._response

    def publish_event(self, **kw: object) -> None:
        self.publish_calls.append(dict(kw))


def _bare_state_machine(manager: _FakeManager | None = None) -> StateMachineProcessBase:
    proc = StateMachineProcessBase.__new__(StateMachineProcessBase)
    proc._manager = manager
    proc._process_id = "deprecation-test"
    proc._sequence_error_cls = SequenceError
    proc._last_command = None
    proc._last_error = None
    return proc


class ManagedProcessBaseAliasTests(unittest.TestCase):
    def _assert_emits(self, fn, *, contains: str) -> object:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = fn()
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertTrue(
            deprecations,
            f"expected DeprecationWarning, got {[w.category.__name__ for w in caught]}",
        )
        self.assertTrue(
            any(contains in str(w.message) for w in deprecations),
            f"expected message containing {contains!r}, got "
            f"{[str(w.message) for w in deprecations]}",
        )
        return result

    def test_rpc_ok_alias_emits_and_matches(self) -> None:
        req = {"request_id": "r1"}
        new = ManagedProcessBase.rpc_ok(req, result={"k": 1})
        old = self._assert_emits(
            lambda: ManagedProcessBase._rpc_ok(req, result={"k": 1}),
            contains="_rpc_ok",
        )
        self.assertEqual(old, new)

    def test_rpc_err_alias_emits_and_matches(self) -> None:
        req = {"request_id": 7}
        new = ManagedProcessBase.rpc_err(req, code="bad", message="b")
        old = self._assert_emits(
            lambda: ManagedProcessBase._rpc_err(req, code="bad", message="b"),
            contains="_rpc_err",
        )
        self.assertEqual(old, new)

    def test_rpc_unknown_alias_emits_and_matches(self) -> None:
        req = {"request_id": 9}
        new = ManagedProcessBase.rpc_unknown(req)
        old = self._assert_emits(
            lambda: ManagedProcessBase._rpc_unknown(req),
            contains="_rpc_unknown",
        )
        self.assertEqual(old, new)

    def test_rpc_invalid_params_alias_emits_and_matches(self) -> None:
        req = {"request_id": 11}
        new = ManagedProcessBase.rpc_invalid_params(req, message="bad")
        old = self._assert_emits(
            lambda: ManagedProcessBase._rpc_invalid_params(req, message="bad"),
            contains="_rpc_invalid_params",
        )
        self.assertEqual(old, new)


class StateMachineBaseAliasTests(unittest.TestCase):
    def _assert_emits(self, fn, *, contains: str) -> object:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = fn()
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertTrue(
            deprecations,
            f"expected DeprecationWarning, got {[w.category.__name__ for w in caught]}",
        )
        self.assertTrue(
            any(contains in str(w.message) for w in deprecations),
            f"expected message containing {contains!r}, got "
            f"{[str(w.message) for w in deprecations]}",
        )
        return result

    def test_command_alias_emits_and_delegates(self) -> None:
        mgr = _FakeManager({"ok": True, "result": 1})
        proc = _bare_state_machine(mgr)
        resp = self._assert_emits(
            lambda: proc._command("dev", "action", {"x": 1}),
            contains="_command",
        )
        self.assertEqual(resp, {"ok": True, "result": 1})
        self.assertEqual(len(mgr.calls), 1)

    def test_publish_transition_event_alias_emits_and_delegates(self) -> None:
        mgr = _FakeManager()
        proc = _bare_state_machine(mgr)
        self._assert_emits(
            lambda: proc._publish_transition_event(
                "IDLE", "RUNNING", reason=None, metadata=None
            ),
            contains="_publish_transition_event",
        )
        self.assertEqual(len(mgr.publish_calls), 1)
        self.assertEqual(
            mgr.publish_calls[0]["topic"], "manager.state_machine.transition"
        )

    def test_handle_state_machine_rpc_alias_emits_and_delegates(self) -> None:
        mgr = _FakeManager()
        proc = _bare_state_machine(mgr)
        # Stub the minimum state needed by handle_state_machine_rpc's
        # process.stop branch (which returns rpc_ok without touching
        # capability machinery).
        import threading

        proc._stop_evt = threading.Event()
        proc._rpc_namespace = "alias-test"
        req = {"request_id": "r", "type": "process.stop"}
        new_resp = proc.handle_state_machine_rpc(req)
        old_resp = self._assert_emits(
            lambda: proc._handle_state_machine_rpc(req),
            contains="_handle_state_machine_rpc",
        )
        self.assertEqual(old_resp, new_resp)


if __name__ == "__main__":
    unittest.main()
