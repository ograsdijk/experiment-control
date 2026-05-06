from __future__ import annotations

import sys
import unittest
from collections import deque
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Any

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
    proc._process_id = "influx_writer"  # noqa: SLF001
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
    proc._batch_max_points = 500  # noqa: SLF001
    proc._flush_interval_s = 1.0  # noqa: SLF001
    proc._points_received = 0  # noqa: SLF001
    proc._points_queued = 0  # noqa: SLF001
    proc._points_written = 0  # noqa: SLF001
    proc._points_skipped_invalid = 0  # noqa: SLF001
    proc._points_skipped_remote = 0  # noqa: SLF001
    proc._points_dropped_overflow = 0  # noqa: SLF001
    proc._write_errors = 0  # noqa: SLF001
    proc._batches_written = 0  # noqa: SLF001
    proc._last_error = None  # noqa: SLF001
    proc._last_published_error_text = None  # noqa: SLF001
    proc._pending_log_payloads = deque(maxlen=200)  # noqa: SLF001
    proc._last_flush_wall_s = None  # noqa: SLF001
    proc._last_flush_mono_s = None  # noqa: SLF001
    return proc


class _FakeManager:
    def __init__(self, responses: list[dict[str, Any] | None]) -> None:
        self.responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    def call(self, payload: dict[str, Any], *, timeout_ms: int | None = None) -> dict[str, Any] | None:
        _ = timeout_ms
        self.calls.append(payload)
        if self.responses:
            return self.responses.popleft()
        return {"ok": True}


class InfluxWriterWideModeTests(unittest.TestCase):
    def test_handle_device_config_uses_yaml_driver_class_for_device_type(self) -> None:
        proc = _make_proc()
        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "trace1",
                "source_kind": "local",
                "is_remote": False,
                "yaml_text": (
                    "driver:\n"
                    "  module: experiment_control.drivers.dummy_trace_driver\n"
                    "  class_name: DummyTraceDriver\n"
                ),
            }
        )
        self.assertEqual(proc._device_type_by_id.get("trace1"), "dummy_trace")  # noqa: SLF001

    def test_handle_device_config_uses_yaml_driver_module_fallback(self) -> None:
        proc = _make_proc()
        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "trace1",
                "source_kind": "local",
                "is_remote": False,
                "yaml_text": (
                    "driver:\n"
                    "  module: experiment_control.drivers.dummy_trace_driver\n"
                ),
            }
        )
        self.assertEqual(proc._device_type_by_id.get("trace1"), "dummy_trace")  # noqa: SLF001

    def test_handle_device_config_prefers_metadata_over_yaml_driver_fallback(self) -> None:
        proc = _make_proc()
        proc._handle_device_config(  # noqa: SLF001
            {
                "device_id": "trace1",
                "source_kind": "local",
                "is_remote": False,
                "device_metadata": {"device_type": "custom_trace"},
                "yaml_text": (
                    "driver:\n"
                    "  module: experiment_control.drivers.dummy_trace_driver\n"
                    "  class_name: DummyTraceDriver\n"
                ),
            }
        )
        self.assertEqual(proc._device_type_by_id.get("trace1"), "custom_trace")  # noqa: SLF001

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

    def test_status_payload_includes_destination_connection_info(self) -> None:
        proc = _make_proc()
        status = proc._status_payload()  # noqa: SLF001
        info = status.get("destinations_info")
        self.assertIsInstance(info, list)
        self.assertEqual(len(info), 1)
        first = info[0]
        self.assertEqual(first.get("name"), "default")
        self.assertEqual(first.get("host"), "127.0.0.1")
        self.assertEqual(first.get("port"), 8086)
        self.assertEqual(first.get("org"), "org")
        self.assertEqual(first.get("bucket"), "bucket")
        self.assertNotIn("token", first)

    def test_status_payload_includes_measurement_resolution_rows(self) -> None:
        proc = _make_proc()
        proc._routes = {  # noqa: SLF001
            "pump1": DeviceRoute(
                destination="default",
                measurement=None,
                device_type=None,
                tags={},
            )
        }
        proc._device_type_by_id = {"pump1": "hipace700"}  # noqa: SLF001
        status = proc._status_payload()  # noqa: SLF001
        rows = status.get("measurement_resolution")
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)
        first = rows[0]
        self.assertEqual(first.get("device_id"), "pump1")
        self.assertEqual(first.get("device_type"), "hipace700")
        self.assertEqual(first.get("destination"), "default")
        self.assertEqual(first.get("measurement"), "hipace700")
        self.assertEqual(first.get("route_measurement"), None)
        self.assertEqual(first.get("route_device_type"), None)

    def test_error_log_publish_failure_queues_retry_and_stderr_fallback(self) -> None:
        proc = _make_proc()
        proc._manager = _FakeManager([None])  # noqa: SLF001
        proc._last_error = "write failed"  # noqa: SLF001

        stderr = StringIO()
        with redirect_stderr(stderr):
            proc._maybe_publish_last_error()  # noqa: SLF001

        self.assertIn("[influx_writer][error] write failed", stderr.getvalue())
        self.assertEqual(proc._last_published_error_text, "write failed")  # noqa: SLF001
        self.assertEqual(len(proc._pending_log_payloads), 1)  # noqa: SLF001
        queued = proc._pending_log_payloads[0]  # noqa: SLF001
        self.assertEqual(queued["severity"], "error")
        self.assertEqual(queued["message"], "write failed")

    def test_pending_error_log_is_retried_and_removed_after_success(self) -> None:
        proc = _make_proc()
        proc._manager = _FakeManager([None, {"ok": True}])  # noqa: SLF001
        proc._last_error = "write failed"  # noqa: SLF001
        with redirect_stderr(StringIO()):
            proc._maybe_publish_last_error()  # noqa: SLF001

        proc._flush_pending_logs()  # noqa: SLF001

        self.assertEqual(len(proc._pending_log_payloads), 0)  # noqa: SLF001
        self.assertEqual(len(proc._manager.calls), 2)  # noqa: SLF001

    def test_same_last_error_is_not_queued_repeatedly(self) -> None:
        proc = _make_proc()
        proc._manager = _FakeManager([None, None])  # noqa: SLF001
        proc._last_error = "write failed"  # noqa: SLF001

        with redirect_stderr(StringIO()):
            proc._maybe_publish_last_error()  # noqa: SLF001
            proc._maybe_publish_last_error()  # noqa: SLF001

        self.assertEqual(len(proc._pending_log_payloads), 1)  # noqa: SLF001
        self.assertEqual(len(proc._manager.calls), 1)  # noqa: SLF001

    def test_changed_last_error_queues_new_log(self) -> None:
        proc = _make_proc()
        proc._manager = _FakeManager([None, None])  # noqa: SLF001

        with redirect_stderr(StringIO()):
            proc._last_error = "first failure"  # noqa: SLF001
            proc._maybe_publish_last_error()  # noqa: SLF001
            proc._last_error = "second failure"  # noqa: SLF001
            proc._maybe_publish_last_error()  # noqa: SLF001

        messages = [item["message"] for item in proc._pending_log_payloads]  # noqa: SLF001
        self.assertEqual(messages, ["first failure", "second failure"])


if __name__ == "__main__":
    unittest.main()
