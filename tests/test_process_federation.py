# ruff: noqa: E402
"""Tests for first-class process federation: config + ACL, hub RPC forwarding +
process-telemetry relay + schema warm, manager_client process telemetry cache,
and sequencer `process:` addressing."""

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import zmq

from experiment_control._manager.route_handlers import route_process_rpc
from experiment_control.federation import parse_federation_config
from experiment_control.federation.hub import FederationHub
from experiment_control.manager import Manager
from experiment_control.manager_client import ManagerClient
from experiment_control.sequencer.ast import CallStep, parse_sequence
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.utils.config_parsing import ConfigError


def _peer(**overrides):
    peer = {
        "peer_id": "lab2",
        "router_rpc": "tcp://10.0.0.22:6000",
        "manager_pub": "tcp://10.0.0.22:6001",
    }
    peer.update(overrides)
    return {"peers": [peer]}


# --------------------------------------------------------------------------
# Config + ACL
# --------------------------------------------------------------------------
class ProcessFederationConfigTests(unittest.TestCase):
    def test_peer_with_only_mirror_processes_parses(self) -> None:
        cfg = parse_federation_config(
            _peer(mirror_processes=[{"local_id": "spb", "remote_process_id": "spb_r"}]),
            local_device_ids=set(),
            manager_raw={},
        )
        peer = cfg.peers[0]
        self.assertEqual(len(peer.mirror_devices), 0)
        self.assertEqual(peer.mirror_processes[0].local_id, "spb")
        self.assertEqual(peer.mirror_processes[0].remote_process_id, "spb_r")
        self.assertIn("spb", cfg.mirrored_local_ids())
        self.assertEqual(cfg.mirrored_process_local_ids(), ("spb",))

    def test_peer_with_neither_devices_nor_processes_errors(self) -> None:
        with self.assertRaises(ConfigError):
            parse_federation_config(_peer(), local_device_ids=set(), manager_raw={})

    def test_process_allowlist_default_deny_all(self) -> None:
        cfg = parse_federation_config(
            _peer(mirror_processes=[{"local_id": "spb", "remote_process_id": "spb_r"}]),
            local_device_ids=set(),
            manager_raw={},
        )
        policy = cfg.peers[0].policy
        # Empty allowlist -> deny everything (vs device default "*").
        self.assertEqual(policy.allow_process_actions, ())
        self.assertFalse(policy.allows_process_action("mw.retune"))
        # Device ACL is independent and still wide open.
        self.assertTrue(policy.allows_device_action("set_frequency"))

    def test_process_allowlist_allows_only_listed(self) -> None:
        cfg = parse_federation_config(
            _peer(
                mirror_processes=[{"local_id": "spb", "remote_process_id": "spb_r"}],
                policy={"allow_process_actions": ["mw.retune"]},
            ),
            local_device_ids=set(),
            manager_raw={},
        )
        policy = cfg.peers[0].policy
        self.assertTrue(policy.allows_process_action("mw.retune"))
        self.assertFalse(policy.allows_process_action("mw.disable_rf"))

    def test_process_deny_takes_precedence(self) -> None:
        cfg = parse_federation_config(
            _peer(
                mirror_processes=[{"local_id": "spb", "remote_process_id": "spb_r"}],
                policy={
                    "allow_process_actions": ["mw.*"],
                    "deny_process_actions": ["mw.abort"],
                },
            ),
            local_device_ids=set(),
            manager_raw={},
        )
        policy = cfg.peers[0].policy
        self.assertTrue(policy.allows_process_action("mw.retune"))
        self.assertFalse(policy.allows_process_action("mw.abort"))

    def test_duplicate_process_local_id_errors(self) -> None:
        with self.assertRaises(ConfigError):
            parse_federation_config(
                _peer(
                    mirror_processes=[
                        {"local_id": "spb", "remote_process_id": "a"},
                        {"local_id": "spb", "remote_process_id": "b"},
                    ]
                ),
                local_device_ids=set(),
                manager_raw={},
            )

    def test_process_local_id_collides_with_device_local_id(self) -> None:
        with self.assertRaises(ConfigError):
            parse_federation_config(
                _peer(
                    mirror_devices=[{"local_id": "x", "remote_device_id": "d"}],
                    mirror_processes=[{"local_id": "x", "remote_process_id": "p"}],
                ),
                local_device_ids=set(),
                manager_raw={},
            )

    def test_process_local_id_collides_with_local_device(self) -> None:
        with self.assertRaises(ConfigError):
            parse_federation_config(
                _peer(mirror_processes=[{"local_id": "psu", "remote_process_id": "p"}]),
                local_device_ids={"psu"},
                manager_raw={},
            )

    def test_duplicate_remote_process_mapping_errors(self) -> None:
        with self.assertRaises(ConfigError):
            parse_federation_config(
                _peer(
                    mirror_processes=[
                        {"local_id": "a", "remote_process_id": "same"},
                        {"local_id": "b", "remote_process_id": "same"},
                    ]
                ),
                local_device_ids=set(),
                manager_raw={},
            )

    def test_device_and_process_can_share_remote_id_string(self) -> None:
        # Disjoint remote keyspaces: a device "x" and a process "x" don't collide.
        cfg = parse_federation_config(
            _peer(
                mirror_devices=[{"local_id": "dev", "remote_device_id": "x"}],
                mirror_processes=[{"local_id": "proc", "remote_process_id": "x"}],
            ),
            local_device_ids=set(),
            manager_raw={},
        )
        self.assertEqual(len(cfg.peers[0].mirror_devices), 1)
        self.assertEqual(len(cfg.peers[0].mirror_processes), 1)


# --------------------------------------------------------------------------
# Hub: RPC forwarding + telemetry relay + schema
# --------------------------------------------------------------------------
def _make_process_hub(allow=("mw.retune",)):
    cfg = parse_federation_config(
        _peer(
            mirror_processes=[{"local_id": "spb", "remote_process_id": "spb_r"}],
            policy={"allow_process_actions": list(allow)},
        ),
        local_device_ids=set(),
        manager_raw={},
    )
    events: list[tuple] = []
    mgr = SimpleNamespace(
        _publish_manager_event=lambda topic, payload: events.append((topic, payload)),
        _telemetry_last_bundle_ts={},
    )
    hub = FederationHub(
        ctx=zmq.Context.instance(),
        poller=zmq.Poller(),
        manager=mgr,
        config=cfg,
        instance_id="lab1",
    )
    return hub, events


class ProcessFederationHubTests(unittest.TestCase):
    def test_is_mirrored_process(self) -> None:
        hub, _ = _make_process_hub()
        self.assertTrue(hub.is_mirrored_process("spb"))
        self.assertFalse(hub.is_mirrored_process("nope"))

    def test_forward_unmirrored_returns_none(self) -> None:
        hub, _ = _make_process_hub()
        self.assertIsNone(
            hub.forward_process_request({"process_id": "other", "request": {"type": "x"}})
        )

    def test_forward_rewrites_process_id_and_keeps_type(self) -> None:
        hub, _ = _make_process_hub()
        captured: list = []
        hub._rpc_call = lambda peer_rt, payload: (  # type: ignore[method-assign]
            captured.append(payload) or {"ok": True, "result": {"state": "RF_ON"}}
        )
        resp = hub.forward_process_request(
            {
                "type": "manager.processes.rpc",
                "process_id": "spb",
                "request": {"type": "mw.retune", "params": {"frequency_ghz": 13.3}},
            }
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(len(captured), 1)
        out = captured[0]
        self.assertEqual(out["process_id"], "spb_r")  # local -> remote
        self.assertEqual(out["type"], "manager.processes.rpc")
        self.assertIn("federation", out)

    def test_forward_denied_action_never_reaches_peer(self) -> None:
        hub, _ = _make_process_hub(allow=("mw.retune",))
        called = {"n": 0}
        hub._rpc_call = lambda *a, **k: called.__setitem__("n", called["n"] + 1)  # type: ignore[method-assign]
        resp = hub.forward_process_request(
            {"process_id": "spb", "request": {"type": "mw.disable_rf"}}
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "federation_acl_denied")
        self.assertEqual(called["n"], 0)

    def test_forward_peer_unavailable(self) -> None:
        hub, _ = _make_process_hub()
        hub._rpc_call = lambda *a, **k: None  # type: ignore[method-assign]
        resp = hub.forward_process_request(
            {"process_id": "spb", "request": {"type": "mw.retune"}}
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "peer_unavailable")

    def test_relay_process_telemetry_maps_id_and_stamps_remote(self) -> None:
        hub, events = _make_process_hub()
        peer_rt = hub._peers["lab2"]
        hub._relay_process_telemetry(
            peer_rt,
            {"process_id": "spb_r", "signals": {"ready": {"value": 1.0}}},
        )
        self.assertEqual(len(events), 1)
        topic, payload = events[0]
        self.assertEqual(topic, "manager.process_telemetry_update")
        self.assertEqual(payload["process_id"], "spb")  # remote -> local
        self.assertTrue(payload["is_remote"])
        self.assertEqual(payload["source_kind"], "process")
        self.assertEqual(payload["owner_peer_id"], "lab2")

    def test_relay_process_telemetry_unknown_id_dropped(self) -> None:
        hub, events = _make_process_hub()
        peer_rt = hub._peers["lab2"]
        hub._relay_process_telemetry(peer_rt, {"process_id": "ghost", "signals": {}})
        self.assertEqual(events, [])

    def test_relay_stamps_is_remote_even_without_origin_meta(self) -> None:
        # is_remote is a trust-boundary flag the hub Influx writer relies on to
        # skip federated data; it must be stamped regardless of include_origin_meta
        # (which only gates the descriptive origin fields).
        cfg = parse_federation_config(
            _peer(
                mirror_processes=[{"local_id": "spb", "remote_process_id": "spb_r"}],
                policy={"allow_process_actions": ["mw.retune"]},
                relay={"include_origin_meta": False},
            ),
            local_device_ids=set(),
            manager_raw={},
        )
        events: list = []
        mgr = SimpleNamespace(
            _publish_manager_event=lambda t, p: events.append((t, p)),
            _telemetry_last_bundle_ts={},
        )
        hub = FederationHub(
            ctx=zmq.Context.instance(),
            poller=zmq.Poller(),
            manager=mgr,
            config=cfg,
            instance_id="lab1",
        )
        hub._relay_process_telemetry(
            hub._peers["lab2"], {"process_id": "spb_r", "signals": {"ready": {"value": 1.0}}}
        )
        self.assertEqual(len(events), 1)
        _topic, payload = events[0]
        self.assertTrue(payload["is_remote"])  # stamped despite include_origin_meta=False
        # Descriptive origin fields are gated by include_origin_meta:
        self.assertNotIn("owner_peer_id", payload)

    def test_relay_overwrites_spoofed_is_remote_false(self) -> None:
        hub, events = _make_process_hub()
        hub._relay_process_telemetry(
            hub._peers["lab2"],
            {"process_id": "spb_r", "is_remote": False, "signals": {}},
        )
        self.assertTrue(events[0][1]["is_remote"])

    def test_process_schema_placeholder_then_warmed(self) -> None:
        hub, _ = _make_process_hub()
        placeholder = hub.process_telemetry_schema_processes()
        self.assertEqual(len(placeholder), 1)
        self.assertEqual(placeholder[0]["process_id"], "spb")
        self.assertEqual(placeholder[0]["signals"], [])
        self.assertEqual(placeholder[0]["source_kind"], "process")
        self.assertTrue(placeholder[0]["is_remote"])

        # Simulate a warmed schema and confirm it surfaces (id rewritten).
        mirror = hub._process_mirrors["spb"]
        mirror.schema_entry = hub._rewrite_process_schema_entry(
            mirror,
            {"process_id": "spb_r", "signals": ["ready"], "dtypes": ["f8"], "units": [""]},
        )
        warmed = hub.process_telemetry_schema_processes()
        self.assertEqual(warmed[0]["process_id"], "spb")
        self.assertEqual(warmed[0]["signals"], ["ready"])
        self.assertTrue(warmed[0]["is_remote"])


# --------------------------------------------------------------------------
# ManagerClient: separate process-telemetry cache
# --------------------------------------------------------------------------
class ProcessTelemetryCacheTests(unittest.TestCase):
    def _client(self) -> ManagerClient:
        return ManagerClient(
            ctx=zmq.Context.instance(),
            manager_rpc="tcp://127.0.0.1:59999",
            manager_pub="tcp://127.0.0.1:59998",
            rpc_timeout_ms=50,
            subscribe_telemetry=False,
        )

    def test_get_latest_process_separate_from_device(self) -> None:
        mc = self._client()
        try:
            mc._handle_process_telemetry_update(
                {
                    "process_id": "spb",
                    "signals": {"ready": {"value": 1.0, "units": ""}},
                    "ts": {"t_wall": 1.0, "t_mono": 1.0},
                }
            )
            sample = mc.get_latest_process("spb", "ready")
            assert sample is not None
            self.assertEqual(sample["value"], 1.0)
            self.assertIn("age_s", sample)
            # Device cache is independent: same id is NOT visible there.
            self.assertIsNone(mc.get_latest("spb", "ready"))
        finally:
            mc.close()


# --------------------------------------------------------------------------
# Sequencer: `process:` addressing
# --------------------------------------------------------------------------
class SequencerProcessAddressingTests(unittest.TestCase):
    def test_call_step_parses_process(self) -> None:
        spec = parse_sequence(
            {
                "version": 1,
                "steps": [
                    {"call": {"process": "spb", "action": "mw.retune", "params": {}}}
                ],
            }
        )
        step = spec.steps[0]
        assert isinstance(step, CallStep)
        self.assertEqual(step.process, "spb")
        self.assertEqual(step.device, "")

    def test_call_step_rejects_both_device_and_process(self) -> None:
        with self.assertRaises(TypeError):
            parse_sequence(
                {
                    "version": 1,
                    "steps": [
                        {"call": {"device": "d", "process": "p", "action": "a"}}
                    ],
                }
            )

    def _runtime(self):
        calls: dict[str, list] = {"device": [], "process": []}
        tel: dict[str, dict] = {
            "device": {("d", "s"): {"value": 1, "t_mono": 9e18}},
            "process": {("spb", "ready"): {"value": 1, "t_mono": 9e18}},
        }
        rt = SequencerRuntime(
            call_device=lambda d, a, p: calls["device"].append((d, a, p)) or {"ok": True, "result": None},
            get_telemetry=lambda d, s: tel["device"].get((d, s)),
            set_stream_context=lambda *a: None,
            call_process=lambda pid, a, p: calls["process"].append((pid, a, p)) or {"ok": True, "result": None},
            get_process_telemetry=lambda pid, s: tel["process"].get((pid, s)),
        )
        return rt, calls

    def test_call_step_dispatches_to_process(self) -> None:
        rt, calls = self._runtime()
        rt._execute_call_step(
            CallStep(device="", action="mw.retune", params={}, process="spb")
        )
        self.assertEqual(calls["process"], [("spb", "mw.retune", {})])
        self.assertEqual(calls["device"], [])

    def test_call_step_dispatches_to_device(self) -> None:
        rt, calls = self._runtime()
        rt._execute_call_step(CallStep(device="d", action="act", params={}))
        self.assertEqual(calls["device"], [("d", "act", {})])
        self.assertEqual(calls["process"], [])

    def test_resolve_value_telemetry_process(self) -> None:
        rt, _ = self._runtime()
        value = rt._resolve_value(
            {"telemetry": {"process": "spb", "signal": "ready"}}
        )
        self.assertEqual(value, 1)

    def test_adaptive_telemetry_supports_process(self) -> None:
        rt, _ = self._runtime()
        value = rt._sample_adaptive_telemetry({"process": "spb", "signal": "ready"})
        self.assertEqual(value, 1)

    def test_adaptive_call_supports_process(self) -> None:
        rt, calls = self._runtime()
        rt._sample_adaptive_call(
            {"process": "spb", "action": "mw.retune", "params": {}}
        )
        self.assertEqual(calls["process"], [("spb", "mw.retune", {})])
        self.assertEqual(calls["device"], [])

    def test_process_call_without_callback_fails_gracefully(self) -> None:
        rt = SequencerRuntime(
            call_device=lambda d, a, p: {"ok": True},
            get_telemetry=lambda d, s: None,
            set_stream_context=lambda *a: None,
        )
        resp = rt._dispatch_call(device="", process="spb", action="mw.retune", params={})
        self.assertFalse(resp["ok"])


# --------------------------------------------------------------------------
# Process telemetry schema advertise (eager + idempotent)
# --------------------------------------------------------------------------
class ProcessTelemetryAdvertiseTests(unittest.TestCase):
    def test_advertise_sends_schema_once(self) -> None:
        from experiment_control.processes.process_base import ManagedProcessBase

        class _P(ManagedProcessBase):
            def process_telemetry_schema(self):
                return [{"name": "ready", "dtype": "f8", "units": ""}]

        p = _P.__new__(_P)  # skip __init__ (needs sockets)
        p._process_id = "spb"
        p._process_telemetry_schema_advertised = False
        calls: list = []
        p._manager = SimpleNamespace(
            advertise_process_telemetry_schema=lambda **k: calls.append(k)
        )
        p._advertise_process_telemetry_schema()
        p._advertise_process_telemetry_schema()  # idempotent
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["process_id"], "spb")
        self.assertEqual(calls[0]["schema"][0]["name"], "ready")

    def test_advertise_noop_without_schema(self) -> None:
        from experiment_control.processes.process_base import ManagedProcessBase

        p = ManagedProcessBase.__new__(ManagedProcessBase)  # base returns None schema
        p._process_id = "x"
        p._process_telemetry_schema_advertised = False
        calls: list = []
        p._manager = SimpleNamespace(
            advertise_process_telemetry_schema=lambda **k: calls.append(k)
        )
        p._advertise_process_telemetry_schema()
        self.assertEqual(calls, [])


# --------------------------------------------------------------------------
# route_process_rpc: local-first precedence
# --------------------------------------------------------------------------
class RouteProcessRpcPrecedenceTests(unittest.TestCase):
    def _manager(self, *, has_local: bool):
        self.fed_calls: list = []
        self.local_calls: list = []
        processes = {}
        if has_local:
            processes["spb"] = SimpleNamespace(state="RUNNING", rpc_endpoint="tcp://x")
        return SimpleNamespace(
            _normalize_command_source=lambda **k: ("sequencer", None),
            _publish_process_command_response=lambda **k: k["response"],
            _processes=processes,
            _federation_hub=SimpleNamespace(
                is_mirrored_process=lambda pid: pid == "spb",
                forward_process_request=lambda req: (
                    self.fed_calls.append(req) or {"ok": True, "result": "FED"}
                ),
            ),
            _call_process_rpc=lambda process_id, request: (
                self.local_calls.append(process_id) or {"ok": True, "result": "LOCAL"}
            ),
        )

    def _req(self):
        return {
            "type": "manager.processes.rpc",
            "process_id": "spb",
            "request": {"type": "mw.retune", "params": {}},
        }

    def test_local_process_wins_over_mirror(self) -> None:
        mgr = self._manager(has_local=True)
        resp = route_process_rpc(
            mgr, self._req(), running_states={"RUNNING"}, starting_state="STARTING"
        )
        self.assertEqual(resp["result"], "LOCAL")
        self.assertEqual(self.fed_calls, [])  # federation NOT consulted

    def test_forwards_when_no_local_process(self) -> None:
        mgr = self._manager(has_local=False)
        resp = route_process_rpc(
            mgr, self._req(), running_states={"RUNNING"}, starting_state="STARTING"
        )
        self.assertEqual(resp["result"], "FED")
        self.assertEqual(self.local_calls, [])


# --------------------------------------------------------------------------
# Federated-process liveness (relayed heartbeats) + list snapshot
# --------------------------------------------------------------------------
class ProcessFederationLivenessTests(unittest.TestCase):
    def test_relay_process_heartbeat_populates_mirror(self) -> None:
        hub, _ = _make_process_hub(allow=("mw.retune",))
        peer_rt = hub._peers["lab2"]
        hub._relay_process_heartbeat(
            peer_rt,
            {
                "process_id": "spb_r",
                "state": "RUNNING",
                "pid": 4321,
                "ts": {"t_mono": 1.0},
            },
        )
        mirror = hub._process_mirrors["spb"]
        self.assertIsNotNone(mirror.last_hb_recv_mono)
        # remote->local id mapping + retained state/pid.
        self.assertEqual(mirror.last_hb_payload["process_id"], "spb")
        self.assertEqual(mirror.last_hb_payload["state"], "RUNNING")
        self.assertEqual(mirror.last_hb_payload["pid"], 4321)

    def test_relay_process_heartbeat_unknown_id_ignored(self) -> None:
        hub, _ = _make_process_hub()
        peer_rt = hub._peers["lab2"]
        hub._relay_process_heartbeat(
            peer_rt, {"process_id": "ghost", "state": "RUNNING"}
        )
        self.assertIsNone(hub._process_mirrors["spb"].last_hb_recv_mono)

    def test_list_processes_snapshot_online_after_heartbeat(self) -> None:
        hub, _ = _make_process_hub(allow=("mw.retune",))
        peer_rt = hub._peers["lab2"]
        hub._relay_process_heartbeat(
            peer_rt, {"process_id": "spb_r", "state": "RUNNING", "pid": 7}
        )
        snap = hub.list_processes_snapshot()
        self.assertEqual(len(snap), 1)
        entry = snap[0]
        self.assertEqual(entry["process_id"], "spb")
        self.assertTrue(entry["is_remote"])
        self.assertEqual(entry["source_kind"], "federated")
        self.assertEqual(entry["owner_peer_id"], "lab2")
        self.assertEqual(entry["remote_process_id"], "spb_r")
        self.assertEqual(entry["state"], "RUNNING")
        self.assertEqual(entry["pid"], 7)
        self.assertEqual(entry["liveness"], "ONLINE")

    def test_list_processes_snapshot_offline_when_no_or_stale_heartbeat(self) -> None:
        hub, _ = _make_process_hub()
        # No heartbeat yet -> OFFLINE + placeholder state.
        snap = hub.list_processes_snapshot()
        self.assertEqual(snap[0]["liveness"], "OFFLINE")
        self.assertEqual(snap[0]["state"], "FEDERATED")
        # Stale heartbeat (older than event_stale_s) -> OFFLINE.
        mirror = hub._process_mirrors["spb"]
        peer_rt = hub._peers["lab2"]
        mirror.last_hb_payload = {"state": "RUNNING"}
        mirror.last_hb_recv_mono = time.monotonic() - (
            peer_rt.config.event_stale_s + 100.0
        )
        self.assertEqual(hub.list_processes_snapshot()[0]["liveness"], "OFFLINE")


# --------------------------------------------------------------------------
# Capability introspection always allowed + per-member ACL annotation
# --------------------------------------------------------------------------
class ProcessFederationCapabilityAclTests(unittest.TestCase):
    def test_capabilities_allowed_and_members_annotated(self) -> None:
        hub, _ = _make_process_hub(allow=("mw.retune",))
        hub._rpc_call = lambda peer_rt, payload: {  # type: ignore[method-assign]
            "ok": True,
            "result": {
                "version": 1,
                "members": [{"name": "mw.retune"}, {"name": "mw.abort"}],
            },
        }
        resp = hub.forward_process_request(
            {
                "type": "manager.processes.rpc",
                "process_id": "spb",
                "request": {"type": "process.capabilities", "params": {}},
            }
        )
        self.assertTrue(resp["ok"])
        by_name = {m["name"]: m for m in resp["result"]["members"]}
        self.assertTrue(by_name["mw.retune"]["federation_allowed"])
        self.assertFalse(by_name["mw.abort"]["federation_allowed"])

    def test_capabilities_not_denied_even_when_not_allowlisted(self) -> None:
        hub, _ = _make_process_hub(allow=())  # deny-all domain actions
        called = {"n": 0}

        def _rpc(peer_rt, payload):
            called["n"] += 1
            return {"ok": True, "result": {"members": []}}

        hub._rpc_call = _rpc  # type: ignore[method-assign]
        resp = hub.forward_process_request(
            {"process_id": "spb", "request": {"type": "process.capabilities"}}
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(called["n"], 1)  # forwarded, not ACL-denied

    def test_domain_action_still_denied(self) -> None:
        hub, _ = _make_process_hub(allow=("mw.retune",))
        called = {"n": 0}
        hub._rpc_call = lambda *a, **k: called.__setitem__(  # type: ignore[method-assign]
            "n", called["n"] + 1
        )
        resp = hub.forward_process_request(
            {"process_id": "spb", "request": {"type": "mw.abort"}}
        )
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "federation_acl_denied")
        self.assertEqual(called["n"], 0)


# --------------------------------------------------------------------------
# Manager merges federated processes into manager.processes.list
# --------------------------------------------------------------------------
class ManagerListProcessesMergeTests(unittest.TestCase):
    def test_merges_and_tags_local_and_remote(self) -> None:
        mgr = object.__new__(Manager)
        mgr._processes = {"local1": object()}
        mgr._process_snapshot = lambda h: {"process_id": "local1", "state": "RUNNING"}

        class _Hub:
            def list_processes_snapshot(self):
                return [
                    {
                        "process_id": "spb",
                        "is_remote": True,
                        "source_kind": "federated",
                        "owner_peer_id": "lab2",
                    }
                ]

        mgr._federation_hub = _Hub()
        result = Manager.list_processes(mgr)
        self.assertEqual([r["process_id"] for r in result], ["local1", "spb"])
        local = next(r for r in result if r["process_id"] == "local1")
        self.assertFalse(local["is_remote"])
        self.assertEqual(local["source_kind"], "local")
        self.assertIsNone(local["owner_peer_id"])
        remote = next(r for r in result if r["process_id"] == "spb")
        self.assertTrue(remote["is_remote"])


if __name__ == "__main__":
    unittest.main()
