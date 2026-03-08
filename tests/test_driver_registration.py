# ruff: noqa: E402

import sys
import unittest
from pathlib import Path
from unittest import mock

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import DeviceRunner


class _FakeSocket:
    def __init__(self, outcome):
        self._outcome = outcome
        self.options: dict[int, int] = {}
        self.connected: list[str] = []
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def setsockopt(self, option: int, value: int) -> None:
        self.options[int(option)] = int(value)

    def connect(self, endpoint: str) -> None:
        self.connected.append(str(endpoint))

    def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(dict(payload))

    def recv_json(self) -> dict[str, object]:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        if callable(self._outcome):
            value = self._outcome()
            if isinstance(value, Exception):
                raise value
            return dict(value)
        return dict(self._outcome)

    def close(self, linger: int = 0) -> None:
        del linger
        self.closed = True


class _FakeContext:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.sockets: list[_FakeSocket] = []

    def socket(self, kind: int) -> _FakeSocket:
        self_kind = int(kind)
        del self_kind
        if self._outcomes:
            outcome = self._outcomes.pop(0)
        else:
            outcome = {"ok": True}
        sock = _FakeSocket(outcome)
        self.sockets.append(sock)
        return sock


class DriverRegistrationTests(unittest.TestCase):
    @staticmethod
    def _runner(
        *,
        ctx: _FakeContext,
        timeout_ms: int = 321,
        retries: int = 3,
        retry_delay_s: float = 0.01,
    ) -> DeviceRunner:
        runner = object.__new__(DeviceRunner)
        runner.ctx = ctx  # type: ignore[attr-defined]
        runner.registry_endpoint = "tcp://127.0.0.1:6002"  # type: ignore[attr-defined]
        runner.device_id = "trace1"  # type: ignore[attr-defined]
        runner.rpc_endpoint = "tcp://127.0.0.1:7001"  # type: ignore[attr-defined]
        runner.pub_endpoint = "tcp://127.0.0.1:7002"  # type: ignore[attr-defined]
        runner._register_timeout_ms = int(timeout_ms)  # type: ignore[attr-defined]
        runner._register_retries = int(retries)  # type: ignore[attr-defined]
        runner._register_retry_delay_s = float(retry_delay_s)  # type: ignore[attr-defined]
        runner.capabilities = lambda: {"version": 1, "members": []}  # type: ignore[attr-defined]
        return runner  # type: ignore[return-value]

    def test_register_with_manager_retries_then_succeeds(self) -> None:
        ctx = _FakeContext([zmq.Again(), zmq.Again(), {"ok": True}])
        runner = self._runner(ctx=ctx, retries=3, retry_delay_s=0.05)
        with mock.patch("experiment_control.driver.time.sleep") as sleep_mock:
            DeviceRunner.register_with_manager(runner)
        self.assertEqual(len(ctx.sockets), 3)
        self.assertEqual(sleep_mock.call_count, 2)
        for sock in ctx.sockets:
            self.assertEqual(sock.options.get(zmq.SNDTIMEO), 321)
            self.assertEqual(sock.options.get(zmq.RCVTIMEO), 321)
            self.assertEqual(sock.options.get(zmq.LINGER), 0)
            self.assertTrue(sock.closed)
            self.assertEqual(sock.connected, ["tcp://127.0.0.1:6002"])
            self.assertEqual(len(sock.sent), 1)

    def test_register_with_manager_raises_after_retries(self) -> None:
        ctx = _FakeContext([zmq.Again(), zmq.Again()])
        runner = self._runner(ctx=ctx, retries=2, retry_delay_s=0.0)
        with self.assertRaises(RuntimeError) as cm:
            DeviceRunner.register_with_manager(runner)
        self.assertIn("failed after 2 attempts", str(cm.exception))
        self.assertIsInstance(cm.exception.__cause__, Exception)
        self.assertEqual(len(ctx.sockets), 2)


if __name__ == "__main__":
    unittest.main()
