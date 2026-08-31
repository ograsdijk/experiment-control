from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from experiment_control import manager as manager_module
from experiment_control._manager import driver_pub
from experiment_control._manager.driver_pub import ingest_telemetry
from experiment_control._manager.models import TelemetrySignal
from experiment_control.federation import parse_federation_config
from experiment_control.federation.hub import FederationHub
from experiment_control.types import TelemetryQuality, Timestamp


def _manager_stub(*, stale_s: float = 10.0) -> Any:
    mgr = SimpleNamespace()
    mgr._telemetry_latest = {}
    mgr._telemetry_last_bundle_ts = {}
    mgr._telemetry_last_recv_mono = {}
    mgr._telemetry_device_order = {}
    mgr._telemetry_cache_max_devices = 16
    mgr._telemetry_cache_max_signals_per_device = 16
    mgr._telemetry_stale_s = stale_s
    mgr.events = []

    def _publish_manager_event(topic: str, payload: dict[str, Any]) -> None:
        mgr.events.append((topic, payload))

    def _parse_timestamp(raw: Any) -> Timestamp:
        if not isinstance(raw, dict):
            raise TypeError("timestamp must be a dict")
        return Timestamp(t_wall=float(raw["t_wall"]), t_mono=float(raw["t_mono"]))

    def _coerce_enum(enum_cls: Any, value: Any, default: Any) -> Any:
        if isinstance(value, enum_cls):
            return value
        try:
            return enum_cls(value)
        except Exception:
            return default

    mgr._publish_manager_event = _publish_manager_event
    mgr._parse_timestamp = _parse_timestamp
    mgr._coerce_enum = _coerce_enum
    mgr._get_device_telemetry_snapshot = lambda device_id: (
        manager_module.Manager._get_device_telemetry_snapshot(mgr, device_id)
    )
    mgr._telemetry_snapshot = lambda: manager_module.Manager._telemetry_snapshot(mgr)
    return mgr


def _ingest(
    mgr: Any,
    *,
    recv_mono: float,
    source_mono: float,
    signals: dict[str, dict[str, Any]],
    seq: int = 1,
) -> None:
    with mock.patch.object(driver_pub.time, "monotonic", return_value=recv_mono):
        ingest_telemetry(
            mgr,
            {
                "device_id": "dev",
                "seq": seq,
                "ts": {"t_wall": 1234.0 + source_mono, "t_mono": source_mono},
                "signals": signals,
            },
            telemetry_signal_cls=TelemetrySignal,
            timestamp_cls=Timestamp,
            telemetry_quality_enum=TelemetryQuality,
        )


class TelemetryReceiveFreshnessTests(unittest.TestCase):
    def test_cross_host_producer_monotonic_does_not_mark_fresh_signal_stale(self) -> None:
        mgr = _manager_stub(stale_s=10.0)

        _ingest(
            mgr,
            recv_mono=2_000_000.0,
            source_mono=10_000.0,
            signals={"pressure": {"value": 1.2e-7, "quality": "OK"}},
        )

        freshness_ts, signal = mgr._telemetry_latest["dev"]["pressure"]
        self.assertEqual(freshness_ts.t_mono, 2_000_000.0)
        self.assertIsNotNone(signal.ts)
        assert signal.ts is not None
        self.assertEqual(signal.ts.t_mono, 10_000.0)
        self.assertEqual(mgr._telemetry_last_bundle_ts["dev"].t_mono, 10_000.0)
        self.assertEqual(mgr._telemetry_last_recv_mono["dev"], 2_000_000.0)

        manager_module.Manager._mark_stale_telemetry(mgr, 2_000_005.0)
        self.assertEqual(
            mgr._telemetry_latest["dev"]["pressure"][1].quality,
            TelemetryQuality.OK,
        )
        self.assertFalse(
            [topic for topic, _payload in mgr.events if topic == "manager.telemetry_stale"]
        )

        manager_module.Manager._mark_stale_telemetry(mgr, 2_000_011.0)
        self.assertEqual(
            mgr._telemetry_latest["dev"]["pressure"][1].quality,
            TelemetryQuality.STALE,
        )
        stale_events = [
            payload for topic, payload in mgr.events if topic == "manager.telemetry_stale"
        ]
        self.assertEqual(len(stale_events), 1)
        self.assertEqual(stale_events[0]["signals"], ["pressure"])
        self.assertAlmostEqual(float(stale_events[0]["age_s"]), 11.0)

    def test_federation_relay_reingests_with_consuming_manager_clock(self) -> None:
        mgr = _manager_stub(stale_s=10.0)
        mgr._ingest_telemetry = lambda payload: manager_module.Manager._ingest_telemetry(
            mgr, payload
        )
        cfg = parse_federation_config(
            {
                "peers": [
                    {
                        "peer_id": "remote",
                        "router_rpc": "tcp://10.0.0.2:6000",
                        "manager_pub": "tcp://10.0.0.2:6001",
                        "mirror_devices": [
                            {"local_id": "mirror", "remote_device_id": "remote_dev"}
                        ],
                    }
                ]
            },
            local_device_ids=set(),
            manager_raw={},
        )
        hub = FederationHub(
            ctx=mock.Mock(),
            poller=mock.Mock(),
            manager=mgr,
            config=cfg,
            instance_id="local",
        )

        with mock.patch.object(driver_pub.time, "monotonic", return_value=50_000.0):
            hub._relay_event(
                hub._peers["remote"],
                "manager.telemetry_update",
                {
                    "device_id": "remote_dev",
                    "seq": 7,
                    "ts": {"t_wall": 1234.0, "t_mono": 123.0},
                    "signals": {"x": {"value": 4.0, "quality": "OK"}},
                },
            )

        recv_ts, signal = mgr._telemetry_latest["mirror"]["x"]
        self.assertEqual(recv_ts.t_mono, 50_000.0)
        self.assertIsNotNone(signal.ts)
        assert signal.ts is not None
        self.assertEqual(signal.ts.t_mono, 123.0)
        self.assertEqual(mgr._telemetry_last_recv_mono["mirror"], 50_000.0)
        self.assertEqual(mgr._telemetry_last_bundle_ts["mirror"].t_mono, 123.0)

        updates = [
            payload for topic, payload in mgr.events if topic == "manager.telemetry_update"
        ]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["device_id"], "mirror")
        self.assertEqual(updates[0]["ts"]["t_mono"], 123.0)
        self.assertEqual(updates[0]["ts"]["t_mono_recv"], 50_000.0)

    def test_partial_bundle_refreshes_only_signals_that_arrived(self) -> None:
        mgr = _manager_stub(stale_s=10.0)

        _ingest(
            mgr,
            recv_mono=100.0,
            source_mono=1_000.0,
            signals={
                "a": {"value": 1, "quality": "OK"},
                "b": {"value": 2, "quality": "OK"},
            },
            seq=1,
        )
        _ingest(
            mgr,
            recv_mono=108.0,
            source_mono=1_008.0,
            signals={"a": {"value": 3, "quality": "OK"}},
            seq=2,
        )

        self.assertEqual(mgr._telemetry_latest["dev"]["a"][0].t_mono, 108.0)
        self.assertEqual(mgr._telemetry_latest["dev"]["b"][0].t_mono, 100.0)

        get_response = manager_module.Manager._route_type_get_telemetry(
            mgr, {"device_id": "dev"}
        )
        snapshot = get_response["telemetry"]
        self.assertEqual(snapshot["a"]["ts"]["t_mono_recv"], 108.0)
        self.assertEqual(snapshot["b"]["ts"]["t_mono_recv"], 100.0)
        self.assertEqual(snapshot["a"]["ts"]["t_mono"], 1_008.0)
        self.assertEqual(snapshot["b"]["ts"]["t_mono"], 1_000.0)

        all_response = manager_module.Manager._route_type_telemetry_snapshot(mgr, {})
        all_snapshot = all_response["result"]["devices"]["dev"]
        self.assertEqual(all_snapshot["a"]["ts"]["t_mono_recv"], 108.0)
        self.assertEqual(all_snapshot["b"]["ts"]["t_mono_recv"], 100.0)

        manager_module.Manager._mark_stale_telemetry(mgr, 111.0)

        self.assertEqual(
            mgr._telemetry_latest["dev"]["a"][1].quality,
            TelemetryQuality.OK,
        )
        self.assertEqual(
            mgr._telemetry_latest["dev"]["b"][1].quality,
            TelemetryQuality.STALE,
        )
        stale_events = [
            payload for topic, payload in mgr.events if topic == "manager.telemetry_stale"
        ]
        self.assertEqual(len(stale_events), 1)
        self.assertEqual(stale_events[0]["signals"], ["b"])
        self.assertAlmostEqual(float(stale_events[0]["age_s"]), 11.0)

    def test_republished_bundle_preserves_producer_timestamp(self) -> None:
        mgr = _manager_stub()
        _ingest(
            mgr,
            recv_mono=50_000.0,
            source_mono=123.0,
            signals={"x": {"value": 4.0, "quality": "OK"}},
        )

        updates = [
            payload for topic, payload in mgr.events if topic == "manager.telemetry_update"
        ]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["ts"]["t_mono"], 123.0)
        self.assertEqual(updates[0]["ts"]["t_mono_recv"], 50_000.0)
        self.assertEqual(mgr._telemetry_latest["dev"]["x"][1].ts.t_mono, 123.0)


if __name__ == "__main__":
    unittest.main()
