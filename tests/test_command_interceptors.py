# ruff: noqa: E402

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.utils.command_interceptors import (
    apply_command_interceptor_chain,
)


@dataclass(frozen=True)
class _Route:
    process_id: str


class CommandInterceptorEngineTests(unittest.TestCase):
    def test_missing_route_process_id_returns_bad_route(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        ok, cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {"x": 1}},
            chain=[_Route("")],
            request_id="req-0",
            caller_process_id="seq",
            is_route_available=lambda _pid: True,
            call_interceptor=lambda _pid, _req: ("ok", {"allow": True}, None),
            publish_event=lambda topic, payload: events.append((topic, payload)),
        )
        self.assertFalse(ok)
        self.assertIsNone(cmd)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "INTERCEPTOR_BAD_ROUTE")
        self.assertEqual(len(events), 1)

    def test_unavailable_route_returns_error(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        ok, cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {"x": 1}},
            chain=[_Route("interlock")],
            request_id="req-1",
            caller_process_id="seq",
            is_route_available=lambda _pid: False,
            call_interceptor=lambda _pid, _req: ("ok", {"allow": True}, None),
            publish_event=lambda topic, payload: events.append((topic, payload)),
        )
        self.assertFalse(ok)
        self.assertIsNone(cmd)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "INTERCEPTOR_UNAVAILABLE")
        self.assertEqual(len(events), 1)

    def test_timeout_returns_timeout_error(self) -> None:
        ok, _cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {}},
            chain=[_Route("interlock")],
            request_id=None,
            caller_process_id=None,
            is_route_available=lambda _pid: True,
            call_interceptor=lambda _pid, _req: ("timeout", None, None),
            publish_event=lambda _topic, _payload: None,
        )
        self.assertFalse(ok)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "INTERCEPTOR_TIMEOUT")

    def test_allow_true_can_modify_params(self) -> None:
        modified: list[tuple[str, dict[str, object]]] = []

        def _call(_pid: str, _req: dict[str, object]):
            return (
                "ok",
                {
                    "ok": True,
                    "allow": True,
                    "command": {
                        "device_id": "trace1",
                        "action": "set",
                        "params": {"x": 2},
                    },
                    "interceptor_id": "r1",
                    "rule": "rule-a",
                },
                None,
            )

        ok, cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {"x": 1}},
            chain=[_Route("interlock")],
            request_id="req-1",
            caller_process_id="seq",
            is_route_available=lambda _pid: True,
            call_interceptor=_call,
            publish_event=lambda topic, payload: modified.append((topic, payload)),
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(cmd, {"device_id": "trace1", "action": "set", "params": {"x": 2}})
        self.assertTrue(any(topic == "manager.command_interceptor.modified" for topic, _ in modified))

    def test_allow_false_returns_rejected_error(self) -> None:
        ok, _cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {}},
            chain=[_Route("interlock")],
            request_id="req-2",
            caller_process_id=None,
            is_route_available=lambda _pid: True,
            call_interceptor=lambda _pid, _req: (
                "ok",
                {
                    "ok": True,
                    "allow": False,
                    "interceptor_id": "id-a",
                    "rule": "rule-b",
                    "error": {"code": "LOW_TEMP", "message": "too cold"},
                },
                None,
            ),
            publish_event=lambda _topic, _payload: None,
        )
        self.assertFalse(ok)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "INTERCEPTOR_REJECTED")
        self.assertEqual(err.get("interceptor_id"), "id-a")

    def test_distinct_ok_false_message(self) -> None:
        ok, _cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {}},
            chain=[_Route("interlock")],
            request_id=None,
            caller_process_id=None,
            is_route_available=lambda _pid: True,
            call_interceptor=lambda _pid, _req: ("ok", {"ok": False}, None),
            publish_event=lambda _topic, _payload: None,
            distinct_ok_false_message=True,
        )
        self.assertFalse(ok)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertIn("error response", str(err.get("message", "")))

    def test_allow_true_cannot_change_route(self) -> None:
        ok, cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {"x": 1}},
            chain=[_Route("interlock")],
            request_id="req-3",
            caller_process_id=None,
            is_route_available=lambda _pid: True,
            call_interceptor=lambda _pid, _req: (
                "ok",
                {
                    "ok": True,
                    "allow": True,
                    "command": {
                        "device_id": "trace2",
                        "action": "set",
                        "params": {"x": 2},
                    },
                },
                None,
            ),
            publish_event=lambda _topic, _payload: None,
        )
        self.assertFalse(ok)
        self.assertIsNone(cmd)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "INTERCEPTOR_BAD_RESPONSE")

    def test_invalid_allow_value_returns_bad_response(self) -> None:
        ok, cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {"x": 1}},
            chain=[_Route("interlock")],
            request_id="req-4",
            caller_process_id=None,
            is_route_available=lambda _pid: True,
            call_interceptor=lambda _pid, _req: ("ok", {"ok": True, "allow": "maybe"}, None),
            publish_event=lambda _topic, _payload: None,
        )
        self.assertFalse(ok)
        self.assertIsNone(cmd)
        self.assertIsInstance(err, dict)
        assert isinstance(err, dict)
        self.assertEqual(err.get("code"), "INTERCEPTOR_BAD_RESPONSE")

    def test_multiple_interceptors_compose_transforms(self) -> None:
        def _call(pid: str, req: dict[str, object]):
            cmd = req.get("command")
            assert isinstance(cmd, dict)
            params = cmd.get("params")
            assert isinstance(params, dict)
            x = int(params.get("x", 0))
            if pid == "a":
                next_x = x + 1
            elif pid == "b":
                next_x = x * 2
            else:
                next_x = x
            return (
                "ok",
                {
                    "ok": True,
                    "allow": True,
                    "command": {
                        "device_id": "trace1",
                        "action": "set",
                        "params": {"x": next_x},
                    },
                },
                None,
            )

        ok, cmd, err = apply_command_interceptor_chain(
            initial_command={"device_id": "trace1", "action": "set", "params": {"x": 3}},
            chain=[_Route("a"), _Route("b")],
            request_id="req-5",
            caller_process_id=None,
            is_route_available=lambda _pid: True,
            call_interceptor=_call,
            publish_event=lambda _topic, _payload: None,
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(cmd, {"device_id": "trace1", "action": "set", "params": {"x": 8}})


if __name__ == "__main__":
    unittest.main()
