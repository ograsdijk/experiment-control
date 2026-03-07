from __future__ import annotations

import sys
import unittest
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.influx_writer import (  # noqa: E402
    DeviceRoute,
    InfluxDestination,
    InfluxWriterProcess,
)


def _make_proc() -> InfluxWriterProcess:
    proc = InfluxWriterProcess.__new__(InfluxWriterProcess)
    proc._enabled = True  # noqa: SLF001
    proc._disabled_devices = set()  # noqa: SLF001
    proc._remote_device_ids = set()  # noqa: SLF001
    proc._instance_id = "lab_a"  # noqa: SLF001
    proc._include_device_type_tag = True  # noqa: SLF001
    proc._include_quality_fields = True  # noqa: SLF001
    proc._include_unit_fields = False  # noqa: SLF001
    proc._device_type_key = "device_type"  # noqa: SLF001
    proc._device_tag_keys = ["location"]  # noqa: SLF001
    proc._destinations = {  # noqa: SLF001
        "default": InfluxDestination(
            name="default",
            url="http://127.0.0.1:8086",
            org="org",
            bucket="bucket",
            token="",
            measurement="unknown_device",
            precision="ns",
            request_timeout_s=5.0,
            static_tags={},
        )
    }
    proc._default_destination = "default"  # noqa: SLF001
    proc._routes = {}  # noqa: SLF001
    proc._device_type_by_id = {}  # noqa: SLF001
    proc._device_tags_by_id = {}  # noqa: SLF001
    proc._queue = deque()  # noqa: SLF001
    proc._max_queue_points = 10_000  # noqa: SLF001
    proc._overflow_policy = "drop_oldest"  # noqa: SLF001
    proc._points_received = 0  # noqa: SLF001
    proc._points_queued = 0  # noqa: SLF001
    proc._points_written = 0  # noqa: SLF001
    proc._points_skipped_invalid = 0  # noqa: SLF001
    proc._points_skipped_remote = 0  # noqa: SLF001
    proc._points_dropped_overflow = 0  # noqa: SLF001
    proc._write_errors = 0  # noqa: SLF001
    proc._batches_written = 0  # noqa: SLF001
    proc._last_error = None  # noqa: SLF001
    proc._last_flush_wall_s = None  # noqa: SLF001
    proc._last_flush_mono_s = None  # noqa: SLF001
    return proc


class InfluxWriterWideModeTests(unittest.TestCase):
    def test_handle_device_config_tracks_type_tags_and_remote_flag(self) -> None:
        proc = _make_proc()
        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "pump1",
                "source_kind": "local",
                "is_remote": False,
                "device_metadata": {
                    "device_type": "hipace700",
                    "location": "rack_a",
                },
            }
        )
        self.assertEqual(proc._device_type_by_id.get("pump1"), "hipace700")  # noqa: SLF001
        self.assertEqual(proc._device_tags_by_id.get("pump1"), {"location": "rack_a"})  # noqa: SLF001
        self.assertNotIn("pump1", proc._remote_device_ids)  # noqa: SLF001

        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "pump1",
                "source_kind": "federated",
                "is_remote": True,
                "device_metadata": {"device_type": "hipace700"},
            }
        )
        self.assertIn("pump1", proc._remote_device_ids)  # noqa: SLF001

    def test_ingest_telemetry_writes_single_wide_row(self) -> None:
        proc = _make_proc()
        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "pump1",
                "source_kind": "local",
                "is_remote": False,
                "device_metadata": {
                    "device_type": "hipace700",
                    "location": "rack_a",
                },
            }
        )
        proc._ingest_telemetry(  # noqa: SLF001
            {
                "device_id": "pump1",
                "ts": {"t_wall": 1_700_000_000.5, "t_mono": 0.0},
                "signals": {
                    "rot_speed_hz": {
                        "value": 250.5,
                        "units": "Hz",
                        "quality": "OK",
                    },
                    "is_running": {
                        "value": True,
                        "units": "",
                        "quality": "OK",
                    },
                    "state": {
                        "value": "READY",
                        "units": "",
                        "quality": "OK",
                    },
                },
            }
        )

        self.assertEqual(len(proc._queue), 1)  # noqa: SLF001
        point = proc._queue[0]  # noqa: SLF001
        self.assertEqual(point.destination, "default")
        line = point.line
        self.assertTrue(line.startswith("hipace700,"))
        self.assertIn("device_id=pump1", line)
        self.assertIn("instance_id=lab_a", line)
        self.assertIn("location=rack_a", line)
        self.assertIn("rot_speed_hz=250.5", line)
        self.assertIn("is_running=true", line)
        self.assertIn('state="READY"', line)
        self.assertIn('rot_speed_hz__quality="OK"', line)
        self.assertNotIn("__unit", line)
        self.assertEqual(proc._points_received, 1)  # noqa: SLF001
        self.assertEqual(proc._points_queued, 1)  # noqa: SLF001

    def test_ingest_skips_remote_devices(self) -> None:
        proc = _make_proc()
        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "hub.trace1",
                "source_kind": "federated",
                "is_remote": True,
                "device_metadata": {"device_type": "dummy_trace"},
            }
        )
        proc._ingest_telemetry(  # noqa: SLF001
            {
                "device_id": "hub.trace1",
                "signals": {"x": {"value": 1.0, "quality": "OK", "units": "V"}},
            }
        )
        self.assertEqual(len(proc._queue), 0)  # noqa: SLF001
        self.assertEqual(proc._points_skipped_remote, 1)  # noqa: SLF001

    def test_route_overrides_measurement_and_tags(self) -> None:
        proc = _make_proc()
        proc._routes = {  # noqa: SLF001
            "pump1": DeviceRoute(
                destination="default",
                measurement="turbo_pump_custom",
                device_type=None,
                tags={"location": "rack_b", "station": "beamline_1"},
            )
        }
        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "pump1",
                "source_kind": "local",
                "is_remote": False,
                "device_metadata": {
                    "device_type": "hipace700",
                    "location": "rack_a",
                },
            }
        )
        proc._ingest_telemetry(  # noqa: SLF001
            {
                "device_id": "pump1",
                "signals": {"speed_hz": {"value": 100.0, "quality": "OK", "units": "Hz"}},
            }
        )
        self.assertEqual(len(proc._queue), 1)  # noqa: SLF001
        line = proc._queue[0].line  # noqa: SLF001
        self.assertTrue(line.startswith("turbo_pump_custom,"))
        self.assertIn("station=beamline_1", line)
        self.assertIn("location=rack_b", line)


if __name__ == "__main__":
    unittest.main()
