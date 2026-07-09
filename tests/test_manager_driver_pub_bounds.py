# ruff: noqa: E402

from __future__ import annotations

import sys
import time
import unittest
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import zmq

from experiment_control._manager import driver_pub
from experiment_control._manager.driver_pub import (
    handle_driver_pub,
    ingest_chunk_ready,
    ingest_telemetry,
)


@dataclass(frozen=True)
class _Timestamp:
    t_wall: float
    t_mono: float


class _TelemetryQuality(StrEnum):
    OK = "OK"
    BAD = "BAD"
    MISSING = "MISSING"
    STALE = "STALE"


@dataclass(frozen=True)
class _TelemetrySignal:
    value: Any
    units: str | None
    quality: _TelemetryQuality
    ts: _Timestamp | None
    quality_source: str


def _build_manager_stub() -> Any:
    mgr = SimpleNamespace()
    mgr._telemetry_latest = {}
    mgr._telemetry_last_bundle_ts = {}
    mgr._latest_chunk_desc = {}
    mgr._devices = {}
    mgr.events = []

    def _publish_manager_event(topic: str, payload: dict[str, Any]) -> None:
        mgr.events.append((topic, payload))

    def _parse_timestamp(raw: Any) -> _Timestamp:
        if isinstance(raw, dict):
            return _Timestamp(
                t_wall=float(raw.get("t_wall", 0.0)),
                t_mono=float(raw.get("t_mono", 0.0)),
            )
        now = time.monotonic()
        return _Timestamp(t_wall=time.time(), t_mono=now)

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
    mgr._ingest_chunk_ready = lambda msg: ingest_chunk_ready(mgr, msg)
    mgr._normalize_topic = lambda topic: str(topic).strip()
    mgr._maybe_publish_drain_cap_hit = (
        lambda socket, cap: mgr.events.append(
            ("manager.drain_cap_hit", {"socket": socket, "cap": cap})
        )
    )
    return mgr


class _FakeDriverSub:
    def __init__(self, messages: list[tuple[str, dict[str, Any]]]) -> None:
        self._messages = list(messages)

    def recv_multipart(self, _flags: int) -> list[bytes]:
        if not self._messages:
            raise zmq.Again()
        topic, payload = self._messages.pop(0)
        return [topic.encode("utf-8"), json.dumps(payload).encode("utf-8")]


class ManagerDriverPubBoundsTests(unittest.TestCase):
    def test_telemetry_device_cache_is_bounded(self) -> None:
        mgr = _build_manager_stub()
        mgr._telemetry_cache_max_devices = 2
        mgr._telemetry_cache_max_signals_per_device = 8

        for device_id in ("dev-1", "dev-2", "dev-3"):
            ingest_telemetry(
                mgr,
                {
                    "device_id": device_id,
                    "seq": 1,
                    "ts": {"t_wall": 1.0, "t_mono": 1.0},
                    "signals": {"x": {"value": 1.0, "quality": "OK"}},
                },
                telemetry_signal_cls=_TelemetrySignal,
                timestamp_cls=_Timestamp,
                telemetry_quality_enum=_TelemetryQuality,
            )

        self.assertEqual(set(mgr._telemetry_latest.keys()), {"dev-2", "dev-3"})
        self.assertNotIn("dev-1", mgr._telemetry_last_bundle_ts)
        self.assertEqual(int(getattr(mgr, "_telemetry_cache_evicted_devices", 0)), 1)

    def test_telemetry_republish_stamps_wall_recv(self) -> None:
        mgr = _build_manager_stub()
        mgr._telemetry_cache_max_devices = 4
        mgr._telemetry_cache_max_signals_per_device = 8

        before = time.time()
        ingest_telemetry(
            mgr,
            {
                "device_id": "dev-1",
                "seq": 1,
                # Source bundle clock, distinct from this manager's "now".
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {"x": {"value": 1.0, "quality": "OK"}},
            },
            telemetry_signal_cls=_TelemetrySignal,
            timestamp_cls=_Timestamp,
            telemetry_quality_enum=_TelemetryQuality,
        )
        after = time.time()

        published = [p for (topic, p) in mgr.events if topic == "manager.telemetry_update"]
        self.assertEqual(len(published), 1)
        ts = published[0]["ts"]
        # Source clock preserved verbatim...
        self.assertEqual(ts["t_wall"], 1.0)
        # ...while t_wall_recv is THIS manager's fresh wall clock at ingest.
        self.assertGreaterEqual(ts["t_wall_recv"], before)
        self.assertLessEqual(ts["t_wall_recv"], after)

    def test_telemetry_signal_cache_is_bounded_per_device(self) -> None:
        mgr = _build_manager_stub()
        mgr._telemetry_cache_max_devices = 4
        mgr._telemetry_cache_max_signals_per_device = 2

        ingest_telemetry(
            mgr,
            {
                "device_id": "dev-1",
                "seq": 1,
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {
                    "s1": {"value": 1.0, "quality": "OK"},
                    "s2": {"value": 2.0, "quality": "OK"},
                    "s3": {"value": 3.0, "quality": "OK"},
                },
            },
            telemetry_signal_cls=_TelemetrySignal,
            timestamp_cls=_Timestamp,
            telemetry_quality_enum=_TelemetryQuality,
        )

        signals = mgr._telemetry_latest["dev-1"]
        self.assertEqual(set(signals.keys()), {"s2", "s3"})
        self.assertEqual(int(getattr(mgr, "_telemetry_cache_evicted_signals", 0)), 1)

    def test_chunk_descriptor_cache_is_bounded(self) -> None:
        mgr = _build_manager_stub()
        mgr._chunk_cache_max_devices = 1
        mgr._chunk_cache_max_streams_per_device = 2

        ingest_chunk_ready(
            mgr,
            {"device_id": "dev-1", "stream": "a", "shm_name": "shm-a", "seq": 1},
        )
        ingest_chunk_ready(
            mgr,
            {"device_id": "dev-1", "stream": "b", "shm_name": "shm-b", "seq": 2},
        )
        ingest_chunk_ready(
            mgr,
            {"device_id": "dev-1", "stream": "c", "shm_name": "shm-c", "seq": 3},
        )

        self.assertEqual(set(mgr._latest_chunk_desc["dev-1"].keys()), {"b", "c"})
        self.assertEqual(int(getattr(mgr, "_chunk_cache_evicted_streams", 0)), 1)

        ingest_chunk_ready(
            mgr,
            {"device_id": "dev-2", "stream": "x", "shm_name": "shm-x", "seq": 1},
        )
        self.assertEqual(set(mgr._latest_chunk_desc.keys()), {"dev-2"})
        self.assertEqual(int(getattr(mgr, "_chunk_cache_evicted_devices", 0)), 1)

    def test_driver_pub_prioritizes_chunk_ready_within_drain_batch(self) -> None:
        mgr = _build_manager_stub()
        mgr._sub = _FakeDriverSub(
            [
                (
                    "dev-1/telemetry",
                    {
                        "device_id": "dev-1",
                        "seq": 1,
                        "ts": {"t_wall": 1.0, "t_mono": 1.0},
                        "signals": {"x": {"value": 1.0, "quality": "OK"}},
                    },
                ),
                (
                    "dev-1/heartbeat",
                    {
                        "device_id": "dev-1",
                        "pid": 123,
                        "seq": 1,
                        "ts": {"t_wall": 1.0, "t_mono": 1.0},
                    },
                ),
                (
                    "dev-1/chunk_ready",
                    {
                        "device_id": "dev-1",
                        "stream": "timestamps",
                        "shm_name": "ec-test-shm",
                        "seq": 5,
                    },
                ),
            ]
        )

        def _ingest_telemetry(msg: dict[str, Any]) -> None:
            mgr.events.append(("ingested.telemetry", msg))

        def _ingest_heartbeat(msg: dict[str, Any]) -> None:
            mgr.events.append(("ingested.heartbeat", msg))

        mgr._ingest_telemetry = _ingest_telemetry
        mgr._ingest_heartbeat = _ingest_heartbeat

        handle_driver_pub(mgr)

        topics = [topic for topic, _payload in mgr.events]
        self.assertLess(
            topics.index("manager.chunk_ready"),
            topics.index("ingested.telemetry"),
        )
        self.assertLess(
            topics.index("manager.chunk_ready"),
            topics.index("ingested.heartbeat"),
        )
        self.assertEqual(int(getattr(mgr, "_driver_chunk_ready_seen_total", 0)), 1)
        self.assertEqual(
            int(getattr(mgr, "_manager_chunk_ready_published_total", 0)),
            1,
        )

    def test_driver_pub_drain_cap_hits_are_counted(self) -> None:
        mgr = _build_manager_stub()
        mgr._sub = _FakeDriverSub(
            [
                (
                    f"dev-{idx}/telemetry",
                    {
                        "device_id": f"dev-{idx}",
                        "seq": idx,
                        "ts": {"t_wall": 1.0, "t_mono": 1.0},
                        "signals": {},
                    },
                )
                for idx in range(3)
            ]
        )
        mgr._ingest_telemetry = lambda msg: mgr.events.append(
            ("ingested.telemetry", msg)
        )

        original_cap = driver_pub.MAX_DRAIN_PER_TICK
        try:
            driver_pub.MAX_DRAIN_PER_TICK = 2
            handle_driver_pub(mgr)
        finally:
            driver_pub.MAX_DRAIN_PER_TICK = original_cap

        self.assertEqual(
            int(getattr(mgr, "_manager_driver_pub_drain_cap_hit_total", 0)),
            1,
        )
        self.assertIn(
            ("manager.drain_cap_hit", {"socket": "driver_pub", "cap": 2}),
            mgr.events,
        )

    def test_chunk_ready_ingest_errors_are_counted(self) -> None:
        mgr = _build_manager_stub()
        mgr._sub = _FakeDriverSub(
            [
                (
                    "dev-1/chunk_ready",
                    {
                        "device_id": "dev-1",
                        "stream": "timestamps",
                        "shm_name": "ec-test-shm",
                        "seq": 5,
                    },
                )
            ]
        )

        def _ingest_chunk_ready(_msg: dict[str, Any]) -> None:
            raise RuntimeError("boom")

        mgr._ingest_chunk_ready = _ingest_chunk_ready

        handle_driver_pub(mgr)

        self.assertEqual(int(getattr(mgr, "_driver_chunk_ready_seen_total", 0)), 1)
        self.assertEqual(
            int(getattr(mgr, "_manager_chunk_ready_ingest_error_total", 0)),
            1,
        )
        self.assertTrue(
            [payload for topic, payload in mgr.events if topic == "manager.chunk_error"]
        )


if __name__ == "__main__":
    unittest.main()
