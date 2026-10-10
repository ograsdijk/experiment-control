# ruff: noqa: E402

import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._driver.runner import DeviceRunner


class _FakeRpcSocket:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send_json(self, obj: Any, **kwargs: Any) -> None:
        # Same contract as zmq: serialize first, raise before sending.
        self.sent.append(json.loads(json.dumps(obj, **kwargs)))


def _runner_with_socket() -> tuple[Any, _FakeRpcSocket]:
    socket = _FakeRpcSocket()
    runner = types.SimpleNamespace(rpc=socket, _rpc_error=DeviceRunner._rpc_error)
    return runner, socket


class DriverRpcReplyTests(unittest.TestCase):
    def test_numpy_results_are_sent_as_plain_json(self) -> None:
        # e.g. a stream method like acquire_trace called as a command.
        runner, socket = _runner_with_socket()
        DeviceRunner._send_rpc_reply(
            runner,
            {
                "id": 1,
                "status": "OK",
                "result": {"trace": np.arange(3, dtype=np.int16), "gain": np.float32(2.5)},
            },
        )
        self.assertEqual(socket.sent, [{"id": 1, "status": "OK", "result": {"trace": [0, 1, 2], "gain": 2.5}}])

    def test_unencodable_result_is_an_error_reply_not_a_crash(self) -> None:
        # This used to raise inside the driver's main loop and kill the driver.
        runner, socket = _runner_with_socket()
        DeviceRunner._send_rpc_reply(runner, {"id": 7, "status": "OK", "result": object()})
        self.assertEqual(len(socket.sent), 1)
        reply = socket.sent[0]
        self.assertEqual(reply["id"], 7)
        self.assertEqual(reply["status"], "ERROR")
        self.assertEqual(reply["error_code"], "result_not_serializable")
        self.assertIn("object", reply["error"])


if __name__ == "__main__":
    unittest.main()
