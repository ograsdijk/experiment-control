from __future__ import annotations

import queue
import sys
import threading
import unittest
from collections import deque
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.influx_writer import (  # noqa: E402
    DeviceRoute,
    InfluxDestination,
    InfluxWriterProcess,
    QueuedPoint,
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
    proc._queue_lock = threading.Lock()  # noqa: SLF001
    proc._http_queue = queue.Queue(maxsize=64)  # noqa: SLF001
    proc._http_thread_dead = False  # noqa: SLF001
    proc._dropped_http_batches = 0  # noqa: SLF001
    proc._http_thread = None  # noqa: SLF001
    proc._counters_lock = threading.Lock()  # noqa: SLF001
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
    proc._signals_skipped_invalid = 0  # noqa: SLF001
    proc._signals_skipped_invalid_seen = set()  # noqa: SLF001
    proc._write_errors = 0  # noqa: SLF001
    proc._batches_written = 0  # noqa: SLF001
    proc._last_error = None  # noqa: SLF001
    proc._last_published_error_text = None  # noqa: SLF001
    proc._pending_log_payloads = deque(maxlen=200)  # noqa: SLF001
    proc._last_flush_wall_s = None  # noqa: SLF001
    proc._last_flush_mono_s = None  # noqa: SLF001
    proc._last_flush_start_wall_s = None  # noqa: SLF001
    proc._last_flush_start_mono_s = None  # noqa: SLF001
    proc._last_flush_duration_s = None  # noqa: SLF001
    proc._last_flush_destination = None  # noqa: SLF001
    proc._destination_retry = {}  # noqa: SLF001
    proc._last_drain_count = 0  # noqa: SLF001
    proc._last_drain_duration_s = 0.0  # noqa: SLF001
    proc._total_drained = 0  # noqa: SLF001
    proc._drain_limited_count = 0  # noqa: SLF001
    proc._drain_parse_errors = 0  # noqa: SLF001
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


class _BgThreadTestMixin:
    @staticmethod
    def _make_bg_proc() -> InfluxWriterProcess:
        proc = _make_proc()
        # process_base attributes the bg thread touches via _record_exception
        proc._phase = None  # noqa: SLF001
        proc._phase_detail = None  # noqa: SLF001
        proc._last_progress_wall = None  # noqa: SLF001
        proc._last_progress_mono = None  # noqa: SLF001
        proc._last_exception = None  # noqa: SLF001
        proc._last_traceback_summary = None  # noqa: SLF001
        proc._progress_lock = threading.RLock()  # noqa: SLF001
        proc._stop_evt = threading.Event()  # noqa: SLF001
        return proc


class InfluxWriterBgHttpThreadTests(unittest.TestCase, _BgThreadTestMixin):
    def test_http_error_keeps_thread_alive_and_requeues(self) -> None:
        proc = self._make_bg_proc()

        attempts: list[int] = []

        def fake_flush_grouped(*, by_destination: dict[str, list[Any]]) -> list[Any]:
            attempts.append(len(attempts))
            if len(attempts) == 1:
                # First batch: simulate HTTPError — _flush_destination_points
                # would have caught it and returned False, so all points come
                # back as failed.
                proc._last_error = "HTTPError status=503"  # noqa: SLF001
                return [p for points in by_destination.values() for p in points]
            return []

        proc._flush_grouped_points = fake_flush_grouped  # type: ignore[assignment]  # noqa: SLF001

        thread = threading.Thread(target=proc._http_thread_run, name="test-http")  # noqa: SLF001
        thread.start()
        try:
            batch1 = {"default": [QueuedPoint(destination="default", line="line1")]}
            batch2 = {"default": [QueuedPoint(destination="default", line="line2")]}
            proc._http_queue.put(batch1)  # noqa: SLF001
            proc._http_queue.put(batch2)  # noqa: SLF001
            # Wait for both to be processed
            deadline = threading.Event()
            for _ in range(50):
                if len(attempts) >= 2:
                    break
                deadline.wait(0.05)
            self.assertEqual(len(attempts), 2)
            self.assertFalse(proc._http_thread_dead)  # noqa: SLF001
            self.assertEqual(proc._last_error, "HTTPError status=503")  # noqa: SLF001
            # First batch's point was requeued
            with proc._queue_lock:  # noqa: SLF001
                queued = list(proc._queue)  # noqa: SLF001
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0].line, "line1")
        finally:
            proc._stop_evt.set()  # noqa: SLF001
            proc._http_queue.put(None)  # noqa: SLF001
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())

    def test_unexpected_exception_kills_thread_and_sets_dead_flag(self) -> None:
        proc = self._make_bg_proc()

        def boom(*, by_destination: dict[str, list[Any]]) -> list[Any]:
            raise RuntimeError("simulated fatal")

        # Force the fatal path: replace _flush_grouped_points to raise, but
        # also block the inner try/except by re-raising from a place that the
        # batch loop doesn't catch — easiest is to make _requeue_failed raise
        # so the inner except re-raises out of the batch loop.
        def detonate(_points: list[QueuedPoint]) -> None:
            raise RuntimeError("simulated fatal")

        proc._flush_grouped_points = boom  # type: ignore[assignment]  # noqa: SLF001
        proc._requeue_failed = detonate  # type: ignore[assignment]  # noqa: SLF001

        thread = threading.Thread(target=proc._http_thread_run, name="test-http")  # noqa: SLF001
        thread.start()
        try:
            batch = {"default": [QueuedPoint(destination="default", line="line1")]}
            proc._http_queue.put(batch)  # noqa: SLF001
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
            self.assertTrue(proc._http_thread_dead)  # noqa: SLF001
            self.assertTrue(proc._stop_evt.is_set())  # noqa: SLF001
            self.assertIsNotNone(proc._last_exception)  # noqa: SLF001
        finally:
            proc._stop_evt.set()  # noqa: SLF001


class InfluxWriterRetryAfterTests(unittest.TestCase):
    def test_retry_after_integer_sets_destination_backoff(self) -> None:
        proc = _make_proc()
        err = self._http_error(429, {"Retry-After": "3"})

        proc._record_destination_http_error("default", err)  # noqa: SLF001

        state = proc._destination_retry["default"]  # noqa: SLF001
        self.assertEqual(state.last_status, 429)
        self.assertAlmostEqual(state.last_retry_after_s or 0.0, 3.0, delta=0.25)
        self.assertGreater(proc._destination_backoff_remaining_s("default"), 0.0)  # noqa: SLF001

    def test_retry_after_http_date_is_parsed(self) -> None:
        proc = _make_proc()
        value = "Sun, 31 May 2026 22:42:12 GMT"
        delay = proc._parse_retry_after(value, now_wall=1780267330.0)  # noqa: SLF001
        self.assertAlmostEqual(delay or 0.0, 2.0, delta=0.01)

    def test_missing_retry_after_uses_exponential_fallback(self) -> None:
        proc = _make_proc()

        proc._record_destination_http_error("default", self._http_error(503, {}))  # noqa: SLF001
        proc._record_destination_http_error("default", self._http_error(503, {}))  # noqa: SLF001

        state = proc._destination_retry["default"]  # noqa: SLF001
        self.assertEqual(state.consecutive_failures, 2)
        self.assertEqual(state.last_retry_after_s, 2.0)

    def test_backoff_skips_only_affected_destination(self) -> None:
        proc = _make_proc()
        proc._destinations["other"] = InfluxDestination(  # noqa: SLF001
            name="other",
            url="http://127.0.0.1:8086",
            org="org",
            bucket="bucket",
            token="",
            measurement="m",
            precision="ns",
            request_timeout_s=5.0,
            static_tags={},
        )
        proc._record_destination_http_error("default", self._http_error(429, {"Retry-After": "5"}))  # noqa: SLF001
        flushed: list[str] = []

        def flush_destination(*, destination_name: str, points: list[QueuedPoint]) -> bool:
            del points
            flushed.append(destination_name)
            return True

        proc._flush_destination_points = flush_destination  # type: ignore[method-assign]  # noqa: SLF001
        failed = proc._flush_grouped_points(  # noqa: SLF001
            by_destination={
                "default": [QueuedPoint(destination="default", line="a")],
                "other": [QueuedPoint(destination="other", line="b")],
            }
        )

        self.assertEqual(flushed, ["other"])
        self.assertEqual([point.line for point in failed], ["a"])

    def test_flush_does_not_enqueue_backoff_only_batch(self) -> None:
        proc = _make_proc()
        proc._batch_max_points = 1  # noqa: SLF001
        proc._record_destination_http_error("default", self._http_error(429, {"Retry-After": "5"}))  # noqa: SLF001
        with proc._queue_lock:  # noqa: SLF001
            proc._queue.append(QueuedPoint(destination="default", line="a"))  # noqa: SLF001
            proc._queue.append(QueuedPoint(destination="default", line="b"))  # noqa: SLF001

        proc._flush()  # noqa: SLF001

        self.assertEqual(proc._http_queue.qsize(), 0)  # noqa: SLF001
        with proc._queue_lock:  # noqa: SLF001
            self.assertEqual([point.line for point in proc._queue], ["a", "b"])  # noqa: SLF001

    @staticmethod
    def _http_error(code: int, headers: dict[str, str]) -> HTTPError:
        from io import BytesIO

        return HTTPError(
            "http://127.0.0.1/write",
            code,
            "error",
            headers,
            BytesIO(b"body"),
        )


class InfluxWriterFlushOverflowTests(unittest.TestCase):
    """Regression coverage for `_flush()` HTTP-queue overflow.

    When the bg HTTP thread is backlogged and `put_nowait` raises
    `queue.Full`, the writer increments `_dropped_http_batches` and
    re-queues the drained points so they aren't lost — unlike the
    HDF writer's analogous path, which drops the batch. This test
    pins both behaviours so a future refactor that "harmonises" the
    two paths can't silently start dropping influx points.
    """

    def test_flush_overflow_bumps_counter_and_requeues_points(self) -> None:
        proc = _make_proc()
        # Force the HTTP queue to overflow on the first put_nowait.
        proc._http_queue = queue.Queue(maxsize=1)  # noqa: SLF001
        proc._http_queue.put_nowait({"sentinel": []})  # noqa: SLF001 — fill it

        points = [
            QueuedPoint(destination="default", line="line1"),
            QueuedPoint(destination="default", line="line2"),
        ]
        for p in points:
            proc._queue.append(p)  # noqa: SLF001
        proc._points_queued = len(points)  # noqa: SLF001

        proc._flush()  # noqa: SLF001

        # Counter bumped.
        self.assertEqual(proc._dropped_http_batches, 1)  # noqa: SLF001

        # Points are re-queued (NOT lost). This is the influx-specific
        # contract — different from HDF's overflow path, which drops.
        with proc._queue_lock:  # noqa: SLF001
            requeued = list(proc._queue)  # noqa: SLF001
        self.assertEqual(len(requeued), 2)
        self.assertEqual({p.line for p in requeued}, {"line1", "line2"})

        # The HTTP queue still holds only the sentinel batch we put in —
        # the drained payload was rejected, not silently consumed.
        self.assertEqual(proc._http_queue.qsize(), 1)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
