# ruff: noqa: E402
"""Manager forwards per-call telemetry errors from drivers.

The driver-side change adds `call_errors` (dict[str, str]) to its
{device_id}/telemetry bundle. The manager re-publishes
manager.telemetry_update; this test asserts that `call_errors` is
forwarded verbatim when present, omitted when absent, and defensively
filtered against non-(str, str) entries.
"""

from __future__ import annotations

import sys
import time
import unittest
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control._manager.driver_pub import ingest_telemetry


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
        return _Timestamp(t_wall=time.time(), t_mono=time.monotonic())

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
    return mgr


def _ingest(mgr: Any, msg: dict[str, Any]) -> None:
    ingest_telemetry(
        mgr,
        msg,
        telemetry_signal_cls=_TelemetrySignal,
        timestamp_cls=_Timestamp,
        telemetry_quality_enum=_TelemetryQuality,
    )


def _telemetry_event(mgr: Any) -> dict[str, Any]:
    for topic, payload in mgr.events:
        if topic == "manager.telemetry_update":
            return payload
    raise AssertionError("no manager.telemetry_update event published")


class ForwardsCallErrorsTests(unittest.TestCase):
    def test_call_errors_forwarded_verbatim_when_present(self) -> None:
        mgr = _build_manager_stub()
        _ingest(
            mgr,
            {
                "device_id": "ctc100",
                "seq": 5,
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {"temp_a": {"value": None, "quality": "BAD"}},
                "call_errors": {
                    "read_temperatures": "ValueError('cannot convert NaN to int')"
                },
            },
        )
        payload = _telemetry_event(mgr)
        self.assertEqual(
            payload["call_errors"],
            {"read_temperatures": "ValueError('cannot convert NaN to int')"},
        )

    def test_call_errors_omitted_when_absent(self) -> None:
        mgr = _build_manager_stub()
        _ingest(
            mgr,
            {
                "device_id": "ctc100",
                "seq": 1,
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {"temp_a": {"value": 1.0, "quality": "OK"}},
            },
        )
        payload = _telemetry_event(mgr)
        self.assertNotIn("call_errors", payload)

    def test_call_errors_omitted_when_empty_dict(self) -> None:
        mgr = _build_manager_stub()
        _ingest(
            mgr,
            {
                "device_id": "ctc100",
                "seq": 1,
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {"temp_a": {"value": 1.0, "quality": "OK"}},
                "call_errors": {},
            },
        )
        payload = _telemetry_event(mgr)
        self.assertNotIn("call_errors", payload)

    def test_call_errors_filtered_against_non_str_keys_and_values(self) -> None:
        mgr = _build_manager_stub()
        _ingest(
            mgr,
            {
                "device_id": "ctc100",
                "seq": 1,
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {"temp_a": {"value": None, "quality": "BAD"}},
                "call_errors": {
                    "read_temperatures": "ValueError('boom')",
                    123: "wrong key type, must drop",  # noqa: PLR2004
                    "empty_key_below": None,  # wrong value type, must drop
                    "": "empty key, must drop",
                },
            },
        )
        payload = _telemetry_event(mgr)
        self.assertEqual(
            payload["call_errors"], {"read_temperatures": "ValueError('boom')"}
        )

    def test_call_errors_dropped_if_not_a_dict(self) -> None:
        mgr = _build_manager_stub()
        _ingest(
            mgr,
            {
                "device_id": "ctc100",
                "seq": 1,
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {"temp_a": {"value": None, "quality": "BAD"}},
                "call_errors": "not a dict",  # malformed producer
            },
        )
        payload = _telemetry_event(mgr)
        self.assertNotIn("call_errors", payload)


if __name__ == "__main__":
    unittest.main()
