# ruff: noqa: E402

import json
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

import experiment_control.federation.hub as hub_mod
from experiment_control.federation import parse_federation_config
from experiment_control.federation.hub import FederationHub


class _FakeDealer:
    """Fake DEALER socket for the background warmup fetch.

    ``responder(payload) -> dict`` returns the canned reply, or ``None`` to
    simulate an unreachable peer (recv raises ``zmq.Again``).
    """

    def __init__(self, responder) -> None:
        self._responder = responder
        self._last: dict | None = None
        self.opts: dict[int, int] = {}
        self.sent: list[dict] = []

    def setsockopt(self, opt: int, val: int) -> None:
        self.opts[opt] = val

    def connect(self, *_a: object) -> None:
        pass

    def send(self, raw: bytes, *_a: object) -> None:
        self._last = json.loads(raw)
        self.sent.append(self._last)

    def poll(self, _timeout: int = 0, _flags: int = 0) -> bool:
        return True

    def recv(self, flags: int = 0) -> bytes:
        resp = self._responder(self._last or {})
        if resp is None:
            raise zmq.Again()
        return json.dumps(resp).encode("utf-8")

    def close(self, *_a: object) -> None:
        pass


class _FakeCtx:
    def __init__(self, responder=lambda _p: None) -> None:
        self._responder = responder
        self.sockets: list[_FakeDealer] = []

    def socket(self, _type: int) -> _FakeDealer:
        sock = _FakeDealer(self._responder)
        self.sockets.append(sock)
        return sock

    def term(self) -> None:
        pass


class FederationHubCapabilityTests(unittest.TestCase):
    def test_cached_capabilities_surface_in_list_devices(self) -> None:
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
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=object(),
            config=cfg,
            instance_id="lab1",
        )

        hub.update_capabilities(
            "lab2.psu",
            {"version": 1, "members": [{"name": "get"}, {"name": "set_voltage"}]},
        )

        devices = hub.list_devices_snapshot()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_id"], "lab2.psu")
        self.assertTrue(devices[0]["is_remote"])
        self.assertEqual(devices[0]["capabilities"]["version"], 1)
        members = devices[0]["capabilities"]["members"]
        self.assertEqual([m["name"] for m in members], ["get", "set_voltage"])

    def test_federated_status_uses_numeric_restart_count(self) -> None:
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
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=SimpleNamespace(_telemetry_last_bundle_ts={}),
            config=cfg,
            instance_id="lab1",
        )

        status = hub.device_status_snapshot("lab2.psu")

        self.assertEqual(status["driver_process"]["state"], "FEDERATED")
        self.assertEqual(status["driver_process"]["restart_count"], 0)

    def test_warm_capabilities_on_startup_fetches_remote_capabilities(self) -> None:
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "lab2",
                        "router_rpc": "tcp://10.0.0.22:6000",
                        "manager_pub": "tcp://10.0.0.22:6001",
                        "warm_capabilities_on_startup": True,
                        "mirror_devices": [
                            {"local_id": "lab2.psu", "remote_device_id": "psu"}
                        ],
                    }
                ]
            },
            local_device_ids=set(),
            manager_raw={},
        )
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=object(),
            config=cfg,
            instance_id="lab1",
        )
        peer_cfg = hub._peers["lab2"].config

        def responder(payload: dict) -> dict | None:
            if payload.get("type") == "device.config.list":
                return {
                    "ok": True,
                    "result": [
                        {
                            "device_id": "psu",
                            "yaml_text": "",
                            "device_metadata": {},
                            "stream_metadata": {},
                            "telemetry_calls": [],
                            "stream_calls": [],
                            "run_meta_calls": [],
                        }
                    ],
                }
            if payload.get("action") == "manager.telemetry.schema.list":
                return {
                    "ok": True,
                    "result": {"devices": [{"device_id": "psu", "signals": [], "dtypes": [], "units": []}]},
                }
            if payload.get("action") == "capabilities":
                return {"ok": True, "result": {"version": 1, "members": [{"name": "get"}]}}
            return None

        # The background warmup fetch issues the capabilities RPC because
        # warm_capabilities_on_startup is set; results are applied on the main
        # thread by _apply_peer_metadata.
        fake_ctx = _FakeCtx(responder)
        config_resp, schema_resp, caps, error = hub._fetch_peer_metadata(fake_ctx, peer_cfg)
        self.assertIsNone(error)
        actions = [p.get("action") for p in fake_ctx.sockets[0].sent]
        self.assertIn("capabilities", actions)
        self.assertEqual(caps["psu"]["version"], 1)

        hub._apply_peer_metadata(hub._peers["lab2"], config_resp, schema_resp, caps)
        devices = hub.list_devices_snapshot()
        self.assertEqual(devices[0]["capabilities"]["version"], 1)


class FederationHubWarmupTests(unittest.TestCase):
    def _make_hub(self, *, ctx: object | None = None) -> FederationHub:
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
            ),
            config=cfg,
            instance_id="lab1",
        )

    def test_activate_does_no_rpc_on_caller_thread_and_starts_warmup_thread(self) -> None:
        # The whole point of the fix: activate() sets up SUB sockets and starts
        # the background warmup thread, but never issues a (blocking) peer RPC
        # on the caller's (poll-loop) thread.
        hub = self._make_hub()
        calls: list[tuple] = []
        hub._rpc_call = lambda *a, **k: calls.append((a, k))  # type: ignore[method-assign]
        # Keep the background thread off the network for a deterministic test.
        hub._fetch_peer_metadata = lambda _ctx, _cfg: (None, None, {}, "stub")  # type: ignore[method-assign]

        hub.activate()
        try:
            self.assertEqual(calls, [])
            self.assertIsNotNone(hub._peers["lab2"].sub_sock)
            self.assertIsNotNone(hub._warmup_thread)
            assert hub._warmup_thread is not None
            self.assertTrue(hub._warmup_thread.is_alive())
        finally:
            hub.close()
        self.assertIsNone(hub._warmup_thread)

    def test_drain_warmup_results_applies_metadata_and_marks_warmed(self) -> None:
        hub = self._make_hub()
        peer_rt = hub._peers["lab2"]
        self.assertFalse(peer_rt.metadata_warmed)

        hub._warmup_results.put(
            (
                "lab2",
                {
                    "ok": True,
                    "result": [
                        {
                            "device_id": "psu",
                            "yaml_text": "",
                            "device_metadata": {},
                            "stream_metadata": {},
                            "telemetry_calls": [],
                            "stream_calls": [],
                            "run_meta_calls": [],
                        }
                    ],
                },
                {"ok": True, "result": {"devices": [{"device_id": "psu", "signals": [], "dtypes": [], "units": []}]}},
                {"psu": {"version": 2}},
                None,
            )
        )

        hub._drain_warmup_results()

        self.assertTrue(peer_rt.metadata_warmed)
        self.assertIsNone(peer_rt.last_error)
        dev = hub.list_devices_snapshot()[0]
        self.assertEqual(dev["capabilities"]["version"], 2)
        cfg_get = hub.device_config_get("lab2.psu")
        assert cfg_get is not None
        self.assertTrue(cfg_get["is_remote"])
        self.assertEqual(cfg_get["remote_device_id"], "psu")

    def test_fetch_peer_metadata_uses_metadata_timeout(self) -> None:
        hub = self._make_hub()
        peer_cfg = hub._peers["lab2"].config

        def responder(payload: dict) -> dict | None:
            if payload.get("type") == "device.config.list":
                return {"ok": True, "result": []}
            if payload.get("action") == "manager.telemetry.schema.list":
                return {"ok": True, "result": {"devices": []}}
            return None

        fake_ctx = _FakeCtx(responder)
        config_resp, schema_resp, caps, error = hub._fetch_peer_metadata(fake_ctx, peer_cfg)

        self.assertIsNone(error)
        self.assertIsNotNone(config_resp)
        self.assertIsNotNone(schema_resp)
        self.assertEqual(caps, {})  # warm_capabilities_on_startup False
        dealer = fake_ctx.sockets[0]
        self.assertEqual(dealer.opts[zmq.RCVTIMEO], peer_cfg.metadata_rpc_timeout_ms)
        self.assertEqual(dealer.opts[zmq.SNDTIMEO], peer_cfg.metadata_rpc_timeout_ms)

    def test_fetch_peer_metadata_unreachable_returns_error(self) -> None:
        hub = self._make_hub()
        peer_cfg = hub._peers["lab2"].config
        fake_ctx = _FakeCtx(lambda _p: None)  # recv raises Again -> timeout

        config_resp, schema_resp, caps, error = hub._fetch_peer_metadata(fake_ctx, peer_cfg)

        self.assertIsNone(config_resp)
        self.assertIsNone(schema_resp)
        self.assertEqual(caps, {})
        self.assertIsNotNone(error)


    def test_rpc_call_unresolvable_peer_fails_fast(self) -> None:
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "lab9",
                        "router_rpc": "tcp://TODO_PLACEHOLDER_HOST:6000",
                        "manager_pub": "tcp://10.0.0.9:6001",
                        "mirror_devices": [
                            {"local_id": "lab9.psu", "remote_device_id": "psu"}
                        ],
                    }
                ]
            },
            local_device_ids=set(),
            manager_raw={},
        )
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=object(),
            config=cfg,
            instance_id="lab1",
        )
        peer_rt = hub._peers["lab9"]
        # Unresolvable host: returns None and sets last_error without ever
        # creating a context or socket (so nothing blocks on DNS).
        self.assertIsNone(hub._rpc_call(peer_rt, {"type": "x"}))
        self.assertIsNotNone(peer_rt.last_error)
        self.assertIsNone(hub._fed_ctx)

    def test_rpc_call_caches_resolution_within_ttl(self) -> None:
        hub = self._make_hub()
        peer_rt = hub._peers["lab2"]
        calls = {"n": 0}

        def _counting_resolve(_endpoint: str) -> None:
            calls["n"] += 1
            return None  # unresolvable -> _rpc_call returns early, no socket

        original = hub_mod.resolve_tcp_endpoint
        hub_mod.resolve_tcp_endpoint = _counting_resolve  # type: ignore[assignment]
        try:
            self.assertIsNone(hub._rpc_call(peer_rt, {"type": "x"}))
            self.assertIsNone(hub._rpc_call(peer_rt, {"type": "x"}))
            self.assertIsNone(hub._rpc_call(peer_rt, {"type": "x"}))
        finally:
            hub_mod.resolve_tcp_endpoint = original  # type: ignore[assignment]
        # Resolved once and reused from the per-peer TTL cache, not per command.
        self.assertEqual(calls["n"], 1)

    def test_warmup_loop_exits_cleanly_when_hub_closed(self) -> None:
        hub = self._make_hub()
        hub._fed_ctx_closed = True
        # Must return (not raise) — _ensure_fed_ctx raises RuntimeError when
        # closed, and the loop must swallow it and exit.
        hub._warmup_loop()

    def test_warmup_rewarms_after_refresh_interval(self) -> None:
        hub = self._make_hub()
        calls = {"n": 0}

        def _fake_fetch(_ctx: object, _cfg: object):
            calls["n"] += 1
            return ({"ok": True, "result": []}, {"ok": True, "result": {"devices": []}}, {}, None)

        hub._fetch_peer_metadata = _fake_fetch  # type: ignore[method-assign]
        original_refresh = hub_mod._METADATA_REFRESH_S
        hub_mod._METADATA_REFRESH_S = 0.05  # re-warm fast for the test
        hub.activate()
        try:
            deadline = time.monotonic() + 3.0
            while calls["n"] < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            hub_mod._METADATA_REFRESH_S = original_refresh
            hub.close()
        # A warmed peer is re-fetched (not permanently 'done').
        self.assertGreaterEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
