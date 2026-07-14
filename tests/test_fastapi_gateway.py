# ruff: noqa: E402

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

import numpy as np
import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.fastapi.gateway import RouterRpcClient, StreamFrameHub, TelemetryHub
from experiment_control.utils.zmq_helpers import json_dumps, safe_json_loads


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


class _RecordReaderStub:
    class _Layout:
        dtype = np.dtype([("sample_seq", "u8"), ("frequency_hz", "f8")])

    layout = _Layout()


class _FrameReaderStub:
    class _Layout:
        dtype = np.dtype("int16")
        shape = (5, 12)

    layout = _Layout()


def _record_event(seq: int, frequency_hz: float) -> dict[str, object]:
    dtype = _RecordReaderStub.layout.dtype
    record = np.asarray((seq, frequency_hz), dtype=dtype).reshape(())
    return {
        "seq": seq,
        "t0_mono_ns": seq * 10,
        "t0_wall_ns": seq * 100,
        "payload": record.tobytes(),
    }


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
        loop = asyncio.new_event_loop()
        client = RouterRpcClient("tcp://127.0.0.1:1", timeout_ms=20, queue_max=8)
        try:
            client.start(loop)
            self.assertTrue(
                self._wait_until(
                    lambda: bool(client._thread is not None and client._thread.is_alive())
                )
            )
            client.close()
            client.start(loop)
            self.assertTrue(
                self._wait_until(
                    lambda: bool(client._thread is not None and client._thread.is_alive())
                )
            )
        finally:
            client.close()
            loop.close()

    def test_router_rpc_client_rejects_when_queue_full(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            client = RouterRpcClient("tcp://127.0.0.1:1", queue_max=1)
            client._loop = loop
            client._thread = _AliveThreadStub()  # type: ignore[assignment]
            fut: asyncio.Future = loop.create_future()
            client._queue.put_nowait(({"type": "already-queued"}, None, fut))
            resp = loop.run_until_complete(client.request({"type": "new-request"}))
            self.assertFalse(bool(resp.get("ok")))
            err = resp.get("error", {})
            self.assertEqual(err.get("code"), "gateway_busy")
            self.assertEqual(int(client.stats().get("queue_rejected", 0)), 1)
        finally:
            loop.close()

    def test_router_rpc_client_close_resolves_pending_queue(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            client = RouterRpcClient("tcp://127.0.0.1:1", queue_max=4)
            client._loop = loop
            client._thread = _StoppedThreadStub()  # type: ignore[assignment]
            fut: asyncio.Future = loop.create_future()
            client._queue.put_nowait(({"type": "queued"}, None, fut))
            client.close()
            # close() schedules fut.set_result via call_soon_threadsafe; run
            # the loop once so the callback fires before we assert.
            loop.run_until_complete(asyncio.sleep(0))
            self.assertTrue(fut.done())
            result = fut.result()
            self.assertFalse(bool(result.get("ok")))
            err = result.get("error", {})
            self.assertEqual(err.get("code"), "gateway_closed")
            self.assertEqual(int(client.stats().get("closed_pending", 0)), 1)
        finally:
            loop.close()

    def test_router_rpc_client_pipelines_concurrent_requests(self) -> None:
        """End-to-end: 10 concurrent requests against a delay-echo ROUTER
        should complete in ~delay time, not ~10*delay (the bug we're
        fixing). Each reply echoes back the inbound `request_id` so the
        client can correlate replies to futures.
        """
        DELAY_MS = 100
        N = 10

        ctx = zmq.Context.instance()
        router_sock = ctx.socket(zmq.ROUTER)
        # OS-assigned port
        port = router_sock.bind_to_random_port("tcp://127.0.0.1")
        endpoint = f"tcp://127.0.0.1:{port}"
        stop_evt = threading.Event()

        def server() -> None:
            poller = zmq.Poller()
            poller.register(router_sock, zmq.POLLIN)
            # All replies are scheduled at start_t + DELAY_MS so the run
            # really is concurrent (not serial).
            pending: list[tuple[float, bytes, dict]] = []
            while not stop_evt.is_set():
                events = dict(poller.poll(10))
                if router_sock in events:
                    while True:
                        try:
                            ident, payload = router_sock.recv_multipart(zmq.NOBLOCK)
                        except zmq.Again:
                            break
                        req = safe_json_loads(payload)
                        if isinstance(req, dict):
                            reply = {"ok": True, "request_id": req.get("request_id")}
                            pending.append(
                                (time.monotonic() + DELAY_MS / 1000.0, ident, reply)
                            )
                now = time.monotonic()
                still: list[tuple[float, bytes, dict]] = []
                for due, ident, reply in pending:
                    if now >= due:
                        try:
                            router_sock.send_multipart([ident, json_dumps(reply)])
                        except Exception:
                            pass
                    else:
                        still.append((due, ident, reply))
                pending = still

        srv = threading.Thread(target=server, daemon=True)
        srv.start()

        loop = asyncio.new_event_loop()
        client = RouterRpcClient(endpoint, timeout_ms=5000, queue_max=64)
        try:
            client.start(loop)

            async def fire_all() -> list[dict]:
                return await asyncio.gather(
                    *[client.request({"type": "ping", "i": i}) for i in range(N)]
                )

            t0 = time.monotonic()
            results = loop.run_until_complete(fire_all())
            elapsed_s = time.monotonic() - t0
        finally:
            client.close()
            stop_evt.set()
            srv.join(timeout=2.0)
            try:
                router_sock.close(0)
            except Exception:
                pass
            loop.close()

        self.assertEqual(len(results), N)
        for r in results:
            self.assertTrue(bool(r.get("ok")), msg=str(r))
        # Sequential dispatch would take ~N * DELAY_MS = 1000 ms.
        # Pipelined should finish in ~DELAY_MS + small overhead.
        budget_s = (DELAY_MS * 3) / 1000.0
        self.assertLess(
            elapsed_s,
            budget_s,
            msg=f"pipelined N={N} took {elapsed_s:.3f}s, expected < {budget_s:.3f}s",
        )

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

    def test_telemetry_hub_fanout_shares_preserialized_string(self) -> None:
        """Hub serializes once and every subscriber receives the same str
        (round-tripped from JSON). This is the load-bearing invariant of
        the P1 optimization: N subscribers no longer each pay
        `json.dumps()`."""
        import json

        async def run() -> tuple[str, str, str]:
            hub = TelemetryHub("tcp://127.0.0.1:1", topics=("any",))
            hub._loop = asyncio.get_running_loop()  # noqa: SLF001
            q1 = hub.subscribe()
            q2 = hub.subscribe()
            hub._fanout('{"topic":"any","payload":{"k":1}}')  # noqa: SLF001
            payload1 = await asyncio.wait_for(q1.get(), timeout=1.0)
            payload2 = await asyncio.wait_for(q2.get(), timeout=1.0)
            return payload1, payload2, '{"topic":"any","payload":{"k":1}}'

        loop = asyncio.new_event_loop()
        try:
            payload1, payload2, expected = loop.run_until_complete(run())
        finally:
            loop.close()
        # Both subscribers received the exact same string instance —
        # confirms the hub didn't re-serialize per subscriber.
        self.assertIs(payload1, payload2)
        # And the string is the JSON we put in.
        self.assertEqual(json.loads(payload1), json.loads(expected))

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

    def test_stream_frame_cap_retains_private_shape_safe_source(self) -> None:
        hub = StreamFrameHub(
            "tcp://127.0.0.1:1",
            topics=("manager.chunk_ready",),
            max_payload_points=20,
        )
        source = np.arange(60, dtype=np.int16).reshape(5, 12)

        msg = hub._build_stream_frame(  # noqa: SLF001
            device_id="digitizer",
            stream="waveforms",
            reader=_FrameReaderStub(),  # type: ignore[arg-type]
            event={"seq": 1, "payload": source.tobytes()},
            context_id=None,
            context_fields=None,
        )

        self.assertIsNotNone(msg)
        assert msg is not None
        payload = msg["payload"]
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["shape"], [20])
        self.assertEqual(payload["original_shape"], [5, 12])
        self.assertEqual(payload["original_point_count"], 60)
        self.assertEqual(payload["max_payload_points"], 20)
        np.testing.assert_array_equal(payload["_source_values"], source)
        self.assertEqual(payload["_source_shape"], [5, 12])
        self.assertEqual(hub.stats()["payload_truncation_count"], 1)

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

    def test_stream_record_batches_preserve_per_record_context(self) -> None:
        hub = StreamFrameHub("tcp://127.0.0.1:1", topics=("manager.chunk_ready",))
        msg = hub._build_stream_records(  # noqa: SLF001
            device_id="hf_wavemeter",
            stream="frequency_records",
            reader=_RecordReaderStub(),  # type: ignore[arg-type]
            events=[
                (_record_event(1, 101.0), 10, {"channel": 1}),
                (_record_event(2, 202.0), 20, {"channel": 2}),
            ],
        )

        self.assertIsNotNone(msg)
        assert msg is not None
        payload = msg["payload"]
        self.assertEqual(payload["records"], [[1, 101.0], [2, 202.0]])
        self.assertEqual(payload["seqs"], [1, 2])
        self.assertEqual(payload["context_ids"], [10, 20])
        self.assertEqual(
            payload["context_fields_by_record"],
            [{"channel": 1}, {"channel": 2}],
        )
        self.assertNotIn("context_id", payload)
        self.assertNotIn("context_fields", payload)

    def test_stream_record_batches_keep_uniform_top_level_context(self) -> None:
        hub = StreamFrameHub("tcp://127.0.0.1:1", topics=("manager.chunk_ready",))
        msg = hub._build_stream_records(  # noqa: SLF001
            device_id="hf_wavemeter",
            stream="frequency_records",
            reader=_RecordReaderStub(),  # type: ignore[arg-type]
            events=[
                (_record_event(1, 101.0), 10, {"dwell_id": 4}),
                (_record_event(2, 202.0), 10, {"dwell_id": 4}),
            ],
        )

        self.assertIsNotNone(msg)
        assert msg is not None
        payload = msg["payload"]
        self.assertEqual(payload["context_ids"], [10, 10])
        self.assertEqual(payload["context_id"], 10)
        self.assertEqual(payload["context_fields"], {"dwell_id": 4})

    def test_stream_record_event_cap_keeps_newest_events(self) -> None:
        hub = StreamFrameHub(
            "tcp://127.0.0.1:1",
            topics=("manager.chunk_ready",),
            max_record_events=2,
        )

        capped, dropped = hub._cap_record_stream_events(  # noqa: SLF001
            [{"seq": 1}, {"seq": 2}, {"seq": 3}]
        )

        self.assertEqual(dropped, 1)
        self.assertEqual([item["seq"] for item in capped], [2, 3])


if __name__ == "__main__":
    unittest.main()
