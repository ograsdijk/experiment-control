# ruff: noqa: E402
"""Regression test for federation relay of events without a device_id.

Prior to the fix, `_resolve_local_id` returned `None` for events with no
`device_id` regardless of `relay.only_mirrored_devices`, so disabling the
flag had no effect. This test asserts the documented behaviour:

* `only_mirrored_devices=True`  (default): drop non-device events.
* `only_mirrored_devices=False`: relay non-device events verbatim
  (with optional origin metadata), but never inject orphan heartbeats
  or telemetry into the manager caches.
"""

import sys
import unittest
from pathlib import Path

import zmq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.federation import parse_federation_config
from experiment_control.federation.hub import FederationHub


class _RecordingManager:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.logs: list[tuple[str, dict]] = []
        self.telemetry: list[dict] = []
        self.heartbeats: list[dict] = []

    def _publish_manager_event(self, topic: str, payload: dict) -> None:
        self.events.append((topic, dict(payload)))

    def _emit_log_from_payload(self, payload: dict, *, default_topic: str) -> None:
        self.logs.append((default_topic, dict(payload)))

    def _ingest_telemetry(self, payload: dict) -> None:
        self.telemetry.append(dict(payload))

    def _ingest_heartbeat(self, payload: dict) -> None:
        self.heartbeats.append(dict(payload))


def _hub(only_mirrored: bool) -> tuple[FederationHub, _RecordingManager]:
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
                    "relay": {"only_mirrored_devices": only_mirrored},
                }
            ]
        },
        local_device_ids=set(),
        manager_raw={},
    )
    manager = _RecordingManager()
    hub = FederationHub(
        ctx=zmq.Context.instance(),
        poller=zmq.Poller(),
        manager=manager,
        config=cfg,
        instance_id="lab1",
    )
    return hub, manager


class FederationRelayNonDeviceTests(unittest.TestCase):
    def test_only_mirrored_true_drops_event_without_device_id(self) -> None:
        hub, manager = _hub(only_mirrored=True)
        peer_rt = hub._peers["lab2"]
        hub._relay_event(
            peer_rt,
            "manager.process.started",
            {"process_id": "sequencer", "ts": {"t_wall": 0.0, "t_mono": 0.0}},
        )
        self.assertEqual(manager.events, [])
        self.assertEqual(manager.logs, [])

    def test_only_mirrored_false_relays_event_without_device_id(self) -> None:
        hub, manager = _hub(only_mirrored=False)
        peer_rt = hub._peers["lab2"]
        hub._relay_event(
            peer_rt,
            "manager.process.started",
            {"process_id": "sequencer", "ts": {"t_wall": 0.0, "t_mono": 0.0}},
        )
        self.assertEqual(len(manager.events), 1)
        topic, payload = manager.events[0]
        self.assertEqual(topic, "manager.process.started")
        # Origin meta annotated when include_origin_meta is true (the default).
        self.assertEqual(payload.get("owner_peer_id"), "lab2")
        self.assertTrue(payload.get("is_remote"))

    def test_only_mirrored_false_relays_log_via_log_path(self) -> None:
        hub, manager = _hub(only_mirrored=False)
        peer_rt = hub._peers["lab2"]
        hub._relay_event(
            peer_rt,
            "manager.log",
            {"severity": "warning", "message": "peer warning"},
        )
        self.assertEqual(len(manager.logs), 1)
        default_topic, payload = manager.logs[0]
        self.assertEqual(default_topic, "manager.log")
        self.assertEqual(payload.get("owner_peer_id"), "lab2")

    def test_only_mirrored_false_does_not_inject_orphan_heartbeat(self) -> None:
        hub, manager = _hub(only_mirrored=False)
        peer_rt = hub._peers["lab2"]
        hub._relay_event(
            peer_rt,
            "manager.heartbeat",
            {"process_id": "sequencer", "state": "RUNNING"},
        )
        # Heartbeat/telemetry topics must not be injected as orphans.
        self.assertEqual(manager.heartbeats, [])
        self.assertEqual(manager.events, [])

    def test_only_mirrored_false_does_not_inject_orphan_telemetry(self) -> None:
        hub, manager = _hub(only_mirrored=False)
        peer_rt = hub._peers["lab2"]
        hub._relay_event(
            peer_rt,
            "manager.telemetry_update",
            {"device_id": "not-mirrored", "signals": {}},
        )
        self.assertEqual(manager.telemetry, [])
        self.assertEqual(manager.events, [])

    def test_mirrored_device_event_still_uses_rewrite_path(self) -> None:
        # Sanity check: when a device_id maps to a mirror, both flag settings
        # still rewrite + relay via the original code path.
        hub, manager = _hub(only_mirrored=True)
        peer_rt = hub._peers["lab2"]
        hub._relay_event(
            peer_rt,
            "manager.process.started",  # any pass-through topic
            {"device_id": "psu", "extra": "data"},
        )
        self.assertEqual(len(manager.events), 1)
        topic, payload = manager.events[0]
        self.assertEqual(topic, "manager.process.started")
        # The device_id was rewritten to the local mirror id.
        self.assertEqual(payload["device_id"], "lab2.psu")


if __name__ == "__main__":
    unittest.main()
