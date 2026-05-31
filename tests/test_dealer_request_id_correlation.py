# ruff: noqa: E402
"""Regression tests for DEALER reply-mismatch correlation.

Two DEALER call sites — ManagerClient.call (process -> manager) and
_DeviceWorker._call_interceptor (router -> interceptor process) — did
not correlate responses to requests. When the first call timed out
(`zmq.Again` before recv), the manager/interceptor's reply later
arrived and sat in the socket's recv buffer. The next call would
recv() the stale reply and mis-attribute it to the new request.

These tests pin the new behaviour:

1. A stale reply left by a prior timed-out call is dropped on the next
   call instead of being returned as the new call's response.
2. A response whose request_id matches the outbound payload's
   request_id is returned normally.
3. A caller-supplied request_id is preserved (not overwritten by the
   transport-level UUID injection).
4. _DeviceWorker._process_socks LRU-evicts oldest entries when the
   cap (_PROCESS_SOCKS_MAX) is exceeded, and closes the evicted
   socket so the file descriptor isn't leaked.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.manager_client import ManagerClient
from experiment_control.processes.device_router import (
    _DeviceWorker,
    _PROCESS_SOCKS_MAX,
)
from experiment_control.utils.zmq_helpers import json_dumps, json_loads


# Make sure each test uses its own zmq.Context so the tests don't share
# socket state when run in parallel or under repeated invocation.
def _new_ctx() -> zmq.Context:
    return zmq.Context()


def _bind_inproc_router(ctx: zmq.Context, name: str) -> tuple[zmq.Socket, str]:
    """Return a bound ROUTER socket + its inproc endpoint."""
    sock = ctx.socket(zmq.ROUTER)
    endpoint = f"inproc://{name}-{uuid.uuid4().hex}"
    sock.bind(endpoint)
    return sock, endpoint


class _FakeManagerServer:
    """Minimal in-process server that responds with a configurable delay.

    Echoes back the request's request_id when respond=True; can also be
    asked to silently drop a request (simulating a hung manager that
    causes the client to time out). Runs in a worker thread so the
    client can issue calls without manual interleaving.
    """

    def __init__(self, ctx: zmq.Context) -> None:
        self._ctx = ctx
        self._sock, self.endpoint = _bind_inproc_router(ctx, "fake-manager")
        self._stop = threading.Event()
        self._behaviours: queue.Queue[tuple[str, float]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enqueue_reply_after(self, delay_s: float) -> None:
        """Next request gets a reply, delayed by delay_s seconds."""
        self._behaviours.put(("reply", delay_s))

    def stop(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=2.0)
        finally:
            try:
                self._sock.close(0)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._sock.poll(50, zmq.POLLIN):
                continue
            try:
                identity, payload_raw = self._sock.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                continue
            try:
                _behaviour, delay = self._behaviours.get_nowait()
            except queue.Empty:
                _behaviour, delay = "reply", 0.0
            payload = json_loads(payload_raw)
            request_id = (
                payload.get("request_id") if isinstance(payload, dict) else None
            )
            if delay > 0:
                # Sleep in small chunks so stop() doesn't have to wait
                # the full delay before joining.
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline and not self._stop.is_set():
                    time.sleep(0.005)
                if self._stop.is_set():
                    return
            reply = {
                "request_id": request_id,
                "ok": True,
                "result": {"echo": payload},
            }
            try:
                self._sock.send_multipart([identity, json_dumps(reply)])
            except Exception:
                pass


class ManagerClientCallCorrelationTests(unittest.TestCase):
    def test_late_reply_is_drained_before_next_call(self) -> None:
        ctx = _new_ctx()
        server = _FakeManagerServer(ctx)
        try:
            # Subscribe-telemetry=False keeps this test focused on the
            # RPC socket.
            client = ManagerClient(
                ctx=ctx,
                manager_rpc=server.endpoint,
                manager_pub="inproc://unused",
                rpc_timeout_ms=100,
                subscribe_telemetry=False,
            )
            try:
                # Make the first request reply after 300ms; the client's
                # 100ms timeout will return None and leave the reply
                # buffered in the DEALER's recv queue.
                server.enqueue_reply_after(0.3)
                first = client.call({"type": "noop", "tag": "first"})
                self.assertIsNone(
                    first,
                    "first call should have timed out before the late reply",
                )
                # Give the server time to send the late reply.
                time.sleep(0.4)
                # Second call: stale reply must be discarded; new reply
                # must be returned and must echo the 'second' tag.
                server.enqueue_reply_after(0.0)
                second = client.call({"type": "noop", "tag": "second"})
                self.assertIsNotNone(second, "second call should have succeeded")
                assert second is not None  # type-narrow for mypy
                echoed = second.get("result", {}).get("echo", {})
                self.assertEqual(
                    echoed.get("tag"),
                    "second",
                    "second call must receive its own reply, not the stale one "
                    "from the first call",
                )
            finally:
                client.close()
        finally:
            server.stop()
            ctx.term()

    def test_response_with_mismatched_request_id_is_skipped(self) -> None:
        # Directly drive the DEALER without the server so we can craft
        # a deliberately-mismatched reply.
        ctx = _new_ctx()
        server_sock, endpoint = _bind_inproc_router(ctx, "skip-test")
        try:
            client = ManagerClient(
                ctx=ctx,
                manager_rpc=endpoint,
                manager_pub="inproc://unused",
                rpc_timeout_ms=200,
                subscribe_telemetry=False,
            )
            try:
                # Spawn a worker that responds with a wrong request_id
                # first, then the correct one.
                done = threading.Event()

                def _server_worker() -> None:
                    if not server_sock.poll(1000, zmq.POLLIN):
                        return
                    identity, payload_raw = server_sock.recv_multipart()
                    payload = json_loads(payload_raw)
                    correct_id = payload.get("request_id")
                    bogus = {
                        "request_id": "definitely-not-the-right-id",
                        "ok": True,
                        "result": {"stale": True},
                    }
                    real = {
                        "request_id": correct_id,
                        "ok": True,
                        "result": {"stale": False},
                    }
                    server_sock.send_multipart([identity, json_dumps(bogus)])
                    server_sock.send_multipart([identity, json_dumps(real)])
                    done.set()

                t = threading.Thread(target=_server_worker, daemon=True)
                t.start()
                resp = client.call({"type": "noop"})
                t.join(timeout=2.0)
                self.assertTrue(done.is_set())
                self.assertIsNotNone(resp)
                assert resp is not None
                self.assertEqual(
                    resp.get("result", {}).get("stale"),
                    False,
                    "client must skip the bogus mismatched reply and return "
                    "the correctly-correlated one",
                )
            finally:
                client.close()
        finally:
            try:
                server_sock.close(0)
            except Exception:
                pass
            ctx.term()

    def test_caller_supplied_request_id_is_preserved(self) -> None:
        ctx = _new_ctx()
        server_sock, endpoint = _bind_inproc_router(ctx, "preserve-test")
        try:
            client = ManagerClient(
                ctx=ctx,
                manager_rpc=endpoint,
                manager_pub="inproc://unused",
                rpc_timeout_ms=500,
                subscribe_telemetry=False,
            )
            try:
                received: list[dict] = []

                def _server_worker() -> None:
                    if not server_sock.poll(1000, zmq.POLLIN):
                        return
                    identity, payload_raw = server_sock.recv_multipart()
                    payload = json_loads(payload_raw)
                    received.append(payload)
                    reply = {
                        "request_id": payload.get("request_id"),
                        "ok": True,
                        "result": {},
                    }
                    server_sock.send_multipart([identity, json_dumps(reply)])

                t = threading.Thread(target=_server_worker, daemon=True)
                t.start()
                client.call({"type": "noop", "request_id": "caller-supplied-id"})
                t.join(timeout=2.0)
                self.assertEqual(len(received), 1)
                self.assertEqual(
                    received[0].get("request_id"),
                    "caller-supplied-id",
                    "caller-supplied request_id must not be overwritten by the "
                    "transport-level UUID injection",
                )
            finally:
                client.close()
        finally:
            try:
                server_sock.close(0)
            except Exception:
                pass
            ctx.term()


def _make_bare_device_worker(ctx: zmq.Context) -> _DeviceWorker:
    """Build a _DeviceWorker just complete enough to exercise _get_process_sock."""
    return _DeviceWorker(
        device_id="test-device",
        ctx=ctx,
        reply_queue=queue.Queue(),
        manager_rpc="inproc://unused-manager-rpc",
        manager_pub="inproc://unused-manager-pub",
        device_rpc_timeout_ms=500,
        interceptor_timeout_ms=500,
        queue_max=16,
    )


class DeviceWorkerProcessSocksLruTests(unittest.TestCase):
    def test_get_process_sock_returns_same_socket_for_same_process_id(self) -> None:
        ctx = _new_ctx()
        try:
            worker = _make_bare_device_worker(ctx)
            try:
                # Need a real bound endpoint or connect() may queue the
                # DEALER frame indefinitely; inproc is cheapest.
                router, ep = _bind_inproc_router(ctx, "lru-stable")
                try:
                    s1 = worker._get_process_sock("p1", ep)
                    s2 = worker._get_process_sock("p1", ep)
                    self.assertIs(s1, s2)
                finally:
                    router.close(0)
            finally:
                # Close any sockets the worker accumulated.
                for _ep, sock in worker._process_socks.values():
                    try:
                        sock.close(0)
                    except Exception:
                        pass
        finally:
            ctx.term()

    def test_endpoint_change_replaces_socket(self) -> None:
        ctx = _new_ctx()
        try:
            worker = _make_bare_device_worker(ctx)
            try:
                router1, ep1 = _bind_inproc_router(ctx, "lru-ep1")
                router2, ep2 = _bind_inproc_router(ctx, "lru-ep2")
                try:
                    s1 = worker._get_process_sock("p1", ep1)
                    s2 = worker._get_process_sock("p1", ep2)
                    self.assertIsNot(s1, s2)
                    self.assertEqual(
                        len(worker._process_socks),
                        1,
                        "endpoint change must replace, not duplicate",
                    )
                finally:
                    router1.close(0)
                    router2.close(0)
            finally:
                for _ep, sock in worker._process_socks.values():
                    try:
                        sock.close(0)
                    except Exception:
                        pass
        finally:
            ctx.term()

    def test_cache_evicts_oldest_when_full(self) -> None:
        ctx = _new_ctx()
        try:
            worker = _make_bare_device_worker(ctx)
            try:
                # Use a single bound router so every connect succeeds.
                router, ep = _bind_inproc_router(ctx, "lru-full")
                try:
                    sockets_by_pid: dict[str, zmq.Socket] = {}
                    # Fill to exactly the cap, then add one more.
                    for i in range(_PROCESS_SOCKS_MAX):
                        sockets_by_pid[f"p{i}"] = worker._get_process_sock(
                            f"p{i}", ep
                        )
                    self.assertEqual(
                        len(worker._process_socks), _PROCESS_SOCKS_MAX
                    )

                    # Adding one more must evict the LRU entry ("p0").
                    new_sock = worker._get_process_sock("p_new", ep)  # noqa: F841
                    self.assertEqual(
                        len(worker._process_socks), _PROCESS_SOCKS_MAX
                    )
                    self.assertNotIn("p0", worker._process_socks)
                    self.assertIn("p_new", worker._process_socks)

                    # The evicted socket must have been closed.
                    evicted = sockets_by_pid["p0"]
                    self.assertTrue(
                        evicted.closed,
                        "evicted DEALER socket must be closed so the file "
                        "descriptor isn't leaked",
                    )
                finally:
                    router.close(0)
            finally:
                for _ep, sock in worker._process_socks.values():
                    try:
                        sock.close(0)
                    except Exception:
                        pass
        finally:
            ctx.term()

    def test_recently_used_entry_is_not_evicted(self) -> None:
        ctx = _new_ctx()
        try:
            worker = _make_bare_device_worker(ctx)
            try:
                router, ep = _bind_inproc_router(ctx, "lru-mru")
                try:
                    for i in range(_PROCESS_SOCKS_MAX):
                        worker._get_process_sock(f"p{i}", ep)
                    # Touch "p0" so it's no longer the LRU.
                    worker._get_process_sock("p0", ep)
                    # Adding one more must now evict "p1" (the new LRU).
                    worker._get_process_sock("p_new", ep)
                    self.assertIn("p0", worker._process_socks)
                    self.assertNotIn("p1", worker._process_socks)
                finally:
                    router.close(0)
            finally:
                for _ep, sock in worker._process_socks.values():
                    try:
                        sock.close(0)
                    except Exception:
                        pass
        finally:
            ctx.term()


class LenientMatchAcceptsNonEchoingRepliesTests(unittest.TestCase):
    """Many manager handlers (manager.interceptors.*, manager.identity,
    manager.devices.list, etc.) return {"ok": ..., "result": ...} without
    a request_id field. The PR's earlier strict match silently dropped
    every such reply and timed out; the lenient match (also used by
    tui_manager.py:813 and client/transport.py:186) accepts them.

    The server-side fix in manager_internal_rpc.handle_internal_rpc now
    injects request_id into responses that don't already have one, so in
    practice these replies WILL carry request_id. The lenient match is
    defense-in-depth for mid-upgrade scenarios and for the
    parse_error/invalid_request paths which reply before any request body
    is parsed.
    """

    def test_response_without_request_id_is_returned_not_skipped(self) -> None:
        ctx = _new_ctx()
        server_sock, endpoint = _bind_inproc_router(ctx, "lenient")
        try:
            client = ManagerClient(
                ctx=ctx,
                manager_rpc=endpoint,
                manager_pub="inproc://unused",
                rpc_timeout_ms=500,
                subscribe_telemetry=False,
            )
            try:
                def _server_worker() -> None:
                    if not server_sock.poll(1000, zmq.POLLIN):
                        return
                    identity, _payload_raw = server_sock.recv_multipart()
                    # Deliberately omit request_id, mimicking a manager
                    # handler that doesn't echo (the pre-fix behaviour
                    # of e.g. route_command_interceptor_list).
                    reply = {"ok": True, "result": {"items": []}}
                    server_sock.send_multipart([identity, json_dumps(reply)])

                t = threading.Thread(target=_server_worker, daemon=True)
                t.start()
                resp = client.call({"type": "manager.interceptors.list"})
                t.join(timeout=2.0)
                self.assertIsNotNone(
                    resp,
                    "lenient match must return responses that lack a "
                    "request_id field instead of silently dropping them "
                    "and timing out",
                )
                assert resp is not None
                self.assertTrue(resp.get("ok"))
                self.assertEqual(resp.get("result"), {"items": []})
            finally:
                client.close()
        finally:
            try:
                server_sock.close(0)
            except Exception:
                pass
            ctx.term()


class ServerSideRequestIdEchoTests(unittest.TestCase):
    """handle_internal_rpc now injects request_id into the response when
    the handler didn't already set one. This makes correlation work
    universally without per-handler edits."""

    def test_handle_internal_rpc_injects_request_id(self) -> None:
        from types import SimpleNamespace
        from experiment_control.manager_internal_rpc import handle_internal_rpc

        # Build a minimal manager stub: the only methods handle_internal_rpc
        # touches are _internal_rpc.recv_multipart / send_multipart and
        # the routing functions.
        sent: list[tuple[bytes, bytes]] = []

        class _FakeSocket:
            def recv_multipart(self) -> tuple[bytes, bytes]:
                return b"identity", json_dumps(
                    {
                        "type": "any-route",
                        "request_id": "caller-rid-xyz",
                    }
                )

            def send_multipart(self, parts: list[bytes]) -> None:
                sent.append((parts[0], parts[1]))

        mgr = SimpleNamespace(_internal_rpc=_FakeSocket())

        # The real route_internal_request returns whatever the handler
        # produces. We patch it (via monkey-patching the module-level
        # import) to return a plain handler reply without request_id —
        # the pre-fix shape that broke correlation.
        import experiment_control.manager_internal_rpc as mod

        original = mod.route_internal_request
        try:
            mod.route_internal_request = lambda _mgr, _req: {
                "ok": True,
                "result": {"items": []},
            }
            handle_internal_rpc(mgr)
        finally:
            mod.route_internal_request = original

        self.assertEqual(len(sent), 1)
        _identity, payload_raw = sent[0]
        reply = json_loads(payload_raw)
        self.assertEqual(
            reply.get("request_id"),
            "caller-rid-xyz",
            "handle_internal_rpc must echo caller's request_id into the "
            "response when the handler didn't set one — this is what "
            "makes DEALER correlation work for the 70+ handlers that "
            "return plain {'ok': ..., 'result': ...} dicts",
        )
        # Handler-provided fields must be preserved.
        self.assertTrue(reply.get("ok"))
        self.assertEqual(reply.get("result"), {"items": []})

    def test_handler_supplied_request_id_is_not_overwritten(self) -> None:
        from types import SimpleNamespace
        from experiment_control.manager_internal_rpc import handle_internal_rpc

        sent: list[tuple[bytes, bytes]] = []

        class _FakeSocket:
            def recv_multipart(self) -> tuple[bytes, bytes]:
                return b"identity", json_dumps(
                    {"type": "any-route", "request_id": "from-caller"}
                )

            def send_multipart(self, parts: list[bytes]) -> None:
                sent.append((parts[0], parts[1]))

        mgr = SimpleNamespace(_internal_rpc=_FakeSocket())

        import experiment_control.manager_internal_rpc as mod

        original = mod.route_internal_request
        try:
            mod.route_internal_request = lambda _mgr, _req: {
                "ok": True,
                "request_id": "handler-supplied",
                "result": {},
            }
            handle_internal_rpc(mgr)
        finally:
            mod.route_internal_request = original

        reply = json_loads(sent[0][1])
        self.assertEqual(reply.get("request_id"), "handler-supplied")


class MaybeStaleFlagIsDeferredTests(unittest.TestCase):
    """The drain-before-send is now gated on a per-instance "maybe stale"
    flag set in the except arm of `call()` / `_call_interceptor()`. Healthy
    calls must pay zero extra `poll(0)` syscalls; only the call AFTER a
    timeout drains.
    """

    def test_initial_state_is_clean(self) -> None:
        ctx = _new_ctx()
        try:
            client = ManagerClient(
                ctx=ctx,
                manager_rpc="inproc://unused",
                manager_pub="inproc://unused",
                rpc_timeout_ms=100,
                subscribe_telemetry=False,
            )
            try:
                self.assertFalse(
                    client._maybe_stale_reply,
                    "fresh ManagerClient must not believe it has a stale "
                    "reply pending",
                )
            finally:
                client.close()
        finally:
            ctx.term()

    def test_timeout_sets_maybe_stale_flag(self) -> None:
        ctx = _new_ctx()
        server_sock, endpoint = _bind_inproc_router(ctx, "stale-flag")
        try:
            client = ManagerClient(
                ctx=ctx,
                manager_rpc=endpoint,
                manager_pub="inproc://unused",
                rpc_timeout_ms=50,
                subscribe_telemetry=False,
            )
            try:
                # Server never responds; call must time out.
                resp = client.call({"type": "no-reply"})
                self.assertIsNone(resp)
                self.assertTrue(
                    client._maybe_stale_reply,
                    "post-timeout, the next call must drain stale replies "
                    "pre-send",
                )
            finally:
                client.close()
        finally:
            try:
                server_sock.close(0)
            except Exception:
                pass
            ctx.term()


if __name__ == "__main__":
    unittest.main()
