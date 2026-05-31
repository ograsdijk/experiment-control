# ruff: noqa: E402

"""Tests for StateMachineProcessBase._command and SequenceError.

These tests instantiate the class via ``__new__`` and set the bare minimum
of attributes _command needs (``_manager``, ``_process_id``,
``_sequence_error_cls``, ``_last_command``). This mirrors how downstream
processes' unit tests construct bare instances and avoids spinning up the
real manager/poller/heartbeat machinery.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.state_machine_base import (
    SequenceError,
    StateMachineProcessBase,
)


class FakeManager:
    def __init__(self, response: dict | None) -> None:
        self.calls: list[dict] = []
        self.timeouts: list[int | None] = []
        self._response = response

    def call(self, req: dict, *, timeout_ms: int | None = None) -> dict | None:
        self.calls.append(dict(req))
        self.timeouts.append(timeout_ms)
        return self._response


def _bare_state_machine(
    manager: FakeManager | None,
    *,
    process_id: str = "test-proc",
    sequence_error_cls: type[Exception] = SequenceError,
) -> StateMachineProcessBase:
    proc = StateMachineProcessBase.__new__(StateMachineProcessBase)
    proc._manager = manager
    proc._process_id = process_id
    proc._sequence_error_cls = sequence_error_cls
    proc._last_command = None
    return proc


class CommandHappyPathTests(unittest.TestCase):
    def test_returns_response_dict_on_ok(self) -> None:
        mgr = FakeManager({"ok": True, "result": 42})
        proc = _bare_state_machine(mgr)
        resp = proc._command("dev1", "set_x", {"x": 5})
        self.assertEqual(resp, {"ok": True, "result": 42})

    def test_returns_response_dict_on_status_OK(self) -> None:
        mgr = FakeManager({"status": "OK", "value": 7})
        proc = _bare_state_machine(mgr)
        self.assertEqual(proc._command("dev1", "get_x", {}), {"status": "OK", "value": 7})

    def test_stores_request_on_last_command(self) -> None:
        mgr = FakeManager({"ok": True})
        proc = _bare_state_machine(mgr, process_id="my-proc")
        proc._command("dev1", "set_x", {"x": 5})
        self.assertEqual(
            proc._last_command,
            {
                "type": "command",
                "device_id": "dev1",
                "action": "set_x",
                "params": {"x": 5},
                "caller_process_id": "my-proc",
            },
        )

    def test_params_are_copied_not_referenced(self) -> None:
        # If _command stored the caller's params dict by reference, later
        # mutations would show up in _last_command — defending against that.
        mgr = FakeManager({"ok": True})
        proc = _bare_state_machine(mgr)
        params = {"x": 1}
        proc._command("dev1", "set_x", params)
        params["x"] = 99
        assert proc._last_command is not None
        self.assertEqual(proc._last_command["params"], {"x": 1})


class CommandTimeoutTests(unittest.TestCase):
    def test_none_timeout_passes_none(self) -> None:
        mgr = FakeManager({"ok": True})
        proc = _bare_state_machine(mgr)
        proc._command("d", "a", {}, timeout_s=None)
        self.assertEqual(mgr.timeouts, [None])

    def test_finite_timeout_converts_to_ms(self) -> None:
        mgr = FakeManager({"ok": True})
        proc = _bare_state_machine(mgr)
        proc._command("d", "a", {}, timeout_s=2.5)
        self.assertEqual(mgr.timeouts, [2500])

    def test_sub_ms_timeout_clamps_to_one(self) -> None:
        # max(1, int(...)) — 0.0001s would int-truncate to 0; clamp to 1.
        mgr = FakeManager({"ok": True})
        proc = _bare_state_machine(mgr)
        proc._command("d", "a", {}, timeout_s=0.0001)
        self.assertEqual(mgr.timeouts, [1])


class CommandFailureTests(unittest.TestCase):
    def test_manager_none_raises_sequence_error(self) -> None:
        proc = _bare_state_machine(None)
        with self.assertRaises(SequenceError) as ctx:
            proc._command("d", "a", {})
        self.assertIn("manager not initialized", str(ctx.exception))

    def test_failed_response_raises_sequence_error_with_payload(self) -> None:
        mgr = FakeManager({"ok": False, "error": "denied"})
        proc = _bare_state_machine(mgr)
        with self.assertRaises(SequenceError) as ctx:
            proc._command("d", "a", {})
        self.assertIn("command failed", str(ctx.exception))
        self.assertIn("'device_id': 'd'", str(ctx.exception))

    def test_status_ERROR_raises(self) -> None:
        mgr = FakeManager({"status": "ERROR"})
        proc = _bare_state_machine(mgr)
        with self.assertRaises(SequenceError):
            proc._command("d", "a", {})

    def test_non_dict_response_raises(self) -> None:
        mgr = FakeManager(None)
        proc = _bare_state_machine(mgr)
        with self.assertRaises(SequenceError):
            proc._command("d", "a", {})


class CustomSequenceErrorClassTests(unittest.TestCase):
    def test_subclass_can_swap_exception_type(self) -> None:
        class MyError(SequenceError):
            pass

        mgr = FakeManager({"ok": False})
        proc = _bare_state_machine(mgr, sequence_error_cls=MyError)
        with self.assertRaises(MyError):
            proc._command("d", "a", {})

    def test_custom_class_still_catchable_as_sequence_error(self) -> None:
        # Subclasses MUST inherit SequenceError so generic handlers keep working.
        class MyError(SequenceError):
            pass

        mgr = FakeManager({"ok": False})
        proc = _bare_state_machine(mgr, sequence_error_cls=MyError)
        try:
            proc._command("d", "a", {})
        except SequenceError:
            return
        self.fail("expected SequenceError (via MyError)")


if __name__ == "__main__":
    unittest.main()
