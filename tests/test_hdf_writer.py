import shutil
import sys
import tempfile
import uuid
import json
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

from experiment_control.processes.hdf_writer import HdfWriter, _create_device_dataset  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
