# ruff: noqa: E402

import asyncio
import concurrent.futures
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.fastapi.gateway import RouterRpcClient, StreamFrameHub, TelemetryHub


class _AliveThreadStub:
    def join(self, timeout: float | None = None) -> None:
        del timeout

    @staticmethod
    def is_alive() -> bool:
        return True


class _StoppedThreadStub:
    def join(self, timeout: float | None = None) -> None:
        del timeout

    @staticmethod
    def is_alive() -> bool:
        return False


class _ReaderStub:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class GatewayLifecycleTests(unittest.TestCase):
    @staticmethod
    def _wait_until(predicate, *, timeout_s: float = 2.0, sleep_s: float = 0.02) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(sleep_s)
        return bool(predicate())

    def test_router_rpc_client_restart_is_safe(self) -> None:
        client = RouterRpcClient("tcp://127.0.0.1:1", timeout_ms=20, queue_max=8)
        try:
            client.start()
            self.assertTrue(
                self._wait_until(
                    lambda: bool(client._thread is not None and client._thread.is_alive())
                )
            )
            client.close()
            client.start()
            self.assertTrue(
                self._wait_until(
                    lambda: bool(client._thread is not None and client._thread.is_alive())
                )
            )
        finally:
            client.close()

    def test_router_rpc_client_rejects_when_queue_full(self) -> None:
        client = RouterRpcClient("tcp://127.0.0.1:1", queue_max=1)
        client._thread = _AliveThreadStub()  # type: ignore[assignment]
        fut: concurrent.futures.Future = concurrent.futures.Future()
        client._queue.put_nowait(({"type": "already-queued"}, None, fut))
        resp = client.request({"type": "new-request"})
        self.assertFalse(bool(resp.get("ok")))
        err = resp.get("error", {})
        self.assertEqual(err.get("code"), "gateway_busy")
        self.assertEqual(int(client.stats().get("queue_rejected", 0)), 1)

    def test_router_rpc_client_close_resolves_pending_queue(self) -> None:
        client = RouterRpcClient("tcp://127.0.0.1:1", queue_max=4)
        client._thread = _StoppedThreadStub()  # type: ignore[assignment]
        fut: concurrent.futures.Future = concurrent.futures.Future()
        client._queue.put_nowait(({"type": "queued"}, None, fut))
        client.close()
        self.assertTrue(fut.done())
        result = fut.result()
        self.assertFalse(bool(result.get("ok")))
        err = result.get("error", {})
        self.assertEqual(err.get("code"), "gateway_closed")
        self.assertEqual(int(client.stats().get("closed_pending", 0)), 1)

    def test_telemetry_hub_restart_is_safe(self) -> None:
        loop = asyncio.new_event_loop()
        hub = TelemetryHub("tcp://127.0.0.1:1", topics=("manager.telemetry_update",))
        try:
            hub.start(loop)
            self.assertTrue(
                self._wait_until(lambda: bool(hub._thread is not None and hub._thread.is_alive()))
            )
            hub.close()
            hub.start(loop)
            self.assertTrue(
                self._wait_until(lambda: bool(hub._thread is not None and hub._thread.is_alive()))
            )
        finally:
            hub.close()
            loop.close()

    def test_stream_frame_hub_restart_is_safe(self) -> None:
        loop = asyncio.new_event_loop()
        hub = StreamFrameHub("tcp://127.0.0.1:1", topics=("manager.chunk_ready",))
        try:
            hub.start(loop)
            self.assertTrue(
                self._wait_until(lambda: bool(hub._thread is not None and hub._thread.is_alive()))
            )
            hub.close()
            hub.start(loop)
            self.assertTrue(
                self._wait_until(lambda: bool(hub._thread is not None and hub._thread.is_alive()))
            )
        finally:
            hub.close()
            loop.close()

    def test_stream_frame_hub_prunes_keys_by_capacity(self) -> None:
        hub = StreamFrameHub(
            "tcp://127.0.0.1:1",
            topics=("manager.chunk_ready",),
            max_stream_keys=2,
            stream_key_ttl_s=600.0,
        )
        keys = [("dev-1", "a"), ("dev-2", "b"), ("dev-3", "c")]
        readers = [_ReaderStub(), _ReaderStub(), _ReaderStub()]
        now = time.monotonic()
        for idx, key in enumerate(keys):
            hub._readers[key] = readers[idx]  # noqa: SLF001
            hub._last_seq[key] = idx  # noqa: SLF001
            hub._stream_context[key] = (None, None)  # noqa: SLF001
            hub._context_by_seq[key] = {}  # noqa: SLF001
            with hub._lock:  # noqa: SLF001
                hub._latest_frame[key] = {  # noqa: SLF001
                    "topic": "manager.stream_frame",
                    "payload": {"device_id": key[0], "stream": key[1]},
                }
            hub._touch_stream_key(key, now_mono=now + idx)  # noqa: SLF001
        hub._prune_stream_keys(now_mono=now + 10.0)  # noqa: SLF001

        self.assertEqual(len(hub._stream_key_order), 2)  # noqa: SLF001
        self.assertNotIn(("dev-1", "a"), hub._readers)  # noqa: SLF001
        self.assertTrue(readers[0].closed)
        self.assertEqual(int(hub.stats().get("dropped_stream_keys_capacity", 0)), 1)

    def test_stream_frame_hub_prunes_keys_by_ttl(self) -> None:
        hub = StreamFrameHub(
            "tcp://127.0.0.1:1",
            topics=("manager.chunk_ready",),
            max_stream_keys=10,
            stream_key_ttl_s=1.0,
        )
        key = ("dev-1", "a")
        reader = _ReaderStub()
        hub._readers[key] = reader  # noqa: SLF001
        hub._last_seq[key] = 1  # noqa: SLF001
        hub._stream_context[key] = (None, None)  # noqa: SLF001
        hub._context_by_seq[key] = {}  # noqa: SLF001
        with hub._lock:  # noqa: SLF001
            hub._latest_frame[key] = {  # noqa: SLF001
                "topic": "manager.stream_frame",
                "payload": {"device_id": key[0], "stream": key[1]},
            }
        now = time.monotonic()
        hub._stream_key_last_seen[key] = now - 5.0  # noqa: SLF001
        hub._stream_key_order[key] = None  # noqa: SLF001

        hub._prune_stream_keys(now_mono=now)  # noqa: SLF001
        self.assertNotIn(key, hub._readers)  # noqa: SLF001
        self.assertTrue(reader.closed)
        self.assertEqual(int(hub.stats().get("dropped_stream_keys_ttl", 0)), 1)


if __name__ == "__main__":
    unittest.main()
