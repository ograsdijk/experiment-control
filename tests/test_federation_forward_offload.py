# ruff: noqa: E402
"""Regression tests for F10 (federation forward blocks the Manager loop).

Two things used to make a mirrored-device forward the weak link on the
Manager's poll loop:

1. ``_handle_internal_rpc`` ran ``route_device_request`` for mirrored
   devices inline, so ``FederationHub.forward_device_request`` (and its
   blocking peer RPC) executed on the poll loop itself -- an unreachable
   or slow peer stalled every other RPC for up to the peer's
   ``rpc_timeout_ms``.
2. ``FederationHub._rpc_call`` opened a brand-new DEALER + TCP connect on
   every single forwarded call.

These tests pin: (a) mirrored "command"/lifecycle-type requests are now
handed to the lifecycle executor exactly like local lifecycle ops, and
(b) ``_rpc_call`` reuses one persistent socket per peer across calls and
only reconnects after a failure.
"""

from __future__ import annotations

import json
import sys
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
from experiment_control.federation.hub import FederationHub
from experiment_control.utils.zmq_helpers import json_dumps


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
        self._last = json.loads(raw)

    def poll(self, _timeout: int = 0, _flags: int = 0) -> bool:
        return True

    def recv(self, flags: int = 0) -> bytes:
        resp = self._responder(self._last or {})
        if resp is None:
            raise zmq.Again()
        return json.dumps(resp).encode("utf-8")

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

    def term(self) -> None:
        pass


def _make_hub(*, ctx: object | None = None) -> FederationHub:
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
    )
    return FederationHub(
        ctx=ctx if ctx is not None else zmq.Context.instance(),
        poller=zmq.Poller(),
        manager=SimpleNamespace(
            _publish_manager_event=lambda *a, **k: None,
            _telemetry_last_bundle_ts={},
            _telemetry_last_recv_mono={},
        ),
        config=cfg,
        instance_id="lab1",
    )


class RpcCallPersistentSocketTests(unittest.TestCase):
    def test_reuses_one_socket_across_successful_calls(self) -> None:
        hub = _make_hub()
        peer_rt = hub._peers["lab2"]
        fake_ctx = _FakeCtx(lambda _p: {"ok": True, "result": {}})
        hub._ensure_fed_ctx = lambda: fake_ctx  # type: ignore[method-assign]

        for _ in range(5):
            resp = hub._rpc_call(peer_rt, {"type": "command", "action": "get"})
            self.assertEqual(resp, {"ok": True, "result": {}})

        self.assertEqual(
            len(fake_ctx.sockets),
            1,
            "F10: each forwarded call must reuse the peer's persistent "
            "DEALER instead of opening a new one (and reconnecting) per call",
        )
        self.assertFalse(fake_ctx.sockets[0].closed)

    def test_failed_call_closes_and_next_call_reconnects(self) -> None:
        hub = _make_hub()
        peer_rt = hub._peers["lab2"]
        fake_ctx = _FakeCtx(lambda _p: None)  # recv raises zmq.Again every time
        hub._ensure_fed_ctx = lambda: fake_ctx  # type: ignore[method-assign]

        resp = hub._rpc_call(peer_rt, {"type": "command", "action": "get"})
        self.assertIsNone(resp)
        self.assertEqual(len(fake_ctx.sockets), 1)
        self.assertTrue(
            fake_ctx.sockets[0].closed,
            "a socket that just timed out must be closed, not reused -- a late "
            "reply for the failed call could otherwise be misdelivered as the "
            "response to a later, unrelated call",
        )

        # Next call reconnects (new socket), not reusing the wedged one.
        resp2 = hub._rpc_call(peer_rt, {"type": "command", "action": "get"})
        self.assertIsNone(resp2)
        self.assertEqual(len(fake_ctx.sockets), 2)


class MirroredForwardDispatchTests(unittest.TestCase):
    """_handle_internal_rpc must route mirrored-device forwards through the
    lifecycle executor, not run them inline on the poll loop."""

    def _make_manager_stub(self, req: dict, *, is_mirrored: bool, in_devices: bool):
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
            _federation_hub=SimpleNamespace(
                is_mirrored_device=lambda device_id: is_mirrored
                and device_id == "lab2.psu"
            ),
            _dispatch_lifecycle_task=lambda identity, req, rtype, device_id: dispatched.append(
                (identity, req, rtype, device_id)
            ),
        )
        return mgr, sent, dispatched

    def test_mirrored_command_is_dispatched_to_lifecycle_executor(self) -> None:
        req = {"type": "command", "device_id": "lab2.psu", "action": "get_current"}
        mgr, sent, dispatched = self._make_manager_stub(
            req, is_mirrored=True, in_devices=False
        )

        handle_internal_rpc(mgr)

        self.assertEqual(len(dispatched), 1)
        identity, dispatched_req, rtype, device_id = dispatched[0]
        self.assertEqual(identity, b"identity")
        self.assertEqual(rtype, "command")
        self.assertEqual(device_id, "lab2.psu")
        self.assertEqual(
            sent,
            [],
            "the reply must come later via the lifecycle reply queue, not be "
            "sent synchronously from _handle_internal_rpc",
        )

    def test_mirrored_lifecycle_type_is_dispatched_to_lifecycle_executor(self) -> None:
        req = {"type": "device.connect", "device_id": "lab2.psu"}
        mgr, sent, dispatched = self._make_manager_stub(
            req, is_mirrored=True, in_devices=False
        )

        handle_internal_rpc(mgr)

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0][2], "device.connect")
        self.assertEqual(sent, [])

    def test_mirrored_non_forwarding_type_runs_inline(self) -> None:
        # device.get_status doesn't forward over the network (no _rpc_call);
        # it must NOT be diverted to the lifecycle executor.
        req = {"type": "device.get_status", "device_id": "lab2.psu"}
        mgr, sent, dispatched = self._make_manager_stub(
            req, is_mirrored=True, in_devices=False
        )
        mgr._devices = {}
        import experiment_control._manager.internal_rpc as mod

        original = mod.route_internal_request
        try:
            mod.route_internal_request = lambda _mgr, _req: {"ok": True, "result": {}}
            handle_internal_rpc(mgr)
        finally:
            mod.route_internal_request = original

        self.assertEqual(dispatched, [])
        self.assertEqual(len(sent), 1)

    def test_local_lifecycle_type_still_dispatched_unaffected_by_mirror_check(
        self,
    ) -> None:
        req = {"type": "device.connect", "device_id": "lab2.psu"}
        mgr, sent, dispatched = self._make_manager_stub(
            req, is_mirrored=False, in_devices=True
        )

        handle_internal_rpc(mgr)

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0][3], "lab2.psu")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
