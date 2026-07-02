import queue
import shutil
import sys
import tempfile
import threading
import time
import uuid
import json
from collections import deque
from contextlib import contextmanager
from pathlib import Path
import unittest

import h5py
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from unittest.mock import patch  # noqa: E402

from experiment_control.processes.hdf_writer import (  # noqa: E402
    HdfWriter,
    _BG_SENTINEL,
    _FlushBatch,
    _convert_value,
    _create_device_dataset,
    _dtype_for,
    _ingest_process_schema,
)
from experiment_control.processes.hdf_writer_dtypes import (  # noqa: E402
    DEFAULT_STR_LEN,
    str_length_for,
)
from experiment_control.shm.shm_ring import ShmRingWriter  # noqa: E402


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


@contextmanager
def _temp_dir() -> object:
    root = ROOT / ".tmp_tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"tmp_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


tempfile.TemporaryDirectory = _temp_dir  # type: ignore[assignment]


class HdfWriterEventModeTests(unittest.TestCase):
    def _make_writer(
        self, *, event_log_mode: str, measurement_schema_path: str | None = None
    ) -> HdfWriter:
        return HdfWriter(
            out_dir="data",
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            measurement_schema_path=measurement_schema_path,
            event_log_mode=event_log_mode,  # type: ignore[arg-type]
        )

    def test_should_keep_event_all(self) -> None:
        writer = self._make_writer(event_log_mode="all")
        self.assertTrue(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.command",
                msg={"ok": True},
            )
        )
        self.assertTrue(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.log",
                msg={"severity": "info"},
            )
        )

    def test_should_keep_event_failures_only(self) -> None:
        writer = self._make_writer(event_log_mode="failures_only")
        self.assertFalse(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.command",
                msg={"ok": True},
            )
        )
        self.assertTrue(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.command",
                msg={"ok": False},
            )
        )
        self.assertFalse(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.log",
                msg={"severity": "info"},
            )
        )
        self.assertTrue(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.log",
                msg={"severity": "error"},
            )
        )

    def test_should_keep_event_none(self) -> None:
        writer = self._make_writer(event_log_mode="none")
        self.assertFalse(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.command",
                msg={"ok": False},
            )
        )
        self.assertFalse(
            writer._should_keep_event(  # noqa: SLF001
                topic="manager.log",
                msg={"severity": "critical"},
            )
        )

    def test_measurement_schema_capabilities_hidden_when_not_configured(self) -> None:
        writer = self._make_writer(event_log_mode="all")
        member_names = set()
        for member in writer._hdf_capability_members():  # noqa: SLF001
            name = getattr(member, "name", None)
            if isinstance(name, str):
                member_names.add(name)
                continue
            if isinstance(member, dict):
                raw = member.get("name")
                if isinstance(raw, str):
                    member_names.add(raw)
        self.assertNotIn("hdf.measurement.schema.get", member_names)
        self.assertNotIn("hdf.measurement.note", member_names)
        self.assertIn("hdf.writing.start", member_names)
        self.assertIn("hdf.writing.stop", member_names)

    def test_measurement_schema_capabilities_present_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            schema_path = Path(td) / "measurement.yaml"
            schema_path.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "profiles": [],
                        "notes": {"fields": []},
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            writer = self._make_writer(
                event_log_mode="all",
                measurement_schema_path=str(schema_path),
            )
            member_names = set()
            for member in writer._hdf_capability_members():  # noqa: SLF001
                name = getattr(member, "name", None)
                if isinstance(name, str):
                    member_names.add(name)
                    continue
                if isinstance(member, dict):
                    raw = member.get("name")
                    if isinstance(raw, str):
                        member_names.add(raw)
            self.assertIn("hdf.measurement.schema.get", member_names)
            self.assertIn("hdf.measurement.note", member_names)
            self.assertIn("hdf.writing.start", member_names)
            self.assertIn("hdf.writing.stop", member_names)


class HdfWriterSequencerTests(unittest.TestCase):
    def test_lifecycle_start_appends_row_with_snapshot_id(self) -> None:
        writer = HdfWriter(
            out_dir="data",
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

        with tempfile.TemporaryDirectory() as td:
            h5_path = Path(td) / "test.h5"
            with h5py.File(h5_path, "w") as h5:
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    ),
                )
                writer._capture_sequencer_yaml_snapshot = lambda: (7, None)  # type: ignore[method-assign]  # noqa: E501
                writer._handle_sequencer_lifecycle(  # noqa: SLF001
                    {
                        "version": 1,
                        "process_id": "sequencer",
                        "event": "start",
                        "ok": True,
                        "source": "rpc",
                        "message": "sequencer started",
                        "ts": {"t_wall": 1.0, "t_mono": 2.0},
                    }
                )

                assert writer._sequencer_events_ds is not None  # noqa: SLF001
                self.assertEqual(int(writer._sequencer_events_ds.shape[0]), 1)  # noqa: SLF001
                row = writer._sequencer_events_ds[0]  # noqa: SLF001
                self.assertEqual(int(row["yaml_snapshot_id"]), 7)
                self.assertEqual(_as_text(row["event"]), "start")
                self.assertEqual(_as_text(row["source"]), "rpc")
                self.assertTrue(bool(row["ok"]))


class HdfWriterMeasurementTests(unittest.TestCase):
    @staticmethod
    def _write_schema(path: Path) -> None:
        data = {
            "version": 1,
            "profiles": [
                {
                    "id": "frequency_scan",
                    "label": "Frequency Scan",
                    "fields": [
                        {"key": "measurement_name", "type": "string", "required": True},
                        {"key": "seed1_power_dbm", "type": "number", "required": True},
                    ],
                }
            ],
            "notes": {
                "fields": [
                    {
                        "key": "author",
                        "type": "string",
                        "required": True,
                        "options": ["alice", "bob"],
                        "allow_custom": True,
                    },
                    {
                        "key": "kind",
                        "type": "string",
                        "required": True,
                        "options": ["note", "issue"],
                    },
                    {"key": "message", "type": "string", "required": True},
                    {"key": "shot_count", "type": "integer", "required": False},
                ]
            },
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def test_measurement_schema_rpc_and_note_append(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            schema_path = root / "measurement.yaml"
            self._write_schema(schema_path)
            writer = HdfWriter(
                out_dir=str(root),
                filename=None,
                manager_rpc="tcp://127.0.0.1:65531",
                manager_pub="tcp://127.0.0.1:65532",
                rpc_timeout_ms=2000,
                timezone="America/Chicago",
                rcvhwm=1000,
                write_every_s=1.0,
                buffer_max_messages=1000,
                flush_every_n=10,
                flush_every_s=1.0,
                disabled_devices=[],
                measurement_schema_path=str(schema_path),
                event_log_mode="all",
            )

            h5_path = root / "test.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id="frequency_scan",
                    values={"measurement_name": "scan-A", "seed1_power_dbm": "-5.2"},
                    require_profile=True,
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )

                schema_resp = writer._handle_rpc(  # noqa: SLF001
                    {"request_id": "s1", "type": "hdf.measurement.schema.get", "params": {}}
                )
                self.assertTrue(bool(schema_resp.get("ok")))
                result = schema_resp.get("result")
                assert isinstance(result, dict)
                schema_obj = result.get("schema")
                assert isinstance(schema_obj, dict)
                self.assertEqual(schema_obj.get("version"), 1)

                note_resp = writer._handle_rpc(  # noqa: SLF001
                    {
                        "request_id": "n1",
                        "type": "hdf.measurement.note",
                        "params": {
                            "author": "alice",
                            "kind": "note",
                            "message": "beam looked stable",
                            "shot_count": "20",
                        },
                    }
                )
                self.assertTrue(bool(note_resp.get("ok")))
                assert writer._measurement_notes_ds is not None  # noqa: SLF001
                self.assertEqual(int(writer._measurement_notes_ds.shape[0]), 1)  # noqa: SLF001
                row = writer._measurement_notes_ds[0]  # noqa: SLF001
                self.assertEqual(_as_text(row["author"]), "alice")
                self.assertEqual(_as_text(row["kind"]), "note")
                self.assertEqual(_as_text(row["message"]), "beam looked stable")

    def test_rotate_requires_measurement_profile_when_schema_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            schema_path = root / "measurement.yaml"
            self._write_schema(schema_path)
            writer = HdfWriter(
                out_dir=str(root),
                filename=None,
                manager_rpc="tcp://127.0.0.1:65531",
                manager_pub="tcp://127.0.0.1:65532",
                rpc_timeout_ms=2000,
                timezone="America/Chicago",
                rcvhwm=1000,
                write_every_s=1.0,
                buffer_max_messages=1000,
                flush_every_n=10,
                flush_every_s=1.0,
                disabled_devices=[],
                measurement_schema_path=str(schema_path),
                event_log_mode="all",
            )
            h5_path = root / "test.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                rotate_resp = writer._handle_rpc(  # noqa: SLF001
                    {"request_id": "r1", "type": "hdf.rotate", "params": {}}
                )
                self.assertFalse(bool(rotate_resp.get("ok")))
                error_obj = rotate_resp.get("error")
                assert isinstance(error_obj, dict)
                self.assertEqual(error_obj.get("code"), "rotate_failed")
                self.assertIn("measurement_profile is required", str(error_obj.get("message")))


class HdfWriterStorageSafetyTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_rotate_rejects_existing_filename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            current_path = root / "current.h5"
            target_path = root / "existing.h5"
            target_path.write_bytes(b"already here")
            writer = self._make_writer(str(root))

            with h5py.File(current_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                resp = writer._handle_rpc(  # noqa: SLF001
                    {
                        "request_id": "r1",
                        "type": "hdf.rotate",
                        "params": {"filename": target_path.name},
                    }
                )
                self.assertFalse(bool(resp.get("ok")))
                error_obj = resp.get("error")
                assert isinstance(error_obj, dict)
                self.assertEqual(error_obj.get("code"), "file_exists")
                self.assertEqual(str(writer._h5.filename), str(current_path))  # noqa: SLF001

    def test_start_writing_rejects_existing_filename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target_path = root / "existing.h5"
            target_path.write_bytes(b"already here")
            writer = self._make_writer(str(root))

            with self.assertRaises(FileExistsError):
                writer._start_writing_file(  # noqa: SLF001
                    filename=target_path.name,
                    disabled_devices=None,
                    measurement_profile=None,
                    measurement_values=None,
                )


class HdfWriterWritingControlTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_writing_stop_then_start_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            current_path = root / "current.h5"
            h5 = h5py.File(current_path, "w")
            try:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                stop_resp = writer._handle_rpc(  # noqa: SLF001
                    {"request_id": "w1", "type": "hdf.writing.stop", "params": {}}
                )
                self.assertTrue(bool(stop_resp.get("ok")))
                self.assertIsNone(writer._h5)  # noqa: SLF001
                stop_result = stop_resp.get("result")
                assert isinstance(stop_result, dict)
                self.assertFalse(bool(stop_result.get("already_stopped")))

                start_resp = writer._handle_rpc(  # noqa: SLF001
                    {
                        "request_id": "w2",
                        "type": "hdf.writing.start",
                        "params": {"filename": "resumed.h5"},
                    }
                )
                self.assertTrue(bool(start_resp.get("ok")))
                self.assertIsNotNone(writer._h5)  # noqa: SLF001
                assert writer._h5 is not None  # noqa: SLF001
                self.assertEqual(Path(writer._h5.filename).name, "resumed.h5")  # noqa: SLF001
            finally:
                writer.close()

    def test_writing_stop_is_idempotent_when_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = self._make_writer(str(Path(td)))
            resp = writer._handle_rpc(  # noqa: SLF001
                {"request_id": "w3", "type": "hdf.writing.stop", "params": {}}
            )
            self.assertTrue(bool(resp.get("ok")))
            result = resp.get("result")
            assert isinstance(result, dict)
            self.assertTrue(bool(result.get("already_stopped")))

    def test_writing_start_rejects_when_already_active(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5 = h5py.File(root / "active.h5", "w")
            try:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                resp = writer._handle_rpc(  # noqa: SLF001
                    {
                        "request_id": "w4",
                        "type": "hdf.writing.start",
                        "params": {"filename": "ignored.h5"},
                    }
                )
                self.assertFalse(bool(resp.get("ok")))
                error_obj = resp.get("error")
                assert isinstance(error_obj, dict)
                self.assertEqual(error_obj.get("code"), "already_writing")
            finally:
                writer.close()


class HdfWriterStreamBufferTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_write_stream_buffers_clears_context_id_on_missing_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "stream_missing_schema.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("cam1", "frames")
                writer._stream_buffers[key] = {  # noqa: SLF001
                    "data": [b"\x01\x02"],
                    "seq": [1],
                    "t0_mono_ns": [10],
                    "t0_wall_ns": [20],
                    "context_id": [30],
                }
                writer._write_stream_buffers()  # noqa: SLF001
                buf = writer._stream_buffers[key]  # noqa: SLF001
                self.assertEqual(buf["data"], [])
                self.assertEqual(buf["seq"], [])
                self.assertEqual(buf["t0_mono_ns"], [])
                self.assertEqual(buf["t0_wall_ns"], [])
                self.assertEqual(buf["context_id"], [])

    def test_write_stream_buffers_clears_context_id_on_bad_payload_size(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "stream_bad_payload.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("cam1", "frames")
                writer._stream_schema[key] = {"dtype": "uint16", "shape": (2,)}  # noqa: SLF001
                writer._stream_buffers[key] = {  # noqa: SLF001
                    "data": [b"\x01\x02\x03"],
                    "seq": [1],
                    "t0_mono_ns": [10],
                    "t0_wall_ns": [20],
                    "context_id": [30],
                }
                writer._write_stream_buffers()  # noqa: SLF001
                buf = writer._stream_buffers[key]  # noqa: SLF001
                self.assertEqual(buf["data"], [])
                self.assertEqual(buf["seq"], [])
                self.assertEqual(buf["t0_mono_ns"], [])
                self.assertEqual(buf["t0_wall_ns"], [])
                self.assertEqual(buf["context_id"], [])

    def test_write_stream_buffers_repairs_misaligned_context_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "stream_context_fix.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("cam1", "frames")
                writer._stream_schema[key] = {"dtype": "uint16", "shape": (1,)}  # noqa: SLF001
                writer._stream_buffers[key] = {  # noqa: SLF001
                    "data": [b"\x01\x00", b"\x02\x00"],
                    "seq": [1, 2],
                    "t0_mono_ns": [10, 11],
                    "t0_wall_ns": [20, 21],
                    "context_id": [30],
                }
                writer._write_stream_buffers()  # noqa: SLF001
                datasets = writer._stream_datasets[("cam1", "frames", 1)]  # noqa: SLF001
                self.assertEqual(list(datasets["context_id"][...]), [-1, -1])


class HdfWriterContextResolutionTests(unittest.TestCase):
    @staticmethod
    def _make_writer(out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    @staticmethod
    def _u16_payload(value: int) -> bytes:
        return np.asarray([value], dtype=np.uint16).tobytes()

    @staticmethod
    def _fake_reader() -> object:
        class _Layout:
            dtype = np.dtype("uint16")
            shape = (1,)

        class _Reader:
            layout = _Layout()

        return _Reader()

    def test_exact_seq_context_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "context_exact.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None,
                    values=None,
                    require_profile=False,
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("trace1", "trace")
                writer._store_context_for_seq(  # noqa: SLF001
                    key=key,
                    seq=5,
                    context_id=42,
                    now_mono=10.0,
                )
                writer._append_chunk_ready_events(  # noqa: SLF001
                    key=key,
                    reader=self._fake_reader(),
                    events=[
                        {
                            "seq": 5,
                            "payload": self._u16_payload(7),
                            "t0_mono_ns": 11,
                            "t0_wall_ns": 12,
                        }
                    ],
                    initial_last_seq=4,
                    now_mono=10.0,
                )
                self.assertEqual(writer._context_resolved_exact, 1)  # noqa: SLF001
                writer._write_stream_buffers()  # noqa: SLF001
                ds = writer._stream_datasets[("trace1", "trace", 1)]["context_id"]  # noqa: SLF001
                self.assertEqual(list(ds[...]), [42])

    def test_late_context_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "context_late.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None,
                    values=None,
                    require_profile=False,
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("trace1", "trace")
                writer._append_chunk_ready_events(  # noqa: SLF001
                    key=key,
                    reader=self._fake_reader(),
                    events=[
                        {
                            "seq": 1,
                            "payload": self._u16_payload(1),
                            "t0_mono_ns": 101,
                            "t0_wall_ns": 102,
                        }
                    ],
                    initial_last_seq=0,
                    now_mono=1.0,
                )
                self.assertEqual(len(writer._stream_pending_by_seq[key]), 1)  # noqa: SLF001
                writer._store_context_for_seq(  # noqa: SLF001
                    key=key,
                    seq=1,
                    context_id=9,
                    now_mono=1.2,
                )
                writer._resolve_pending_stream_event(  # noqa: SLF001
                    key=key,
                    seq=1,
                    context_id=9,
                )
                self.assertEqual(writer._context_late_resolved, 1)  # noqa: SLF001
                writer._write_stream_buffers()  # noqa: SLF001
                ds = writer._stream_datasets[("trace1", "trace", 1)]["context_id"]  # noqa: SLF001
                self.assertEqual(list(ds[...]), [9])

    def test_pending_context_ttl_expires_to_minus_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            writer._context_resolve_ttl_s = 0.01  # noqa: SLF001
            h5_path = root / "context_ttl.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None,
                    values=None,
                    require_profile=False,
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("trace1", "trace")
                writer._append_chunk_ready_events(  # noqa: SLF001
                    key=key,
                    reader=self._fake_reader(),
                    events=[
                        {
                            "seq": 1,
                            "payload": self._u16_payload(1),
                            "t0_mono_ns": 101,
                            "t0_wall_ns": 102,
                        }
                    ],
                    initial_last_seq=0,
                    now_mono=1.0,
                )
                writer._expire_pending_context(key=key, now_mono=1.02)  # noqa: SLF001
                self.assertEqual(writer._context_written_minus1_missing, 1)  # noqa: SLF001
                writer._write_stream_buffers()  # noqa: SLF001
                ds = writer._stream_datasets[("trace1", "trace", 1)]["context_id"]  # noqa: SLF001
                self.assertEqual(list(ds[...]), [-1])

    def test_pending_context_overflow_evicts_oldest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            writer._context_pending_max_per_stream = 2  # noqa: SLF001
            h5_path = root / "context_overflow.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None,
                    values=None,
                    require_profile=False,
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("trace1", "trace")
                writer._append_chunk_ready_events(  # noqa: SLF001
                    key=key,
                    reader=self._fake_reader(),
                    events=[
                        {
                            "seq": 1,
                            "payload": self._u16_payload(1),
                            "t0_mono_ns": 101,
                            "t0_wall_ns": 102,
                        },
                        {
                            "seq": 2,
                            "payload": self._u16_payload(2),
                            "t0_mono_ns": 111,
                            "t0_wall_ns": 112,
                        },
                        {
                            "seq": 3,
                            "payload": self._u16_payload(3),
                            "t0_mono_ns": 121,
                            "t0_wall_ns": 122,
                        },
                    ],
                    initial_last_seq=0,
                    now_mono=1.0,
                )
                pending = writer._stream_pending_by_seq[key]  # noqa: SLF001
                self.assertEqual(sorted(pending.keys()), [2, 3])
                self.assertEqual(writer._context_evicted_pending_overflow, 1)  # noqa: SLF001
                self.assertEqual(writer._context_written_minus1_missing, 1)  # noqa: SLF001

    def test_missing_context_does_not_reuse_previous_context(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            writer._context_resolve_ttl_s = 0.01  # noqa: SLF001
            h5_path = root / "context_no_sticky.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None,
                    values=None,
                    require_profile=False,
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                key = ("trace1", "trace")
                writer._store_context_for_seq(  # noqa: SLF001
                    key=key,
                    seq=10,
                    context_id=77,
                    now_mono=1.0,
                )
                writer._append_chunk_ready_events(  # noqa: SLF001
                    key=key,
                    reader=self._fake_reader(),
                    events=[
                        {
                            "seq": 10,
                            "payload": self._u16_payload(10),
                            "t0_mono_ns": 201,
                            "t0_wall_ns": 202,
                        }
                    ],
                    initial_last_seq=9,
                    now_mono=1.0,
                )
                writer._write_stream_buffers()  # noqa: SLF001
                writer._append_chunk_ready_events(  # noqa: SLF001
                    key=key,
                    reader=self._fake_reader(),
                    events=[
                        {
                            "seq": 11,
                            "payload": self._u16_payload(11),
                            "t0_mono_ns": 211,
                            "t0_wall_ns": 212,
                        }
                    ],
                    initial_last_seq=10,
                    now_mono=1.01,
                )
                writer._expire_pending_context(key=key, now_mono=1.03)  # noqa: SLF001
                writer._write_stream_buffers()  # noqa: SLF001
                ds = writer._stream_datasets[("trace1", "trace", 1)]["context_id"]  # noqa: SLF001
                self.assertEqual(list(ds[...]), [77, -1])


class HdfWriterContextColumnTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_bool_context_column_uses_uint8_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "bool_context.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                writer._init_context_columns_from_spec(  # noqa: SLF001
                    {"flag": "bool"},
                    source="explicit",
                )
                ds = writer._context_columns_datasets["flag"]  # noqa: SLF001
                self.assertEqual(ds.dtype, np.dtype("uint8"))
                self.assertEqual(_as_text(ds.attrs["dtype"]), "bool")
                self.assertEqual(int(ds.attrs["missing"]), 255)

    def test_infer_bool_context_column_as_bool(self) -> None:
        writer = self._make_writer("data")
        spec = writer._infer_context_columns_from_fields({"flag": True})  # noqa: SLF001
        self.assertEqual(spec, {"flag": "bool"})

    def test_coerce_bool_context_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "bool_context_coerce.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                writer._init_context_columns_from_spec(  # noqa: SLF001
                    {"flag": "bool"},
                    source="explicit",
                )
                self.assertEqual(int(writer._coerce_context_value("flag", True)), 1)  # noqa: SLF001
                self.assertEqual(int(writer._coerce_context_value("flag", False)), 0)  # noqa: SLF001
                self.assertEqual(int(writer._coerce_context_value("flag", None)), 255)  # noqa: SLF001


class HdfWriterCompressionTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_telemetry_dataset_uses_lzf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            h5_path = Path(td) / "telemetry_compression.h5"
            with h5py.File(h5_path, "w") as h5:
                telemetry_group = h5.require_group("telemetry")
                ds = _create_device_dataset(
                    telemetry_group,
                    "dev1",
                    ["signal_a"],
                    ["float64"],
                    [""],
                )
                self.assertEqual(ds.compression, "lzf")
                self.assertEqual(ds.chunks, (64,))

    def test_telemetry_dataset_has_t_wall_recv_column(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            h5_path = Path(td) / "recv_column.h5"
            with h5py.File(h5_path, "w") as h5:
                telemetry_group = h5.require_group("telemetry")
                ds = _create_device_dataset(
                    telemetry_group,
                    "dev1",
                    ["signal_a"],
                    ["float64"],
                    [""],
                )
                names = ds.dtype.names
                assert names is not None
                self.assertIn("t_wall_recv", names)
                # Grouped with the other timestamps, ahead of seq + signals.
                self.assertEqual(
                    names[:4], ("t_wall", "t_mono", "t_wall_recv", "seq")
                )

    def test_telemetry_t_wall_recv_written_and_nan_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "recv_write.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                with writer._h5_lock:  # noqa: SLF001
                    writer._configure_active_file(  # noqa: SLF001
                        h5,
                        write_every_s=1.0,
                        load_manager_state=False,
                        measurement_meta=meta,
                    )
                    # Inject a device dataset + map entry so _ensure_device
                    # short-circuits (no schema RPC needed).
                    assert writer._telemetry_group is not None  # noqa: SLF001
                    ds = _create_device_dataset(
                        writer._telemetry_group,  # noqa: SLF001
                        "dev1",
                        ["signal_a"],
                        ["float64"],
                        [""],
                    )
                    writer._datasets["dev1"] = ds  # noqa: SLF001
                    writer._device_map["dev1"] = {  # noqa: SLF001
                        "signals": ["signal_a"],
                        "dtypes": ["float64"],
                    }
                    writer._write_buffered_rows_batch(  # noqa: SLF001
                        [
                            {
                                "device_id": "dev1",
                                "seq": 0,
                                "ts": {
                                    "t_wall": 100.0,
                                    "t_mono": 5.0,
                                    "t_wall_recv": 100.25,
                                },
                                "signals": {"signal_a": {"value": 1.0}},
                            },
                            {
                                "device_id": "dev1",
                                "seq": 1,
                                # No t_wall_recv (e.g. an older manager).
                                "ts": {"t_wall": 101.0, "t_mono": 6.0},
                                "signals": {"signal_a": {"value": 2.0}},
                            },
                        ]
                    )

                self.assertEqual(int(ds.shape[0]), 2)
                self.assertAlmostEqual(float(ds[0]["t_wall_recv"]), 100.25)
                self.assertAlmostEqual(float(ds[0]["t_wall"]), 100.0)
                self.assertTrue(np.isnan(float(ds[1]["t_wall_recv"])))

    def test_stream_data_dataset_uses_lzf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "stream_compression.h5"
            with h5py.File(h5_path, "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=meta,
                )
                datasets = writer._ensure_stream_dataset(  # noqa: SLF001
                    "cam1",
                    "frames",
                    "uint16",
                    (2,),
                    session=1,
                )
                self.assertEqual(datasets["data"].compression, "lzf")


class HdfWriterFixedLengthStringTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_str_length_for(self) -> None:
        self.assertEqual(str_length_for("str"), DEFAULT_STR_LEN)
        self.assertEqual(str_length_for("str:16"), 16)
        self.assertIsNone(str_length_for("float64"))
        self.assertIsNone(str_length_for("int32"))
        with self.assertRaises(ValueError):
            str_length_for("str:0")
        with self.assertRaises(ValueError):
            str_length_for("str:abc")

    def test_dtype_for_str_is_fixed_length_bytes(self) -> None:
        dt = _dtype_for("str:16")
        self.assertEqual(dt.kind, "S")
        self.assertEqual(dt.itemsize, 16)
        self.assertEqual(_dtype_for("str").itemsize, DEFAULT_STR_LEN)

    def test_convert_value_encodes_and_truncates_utf8(self) -> None:
        # ASCII within bound: exact bytes.
        self.assertEqual(_convert_value("SETTLED", "str:16"), b"SETTLED")
        # None -> empty.
        self.assertEqual(_convert_value(None, "str:16"), b"")
        # Overlong is truncated to N bytes.
        self.assertEqual(len(_convert_value("A" * 40, "str:16")), 16)
        # Non-ASCII does not raise and stays valid UTF-8 after truncation.
        out = _convert_value("café-ünîcodé-tail", "str:8")
        self.assertIsInstance(out, bytes)
        self.assertLessEqual(len(out), 8)
        out.decode("utf-8")  # must not raise

    def test_string_signal_roundtrip_with_shuffle_and_lzf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            with h5py.File(root / "strsig.h5", "w") as h5:
                meta = writer._build_measurement_metadata(  # noqa: SLF001
                    profile_id=None, values=None, require_profile=False
                )
                with writer._h5_lock:  # noqa: SLF001
                    writer._configure_active_file(  # noqa: SLF001
                        h5,
                        write_every_s=1.0,
                        load_manager_state=False,
                        measurement_meta=meta,
                    )
                    assert writer._telemetry_group is not None  # noqa: SLF001
                    ds = _create_device_dataset(
                        writer._telemetry_group,  # noqa: SLF001
                        "wm1",
                        ["switch_state"],
                        ["str:16"],
                        [""],
                    )
                    writer._datasets["wm1"] = ds  # noqa: SLF001
                    writer._device_map["wm1"] = {  # noqa: SLF001
                        "signals": ["switch_state"],
                        "dtypes": ["str:16"],
                    }
                    # Fixed-length string field keeps the compound filterable.
                    self.assertEqual(ds.compression, "lzf")
                    self.assertTrue(ds.shuffle)
                    self.assertEqual(ds.dtype["switch_state"].kind, "S")
                    writer._write_buffered_rows_batch(  # noqa: SLF001
                        [
                            {
                                "device_id": "wm1",
                                "seq": 0,
                                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                                "signals": {"switch_state": {"value": "SETTLED"}},
                            },
                            {
                                "device_id": "wm1",
                                "seq": 1,
                                "ts": {"t_wall": 2.0, "t_mono": 2.0},
                                "signals": {
                                    "switch_state": {"value": "INVERSE_SAWTOOTH_TOO_LONG"}
                                },
                            },
                        ]
                    )
                self.assertEqual(int(ds.shape[0]), 2)
                self.assertEqual(bytes(ds[0]["switch_state"]).decode("utf-8"), "SETTLED")
                # Overlong value truncated to 16 bytes.
                self.assertEqual(
                    bytes(ds[1]["switch_state"]).decode("utf-8"), "INVERSE_SAWTOOTH"
                )


class HdfWriterMetadataConsolidationTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_configure_active_file_merges_stream_output_attrs_with_stream_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))

            config_payload = {
                "device_id": "trace1",
                "source_kind": "local",
                "is_remote": False,
                "yaml_text": None,
                "device_metadata": {"device_type": "dummy_trace"},
                "stream_metadata": {"trace": {"from_config": "cfg", "shared": "config"}},
                "stream_calls": [
                    {
                        "method": "acquire_trace",
                        "outputs": [
                            {
                                "stream": "trace",
                                "dtype": "float64",
                                "shape": [8],
                                "units": "V",
                                "description": "trace samples",
                                "attrs": {"from_call": "call", "shared": "call"},
                            }
                        ],
                    }
                ],
            }

            writer._fetch_schema_with_backoff = (  # type: ignore[method-assign]  # noqa: SLF001
                lambda timeout_s=5.0: {"devices": []}
            )
            writer._fetch_config_with_backoff = (  # type: ignore[method-assign]  # noqa: SLF001
                lambda timeout_s=5.0: [config_payload]
            )

            calls: list[tuple[str, str]] = []

            def _fake_call(
                *, device_id: str, action: str, timeout_ms: int = 1200
            ) -> object | None:
                _ = timeout_ms
                calls.append((device_id, action))
                return None

            writer._call_optional_device_action = _fake_call  # type: ignore[method-assign]  # noqa: SLF001

            h5_path = root / "runtime_meta_merge.h5"
            with h5py.File(h5_path, "w") as h5:
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=True,
                    measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    ),
                )

                pending = writer._pending_stream_metadata.get(("trace1", "trace"))  # noqa: SLF001
                self.assertIsInstance(pending, dict)
                assert isinstance(pending, dict)
                self.assertEqual(pending.get("from_config"), "cfg")
                self.assertEqual(pending.get("from_call"), "call")
                self.assertEqual(pending.get("units"), "V")
                self.assertEqual(pending.get("description"), "trace samples")
                self.assertEqual(pending.get("shared"), "config")
                self.assertNotIn("from_driver", pending)

            self.assertIn(("trace1", "collect_run_metadata"), calls)
            self.assertNotIn(("trace1", "device_metadata"), calls)
            self.assertNotIn(("trace1", "stream_metadata"), calls)

    def test_configure_active_file_collects_run_metadata_for_local_devices(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))

            local_cfg = {
                "device_id": "trace1",
                "source_kind": "local",
                "is_remote": False,
                "yaml_text": None,
                "device_metadata": {"device_type": "dummy_trace"},
                "stream_metadata": {},
                "stream_calls": [],
            }
            remote_cfg = {
                "device_id": "remote_trace",
                "source_kind": "federated",
                "is_remote": True,
                "yaml_text": None,
                "device_metadata": {"device_type": "dummy_trace"},
                "stream_metadata": {},
                "stream_calls": [],
            }

            writer._fetch_schema_with_backoff = (  # type: ignore[method-assign]  # noqa: SLF001
                lambda timeout_s=5.0: {"devices": []}
            )
            writer._fetch_config_with_backoff = (  # type: ignore[method-assign]  # noqa: SLF001
                lambda timeout_s=5.0: [local_cfg, remote_cfg]
            )

            calls: list[tuple[str, str]] = []

            def _fake_call(
                *, device_id: str, action: str, timeout_ms: int = 1200
            ) -> object | None:
                _ = timeout_ms
                calls.append((device_id, action))
                if action == "collect_run_metadata" and device_id == "trace1":
                    return {"frequency_hz": 12_345.0, "mode": "lock"}
                return None

            writer._call_optional_device_action = _fake_call  # type: ignore[method-assign]  # noqa: SLF001

            h5_path = root / "run_meta_on_configure.h5"
            with h5py.File(h5_path, "w") as h5:
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=True,
                    measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    ),
                )

                run_meta_group = h5.get("run_metadata")
                self.assertIsNotNone(run_meta_group)
                assert run_meta_group is not None
                ds = run_meta_group["trace1"]["json"]
                payload = json.loads(_as_text(ds[()]))
                self.assertEqual(payload.get("frequency_hz"), 12_345.0)
                self.assertEqual(payload.get("mode"), "lock")
                self.assertNotIn("remote_trace", run_meta_group)

            self.assertIn(("trace1", "collect_run_metadata"), calls)
            self.assertNotIn(("remote_trace", "collect_run_metadata"), calls)


class HdfWriterDeviceMetadataStorageTests(unittest.TestCase):
    def _make_writer(self, out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65531",
            manager_pub="tcp://127.0.0.1:65532",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_handle_device_config_writes_metadata_json_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "device_metadata_write.h5"

            with h5py.File(h5_path, "w") as h5:
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    ),
                )
                writer._handle_device_config(  # noqa: SLF001
                    {
                        "device_id": "trace1",
                        "yaml_text": "version: 1\n",
                        "device_metadata": {
                            "device_type": "dummy_trace",
                            "location": "rack_a",
                        },
                        "stream_metadata": {
                            "trace": {"scale": 2.0, "units": "V"},
                        },
                        "stream_calls": [
                            {
                                "method": "acquire_trace",
                                "outputs": [
                                    {
                                        "stream": "trace",
                                        "dtype": "float64",
                                        "shape": [8],
                                    }
                                ],
                            }
                        ],
                        "run_meta_calls": [
                            {
                                "method": "read_gain",
                                "kwargs": {},
                                "outputs": [
                                    {
                                        "key": "adc_gain",
                                        "kind": "scalar",
                                        "dtype": "float64",
                                        "units": "V/V",
                                    }
                                ],
                            }
                        ],
                    }
                )
                device_ds = h5["config"]["trace1"]["device_metadata_json"]
                stream_ds = h5["config"]["trace1"]["stream_metadata_json"]
                run_meta_ds = h5["config"]["trace1"]["run_meta_calls_json"]
                device_payload = json.loads(_as_text(device_ds[()]))
                stream_payload = json.loads(_as_text(stream_ds[()]))
                run_meta_payload = json.loads(_as_text(run_meta_ds[()]))
                self.assertEqual(device_payload.get("device_type"), "dummy_trace")
                self.assertEqual(device_payload.get("location"), "rack_a")
                self.assertEqual(stream_payload.get("trace", {}).get("scale"), 2.0)
                self.assertEqual(stream_payload.get("trace", {}).get("units"), "V")
                self.assertEqual(len(run_meta_payload), 1)
                self.assertEqual(run_meta_payload[0].get("method"), "read_gain")

    def test_device_metadata_json_updates_on_subsequent_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "device_metadata_update.h5"

            with h5py.File(h5_path, "w") as h5:
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    ),
                )
                writer._handle_device_config(  # noqa: SLF001
                    {
                        "device_id": "trace1",
                        "yaml_text": "version: 1\n",
                        "device_metadata": {"location": "rack_a"},
                        "stream_metadata": {},
                        "stream_calls": [],
                    }
                )
                writer._handle_device_config(  # noqa: SLF001
                    {
                        "device_id": "trace1",
                        "yaml_text": "version: 1\n",
                        "device_metadata": {"location": "rack_b", "serial": "SN-42"},
                        "stream_metadata": {},
                        "stream_calls": [],
                    }
                )
                ds = h5["config"]["trace1"]["device_metadata_json"]
                payload = json.loads(_as_text(ds[()]))
                self.assertEqual(payload.get("location"), "rack_b")
                self.assertEqual(payload.get("serial"), "SN-42")

    def test_stream_metadata_json_updates_on_subsequent_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "stream_metadata_update.h5"

            with h5py.File(h5_path, "w") as h5:
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    ),
                )
                writer._handle_device_config(  # noqa: SLF001
                    {
                        "device_id": "trace1",
                        "yaml_text": "version: 1\n",
                        "device_metadata": {"location": "rack_a"},
                        "stream_metadata": {"trace": {"scale": 1.0, "units": "V"}},
                        "stream_calls": [],
                        "run_meta_calls": [{"method": "read_gain"}],
                    }
                )
                writer._handle_device_config(  # noqa: SLF001
                    {
                        "device_id": "trace1",
                        "yaml_text": "version: 1\n",
                        "device_metadata": {"location": "rack_a"},
                        "stream_metadata": {"trace": {"scale": 2.0, "units": "counts"}},
                        "stream_calls": [],
                        "run_meta_calls": [{"method": "read_offset"}],
                    }
                )
                stream_ds = h5["config"]["trace1"]["stream_metadata_json"]
                run_meta_ds = h5["config"]["trace1"]["run_meta_calls_json"]
                stream_payload = json.loads(_as_text(stream_ds[()]))
                run_meta_payload = json.loads(_as_text(run_meta_ds[()]))
                self.assertEqual(stream_payload.get("trace", {}).get("scale"), 2.0)
                self.assertEqual(stream_payload.get("trace", {}).get("units"), "counts")
                self.assertEqual(len(run_meta_payload), 1)
                self.assertEqual(run_meta_payload[0].get("method"), "read_offset")

    def test_metadata_json_snapshots_default_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            h5_path = root / "device_metadata_default.h5"

            with h5py.File(h5_path, "w") as h5:
                writer._configure_active_file(  # noqa: SLF001
                    h5,
                    write_every_s=1.0,
                    load_manager_state=False,
                    measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    ),
                )
                writer._handle_device_config(  # noqa: SLF001
                    {
                        "device_id": "trace1",
                        "yaml_text": "version: 1\n",
                        "device_metadata": "not-a-dict",
                        "stream_metadata": "not-a-dict",
                        "stream_calls": [],
                        "run_meta_calls": "not-a-list",
                    }
                )
                device_ds = h5["config"]["trace1"]["device_metadata_json"]
                stream_ds = h5["config"]["trace1"]["stream_metadata_json"]
                run_meta_ds = h5["config"]["trace1"]["run_meta_calls_json"]
                device_payload = json.loads(_as_text(device_ds[()]))
                stream_payload = json.loads(_as_text(stream_ds[()]))
                run_meta_payload = json.loads(_as_text(run_meta_ds[()]))
                self.assertEqual(device_payload, {})
                self.assertEqual(stream_payload, {})
                self.assertEqual(run_meta_payload, [])


def _make_bg_test_writer() -> HdfWriter:
    """Build a writer just complete enough to exercise the bg flush thread."""
    return HdfWriter(
        out_dir="data",
        filename=None,
        manager_rpc="tcp://127.0.0.1:65541",
        manager_pub="tcp://127.0.0.1:65542",
        rpc_timeout_ms=2000,
        timezone="America/Chicago",
        rcvhwm=1000,
        write_every_s=1.0,
        buffer_max_messages=1000,
        flush_every_n=10,
        flush_every_s=1.0,
        disabled_devices=[],
        bg_join_timeout_s=0.5,
    )


def _spawn_bg_thread(writer: HdfWriter) -> threading.Thread:
    writer._start_bg_thread()  # noqa: SLF001
    assert writer._bg_thread is not None  # noqa: SLF001
    return writer._bg_thread  # noqa: SLF001


def _wait_for(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class HdfWriterBgFlushThreadTests(unittest.TestCase):
    def test_flush_batch_dispatch_failure_keeps_thread_alive(self) -> None:
        """A failing _handle_flush_batch is logged + dropped without killing
        the thread or setting the watchdog flag. Production policy: a
        single batch failure is recoverable; only fatal/escaped exceptions
        stop the process."""
        writer = _make_bg_test_writer()

        calls: list[int] = []

        def boom(_batch: _FlushBatch) -> None:
            calls.append(len(calls))
            raise RuntimeError("simulated write failure")

        writer._handle_flush_batch = boom  # type: ignore[assignment]  # noqa: SLF001
        thread = _spawn_bg_thread(writer)
        try:
            writer._bg_queue.put(_FlushBatch())  # noqa: SLF001
            writer._bg_queue.put(_FlushBatch())  # noqa: SLF001
            self.assertTrue(_wait_for(lambda: len(calls) >= 2))
            self.assertFalse(writer._bg_thread_dead)  # noqa: SLF001
            self.assertFalse(writer._stop_evt.is_set())  # noqa: SLF001
            self.assertEqual(
                writer._error_counts.get("bg._FlushBatch.failed"),  # noqa: SLF001
                2,
            )
        finally:
            writer._stop_evt.set()  # noqa: SLF001
            writer._bg_queue.put(_BG_SENTINEL)  # noqa: SLF001
            thread.join(timeout=1.0)
            self.assertFalse(thread.is_alive())

    def test_fatal_bg_thread_exception_sets_watchdog_and_stops(self) -> None:
        """A bare exception escaping the outer queue.get loop is fatal —
        the thread sets _bg_thread_dead, _stop_evt, exits cleanly. The
        main run loop's watchdog check in turn breaks the run loop."""
        writer = _make_bg_test_writer()

        # Replace the queue with a sentinel that raises on the first .get()
        # call, simulating an unhandled error escaping the inner try/except.
        class BoomQueue:
            def get(self, *args, **kwargs):
                raise RuntimeError("simulated queue.get failure")

        writer._bg_queue = BoomQueue()  # type: ignore[assignment]  # noqa: SLF001
        thread = _spawn_bg_thread(writer)
        try:
            thread.join(timeout=1.0)
            self.assertFalse(thread.is_alive())
            self.assertTrue(writer._bg_thread_dead)  # noqa: SLF001
            self.assertTrue(writer._stop_evt.is_set())  # noqa: SLF001
            self.assertEqual(
                writer._error_counts.get("bg_thread_fatal"), 1  # noqa: SLF001
            )
            self.assertIsNotNone(writer._last_exception)  # noqa: SLF001
        finally:
            writer._stop_evt.set()  # noqa: SLF001

    def test_shutdown_sentinel_drains_queue_cleanly(self) -> None:
        """Sentinel posted to the queue makes the bg thread exit
        immediately even if real batches remain queued behind it."""
        writer = _make_bg_test_writer()

        handled: list[_FlushBatch] = []

        def record(batch: _FlushBatch) -> None:
            handled.append(batch)

        writer._handle_flush_batch = record  # type: ignore[assignment]  # noqa: SLF001
        thread = _spawn_bg_thread(writer)
        try:
            first = _FlushBatch()
            writer._bg_queue.put(first)  # noqa: SLF001
            writer._bg_queue.put(_BG_SENTINEL)  # noqa: SLF001
            writer._bg_queue.put(_FlushBatch())  # noqa: SLF001 — should NOT be processed
            thread.join(timeout=1.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(handled, [first])
        finally:
            writer._stop_evt.set()  # noqa: SLF001


class HdfWriterFlushBatchOverflowTests(unittest.TestCase):
    """Regression coverage for `_enqueue_flush_batch` overflow handling.

    The writer is now **non-dropping**: when the bg queue is full,
    `_enqueue_flush_batch` leaves all data in the in-memory reservoirs and
    returns False, regardless of `force_flush`. It bumps the *deferred*
    counter (never the dropped counter) and publishes a rate-limited
    `hdf.flush_batch_deferred` event. Nothing is discarded — the reservoir
    is drained on the next successful enqueue.
    """

    def _make_writer(self) -> HdfWriter:
        return HdfWriter(
            out_dir="data",
            filename=None,
            manager_rpc="tcp://127.0.0.1:65551",
            manager_pub="tcp://127.0.0.1:65552",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def _shrink_bg_queue(self, writer: HdfWriter) -> None:
        # Replace the bounded bg queue with a tiny one so the second
        # put_nowait raises queue.Full deterministically. Cast away the
        # typing on the protected attribute the way the existing bg-thread
        # tests do.
        writer._bg_queue = queue.Queue(maxsize=1)  # type: ignore[assignment]  # noqa: SLF001

    @staticmethod
    def _seed_buf_with_row(writer: HdfWriter, row: dict) -> None:
        # The writer's `_buf` is only allocated inside run(); for these tests
        # we just need a non-empty drain buffer so the snapshot has data we
        # can assert is lost on overflow.
        writer._buf = deque([row])  # noqa: SLF001

    def test_overflow_defers_batch_without_dropping_even_on_force_flush(self) -> None:
        writer = self._make_writer()
        self._shrink_bg_queue(writer)
        published: list[tuple[str, dict]] = []

        def fake_publish(*, topic, payload, **_kw) -> bool:
            published.append((topic, dict(payload)))
            return True

        writer._publish_process_event = fake_publish  # type: ignore[method-assign]  # noqa: SLF001

        # Fill the queue with a first batch (this one fits).
        self._seed_buf_with_row(writer, {"device_id": "dev1", "v": 1})
        self.assertTrue(writer._enqueue_flush_batch(force_flush=True))  # noqa: SLF001
        self.assertEqual(writer._bg_queue.qsize(), 1)  # noqa: SLF001

        # Second batch: snapshot has real data, queue is full. Non-dropping:
        # even with force_flush=True the data stays in the reservoir.
        self._seed_buf_with_row(writer, {"device_id": "dev1", "v": 2})
        result = writer._enqueue_flush_batch(force_flush=True)  # noqa: SLF001

        self.assertFalse(result)
        # Deferred, never dropped.
        self.assertEqual(writer._deferred_flush_batches, 1)  # noqa: SLF001
        self.assertEqual(writer._dropped_flush_batches, 0)  # noqa: SLF001
        self.assertEqual(
            writer._error_counts.get("bg.flush_batch.deferred"), 1  # noqa: SLF001
        )
        self.assertIsNone(
            writer._error_counts.get("bg.flush_batch.dropped")  # noqa: SLF001
        )

        # The reservoir is PRESERVED (not discarded): `_buf` still holds v=2.
        self.assertEqual(len(writer._buf or []), 1)  # noqa: SLF001
        self.assertEqual(list(writer._buf or [])[0].get("v"), 2)  # noqa: SLF001

        # A deferred (not dropped) event was published.
        self.assertEqual(len(published), 1)
        topic, payload = published[0]
        self.assertEqual(topic, "hdf.flush_batch_deferred")
        self.assertEqual(payload.get("queue_max"), 1)
        self.assertEqual(payload.get("deferred_total"), 1)
        self.assertIn("queue_depth", payload)

    def test_overflow_event_is_rate_limited_to_one_per_second(self) -> None:
        writer = self._make_writer()
        self._shrink_bg_queue(writer)
        published: list[tuple[str, dict]] = []

        def fake_publish(*, topic, payload, **_kw) -> bool:
            published.append((topic, dict(payload)))
            return True

        writer._publish_process_event = fake_publish  # type: ignore[method-assign]  # noqa: SLF001

        # First put fills the queue.
        self._seed_buf_with_row(writer, {"device_id": "dev1", "v": 1})
        self.assertTrue(writer._enqueue_flush_batch(force_flush=True))  # noqa: SLF001

        # First overflow publishes a deferred event.
        self._seed_buf_with_row(writer, {"device_id": "dev1", "v": 2})
        self.assertFalse(writer._enqueue_flush_batch(force_flush=True))  # noqa: SLF001
        self.assertEqual(len(published), 1)

        # Second overflow within the 1-second window: counter still bumps,
        # but no additional event publish (rate-limited).
        self.assertFalse(writer._enqueue_flush_batch(force_flush=True))  # noqa: SLF001
        self.assertEqual(writer._deferred_flush_batches, 2)  # noqa: SLF001
        self.assertEqual(
            writer._error_counts.get("bg.flush_batch.deferred"), 2  # noqa: SLF001
        )
        self.assertEqual(len(published), 1)

        # Advance the rate-limit window and a new overflow publishes again.
        writer._last_flush_defer_event_mono = time.monotonic() - 2.0  # noqa: SLF001
        self.assertFalse(writer._enqueue_flush_batch(force_flush=True))  # noqa: SLF001
        self.assertEqual(len(published), 2)
        self.assertEqual(published[-1][1].get("deferred_total"), 3)


class HdfWriterFlushBatchDeferTests(unittest.TestCase):
    """Coverage for the defer-when-full path on `_enqueue_flush_batch`.

    With `force_flush=False`, an overflowing bg queue causes the writer
    to leave `_buf`/`_event_buf` rows in place (the 200k in-memory deque
    already handles bounded overflow) and only snapshot-and-discard the
    unbounded `_stream_buffers` and `_pending_stream_metadata` dicts.
    A `hdf.flush_batch_deferred` event is published, rate-limited to
    once per second. Force-flush callers still hit the legacy drop path.
    """

    def _make_writer(self) -> HdfWriter:
        return HdfWriter(
            out_dir="data",
            filename=None,
            manager_rpc="tcp://127.0.0.1:65553",
            manager_pub="tcp://127.0.0.1:65554",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def _shrink_bg_queue(self, writer: HdfWriter) -> None:
        writer._bg_queue = queue.Queue(maxsize=1)  # type: ignore[assignment]  # noqa: SLF001

    def _fill_bg_queue(self, writer: HdfWriter) -> None:
        writer._bg_queue.put_nowait(_FlushBatch())  # noqa: SLF001

    def test_overflow_defers_when_force_flush_false(self) -> None:
        writer = self._make_writer()
        self._shrink_bg_queue(writer)
        published: list[tuple[str, dict]] = []

        def fake_publish(*, topic, payload, **_kw) -> bool:
            published.append((topic, dict(payload)))
            return True

        writer._publish_process_event = fake_publish  # type: ignore[method-assign]  # noqa: SLF001

        # Saturate the bg queue and populate the in-memory deque with N rows.
        self._fill_bg_queue(writer)
        n_rows = 5
        seed_rows = [{"device_id": "dev1", "v": i} for i in range(n_rows)]
        writer._buf = deque(seed_rows)  # noqa: SLF001

        result = writer._enqueue_flush_batch(force_flush=False)  # noqa: SLF001

        # Deferred: returns False but does NOT drop the snapshot.
        self.assertFalse(result)
        self.assertEqual(writer._deferred_flush_batches, 1)  # noqa: SLF001
        self.assertEqual(writer._dropped_flush_batches, 0)  # noqa: SLF001
        self.assertEqual(
            writer._error_counts.get("bg.flush_batch.deferred"), 1  # noqa: SLF001
        )
        self.assertIsNone(  # noqa: SLF001 — `_error_counts` only carries the deferred bucket
            writer._error_counts.get("bg.flush_batch.dropped")
        )

        # Rows are preserved in the in-memory deque.
        self.assertEqual(len(writer._buf or []), n_rows)  # noqa: SLF001
        self.assertEqual(list(writer._buf or []), seed_rows)  # noqa: SLF001

        # Defer event published with the expected payload shape.
        self.assertEqual(len(published), 1)
        topic, payload = published[0]
        self.assertEqual(topic, "hdf.flush_batch_deferred")
        self.assertEqual(payload.get("queue_max"), 1)
        self.assertEqual(payload.get("deferred_total"), 1)
        self.assertEqual(payload.get("buffered_rows"), n_rows)
        self.assertIn("buffered_events", payload)
        self.assertIn("buffered_streams", payload)

    def test_defer_preserves_stream_buffers(self) -> None:
        writer = self._make_writer()
        self._shrink_bg_queue(writer)
        writer._publish_process_event = (  # type: ignore[method-assign]  # noqa: SLF001
            lambda **_kw: True
        )

        self._fill_bg_queue(writer)
        seed_rows = [{"device_id": "dev1", "v": 1}]
        writer._buf = deque(seed_rows)  # noqa: SLF001
        # Stream-buffer shape: {(device_id, stream): {col: list[value]}}.
        stream_buffers = {
            ("dev1", "streamA"): {"data": [b"x", b"y", b"z"]},
            ("dev1", "streamB"): {"data": [b"w"]},
        }
        writer._stream_buffers = {  # noqa: SLF001
            k: dict(v) for k, v in stream_buffers.items()
        }
        writer._pending_stream_metadata = {  # noqa: SLF001
            ("dev1", "streamA"): {"units": "V"},
        }

        result = writer._enqueue_flush_batch(force_flush=False)  # noqa: SLF001

        self.assertFalse(result)
        # Non-dropping: the reservoir is fully preserved so nothing is lost.
        self.assertEqual(writer._stream_buffers, stream_buffers)  # noqa: SLF001
        self.assertEqual(
            writer._pending_stream_metadata,  # noqa: SLF001
            {("dev1", "streamA"): {"units": "V"}},
        )
        self.assertEqual(list(writer._buf or []), seed_rows)  # noqa: SLF001

    def test_defer_event_rate_limited(self) -> None:
        writer = self._make_writer()
        self._shrink_bg_queue(writer)
        published: list[tuple[str, dict]] = []

        def fake_publish(*, topic, payload, **_kw) -> bool:
            published.append((topic, dict(payload)))
            return True

        writer._publish_process_event = fake_publish  # type: ignore[method-assign]  # noqa: SLF001

        self._fill_bg_queue(writer)
        writer._buf = deque([{"device_id": "dev1", "v": 1}])  # noqa: SLF001

        # First defer publishes an event.
        self.assertFalse(writer._enqueue_flush_batch(force_flush=False))  # noqa: SLF001
        self.assertEqual(len(published), 1)

        # Second defer within the 1-second window: counter still bumps,
        # but no additional event publish.
        self.assertFalse(writer._enqueue_flush_batch(force_flush=False))  # noqa: SLF001
        self.assertEqual(writer._deferred_flush_batches, 2)  # noqa: SLF001
        self.assertEqual(len(published), 1)

        # Advance the rate-limit window and a new defer publishes again.
        writer._last_flush_defer_event_mono = time.monotonic() - 2.0  # noqa: SLF001
        self.assertFalse(writer._enqueue_flush_batch(force_flush=False))  # noqa: SLF001
        self.assertEqual(len(published), 2)
        self.assertEqual(published[-1][1].get("deferred_total"), 3)


class HdfWriterLosslessHandoffTests(unittest.TestCase):
    """Pins the non-dropping hand-off + drain guarantees introduced to stop the
    writer losing frames/telemetry between the main loop and the bg thread."""

    @staticmethod
    def _make_writer(out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65541",
            manager_pub="tcp://127.0.0.1:65542",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    @staticmethod
    def _u16_payload(value: int) -> bytes:
        return np.asarray([value], dtype=np.uint16).tobytes()

    @staticmethod
    def _fake_reader() -> object:
        class _Layout:
            dtype = np.dtype("uint16")
            shape = (1,)

        class _Reader:
            layout = _Layout()

        return _Reader()

    def _configure(self, writer: HdfWriter, h5: "h5py.File") -> None:
        meta = writer._build_measurement_metadata(  # noqa: SLF001
            profile_id=None, values=None, require_profile=False
        )
        writer._configure_active_file(  # noqa: SLF001
            h5, write_every_s=1.0, load_manager_state=False, measurement_meta=meta
        )

    def _seed_stream(self, writer: HdfWriter, key, seqs) -> None:
        for s in seqs:
            writer._store_context_for_seq(  # noqa: SLF001
                key=key, seq=s, context_id=100 + s, now_mono=1.0
            )
        writer._append_chunk_ready_events(  # noqa: SLF001
            key=key,
            reader=self._fake_reader(),
            events=[
                {
                    "seq": s,
                    "payload": self._u16_payload(s),
                    "t0_mono_ns": s,
                    "t0_wall_ns": s,
                }
                for s in seqs
            ],
            initial_last_seq=seqs[0] - 1,
            now_mono=1.0,
        )

    def test_record_context_defers_and_bg_writes_it(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            with h5py.File(root / "ctx.h5", "w") as h5:
                self._configure(writer, h5)
                # Deferred: buffered on the main thread, NOT written to h5py.
                writer._record_context(7, {"amp": 1.0})  # noqa: SLF001
                writer._record_context(7, {"amp": 1.0})  # dedup  # noqa: SLF001
                self.assertEqual(len(writer._pending_context_rows), 1)  # noqa: SLF001
                self.assertEqual(writer._context_table_ds.shape[0], 0)  # noqa: SLF001
                # Bg write path drains + persists it.
                _r, _e, _s, _m, context_rows = (
                    writer._snapshot_main_loop_buffers()  # noqa: SLF001
                )
                self.assertEqual(len(context_rows), 1)
                self.assertEqual(writer._pending_context_rows, [])  # noqa: SLF001
                writer._handle_flush_batch(  # noqa: SLF001
                    _FlushBatch(context_rows=context_rows, force_flush=True)
                )
                self.assertEqual(writer._context_table_ds.shape[0], 1)  # noqa: SLF001
                self.assertEqual(
                    int(writer._context_table_ds[0]["context_id"]), 7  # noqa: SLF001
                )

    def test_stream_handoff_via_snapshot_is_lossless(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            with h5py.File(root / "lossless.h5", "w") as h5:
                self._configure(writer, h5)
                key = ("trace1", "trace")
                seqs = list(range(1, 51))
                self._seed_stream(writer, key, seqs)
                # Simulate the bg hand-off: self-contained snapshot -> bg write.
                rows, event_rows, stream_buffers, stream_meta, context_rows = (
                    writer._snapshot_main_loop_buffers()  # noqa: SLF001
                )
                # stream_meta carries dtype/shape/session so the bg write needs
                # none of the main-owned maps.
                self.assertIn(key, stream_meta)
                writer._handle_flush_batch(  # noqa: SLF001
                    _FlushBatch(
                        buffered_rows=rows,
                        event_rows=event_rows,
                        stream_batches=stream_buffers,
                        stream_meta=stream_meta,
                        context_rows=context_rows,
                        force_flush=True,
                    )
                )
                dsets = writer._stream_datasets[("trace1", "trace", 1)]  # noqa: SLF001
                self.assertEqual(list(dsets["seq"][...]), seqs)
                self.assertEqual(int(dsets["data"].attrs["dropped_total"]), 0)

    def test_deferred_stream_batch_lands_after_queue_drains(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            writer._bg_queue = queue.Queue(maxsize=1)  # noqa: SLF001
            writer._publish_process_event = lambda **_k: True  # type: ignore[method-assign]  # noqa: SLF001,E731
            with h5py.File(root / "defer.h5", "w") as h5:
                self._configure(writer, h5)
                key = ("trace1", "trace")
                seqs = list(range(1, 6))
                self._seed_stream(writer, key, seqs)
                # Saturate the queue so the enqueue defers (non-dropping).
                writer._bg_queue.put_nowait(_FlushBatch())  # noqa: SLF001
                self.assertFalse(
                    writer._enqueue_flush_batch(force_flush=True)  # noqa: SLF001
                )
                # Reservoir preserved — nothing dropped.
                self.assertEqual(len(writer._stream_buffers[key]["data"]), 5)  # noqa: SLF001
                # Drain the placeholder, enqueue again (now succeeds), process it.
                writer._bg_queue.get_nowait()  # noqa: SLF001
                self.assertTrue(
                    writer._enqueue_flush_batch(force_flush=True)  # noqa: SLF001
                )
                writer._handle_flush_batch(writer._bg_queue.get_nowait())  # noqa: SLF001
                dsets = writer._stream_datasets[("trace1", "trace", 1)]  # noqa: SLF001
                self.assertEqual(list(dsets["seq"][...]), seqs)

    def test_stop_writing_flushes_buffered_streams_before_close(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            new_file = writer._start_writing_file(filename="stopflush.h5")  # noqa: SLF001
            key = ("trace1", "trace")
            seqs = [1, 2, 3]
            self._seed_stream(writer, key, seqs)
            # No bg thread running, so quiesce is a no-op and the synchronous
            # drain writes the live reservoir before the file closes.
            old = writer._stop_writing_file()  # noqa: SLF001
            self.assertEqual(old, new_file)
            with h5py.File(new_file, "r") as h5:
                ds = h5["streams/trace1/trace/session_001/seq"]
                self.assertEqual(list(ds[...]), seqs)

    def test_close_drains_leftover_queued_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            writer = self._make_writer(str(root))
            new_file = writer._start_writing_file(filename="closeflush.h5")  # noqa: SLF001
            key = ("trace1", "trace")
            seqs = [1, 2, 3, 4]
            self._seed_stream(writer, key, seqs)
            # Snapshot into a batch and put it on the queue WITHOUT a bg thread
            # to consume it (simulates a batch left queued when the bg thread
            # stopped). close() must drain it before closing the file.
            rows, event_rows, stream_buffers, stream_meta, context_rows = (
                writer._snapshot_main_loop_buffers()  # noqa: SLF001
            )
            writer._bg_queue.put_nowait(  # noqa: SLF001
                _FlushBatch(
                    buffered_rows=rows,
                    event_rows=event_rows,
                    stream_batches=stream_buffers,
                    stream_meta=stream_meta,
                    context_rows=context_rows,
                    force_flush=True,
                )
            )
            writer.close()
            with h5py.File(new_file, "r") as h5:
                ds = h5["streams/trace1/trace/session_001/seq"]
                self.assertEqual(list(ds[...]), seqs)


class WritingActiveTelemetryTests(unittest.TestCase):
    def test_process_telemetry_schema_declares_writing_active(self) -> None:
        proc = object.__new__(HdfWriter)
        schema = proc.process_telemetry_schema()
        self.assertEqual(
            schema, [{"name": "writing_active", "dtype": "bool", "units": ""}]
        )

    def test_publish_writing_active_sends_schema_then_signal(self) -> None:
        import experiment_control.processes.hdf_writer as hw

        calls: list[dict] = []

        def _fake_rpc(_ctx, _endpoint, payload, timeout_ms=2000):
            calls.append(payload)
            return {"ok": True}

        proc = object.__new__(HdfWriter)
        proc._process_id = "hdf_writer"
        proc._ctx = None
        proc._manager_rpc = "tcp://127.0.0.1:1"
        proc._rpc_timeout_ms = 2000
        proc._writing_active_rpc_timeout_ms = 1000
        proc._writing_active = True
        proc._writing_active_schema_advertised = False

        orig = hw._manager_rpc
        hw._manager_rpc = _fake_rpc
        try:
            proc._publish_writing_active_telemetry()
            # second call: schema already advertised, only the signal goes out
            proc._publish_writing_active_telemetry()
        finally:
            hw._manager_rpc = orig

        # First pass: schema advertise + telemetry publish; second pass: publish only.
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            calls[0]["type"], "manager.process_telemetry.schema.advertise"
        )
        self.assertEqual(calls[1]["type"], "manager.events.publish")
        self.assertEqual(calls[1]["topic"], "manager.process_telemetry_update")
        sig = calls[1]["payload"]["signals"]["writing_active"]
        self.assertEqual(sig["value"], True)
        self.assertEqual(sig["quality"], "OK")
        self.assertEqual(calls[2]["type"], "manager.events.publish")

    def test_schedule_skips_when_previous_publish_in_flight(self) -> None:
        submitted: list = []

        class _Fut:
            def __init__(self, done: bool) -> None:
                self._done = done

            def done(self) -> bool:
                return self._done

        class _Exec:
            def submit(self, fn, *a, **k):
                submitted.append(fn)
                return _Fut(False)

        proc = object.__new__(HdfWriter)
        proc._process_id = "hdf_writer"
        proc._telemetry_executor = _Exec()
        proc._telemetry_future = None

        # First schedule submits.
        proc._schedule_writing_active_publish()
        self.assertEqual(len(submitted), 1)
        # Second schedule while the future is not done -> skip (no pile-up).
        proc._schedule_writing_active_publish()
        self.assertEqual(len(submitted), 1)
        # Once the prior future is done, a new schedule submits again.
        proc._telemetry_future = _Fut(True)
        proc._schedule_writing_active_publish()
        self.assertEqual(len(submitted), 2)

    def test_schedule_noop_without_process_id(self) -> None:
        submitted: list = []

        class _Exec:
            def submit(self, fn, *a, **k):
                submitted.append(fn)
                return None

        proc = object.__new__(HdfWriter)
        proc._process_id = ""
        proc._telemetry_executor = _Exec()
        proc._telemetry_future = None
        proc._schedule_writing_active_publish()
        self.assertEqual(submitted, [])


class HdfWriterRunMetadataCaptureTests(unittest.TestCase):
    """Regression tests for the file-open stall that silently dropped all
    telemetry (config + datasets present, 0 telemetry rows, dropped_local=0).
    Root cause: run-metadata capture ran its slow serial per-device RPCs
    inline in _configure_active_file, blocking the single SUB-drain loop.
    Fix: capture runs OFF the main loop (bg thread) with the RPCs parallelized.
    """

    def _open_writer_file(self, writer: HdfWriter, h5: h5py.File) -> None:
        meta = writer._build_measurement_metadata(  # noqa: SLF001
            profile_id=None, values=None, require_profile=False
        )
        with writer._h5_lock:  # noqa: SLF001
            writer._configure_active_file(  # noqa: SLF001
                h5,
                write_every_s=1.0,
                load_manager_state=False,
                measurement_meta=meta,
            )

    def test_capture_run_metadata_runs_rpcs_in_parallel(self) -> None:
        writer = _make_bg_test_writer()
        lock = threading.Lock()
        active = [0]
        peak = [0]

        def collect(*, device_id: str, action: str, timeout_ms: int) -> dict:
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.3)
            with lock:
                active[0] -= 1
            return {"device": device_id}

        writer._call_optional_device_action = collect  # type: ignore[assignment]  # noqa: SLF001
        configs = [{"device_id": f"d{i}"} for i in range(4)]
        with _temp_dir() as td:
            with h5py.File(Path(td) / "parallel.h5", "w") as h5:
                self._open_writer_file(writer, h5)
                t0 = time.monotonic()
                writer._capture_run_metadata_for_configs(configs)  # noqa: SLF001
                elapsed = time.monotonic() - t0
                self.assertGreater(peak[0], 1, "collect RPCs did not run concurrently")
                self.assertLess(elapsed, 4 * 0.3 * 0.8, "capture ran serially")
                self.assertEqual(len(h5["run_metadata"]), 4)
                self.assertIn("json", h5["run_metadata"]["d0"])

    def test_capture_deferred_to_bg_thread(self) -> None:
        writer = _make_bg_test_writer()
        seen: dict[str, str] = {}
        done = threading.Event()

        def rec(configs, *, expected_measurement_id=None) -> None:  # noqa: ANN001
            seen["thread"] = threading.current_thread().name
            done.set()

        writer._capture_run_metadata_for_configs = rec  # type: ignore[assignment]  # noqa: SLF001
        thread = _spawn_bg_thread(writer)
        try:
            writer._enqueue_run_metadata_capture([{"device_id": "d0"}])  # noqa: SLF001
            self.assertTrue(done.wait(2.0))
            self.assertEqual(seen["thread"], "hdf-bg-flush")
        finally:
            writer._stop_evt.set()  # noqa: SLF001
            writer._bg_queue.put(_BG_SENTINEL)  # noqa: SLF001
            thread.join(timeout=1.0)

    def test_capture_inline_fallback_when_no_bg_thread(self) -> None:
        writer = _make_bg_test_writer()
        seen: dict[str, str] = {}

        def rec(configs, *, expected_measurement_id=None) -> None:  # noqa: ANN001
            seen["thread"] = threading.current_thread().name

        writer._capture_run_metadata_for_configs = rec  # type: ignore[assignment]  # noqa: SLF001
        # No bg thread spawned -> _bg_thread is None -> inline fallback so
        # metadata is never silently skipped.
        writer._enqueue_run_metadata_capture([{"device_id": "d0"}])  # noqa: SLF001
        self.assertEqual(seen["thread"], threading.current_thread().name)

    def test_optional_device_action_accepts_status_ok_envelope(self) -> None:
        """Device commands routed via the manager return the raw driver
        envelope {"status": "OK", "result"} on success; only manager-level
        failures use {"ok": False}. _call_optional_device_action must accept
        both, else every collect_run_metadata is dropped (empty /run_metadata)."""
        import experiment_control.processes.hdf_writer as hw

        writer = _make_bg_test_writer()
        orig = hw._manager_rpc
        try:
            hw._manager_rpc = (  # type: ignore[assignment]
                lambda ctx, ep, payload, timeout_ms=2000: {
                    "id": 1,
                    "status": "OK",
                    "result": {"sample_rate_hz": 1.0},
                }
            )
            got = writer._call_optional_device_action(  # noqa: SLF001
                device_id="d0", action="collect_run_metadata", timeout_ms=500
            )
            self.assertEqual(got, {"sample_rate_hz": 1.0})

            hw._manager_rpc = (  # type: ignore[assignment]
                lambda *a, **k: {"ok": False, "error": {"code": "driver_not_running"}}
            )
            self.assertIsNone(
                writer._call_optional_device_action(  # noqa: SLF001
                    device_id="d0", action="collect_run_metadata", timeout_ms=500
                )
            )

            hw._manager_rpc = (  # type: ignore[assignment]
                lambda *a, **k: {"id": 1, "status": "ERROR", "error": "disconnected"}
            )
            self.assertIsNone(
                writer._call_optional_device_action(  # noqa: SLF001
                    device_id="d0", action="collect_run_metadata", timeout_ms=500
                )
            )
        finally:
            hw._manager_rpc = orig  # type: ignore[assignment]

    def test_start_writing_does_not_stall_on_slow_run_metadata(self) -> None:
        """Load-manager-state file-open must return promptly even when
        collect_run_metadata is slow — the capture is deferred to the bg
        thread. This is the direct regression for the telemetry-loss stall."""
        writer = _make_bg_test_writer()
        writer._fetch_schema_with_backoff = lambda **k: None  # type: ignore[assignment]  # noqa: SLF001
        writer._fetch_process_schema_best_effort = lambda: None  # type: ignore[assignment]  # noqa: SLF001
        writer._fetch_config_with_backoff = (  # type: ignore[assignment]  # noqa: SLF001
            lambda **k: [{"device_id": "d0"}, {"device_id": "d1"}]
        )

        def slow_collect(*, device_id: str, action: str, timeout_ms: int) -> dict:
            time.sleep(2.0)
            return {"device": device_id, "sample_rate_hz": 1.0}

        writer._call_optional_device_action = slow_collect  # type: ignore[assignment]  # noqa: SLF001
        thread = _spawn_bg_thread(writer)
        try:
            with _temp_dir() as td:
                with h5py.File(Path(td) / "stall.h5", "w") as h5:
                    meta = writer._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None, values=None, require_profile=False
                    )
                    t0 = time.monotonic()
                    with writer._h5_lock:  # noqa: SLF001
                        writer._configure_active_file(  # noqa: SLF001
                            h5,
                            write_every_s=1.0,
                            load_manager_state=True,
                            measurement_meta=meta,
                        )
                    elapsed = time.monotonic() - t0
                    # File-open did NOT wait on the 2s collect RPCs.
                    self.assertLess(elapsed, 1.0)

                    def captured() -> bool:
                        with writer._h5_lock:  # noqa: SLF001
                            grp = h5.get("run_metadata")
                            return bool(
                                grp is not None and "d0" in grp and "d1" in grp
                            )

                    # ...but the bg thread still captures it shortly after.
                    self.assertTrue(_wait_for(captured, timeout_s=6.0))
        finally:
            writer._stop_evt.set()  # noqa: SLF001
            writer._bg_queue.put(_BG_SENTINEL)  # noqa: SLF001
            thread.join(timeout=2.0)


class HdfWriterProcessDisableTests(unittest.TestCase):
    """Disabling process telemetry from the HDF file, a separate namespace
    from device disable (federated processes in particular)."""

    @staticmethod
    def _make_writer(out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65561",
            manager_pub="tcp://127.0.0.1:65562",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    def test_device_and_process_filters_are_separate_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            w = self._make_writer(td)
            w._disabled_devices = {"shared_id"}  # noqa: SLF001
            w._disabled_processes = {"proc1"}  # noqa: SLF001
            # Device filter ignores the process disable and vice versa.
            self.assertFalse(w._is_device_enabled("shared_id"))  # noqa: SLF001
            self.assertTrue(w._is_device_enabled("proc1"))  # noqa: SLF001
            self.assertFalse(w._is_process_enabled("proc1"))  # noqa: SLF001
            self.assertTrue(w._is_process_enabled("shared_id"))  # noqa: SLF001

    def test_ingest_process_schema_skips_disabled_and_reports_seen(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with h5py.File(Path(td) / "p.h5", "w") as h5:
                grp = h5.require_group("process_telemetry")
                datasets: dict = {}
                device_map: dict = {}
                schema = {
                    "processes": [
                        {"process_id": "p1", "signals": ["x"], "dtypes": ["float64"], "units": [""]},
                        {"process_id": "p2", "signals": ["y"], "dtypes": ["float64"], "units": [""]},
                    ]
                }
                seen = _ingest_process_schema(
                    schema, grp, datasets, device_map,
                    write_enabled=lambda pid: pid != "p2",
                )
                self.assertEqual(seen, {"p1", "p2"})
                self.assertIn("p1", datasets)  # enabled -> dataset created
                self.assertNotIn("p2", datasets)  # disabled -> no dataset
                self.assertEqual(datasets["p1"].attrs["source_kind"], "process")

    def test_process_telemetry_gated_by_process_filter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            w = self._make_writer(td)
            w._buf = deque(maxlen=1000)  # noqa: SLF001
            handlers = w._build_topic_handlers()  # noqa: SLF001
            handler = handlers["manager.process_telemetry_update"]
            msg = {
                "process_id": "spb_microwave",
                "version": 1,
                "signals": {"lock": {"value": 1.0}},
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
            }
            # Disabled: registered as known, but not buffered.
            w._disabled_processes = {"spb_microwave"}  # noqa: SLF001
            handler(msg)
            self.assertIn("spb_microwave", w._known_process_ids)  # noqa: SLF001
            self.assertEqual(len(w._buf), 0)  # noqa: SLF001
            # Enabled: buffered.
            w._disabled_processes = set()  # noqa: SLF001
            handler(msg)
            self.assertEqual(len(w._buf), 1)  # noqa: SLF001
            self.assertEqual(list(w._buf)[0]["device_id"], "spb_microwave")  # noqa: SLF001

    def test_processes_toggle_rpc(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            w = self._make_writer(td)
            w._known_process_ids = {"spb_microwave"}  # noqa: SLF001
            resp = w._rpc_hdf_processes_disable(  # noqa: SLF001
                {"params": {"process_ids": ["spb_microwave"]}}
            )
            self.assertTrue(resp["ok"])
            self.assertIn("spb_microwave", w._disabled_processes)  # noqa: SLF001
            self.assertEqual(resp["result"]["disabled_processes"], ["spb_microwave"])
            self.assertEqual(resp["result"]["changed"], ["spb_microwave"])
            # Filter state also surfaces known/enabled.
            self.assertIn("spb_microwave", resp["result"]["known_processes"])
            self.assertNotIn("spb_microwave", resp["result"]["enabled_known_processes"])

            resp2 = w._rpc_hdf_processes_enable(  # noqa: SLF001
                {"params": {"process_ids": ["spb_microwave"]}}
            )
            self.assertTrue(resp2["ok"])
            self.assertNotIn("spb_microwave", w._disabled_processes)  # noqa: SLF001
            self.assertIn(
                "spb_microwave", resp2["result"]["enabled_known_processes"]
            )

    def test_processes_toggle_rpc_requires_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            w = self._make_writer(td)
            resp = w._rpc_hdf_processes_disable({"params": {"process_ids": []}})  # noqa: SLF001
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error"]["code"], "invalid_params")


class HdfWriterStreamStartSkipsOldRingTests(unittest.TestCase):
    """On a fresh reader attach the writer must skip frames already buffered in
    the producer-owned, persistent SHM ring from before the writer (re)started,
    instead of replaying them into the new file."""

    @staticmethod
    def _make_writer(out_dir: str) -> HdfWriter:
        return HdfWriter(
            out_dir=out_dir,
            filename=None,
            manager_rpc="tcp://127.0.0.1:65571",
            manager_pub="tcp://127.0.0.1:65572",
            rpc_timeout_ms=2000,
            timezone="America/Chicago",
            rcvhwm=1000,
            write_every_s=1.0,
            buffer_max_messages=1000,
            flush_every_n=10,
            flush_every_s=1.0,
            disabled_devices=[],
            event_log_mode="all",
        )

    class _FakeReader:
        name = "cntx_fake"

        class layout:  # noqa: N801
            dtype = np.dtype("uint16")
            shape = (1,)

        def close(self) -> None:
            pass

    def test_fresh_attach_seeds_last_seq_to_initial_minus_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            w = self._make_writer(td)
            key = ("dev", "trace")
            with patch(
                "experiment_control.processes.hdf_writer.ShmRingReader.attach",
                return_value=self._FakeReader(),
            ):
                reader = w._ensure_chunk_ready_reader(  # noqa: SLF001
                    key=key,
                    device_id="dev",
                    stream="trace",
                    shm_name="cntx_fake",
                    initial_seq=1001,
                )
            self.assertIsNotNone(reader)
            # Seeded to just before the triggering chunk -> pre-existing frames
            # (seq <= 1000) are skipped, the triggering frame (1001) is kept.
            self.assertEqual(w._stream_last_seq[key], 1000)  # noqa: SLF001

    def test_fresh_attach_without_seq_falls_back_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            w = self._make_writer(td)
            key = ("dev", "trace")
            with patch(
                "experiment_control.processes.hdf_writer.ShmRingReader.attach",
                return_value=self._FakeReader(),
            ):
                w._ensure_chunk_ready_reader(  # noqa: SLF001
                    key=key,
                    device_id="dev",
                    stream="trace",
                    shm_name="cntx_fake",
                    initial_seq=None,
                )
            self.assertEqual(w._stream_last_seq[key], 0)  # noqa: SLF001

    def test_start_skips_frames_already_in_ring(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = f"ec_hdf_skip_{uuid.uuid4().hex}"
            w = self._make_writer(str(root))
            ring = ShmRingWriter.create(
                name=name,
                dtype=np.dtype("uint16"),
                shape=(1,),
                slot_count=16,
                layout_version=1,  # frame stream (no header last-seq hint)
            )
            try:
                # Frames buffered BEFORE the writer (re)started.
                for i in range(1, 6):
                    ring.write(
                        np.asarray([i], dtype=np.uint16), t0_mono_ns=i, t0_wall_ns=i
                    )
                with h5py.File(root / "s.h5", "w") as h5:
                    meta = w._build_measurement_metadata(  # noqa: SLF001
                        profile_id=None, values=None, require_profile=False
                    )
                    w._configure_active_file(  # noqa: SLF001
                        h5,
                        write_every_s=1.0,
                        load_manager_state=False,
                        measurement_meta=meta,
                    )
                    # New acquisition: one fresh frame + its chunk_ready.
                    new_seq = ring.write(
                        np.asarray([6], dtype=np.uint16), t0_mono_ns=6, t0_wall_ns=6
                    )
                    self.assertEqual(new_seq, 6)
                    w._handle_chunk_ready(  # noqa: SLF001
                        {
                            "device_id": "trace1",
                            "stream": "trace",
                            "shm_name": name,
                            "seq": new_seq,
                        }
                    )
                    key = ("trace1", "trace")
                    # Only the new frame was picked up; old 1..5 were skipped.
                    self.assertEqual(w._stream_last_seq[key], 6)  # noqa: SLF001
                    pending = set(
                        w._stream_pending_by_seq.get(key, {}).keys()  # noqa: SLF001
                    )
                    buf = w._stream_buffers.get(key, {})  # noqa: SLF001
                    buffered = set(
                        buf.get("seq", []) if isinstance(buf, dict) else []
                    )
                    self.assertEqual(pending | buffered, {6})
            finally:
                ring.close()
                ring.unlink()


if __name__ == "__main__":
    unittest.main()
