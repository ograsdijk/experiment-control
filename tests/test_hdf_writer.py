import shutil
import sys
import tempfile
import uuid
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


if __name__ == "__main__":
    unittest.main()
