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


def _runner_with_socket(stream_actions: tuple[str, ...] = ()) -> tuple[Any, _FakeRpcSocket]:
    socket = _FakeRpcSocket()
    runner = types.SimpleNamespace(
        rpc=socket,
        _rpc_error=DeviceRunner._rpc_error,
        _stream_rpc={name: None for name in stream_actions},
    )
    return runner, socket


class DriverRpcReplyTests(unittest.TestCase):
    def test_numpy_scalars_are_sent_as_numbers(self) -> None:
        runner, socket = _runner_with_socket()
        DeviceRunner._send_rpc_reply(
            runner,
            {"id": 1, "status": "OK", "result": {"gain": np.float32(2.5), "n": np.int16(3)}},
            action="read_gain",
        )
        self.assertEqual(socket.sent, [{"id": 1, "status": "OK", "result": {"gain": 2.5, "n": 3}}])

    def test_array_result_is_refused_with_the_stream_command_hint(self) -> None:
        # Calling a stream method directly (acquire_trace) returns an array.
        # It used to kill the driver; bulk data belongs on the stream, so the
        # reply is an error pointing at stream__acquire_trace, not a JSON list.
        runner, socket = _runner_with_socket(("stream__acquire_trace",))
        DeviceRunner._send_rpc_reply(
            runner,
            {"id": 2, "status": "OK", "result": np.zeros(5000, dtype=np.int16)},
            action="acquire_trace",
        )
        self.assertEqual(len(socket.sent), 1)
        reply = socket.sent[0]
        self.assertEqual(reply["status"], "ERROR")
        self.assertEqual(reply["error_code"], "result_not_serializable")
        self.assertIn("(5000,)", reply["error"])
        self.assertIn("stream__acquire_trace", reply["error"])

    def test_unencodable_result_is_an_error_reply_not_a_crash(self) -> None:
        runner, socket = _runner_with_socket()
        DeviceRunner._send_rpc_reply(runner, {"id": 7, "status": "OK", "result": object()}, action="x")
        self.assertEqual(len(socket.sent), 1)
        reply = socket.sent[0]
        self.assertEqual(reply["id"], 7)
        self.assertEqual(reply["status"], "ERROR")
        self.assertEqual(reply["error_code"], "result_not_serializable")
        self.assertIn("object", reply["error"])
        self.assertNotIn("stream__", reply["error"])


if __name__ == "__main__":
    unittest.main()
