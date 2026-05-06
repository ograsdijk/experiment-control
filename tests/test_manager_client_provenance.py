# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager_client import ManagerClient
from experiment_control.utils.zmq_helpers import json_loads


class _Socket:
    def __init__(self) -> None:
        self.sent: dict | None = None

    def connect(self, _endpoint: str) -> None:
        pass

    def setsockopt(self, *_args) -> None:
        pass

    def send(self, payload: str) -> None:
        self.sent = json_loads(payload)

    def recv(self) -> str:
        return '{"ok": true}'


class _Context:
    def __init__(self, socket: _Socket) -> None:
        self.socket_obj = socket

    def socket(self, _kind):
        return self.socket_obj


class ManagerClientProvenanceTests(unittest.TestCase):
    def test_process_command_backfills_provenance(self) -> None:
        socket = _Socket()
        client = ManagerClient(
            ctx=_Context(socket),
            manager_rpc="tcp://127.0.0.1:1",
            manager_pub="tcp://127.0.0.1:2",
            rpc_timeout_ms=100,
            process_id="step_guard",
            subscribe_telemetry=False,
        )
        client.call({"type": "command", "device_id": "sg", "action": "set"})

        self.assertEqual(socket.sent["caller_process_id"], "step_guard")
        self.assertEqual(socket.sent["source_kind"], "process")
        self.assertEqual(socket.sent["source_id"], "step_guard")

    def test_process_command_preserves_explicit_provenance(self) -> None:
        socket = _Socket()
        client = ManagerClient(
            ctx=_Context(socket),
            manager_rpc="tcp://127.0.0.1:1",
            manager_pub="tcp://127.0.0.1:2",
            rpc_timeout_ms=100,
            process_id="step_guard",
            subscribe_telemetry=False,
        )
        client.call(
            {
                "type": "command",
                "device_id": "sg",
                "action": "set",
                "caller_process_id": "other",
                "source_kind": "custom",
                "source_id": "manual",
            }
        )

        self.assertEqual(socket.sent["caller_process_id"], "other")
        self.assertEqual(socket.sent["source_kind"], "custom")
        self.assertEqual(socket.sent["source_id"], "manual")


if __name__ == "__main__":
    unittest.main()
