from __future__ import annotations

import asyncio
import time
import math
import queue
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np
import zmq

from ..shm.shm_ring import ShmRingReader
from ..utils.zmq_helpers import json_dumps, safe_json_loads


@dataclass(frozen=True)
class GatewaySettings:
    router_rpc: str
    manager_pub: str
    instance_id: str | None = None
    router_rpc_public_hint: str | None = None
    manager_pub_public_hint: str | None = None
    rpc_timeout_ms: int = 2000
    rpc_queue_max: int = 1024
    stream_max_payload_points: int = 200_000
    stream_max_record_events: int = 512
    stream_max_keys: int = 1024
    stream_key_ttl_s: float = 600.0
    telemetry_topics: tuple[str, ...] = ("manager.telemetry_update",)
    log_topics: tuple[str, ...] = ("manager.log",)
    stream_topics: tuple[str, ...] = ("manager.chunk_ready",)
    stream_analysis_topics: tuple[str, ...] = (
        "manager.stream_analysis.output",
        "manager.stream_analysis.trace_ready",
        "manager.stream_analysis.workspace_status",
        "manager.stream_analysis.error",
    )


class RouterRpcClient:
    """Async-native pipelined RPC client over a single DEALER socket.

    Requests are dispatched to the router (DeviceRouter's `external_rpc`
    ROUTER bind) immediately on submission; replies are matched back to
    callers by `request_id`. Multiple in-flight requests can be
    outstanding concurrently — the only serialisation is at the wire
    level (single socket), not at the request level, so N parallel
    HTTP handlers no longer wait in line for the previous reply.

    Callers use `await router.request(payload)` from asyncio code; the
    coroutine returns when the matching reply arrives, when the
    per-request deadline expires (`gateway_timeout`), or when the
    client shuts down (`gateway_closed`).
    """

    def __init__(
        self, endpoint: str, *, timeout_ms: int = 2000, queue_max: int = 1024
    ) -> None:
        self._endpoint = endpoint
        self._timeout_ms = int(timeout_ms)
        self._ctx = zmq.Context.instance()
        self._queue_max = max(1, int(queue_max))
        # Tuple shape kept intentionally compatible with prior tests that
        # `put_nowait` directly; only the future type changed.
        self._queue: queue.Queue[
            tuple[dict[str, Any], int | None, "asyncio.Future[dict]"]
        ] = queue.Queue(maxsize=self._queue_max)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._queue_rejected = 0
        self._closed_pending = 0
        self._inflight_depth = 0

    @staticmethod
    def _error(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict:
        err: dict[str, Any] = {"code": code, "message": message}
        if details:
            err["details"] = details
        return {"ok": False, "error": err}

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the dispatcher thread. `loop` is captured so the worker
        can schedule asyncio future resolution from its thread via
        `loop.call_soon_threadsafe` — same pattern as TelemetryHub."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._loop = loop
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="router-rpc", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        drained = 0
        while True:
            try:
                _payload, _timeout_ms, fut = self._queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            self._set_future_result(
                fut,
                self._error(
                    "gateway_closed",
                    "router rpc client closed before request was processed",
                ),
            )
        if drained:
            with self._lock:
                self._closed_pending += drained
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if not thread.is_alive():
                self._thread = None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            queue_rejected = int(self._queue_rejected)
            closed_pending = int(self._closed_pending)
            inflight_depth = int(self._inflight_depth)
        return {
            "queue_depth": int(self._queue.qsize()),
            "queue_max": int(self._queue_max),
            "queue_rejected": queue_rejected,
            "closed_pending": closed_pending,
            "inflight_depth": inflight_depth,
            "thread_alive": bool(self._thread is not None and self._thread.is_alive()),
        }

    async def request(
        self, payload: dict[str, Any], timeout_ms: int | None = None
    ) -> dict:
        if self._thread is None or not self._thread.is_alive() or self._loop is None:
            raise RuntimeError("RouterRpcClient not started")
        # Ensure every request carries a request_id so the worker can
        # correlate the reply back. DeviceRouter echoes whatever id we
        # send (see processes/device_router.py `_inject_request_id`).
        if "request_id" not in payload:
            payload = dict(payload)
            payload["request_id"] = uuid.uuid4().hex
        fut: asyncio.Future[dict] = self._loop.create_future()
        try:
            self._queue.put_nowait((payload, timeout_ms, fut))
        except queue.Full:
            with self._lock:
                self._queue_rejected += 1
            return self._error(
                "gateway_busy",
                "router rpc queue is full",
                details={
                    "queue_depth": int(self._queue.qsize()),
                    "queue_max": int(self._queue_max),
                },
            )
        # Worker is the source of truth for timeouts (resolves the
        # future with `gateway_timeout` on expiry). `asyncio.wait_for`
        # here is a belt-and-suspenders safety net in case the worker
        # stalls beyond the deadline.
        safety_s = (timeout_ms or self._timeout_ms) / 1000.0 + 1.0
        try:
            return await asyncio.wait_for(fut, timeout=safety_s)
        except asyncio.TimeoutError:
            return self._error("gateway_timeout", "router rpc timed out")

    def _set_future_result(self, fut: "asyncio.Future[dict]", result: dict) -> None:
        loop = self._loop
        if loop is None or fut.done():
            return

        def _setter() -> None:
            if not fut.done():
                fut.set_result(result)

        try:
            loop.call_soon_threadsafe(_setter)
        except RuntimeError:
            # Loop is closed; nothing we can do. The asyncio.wait_for
            # in `request()` will surface a timeout to the caller.
            pass

    def _drain_replies(
        self,
        sock: zmq.Socket,
        pending: dict[Any, tuple["asyncio.Future[dict]", float]],
    ) -> None:
        while True:
            try:
                raw = sock.recv(zmq.NOBLOCK)
            except zmq.Again:
                return
            resp = safe_json_loads(raw)
            if not isinstance(resp, dict):
                continue
            rid = resp.get("request_id")
            entry = pending.pop(rid, None) if rid is not None else None
            if entry is None:
                # Late or unknown reply: silently drop.
                continue
            fut, _deadline = entry
            self._set_future_result(fut, resp)

    def _sweep_expired(
        self, pending: dict[Any, tuple["asyncio.Future[dict]", float]]
    ) -> None:
        now = time.monotonic()
        expired = [rid for rid, (_, dl) in pending.items() if now > dl]
        for rid in expired:
            fut, _ = pending.pop(rid)
            self._set_future_result(
                fut, self._error("gateway_timeout", "router rpc timed out")
            )
        # Also drop entries whose caller has already cancelled (e.g. the
        # outer `asyncio.wait_for` safety net fired). Without this they
        # linger in `pending` until their own deadline elapses.
        cancelled = [rid for rid, (fut, _) in pending.items() if fut.done()]
        for rid in cancelled:
            pending.pop(rid, None)

    def _fail_all_inflight(
        self,
        pending: dict[Any, tuple["asyncio.Future[dict]", float]],
        *,
        reason: str,
    ) -> None:
        for rid in list(pending.keys()):
            fut, _ = pending.pop(rid)
            self._set_future_result(
                fut,
                {
                    "ok": False,
                    "error": {"code": "gateway_error", "message": reason},
                },
            )

    def _run(self) -> None:
        sock = self._ctx.socket(zmq.DEALER)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(self._endpoint)
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        pending: dict[Any, tuple["asyncio.Future[dict]", float]] = {}

        while not self._stop.is_set():
            # 1) Drain queue: send every pending submission immediately.
            while True:
                try:
                    payload, timeout_ms, fut = self._queue.get_nowait()
                except queue.Empty:
                    break
                rid = payload.get("request_id")
                eff_timeout_ms = int(timeout_ms or self._timeout_ms)
                deadline = time.monotonic() + (eff_timeout_ms / 1000.0)
                try:
                    sock.send(json_dumps(payload), zmq.NOBLOCK)
                except Exception as exc:
                    self._set_future_result(
                        fut,
                        {
                            "ok": False,
                            "error": {
                                "code": "gateway_error",
                                "message": str(exc),
                            },
                        },
                    )
                    continue
                if rid is None:
                    # Should not happen — `request()` always assigns one
                    # — but if it does, fail fast so we don't leak.
                    self._set_future_result(
                        fut,
                        self._error(
                            "gateway_error",
                            "internal: request submitted without request_id",
                        ),
                    )
                    continue
                pending[rid] = (fut, deadline)

            # Best-effort observability: read by stats() racily and may
            # be off-by-one under contention. Worker thread is the only
            # writer of `pending`, so no lock needed for the write.
            self._inflight_depth = len(pending)

            # 2) Poll for replies, bounded by the next deadline or 100 ms.
            now = time.monotonic()
            if pending:
                next_dl = min(d for _, d in pending.values())
                poll_ms = max(0, min(100, int((next_dl - now) * 1000)))
            else:
                poll_ms = 100

            try:
                events = dict(poller.poll(poll_ms))
            except zmq.ZMQError as exc:
                self._fail_all_inflight(pending, reason=str(exc))
                # Recreate the socket to recover from terminal errors.
                try:
                    sock.setsockopt(zmq.LINGER, 0)
                    sock.close(0)
                except Exception:
                    pass
                sock = self._ctx.socket(zmq.DEALER)
                sock.setsockopt(zmq.LINGER, 0)
                sock.connect(self._endpoint)
                poller = zmq.Poller()
                poller.register(sock, zmq.POLLIN)
                continue

            if sock in events:
                try:
                    self._drain_replies(sock, pending)
                except Exception as exc:
                    self._fail_all_inflight(pending, reason=str(exc))
                    try:
                        sock.setsockopt(zmq.LINGER, 0)
                        sock.close(0)
                    except Exception:
                        pass
                    sock = self._ctx.socket(zmq.DEALER)
                    sock.setsockopt(zmq.LINGER, 0)
                    sock.connect(self._endpoint)
                    poller = zmq.Poller()
                    poller.register(sock, zmq.POLLIN)
                    continue

            # 3) Time out any expired in-flight requests.
            self._sweep_expired(pending)

        # Shutdown: fail any still-in-flight callers.
        sock.setsockopt(zmq.LINGER, 0)
        sock.close(0)
        for fut, _ in pending.values():
            self._set_future_result(
                fut,
                self._error(
                    "gateway_closed",
                    "router rpc client closed with in-flight request",
                ),
            )
        with self._lock:
            self._inflight_depth = 0


class TelemetryHub:
    """ZMQ → WebSocket fan-out hub for simple topic broadcast streams.

    Items put on subscriber queues are **pre-serialized JSON strings**, not
    dicts. The hub serializes each incoming ZMQ payload exactly once in
    its background thread and shares the resulting string across every
    subscriber, so N WS clients no longer each pay `json.dumps()` on the
    same payload. WS handlers should consume via `ws.send_text(payload)`.
    """

    def __init__(self, endpoint: str, *, topics: tuple[str, ...]) -> None:
        self._endpoint = endpoint
        self._topics = topics
        self._ctx = zmq.Context.instance()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run, name="telemetry-hub", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if not self._thread.is_alive():
                self._thread = None
        self._loop = None

    def subscribe(self, *, maxsize: int = 100) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    def _fanout(self, payload: str) -> None:
        with self._lock:
            queues = list(self._queues)
        for q in queues:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _run(self) -> None:
        sub = self._ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        for topic in self._topics:
            sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
        sub.connect(self._endpoint)
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        while not self._stop.is_set():
            events = dict(poller.poll(100))
            if sub not in events:
                continue
            try:
                topic_b, payload_b = sub.recv_multipart()
            except Exception:
                continue
            payload = safe_json_loads(payload_b)
            payload = _sanitize_json(payload)
            msg = {
                "topic": topic_b.decode("utf-8"),
                "payload": payload,
            }
            # Serialize once in the hub thread; the resulting str is the
            # exact bytes every subscriber will send over its WebSocket.
            # N subscribers no longer each pay `json.dumps(msg)` per
            # payload.
            try:
                serialized = json_dumps(msg).decode("utf-8")
            except Exception:
                continue
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._fanout, serialized)
        sub.setsockopt(zmq.LINGER, 0)
        sub.close(0)


class StreamFrameHub:
    def __init__(
        self,
        endpoint: str,
        *,
        topics: tuple[str, ...] = ("manager.chunk_ready",),
        max_payload_points: int = 200_000,
        max_record_events: int = 512,
        max_stream_keys: int = 1024,
        stream_key_ttl_s: float = 600.0,
    ) -> None:
        self._endpoint = endpoint
        self._topics = topics
        self._max_payload_points = max(1, int(max_payload_points))
        self._max_record_events = max(1, int(max_record_events))
        self._max_stream_keys = max(1, int(max_stream_keys))
        self._stream_key_ttl_s = max(0.0, float(stream_key_ttl_s))
        self._ctx = zmq.Context.instance()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: dict[asyncio.Queue, tuple[str, str] | None] = {}
        self._lock = threading.Lock()
        self._readers: dict[tuple[str, str], ShmRingReader] = {}
        self._last_seq: dict[tuple[str, str], int] = {}
        self._stream_context: dict[
            tuple[str, str], tuple[int | None, dict[str, Any] | None]
        ] = {}
        # Per-stream context keyed by stream sequence number. Uses
        # OrderedDict so we can evict the lowest-seq entry in O(1) via
        # popitem(last=False), mirroring the B3 pattern in
        # `processes/stream_analysis.py`. With re-insertion-on-update
        # (see the setdefault site below) insertion order tracks
        # lowest-seq even when seqs arrive out-of-order from the
        # publisher (network re-order / replay / retry).
        self._context_by_seq: dict[
            tuple[str, str],
            "OrderedDict[int, tuple[int | None, dict[str, Any] | None]]",
        ] = {}
        self._context_cache_limit = 8192
        self._latest_frame: dict[tuple[str, str], dict[str, Any]] = {}
        self._stream_key_order: dict[tuple[str, str], None] = {}
        self._stream_key_last_seen: dict[tuple[str, str], float] = {}
        self._stream_key_dropped_capacity = 0
        self._stream_key_dropped_ttl = 0

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run, name="stream-frame-hub", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if not self._thread.is_alive():
                self._thread = None
        self._loop = None
        with self._lock:
            self._queues.clear()
        for reader in list(self._readers.values()):
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()
        self._last_seq.clear()
        self._stream_context.clear()
        self._context_by_seq.clear()
        self._latest_frame.clear()
        self._stream_key_order.clear()
        self._stream_key_last_seen.clear()

    def _drop_stream_key(self, key: tuple[str, str], *, reason: str) -> None:
        reader = self._readers.pop(key, None)
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
        self._last_seq.pop(key, None)
        self._stream_context.pop(key, None)
        self._context_by_seq.pop(key, None)
        with self._lock:
            self._latest_frame.pop(key, None)
        self._stream_key_order.pop(key, None)
        self._stream_key_last_seen.pop(key, None)
        if reason == "capacity":
            self._stream_key_dropped_capacity += 1
        elif reason == "ttl":
            self._stream_key_dropped_ttl += 1

    def _touch_stream_key(self, key: tuple[str, str], *, now_mono: float) -> None:
        self._stream_key_last_seen[key] = now_mono
        if key in self._stream_key_order:
            self._stream_key_order.pop(key, None)
        self._stream_key_order[key] = None

    def _prune_stream_keys(self, *, now_mono: float) -> None:
        if self._stream_key_ttl_s > 0.0:
            stale = [
                key
                for key, seen in self._stream_key_last_seen.items()
                if (now_mono - float(seen)) > self._stream_key_ttl_s
            ]
            for key in stale:
                self._drop_stream_key(key, reason="ttl")
        while len(self._stream_key_order) > self._max_stream_keys:
            oldest = next(iter(self._stream_key_order), None)
            if oldest is None:
                break
            self._drop_stream_key(oldest, reason="capacity")

    def subscribe(
        self,
        *,
        maxsize: int = 100,
        device_id: str | None = None,
        stream: str | None = None,
    ) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        filter_key: tuple[str, str] | None = None
        device_text = str(device_id or "").strip()
        stream_text = str(stream or "").strip()
        if device_text and stream_text:
            filter_key = (device_text, stream_text)
        with self._lock:
            self._queues[q] = filter_key
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.pop(q, None)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            latest_frame_count = int(len(self._latest_frame))
            subscriber_count = int(len(self._queues))
        return {
            "max_payload_points": int(self._max_payload_points),
            "max_record_events": int(self._max_record_events),
            "max_stream_keys": int(self._max_stream_keys),
            "stream_key_ttl_s": float(self._stream_key_ttl_s),
            "latest_frame_count": latest_frame_count,
            "subscriber_count": subscriber_count,
            "reader_count": int(len(self._readers)),
            "dropped_stream_keys_capacity": int(self._stream_key_dropped_capacity),
            "dropped_stream_keys_ttl": int(self._stream_key_dropped_ttl),
        }

    def get_latest_frame(self, *, device_id: str, stream: str) -> dict[str, Any] | None:
        key = (str(device_id).strip(), str(stream).strip())
        if not key[0] or not key[1]:
            return None
        with self._lock:
            latest = self._latest_frame.get(key)
            if latest is None:
                return None
            # PerfA: latest was stored with an already-sanitised payload
            # (see the snapshot writes in _run); a shallow copy of the
            # wrapper dict suffices.
            return dict(latest)

    def _fanout(self, msg: dict[str, Any]) -> None:
        payload = msg.get("payload")
        message_key: tuple[str, str] | None = None
        if isinstance(payload, dict):
            msg_device_id = str(payload.get("device_id") or "").strip()
            msg_stream = str(payload.get("stream") or "").strip()
            if msg_device_id and msg_stream:
                message_key = (msg_device_id, msg_stream)
        with self._lock:
            queue_items = list(self._queues.items())
        for q, filter_key in queue_items:
            if filter_key is not None and filter_key != message_key:
                continue
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    @staticmethod
    def _normalize_chunk_payload(raw: Any) -> tuple[str, str, str] | None:
        if not isinstance(raw, dict):
            return None
        device_id = str(raw.get("device_id") or "").strip()
        stream = str(raw.get("stream") or "").strip()
        shm_name = str(raw.get("shm_name") or "").strip()
        if not device_id or not stream or not shm_name:
            return None
        return device_id, stream, shm_name

    @staticmethod
    def _normalize_int(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    def _cap_record_stream_events(
        self, events: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        if len(events) <= self._max_record_events:
            return events, 0
        dropped = len(events) - self._max_record_events
        return events[-self._max_record_events :], dropped

    def _build_stream_frame(
        self,
        *,
        device_id: str,
        stream: str,
        reader: ShmRingReader,
        event: dict[str, Any],
        context_id: int | None,
        context_fields: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload_bytes = event.get("payload")
        if not isinstance(payload_bytes, (bytes, bytearray, memoryview)):
            return None
        dtype = reader.layout.dtype
        shape = tuple(int(x) for x in reader.layout.shape)
        try:
            arr = np.frombuffer(payload_bytes, dtype=dtype)
            arr = arr.reshape(shape)
        except Exception:
            return None

        values_arr: np.ndarray = arr
        truncated = False
        if values_arr.size > self._max_payload_points:
            values_arr = values_arr.reshape(-1)[: self._max_payload_points]
            truncated = True

        # Mirrors stream_analysis._build_trace_snapshot_payload: avoid
        # walking the values list through _sanitize_json on every frame.
        # `np.isfinite(...).all()` is one vectorised pass; the slow
        # per-element scrub only triggers when there's real NaN/Inf to
        # replace. Integer dtypes are always finite so they take the
        # fast path trivially.
        values = values_arr.tolist()
        try:
            needs_scrub = (
                values_arr.size > 0
                and values_arr.dtype.kind in ("f", "c")
                and not bool(np.isfinite(values_arr).all())
            )
        except (TypeError, ValueError):
            needs_scrub = True
        if needs_scrub:
            for i, x in enumerate(values):
                if isinstance(x, float) and not math.isfinite(x):
                    values[i] = None
        out: dict[str, Any] = {
            "version": 1,
            "device_id": device_id,
            "stream": stream,
            "seq": self._normalize_int(event.get("seq")),
            "t0_mono_ns": self._normalize_int(event.get("t0_mono_ns")),
            "t0_wall_ns": self._normalize_int(event.get("t0_wall_ns")),
            "dtype": str(dtype),
            "shape": (
                [int(x) for x in values_arr.shape]
                if truncated
                else [int(x) for x in shape]
            ),
            "values": values,
            "_binary_values": values_arr.astype(np.float64, copy=False),
        }
        if context_id is not None:
            out["context_id"] = int(context_id)
        if context_fields:
            out["context_fields"] = _sanitize_json(dict(context_fields))
        if truncated:
            out["truncated"] = True
        return {"topic": "manager.stream_frame", "payload": out}

    def _record_event_to_values(
        self,
        *,
        reader: ShmRingReader,
        event: dict[str, Any],
    ) -> list[Any] | None:
        payload_bytes = event.get("payload")
        if not isinstance(payload_bytes, (bytes, bytearray, memoryview)):
            return None
        dtype = reader.layout.dtype
        names = dtype.names or ()
        try:
            arr = np.frombuffer(payload_bytes, dtype=dtype).reshape(())
        except Exception:
            return None
        values: list[Any] = []
        for name in names:
            try:
                value = arr[name].item()
            except Exception:
                value = None
            values.append(_sanitize_json(value))
        return values

    def _build_stream_records(
        self,
        *,
        device_id: str,
        stream: str,
        reader: ShmRingReader,
        events: list[tuple[dict[str, Any], int | None, dict[str, Any] | None]],
        dropped_record_count: int = 0,
    ) -> dict[str, Any] | None:
        dtype = reader.layout.dtype
        names = list(dtype.names or ())
        if not names:
            return None
        records: list[list[Any]] = []
        seqs: list[int] = []
        t0_mono_ns: list[int | None] = []
        t0_wall_ns: list[int | None] = []
        context_ids: list[int | None] = []
        context_fields_by_record: list[dict[str, Any] | None] = []
        for event, context_id, context_fields in events:
            seq = self._normalize_int(event.get("seq"))
            if seq is None:
                continue
            values = self._record_event_to_values(reader=reader, event=event)
            if values is None:
                continue
            records.append(values)
            seqs.append(int(seq))
            t0_mono_ns.append(self._normalize_int(event.get("t0_mono_ns")))
            t0_wall_ns.append(self._normalize_int(event.get("t0_wall_ns")))
            context_ids.append(int(context_id) if context_id is not None else None)
            context_fields_by_record.append(
                _sanitize_json(dict(context_fields))
                if isinstance(context_fields, dict)
                else None
            )
        if not records:
            return None
        out: dict[str, Any] = {
            "version": 1,
            "device_id": device_id,
            "stream": stream,
            "stream_kind": "records",
            "fields": names,
            "records": records,
            "seqs": seqs,
            "t0_mono_ns": t0_mono_ns,
            "t0_wall_ns": t0_wall_ns,
            "context_ids": context_ids,
            "context_fields_by_record": context_fields_by_record,
            "dtype": str(dtype),
            "record_count": len(records),
        }
        if dropped_record_count > 0:
            out["truncated"] = True
            out["dropped_record_count"] = int(dropped_record_count)
        if (
            context_ids
            and context_ids[0] is not None
            and all(item == context_ids[0] for item in context_ids)
        ):
            out["context_id"] = int(context_ids[0])
        if (
            context_fields_by_record
            and context_fields_by_record[0]
            and all(item == context_fields_by_record[0] for item in context_fields_by_record)
        ):
            out["context_fields"] = dict(context_fields_by_record[0])
        return {"topic": "manager.stream_records", "payload": out}

    def _run(self) -> None:
        sub = self._ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        for topic in self._topics:
            sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
        sub.connect(self._endpoint)
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        while not self._stop.is_set():
            self._prune_stream_keys(now_mono=time.monotonic())
            events = dict(poller.poll(100))
            if sub not in events:
                continue
            try:
                _topic_b, payload_b = sub.recv_multipart()
            except Exception:
                continue

            payload = safe_json_loads(payload_b)
            normalized = self._normalize_chunk_payload(payload)
            if normalized is None:
                continue
            device_id, stream, shm_name = normalized
            key = (device_id, stream)
            now_mono = time.monotonic()
            self._touch_stream_key(key, now_mono=now_mono)
            self._prune_stream_keys(now_mono=now_mono)

            context_id: int | None = None
            context_fields: dict[str, Any] | None = None
            msg_seq: int | None = None
            if isinstance(payload, dict):
                msg_seq = self._normalize_int(payload.get("seq"))
                context_id_raw = payload.get("context_id")
                if context_id_raw is not None:
                    context_id = self._normalize_int(context_id_raw)
                fields = payload.get("context_fields")
                if isinstance(fields, dict):
                    context_fields = fields
            if msg_seq is not None:
                bucket = self._context_by_seq.setdefault(key, OrderedDict())
                seq_key = int(msg_seq)
                # If the seq already has an entry, drop it first so the
                # refreshed value lands at the end of the insertion
                # order. Without this, an out-of-order or replayed seq
                # would update in place and `popitem(last=False)` would
                # evict the wrong entry (matches the B3 sibling pattern
                # in processes/stream_analysis.py).
                if seq_key in bucket:
                    del bucket[seq_key]
                bucket[seq_key] = (
                    int(context_id) if context_id is not None else None,
                    dict(context_fields) if context_fields is not None else None,
                )
                # O(1) drop-oldest via OrderedDict; insertion order now
                # tracks lowest-seq even under out-of-order arrival, so
                # we never accidentally evict a still-needed recent seq.
                while len(bucket) > self._context_cache_limit:
                    bucket.popitem(last=False)

            reader = self._readers.get(key)
            if reader is None or reader.name != shm_name:
                if reader is not None:
                    try:
                        reader.close()
                    except Exception:
                        pass
                try:
                    reader = ShmRingReader.attach(shm_name)
                except Exception:
                    self._drop_stream_key(key, reason="error")
                    continue
                self._readers[key] = reader
                self._last_seq[key] = 0
                self._stream_context.pop(key, None)
                self._context_by_seq.pop(key, None)

            last_seq = int(self._last_seq.get(key, 0))
            try:
                stream_events_all = reader.read_events(last_seq)
            except Exception:
                self._drop_stream_key(key, reason="error")
                continue
            if msg_seq is None:
                stream_events = stream_events_all
            else:
                stream_events = []
                for event in stream_events_all:
                    seq = self._normalize_int(event.get("seq"))
                    if seq is None:
                        continue
                    if seq <= msg_seq:
                        stream_events.append(event)
            if not stream_events:
                context_bucket = self._context_by_seq.get(key)
                if context_bucket is not None:
                    stale = [seq for seq in context_bucket.keys() if int(seq) <= int(last_seq)]
                    for seq in stale:
                        context_bucket.pop(seq, None)
                    if not context_bucket:
                        self._context_by_seq.pop(key, None)
                continue

            is_record_stream = reader.layout.dtype.fields is not None

            # Avoid flooding websocket clients after reconnect/attach for frame streams.
            record_events_dropped = 0
            if is_record_stream:
                stream_events, record_events_dropped = self._cap_record_stream_events(
                    stream_events
                )
            elif len(stream_events) > 4:
                stream_events = stream_events[-4:]

            current_context_id, current_context_fields = self._stream_context.get(
                key, (None, None)
            )
            if current_context_fields is not None:
                current_context_fields = dict(current_context_fields)

            latest_seq = last_seq
            record_batch_events: list[
                tuple[dict[str, Any], int | None, dict[str, Any] | None]
            ] = []
            for event in stream_events:
                seq_raw = self._normalize_int(event.get("seq"))
                if seq_raw is None:
                    continue
                latest_seq = max(latest_seq, seq_raw)
                event_context_id: int | None = None
                event_context_fields: dict[str, Any] | None = None
                seq_context_bucket = self._context_by_seq.get(key)
                if seq_context_bucket is not None:
                    item = seq_context_bucket.pop(int(seq_raw), None)
                    if item is not None:
                        event_context_id, event_context_fields = item
                    if not seq_context_bucket:
                        self._context_by_seq.pop(key, None)
                if (
                    event_context_id is None
                    and event_context_fields is None
                    and msg_seq is not None
                    and seq_raw == msg_seq
                ):
                    event_context_id = context_id
                    event_context_fields = context_fields
                if event_context_id is None and event_context_fields is None:
                    event_context_id = current_context_id
                    event_context_fields = current_context_fields
                else:
                    current_context_id = event_context_id
                    current_context_fields = (
                        dict(event_context_fields)
                        if isinstance(event_context_fields, dict)
                        else None
                    )
                if is_record_stream:
                    record_batch_events.append(
                        (
                            event,
                            event_context_id,
                            dict(event_context_fields)
                            if isinstance(event_context_fields, dict)
                            else None,
                        )
                    )
                    continue
                msg = self._build_stream_frame(
                    device_id=device_id,
                    stream=stream,
                    reader=reader,
                    event=event,
                    context_id=event_context_id,
                    context_fields=event_context_fields,
                )
                if msg is not None and self._loop is not None:
                    payload_obj = msg.get("payload")
                    if isinstance(payload_obj, dict):
                        # PerfA: payload was already sanitised in
                        # `_build_stream_frame` (values via line 541,
                        # context_fields via line 560). A shallow dict()
                        # copy detaches the snapshot reference from the
                        # outbound msg dict; inner values/contexts are
                        # read-only downstream so sharing the inner refs
                        # is safe. Skips one O(payload-size) recursive
                        # walk per frame.
                        with self._lock:
                            self._latest_frame[key] = {
                                "topic": str(msg.get("topic") or "manager.stream_frame"),
                                "payload": dict(payload_obj),
                            }
                    self._loop.call_soon_threadsafe(self._fanout, msg)

            if is_record_stream and record_batch_events:
                msg = self._build_stream_records(
                    device_id=device_id,
                    stream=stream,
                    reader=reader,
                    events=record_batch_events,
                    dropped_record_count=record_events_dropped,
                )
                if msg is not None and self._loop is not None:
                    payload_obj = msg.get("payload")
                    if isinstance(payload_obj, dict):
                        # PerfA: see _build_stream_frame snapshot above —
                        # record payload values were sanitised at line
                        # 586 and context_fields at line 621; shallow
                        # copy is enough to detach the snapshot ref.
                        with self._lock:
                            self._latest_frame[key] = {
                                "topic": str(
                                    msg.get("topic") or "manager.stream_records"
                                ),
                                "payload": dict(payload_obj),
                            }
                    self._loop.call_soon_threadsafe(self._fanout, msg)

            self._last_seq[key] = latest_seq
            context_bucket = self._context_by_seq.get(key)
            if context_bucket is not None:
                stale = [seq for seq in context_bucket.keys() if int(seq) <= int(latest_seq)]
                for seq in stale:
                    context_bucket.pop(seq, None)
                if not context_bucket:
                    self._context_by_seq.pop(key, None)
            if current_context_id is None and current_context_fields is None:
                self._stream_context.pop(key, None)
            else:
                self._stream_context[key] = (
                    int(current_context_id) if current_context_id is not None else None,
                    dict(current_context_fields)
                    if isinstance(current_context_fields, dict)
                    else None,
                )

        sub.setsockopt(zmq.LINGER, 0)
        sub.close(0)


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return None
    if isinstance(value, dict):
        return {k: _sanitize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(v) for v in value]
    return value
