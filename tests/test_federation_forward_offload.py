# ruff: noqa: E402
"""Regression tests for F10 (federation forward blocks the Manager loop) and
its follow-up fix (per-mirror forward workers).

Original F10 problem: mirrored-device/process forwards ran inline on the
Manager's poll loop (FederationHub.forward_device_request/forward_process_request
-> a blocking peer RPC), so an unreachable/slow peer stalled every other RPC
for up to the peer's rpc_timeout_ms, and each forward opened a brand-new
DEALER + TCP connect.

First fix attempt: route mirrored forwards through the Manager's shared
local-device lifecycle thread pool, with one persistent socket per PEER
guarded by a single per-peer lock. A follow-up review found this reintroduced
the same class of stall: the per-peer lock let a device forward on the
lifecycle pool block a process forward still running on the poll loop, and a
flood of mirrored-device traffic to one dead peer could exhaust the shared
32-worker pool, starving unrelated local device.connect/disconnect work.

Current design (this file): one dedicated ``_FederationForwardWorker`` per
mirrored device/process (mirroring processes/device_router.py's
_MirroredDeviceWorker), each with its own persistent socket and bounded
queue, fully decoupled from the Manager's lifecycle pool and from each
other. FederationHub.try_dispatch_device_forward/try_dispatch_process_forward
queue onto that worker and return immediately; the worker delivers the reply
asynchronously via the shared reply queue.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._manager.internal_rpc import handle_internal_rpc
from experiment_control.federation import parse_federation_config
from experiment_control.federation.hub import FederationHub, _FederationForwardWorker
from experiment_control.utils.zmq_helpers import json_dumps, safe_json_loads


class _FakeDealer:
    """Fake persistent DEALER: records how many times a NEW one was made."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self._last: dict | None = None
        self.opts: dict[int, int] = {}
        self.closed = False

    def setsockopt(self, opt: int, val: int) -> None:
        self.opts[opt] = val

    def connect(self, *_a: object) -> None:
        pass

    def send(self, raw: bytes, *_a: object) -> None:
        self._last = safe_json_loads(raw)

    def recv(self, flags: int = 0) -> bytes:
        resp = self._responder(self._last or {})
        if resp is None:
            raise zmq.Again()
        return json_dumps(resp)

    def close(self, *_a: object) -> None:
        self.closed = True


class _FakeCtx:
    def __init__(self, responder=lambda _p: {"ok": True, "result": {}}) -> None:
        self._responder = responder
        self.sockets: list[_FakeDealer] = []

    def socket(self, _type: int) -> _FakeDealer:
        sock = _FakeDealer(self._responder)
        self.sockets.append(sock)
        return sock


class ForwardWorkerPersistentSocketTests(unittest.TestCase):
    """Drives _FederationForwardWorker._forward() directly (the worker's
    per-call unit of work) rather than spinning up its background thread,
    for deterministic single-threaded assertions."""

    def _make_worker(self, ctx) -> _FederationForwardWorker:
        return _FederationForwardWorker(
            name="test-forward-worker",
            ctx=ctx,
            router_rpc="tcp://10.0.0.22:6000",
            rpc_timeout_ms=50,
        )

    def test_reuses_one_socket_across_successful_calls(self) -> None:
        fake_ctx = _FakeCtx(lambda _p: {"ok": True, "result": {}})
        worker = self._make_worker(fake_ctx)

        for _ in range(5):
            resp = worker._forward({"type": "command", "action": "get"})
            self.assertEqual(resp, {"ok": True, "result": {}})

        self.assertEqual(
            len(fake_ctx.sockets),
            1,
            "each forward on this worker must reuse its persistent DEALER "
            "instead of opening a new one per call",
        )
        self.assertFalse(fake_ctx.sockets[0].closed)

    def test_failed_call_closes_and_next_call_reconnects(self) -> None:
        fake_ctx = _FakeCtx(lambda _p: None)  # recv raises zmq.Again every time
        worker = self._make_worker(fake_ctx)

        resp = worker._forward({"type": "command", "action": "get"})
        self.assertIsNone(resp)
        self.assertEqual(len(fake_ctx.sockets), 1)
        self.assertTrue(
            fake_ctx.sockets[0].closed,
            "a socket that just timed out must be closed, not reused -- a late "
            "reply for the failed call could otherwise be misdelivered as the "
            "response to a later, unrelated call",
        )

        # Next call reconnects (new socket), not reusing the wedged one.
        resp2 = worker._forward({"type": "command", "action": "get"})
        self.assertIsNone(resp2)
        self.assertEqual(len(fake_ctx.sockets), 2)

    def test_run_thread_delivers_result_via_callback(self) -> None:
        # End-to-end through the real thread + queue, unlike the two tests
        # above (which call _forward directly for determinism).
        from experiment_control.federation.hub import _ForwardTask

        fake_ctx = _FakeCtx(lambda _p: {"ok": True, "result": {"value": 42}})
        worker = self._make_worker(fake_ctx)
        worker.start()
        try:
            results: list = []
            worker.submit(
                _ForwardTask(
                    outbound={"type": "command"}, on_result=results.append
                )
            )
            deadline = time.monotonic() + 2.0
            while not results and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(results, [{"ok": True, "result": {"value": 42}}])
        finally:
            worker.stop()
            worker.join(timeout=2.0)


class ForwardWorkerIsolationTests(unittest.TestCase):
    """Proves the review finding this design fixes: two mirrored devices
    sharing one peer must not serialize against each other (the previous
    per-peer-lock design forced this), and a stuck forward must never touch
    threads outside its own worker (no shared executor to exhaust)."""

    def test_slow_device_forward_does_not_delay_a_different_mirrored_device(
        self,
    ) -> None:
        import threading

        release_slow = threading.Event()

        def _slow_responder(_payload):
            release_slow.wait(timeout=2.0)
            return {"ok": True, "result": "slow-done"}

        def _fast_responder(_payload):
            return {"ok": True, "result": "fast-done"}

        slow_worker = _FederationForwardWorker(
            name="w-slow",
            ctx=_FakeCtx(_slow_responder),
            router_rpc="tcp://10.0.0.22:6000",
            rpc_timeout_ms=5000,
        )
        fast_worker = _FederationForwardWorker(
            name="w-fast",
            ctx=_FakeCtx(_fast_responder),
            router_rpc="tcp://10.0.0.22:6000",
            rpc_timeout_ms=5000,
        )
        slow_worker.start()
        fast_worker.start()
        try:
            from experiment_control.federation.hub import _ForwardTask

            slow_results: list = []
            fast_results: list = []
            slow_worker.submit(
                _ForwardTask(outbound={"device_id": "psu1"}, on_result=slow_results.append)
            )
            # Give the slow worker a moment to actually enter its blocking
            # responder before racing the fast one against it.
            time.sleep(0.05)

            fast_worker.submit(
                _ForwardTask(outbound={"device_id": "psu2"}, on_result=fast_results.append)
            )
            deadline = time.monotonic() + 2.0
            while not fast_results and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(
                fast_results,
                [{"ok": True, "result": "fast-done"}],
                "a different mirrored device on the same peer must complete "
                "promptly even while another device's forward is blocked -- "
                "each mirror has its own worker/socket, not a shared per-peer "
                "one",
            )
            self.assertEqual(
                slow_results, [], "sanity: the slow forward is still in flight"
            )
        finally:
            release_slow.set()
            slow_worker.stop()
            fast_worker.stop()
            slow_worker.join(timeout=2.0)
            fast_worker.join(timeout=2.0)


class _FakeMirrorWorker:
    """Stand-in for _FederationForwardWorker: submit() runs synchronously on
    the calling thread instead of a real worker thread."""

    def __init__(self, respond) -> None:
        self.respond = respond  # Callable[[Json], Json | None]
        self.outbound_calls: list = []

    def submit(self, task) -> bool:
        self.outbound_calls.append(task.outbound)
        task.on_result(self.respond(task.outbound))
        return True


class _AlwaysBusyWorker:
    def submit(self, _task) -> bool:
        return False


def _make_device_hub() -> FederationHub:
    cfg = parse_federation_config(
        {
            "peers": [
                {
                    "peer_id": "lab2",
                    "router_rpc": "tcp://10.0.0.22:6000",
                    "manager_pub": "tcp://10.0.0.22:6001",
                    "policy": {"allow_device_actions": ["get_current"]},
                    "mirror_devices": [
                        {"local_id": "lab2.psu", "remote_device_id": "psu"}
                    ],
                }
            ]
        },
        local_device_ids=set(),
        manager_raw={},
    )
    return FederationHub(
        ctx=zmq.Context.instance(),
        poller=zmq.Poller(),
        manager=SimpleNamespace(),
        config=cfg,
        instance_id="lab1",
    )


def _dispatch_device_forward(hub, req, *, respond=None, worker=None):
    device_id = req.get("device_id")
    if worker is not None:
        hub._device_forward_workers[device_id] = worker
    elif respond is not None:
        hub._device_forward_workers[device_id] = _FakeMirrorWorker(respond)
    dispatched = hub.try_dispatch_device_forward(
        identity=b"test-identity",
        req=req,
        rtype=str(req.get("type")),
        device_id=device_id,
    )
    resp = None
    if dispatched:
        _identity, resp = hub._reply_queue.get_nowait()
    return dispatched, resp


class DeviceForwardDispatchTests(unittest.TestCase):
    """try_dispatch_device_forward's ACL/rewrite/capabilities-cache/backpressure
    logic (the direct replacement for the old synchronous
    forward_device_request), driven with a fake worker in place of a real
    background thread."""

    def test_unmirrored_device_returns_false(self) -> None:
        hub = _make_device_hub()
        dispatched, _resp = _dispatch_device_forward(
            hub, {"type": "command", "device_id": "other", "action": "get_current"}
        )
        self.assertFalse(dispatched)

    def test_command_rewrites_device_id_and_adds_federation_meta(self) -> None:
        hub = _make_device_hub()
        worker = _FakeMirrorWorker(lambda _o: {"ok": True, "result": {"current": 1.0}})
        dispatched, resp = _dispatch_device_forward(
            hub,
            {"type": "command", "device_id": "lab2.psu", "action": "get_current"},
            worker=worker,
        )
        self.assertTrue(dispatched)
        self.assertTrue(resp["ok"])
        self.assertEqual(len(worker.outbound_calls), 1)
        out = worker.outbound_calls[0]
        self.assertEqual(out["device_id"], "psu")  # local -> remote
        self.assertIn("federation", out)

    def test_command_denied_by_acl_never_reaches_worker(self) -> None:
        hub = _make_device_hub()  # allow_device_actions only permits get_current
        worker = _FakeMirrorWorker(lambda _o: {"ok": True, "result": {}})
        dispatched, resp = _dispatch_device_forward(
            hub,
            {"type": "command", "device_id": "lab2.psu", "action": "set_voltage"},
            worker=worker,
        )
        self.assertTrue(dispatched)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "federation_acl_denied")
        self.assertEqual(worker.outbound_calls, [])

    def test_peer_unavailable_maps_to_error(self) -> None:
        hub = _make_device_hub()
        dispatched, resp = _dispatch_device_forward(
            hub,
            {"type": "command", "device_id": "lab2.psu", "action": "get_current"},
            respond=lambda _o: None,
        )
        self.assertTrue(dispatched)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "peer_unavailable")

    def test_capabilities_response_updates_mirror_cache(self) -> None:
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "lab2",
                        "router_rpc": "tcp://10.0.0.22:6000",
                        "manager_pub": "tcp://10.0.0.22:6001",
                        "mirror_devices": [
                            {"local_id": "lab2.psu", "remote_device_id": "psu"}
                        ],
                    }
                ]
            },
            local_device_ids=set(),
            manager_raw={},
        )  # default policy allows all device actions ("*")
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=SimpleNamespace(),
            config=cfg,
            instance_id="lab1",
        )
        dispatched, resp = _dispatch_device_forward(
            hub,
            {"type": "command", "device_id": "lab2.psu", "action": "capabilities"},
            respond=lambda _o: {
                "ok": True,
                "result": {"version": 1, "members": [{"name": "get_current"}]},
            },
        )
        self.assertTrue(dispatched)
        self.assertTrue(resp["ok"])
        self.assertEqual(hub._mirrors["lab2.psu"].capabilities["version"], 1)

    def test_busy_worker_replies_without_submitting(self) -> None:
        hub = _make_device_hub()
        dispatched, resp = _dispatch_device_forward(
            hub,
            {"type": "command", "device_id": "lab2.psu", "action": "get_current"},
            worker=_AlwaysBusyWorker(),
        )
        self.assertTrue(dispatched)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "federation_forward_busy")

    def test_missing_worker_replies_without_crashing(self) -> None:
        # No worker registered for this mirror at all (e.g. activate() never
        # ran) -- must reply with an error, not raise.
        hub = _make_device_hub()
        dispatched, resp = _dispatch_device_forward(
            hub, {"type": "command", "device_id": "lab2.psu", "action": "get_current"}
        )
        self.assertTrue(dispatched)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "federation_forward_no_worker")


class _FakeForwardHub:
    """Fake FederationHub for driving _handle_internal_rpc's dispatch logic
    without touching real sockets/workers."""

    def __init__(self, *, mirrored_devices=(), mirrored_processes=()) -> None:
        self._mirrored_devices = set(mirrored_devices)
        self._mirrored_processes = set(mirrored_processes)
        self.device_forward_calls: list = []
        self.process_forward_calls: list = []

    def is_mirrored_device(self, device_id: str) -> bool:
        return device_id in self._mirrored_devices

    def is_mirrored_process(self, process_id: str) -> bool:
        return process_id in self._mirrored_processes

    def try_dispatch_device_forward(self, *, identity, req, rtype, device_id) -> bool:
        if device_id not in self._mirrored_devices:
            return False
        if rtype != "command" and rtype not in {
            "device.connect",
            "device.disconnect",
            "device.driver.start",
            "device.driver.stop",
            "device.driver.restart",
            "device.recover",
        }:
            return False
        self.device_forward_calls.append((identity, req, rtype, device_id))
        return True

    def try_dispatch_process_forward(self, *, identity, req, process_id) -> bool:
        if process_id not in self._mirrored_processes:
            return False
        self.process_forward_calls.append((identity, req, process_id))
        return True


class MirroredForwardDispatchTests(unittest.TestCase):
    """_handle_internal_rpc must queue mirrored-device/process forwards onto
    FederationHub's per-mirror workers, not run them inline and not divert
    them onto the local-device lifecycle pool."""

    def _make_manager_stub(
        self, req: dict, *, hub: _FakeForwardHub, in_devices: bool, in_processes: bool
    ):
        sent: list[tuple[bytes, bytes]] = []
        dispatched: list[tuple] = []

        class _FakeSocket:
            def recv_multipart(self) -> tuple[bytes, bytes]:
                return b"identity", json_dumps(req)

            def send_multipart(self, parts: list[bytes]) -> None:
                sent.append((parts[0], parts[1]))

        mgr = SimpleNamespace(
            _internal_rpc=_FakeSocket(),
            _devices={"lab2.psu": object()} if in_devices else {},
            _processes={"spb": object()} if in_processes else {},
            _federation_hub=hub,
            _dispatch_lifecycle_task=lambda identity, req, rtype, device_id: dispatched.append(
                (identity, req, rtype, device_id)
            ),
        )
        return mgr, sent, dispatched

    def test_mirrored_command_is_dispatched_to_device_forward_worker(self) -> None:
        req = {"type": "command", "device_id": "lab2.psu", "action": "get_current"}
        hub = _FakeForwardHub(mirrored_devices={"lab2.psu"})
        mgr, sent, dispatched = self._make_manager_stub(
            req, hub=hub, in_devices=False, in_processes=False
        )

        handle_internal_rpc(mgr)

        self.assertEqual(len(hub.device_forward_calls), 1)
        identity, _req, rtype, device_id = hub.device_forward_calls[0]
        self.assertEqual(identity, b"identity")
        self.assertEqual(rtype, "command")
        self.assertEqual(device_id, "lab2.psu")
        self.assertEqual(
            dispatched,
            [],
            "a mirrored forward must never touch the local-device lifecycle "
            "pool (_dispatch_lifecycle_task)",
        )
        self.assertEqual(
            sent,
            [],
            "the reply must come later via the reply queue, not be sent "
            "synchronously from _handle_internal_rpc",
        )

    def test_mirrored_lifecycle_type_is_dispatched_to_device_forward_worker(self) -> None:
        req = {"type": "device.connect", "device_id": "lab2.psu"}
        hub = _FakeForwardHub(mirrored_devices={"lab2.psu"})
        mgr, sent, dispatched = self._make_manager_stub(
            req, hub=hub, in_devices=False, in_processes=False
        )

        handle_internal_rpc(mgr)

        self.assertEqual(len(hub.device_forward_calls), 1)
        self.assertEqual(hub.device_forward_calls[0][2], "device.connect")
        self.assertEqual(dispatched, [])
        self.assertEqual(sent, [])

    def test_mirrored_non_forwarding_type_runs_inline(self) -> None:
        # device.get_status doesn't forward over the network; it must NOT be
        # diverted to a forward worker or the lifecycle pool.
        req = {"type": "device.get_status", "device_id": "lab2.psu"}
        hub = _FakeForwardHub(mirrored_devices={"lab2.psu"})
        mgr, sent, dispatched = self._make_manager_stub(
            req, hub=hub, in_devices=False, in_processes=False
        )
        import experiment_control._manager.internal_rpc as mod

        original = mod.route_internal_request
        try:
            mod.route_internal_request = lambda _mgr, _req: {"ok": True, "result": {}}
            handle_internal_rpc(mgr)
        finally:
            mod.route_internal_request = original

        self.assertEqual(hub.device_forward_calls, [])
        self.assertEqual(dispatched, [])
        self.assertEqual(len(sent), 1)

    def test_local_lifecycle_type_still_dispatched_to_lifecycle_pool(self) -> None:
        req = {"type": "device.connect", "device_id": "lab2.psu"}
        hub = _FakeForwardHub(mirrored_devices=set())  # not mirrored
        mgr, sent, dispatched = self._make_manager_stub(
            req, hub=hub, in_devices=True, in_processes=False
        )

        handle_internal_rpc(mgr)

        self.assertEqual(hub.device_forward_calls, [])
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0][3], "lab2.psu")
        self.assertEqual(sent, [])

    def test_mirrored_process_rpc_is_dispatched_to_process_forward_worker(self) -> None:
        req = {
            "type": "manager.processes.rpc",
            "process_id": "spb",
            "request": {"type": "mw.retune"},
        }
        hub = _FakeForwardHub(mirrored_processes={"spb"})
        mgr, sent, dispatched = self._make_manager_stub(
            req, hub=hub, in_devices=False, in_processes=False
        )

        handle_internal_rpc(mgr)

        self.assertEqual(len(hub.process_forward_calls), 1)
        identity, _req, process_id = hub.process_forward_calls[0]
        self.assertEqual(identity, b"identity")
        self.assertEqual(process_id, "spb")
        self.assertEqual(sent, [])

    def test_local_process_wins_over_mirror(self) -> None:
        # A local process registered at runtime with the same id as a mirror
        # (processes, unlike devices, aren't config-time collision-checked
        # against federation mirrors) must dispatch locally, not forward.
        req = {
            "type": "manager.processes.rpc",
            "process_id": "spb",
            "request": {"type": "mw.retune"},
        }
        hub = _FakeForwardHub(mirrored_processes={"spb"})
        mgr, sent, dispatched = self._make_manager_stub(
            req, hub=hub, in_devices=False, in_processes=True
        )
        import experiment_control._manager.internal_rpc as mod

        original = mod.route_internal_request
        try:
            mod.route_internal_request = lambda _mgr, _req: {"ok": True, "result": "LOCAL"}
            handle_internal_rpc(mgr)
        finally:
            mod.route_internal_request = original

        self.assertEqual(
            hub.process_forward_calls,
            [],
            "federation must not be consulted when a local process owns this id",
        )
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
