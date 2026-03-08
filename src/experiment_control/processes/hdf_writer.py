from __future__ import annotations

import argparse
import copy
import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Literal, cast

import h5py
import numpy as np
import zmq

from ..capabilities import capabilities_payload, method, param
from ..schemas.measurement import (
    MeasurementSchema,
    measurement_schema_from_json,
    measurement_schema_to_json,
    normalize_measurement_note_values,
    normalize_measurement_values,
)
from ..shm.shm_ring import ShmRingReader
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.value_coercion import coerce_scalar
from ..utils.logging_levels import normalize_log_severity
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import json_dumps, json_loads, safe_json_loads
from .process_base import ManagedProcessBase

Json = dict[str, Any]
EventLogMode = Literal["all", "failures_only", "none"]
EVENT_LOG_MODES: tuple[EventLogMode, ...] = ("all", "failures_only", "none")


DTYPE_MAP: dict[str, np.dtype[Any]] = {
    "float64": np.dtype("float64"),
    "float32": np.dtype("float32"),
    "int64": np.dtype("int64"),
    "int32": np.dtype("int32"),
    "uint64": np.dtype("uint64"),
    "uint32": np.dtype("uint32"),
    "bool": np.dtype("bool"),
}
DEFAULT_NUMERIC_COMPRESSION = "lzf"
DEFAULT_NUMERIC_SHUFFLE = True


def _default_filename() -> str:
    return time.strftime("%Y_%m_%d-%H_%M_%S.h5", time.localtime())


def _event_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("kind", str_dt),
            ("severity", str_dt),
            ("device_id", str_dt),
            ("action", str_dt),
            ("params_json", str_dt),
            ("ok", np.bool_),
            ("error", str_dt),
            ("result_json", str_dt),
            ("topic", str_dt),
            ("message", str_dt),
            ("payload_json", str_dt),
        ]
    )


def _context_table_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("context_id", np.int64),
            ("ts_wall_ns", np.int64),
            ("ts_mono_ns", np.int64),
            ("fields_json", str_dt),
        ]
    )


def _sequencer_event_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("process_id", str_dt),
            ("event", str_dt),
            ("source", str_dt),
            ("ok", np.bool_),
            ("message", str_dt),
            ("payload_json", str_dt),
            ("yaml_snapshot_id", np.int64),
        ]
    )


def _sequencer_yaml_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("snapshot_id", np.int64),
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("process_id", str_dt),
            ("source", str_dt),
            ("text", str_dt),
        ]
    )


def _measurement_note_dtype() -> np.dtype[Any]:
    str_dt = h5py.string_dtype("utf-8")
    return np.dtype(
        [
            ("t_wall", np.float64),
            ("t_mono", np.float64),
            ("author", str_dt),
            ("kind", str_dt),
            ("message", str_dt),
            ("payload_json", str_dt),
        ]
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control hdf writer")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--filename", default=None)
    add_manager_args(p)
    p.add_argument("--timezone", default="America/Chicago")
    add_process_id_arg(p, default=None)
    add_rpc_timeout_arg(p, default_ms=2000)
    add_heartbeat_args(p, default_period_s=1.0)
    p.add_argument("--rcvhwm", type=int, default=10_000)
    p.add_argument("--write-every-s", type=float, default=1.0)
    p.add_argument("--buffer-max-messages", type=int, default=200_000)
    p.add_argument("--flush-every-n", type=int, default=200)
    p.add_argument("--flush-every-s", type=float, default=2.0)
    p.add_argument("--disabled-devices", nargs="*", default=None)
    p.add_argument("--measurement-schema-path", default=None)
    p.add_argument("--autostart-writing", default=None)
    p.add_argument(
        "--event-log-mode",
        choices=list(EVENT_LOG_MODES),
        default="all",
    )
    return p.parse_args(argv)


def _schema_rpc(ctx: zmq.Context, endpoint: str, timeout_ms: int = 2000) -> Json:
    sock = ctx.socket(zmq.DEALER)
    sock.connect(endpoint)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        msg = {"action": "telemetry.schema.list"}
        sock.send(json_dumps(msg))
        raw = sock.recv()
        resp = json_loads(raw)
        if not isinstance(resp, dict):
            raise TypeError("Invalid schema response")
        if not resp.get("ok", False):
            raise RuntimeError(f"Schema request failed: {resp.get('error')}")
        result = resp.get("result")
        if not isinstance(result, dict):
            raise TypeError("Schema result missing")
        return result
    finally:
        sock.close()


def _manager_rpc(
    ctx: zmq.Context, endpoint: str, payload: Json, timeout_ms: int = 2000
) -> Json:
    sock = ctx.socket(zmq.DEALER)
    sock.connect(endpoint)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        sock.send(json_dumps(payload))
        raw = sock.recv()
        resp = json_loads(raw)
        if not isinstance(resp, dict):
            raise TypeError("Invalid manager response")
        return resp
    finally:
        sock.close()


def _config_rpc(ctx: zmq.Context, endpoint: str, timeout_ms: int = 2000) -> list[Json]:
    sock = ctx.socket(zmq.DEALER)
    sock.connect(endpoint)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        msg = {"type": "device.config.list"}
        sock.send(json_dumps(msg))
        raw = sock.recv()
        resp = json_loads(raw)
        if not isinstance(resp, dict):
            raise TypeError("Invalid config response")
        if not resp.get("ok", False):
            raise RuntimeError(f"Config request failed: {resp.get('error')}")
        result = resp.get("result")
        if result is None:
            return []
        if not isinstance(result, list):
            raise TypeError("Config result missing")
        return [r for r in result if isinstance(r, dict)]
    finally:
        sock.close()


def _dtype_for(dtype_str: str) -> np.dtype[Any]:
    if dtype_str not in DTYPE_MAP:
        raise ValueError(f"Unsupported dtype {dtype_str!r}")
    return DTYPE_MAP[dtype_str]


def _convert_value(value: Any, dtype_str: str) -> Any:
    return coerce_scalar(value, dtype_str)


def _create_device_dataset(
    telemetry_group: h5py.Group,
    device_id: str,
    signals: list[str],
    dtypes: list[str],
    units: list[str],
    *,
    chunk_size: int = 1024,
) -> h5py.Dataset:
    device_group = telemetry_group.require_group(device_id)

    fields: list[tuple[str, Any]] = [("t_wall", np.float64), ("t_mono", np.float64)]
    fields.append(("seq", np.int64))
    for name, dtype_str in zip(signals, dtypes, strict=True):
        fields.append((name, _dtype_for(dtype_str)))

    ds = device_group.create_dataset(
        "data",
        shape=(0,),
        maxshape=(None,),
        dtype=np.dtype(fields),
        chunks=(chunk_size,),
        compression=DEFAULT_NUMERIC_COMPRESSION,
        shuffle=DEFAULT_NUMERIC_SHUFFLE,
    )

    str_dt = h5py.string_dtype("utf-8")
    ds.attrs["device_id"] = device_id
    ds.attrs["signals"] = np.array(signals, dtype=str_dt)
    ds.attrs["dtypes"] = np.array(dtypes, dtype=str_dt)
    ds.attrs["units"] = np.array(units, dtype=str_dt)
    return ds


def _ingest_schema(
    schema: Json,
    telemetry_group: h5py.Group,
    datasets: dict[str, h5py.Dataset],
    *,
    write_enabled: Callable[[str], bool] | None = None,
    chunk_size: int = 1024,
) -> dict[str, Json]:
    devices_raw = schema.get("devices", [])
    if not isinstance(devices_raw, list):
        raise TypeError("Schema devices must be a list")

    device_map: dict[str, Json] = {}
    for device in devices_raw:
        if not isinstance(device, dict):
            continue
        device_id = str(device["device_id"])
        signals = list(device["signals"])
        dtypes = list(device["dtypes"])
        units = list(device["units"])

        device_map[device_id] = {
            "signals": signals,
            "dtypes": dtypes,
            "units": units,
        }

        can_write = True if write_enabled is None else bool(write_enabled(device_id))
        if can_write and device_id not in datasets and signals:
            datasets[device_id] = _create_device_dataset(
                telemetry_group,
                device_id,
                signals,
                dtypes,
                units,
                chunk_size=chunk_size,
            )

    return device_map


class HdfWriter(ManagedProcessBase):
    def __init__(
        self,
        *,
        out_dir: str,
        filename: str | None,
        manager_rpc: str,
        manager_pub: str,
        rpc_timeout_ms: int = 2000,
        timezone: str,
        rcvhwm: int,
        write_every_s: float,
        buffer_max_messages: int,
        flush_every_n: int,
        flush_every_s: float,
        disabled_devices: list[str] | None = None,
        measurement_schema_path: str | None = None,
        autostart_writing: bool | str | None = None,
        event_log_mode: EventLogMode = "all",
    ) -> None:
        super().__init__(
            process_id=None,
            heartbeat_endpoint=None,
            heartbeat_period_s=1.0,
        )
        self._out_dir = Path(out_dir)
        self._filename = filename
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._rpc_timeout_ms = int(rpc_timeout_ms)
        self._timezone = timezone
        self._rcvhwm = int(rcvhwm)
        self._write_every_s = float(write_every_s)
        self._buffer_max_messages = int(buffer_max_messages)
        self._flush_every_n = int(flush_every_n)
        self._flush_every_s = float(flush_every_s)
        self._disabled_devices = self._normalize_device_ids(disabled_devices or [])
        self._measurement_schema_path = self._normalize_schema_path(measurement_schema_path)
        self._autostart_writing = self._normalize_autostart_writing(
            autostart_writing,
            schema_configured=self._measurement_schema_path is not None,
        )
        self._measurement_schema: MeasurementSchema | None = None
        self._measurement_schema_source: str | None = None
        self._measurement_schema_error: str | None = None
        self._event_log_mode: EventLogMode = self._normalize_event_log_mode(
            event_log_mode
        )
        self._load_measurement_schema()
        self._latest_device_config: dict[str, Json] = {}

        self._h5: h5py.File | None = None
        self._telemetry_group: h5py.Group | None = None
        self._streams_group: h5py.Group | None = None
        self._config_group: h5py.Group | None = None
        self._run_meta_group: h5py.Group | None = None
        self._events_group: h5py.Group | None = None
        self._events_ds: h5py.Dataset | None = None
        self._sequencer_group: h5py.Group | None = None
        self._sequencer_events_ds: h5py.Dataset | None = None
        self._sequencer_yaml_ds: h5py.Dataset | None = None
        self._sequencer_yaml_next_id: int = 0
        self._measurement_group: h5py.Group | None = None
        self._measurement_header_ds: h5py.Dataset | None = None
        self._measurement_notes_ds: h5py.Dataset | None = None
        self._measurement_id: str | None = None
        self._measurement_type: str | None = None
        self._measurement_schema_version: int | None = None
        self._measurement_started_wall_ns: int | None = None
        self._measurement_ended_wall_ns: int | None = None
        self._context_table_group: h5py.Group | None = None
        self._context_table_ds: h5py.Dataset | None = None
        self._context_columns_group: h5py.Group | None = None
        self._context_columns_datasets: dict[str, h5py.Dataset] = {}
        self._context_columns_types: dict[str, str] = {}
        self._context_columns_missing: dict[str, Any] = {}
        self._context_columns_source: str | None = None
        self._context_columns_ready = False
        self._context_columns_fetch_attempted = False
        self._sequencer_process_id = "sequencer"
        self._seen_context_ids: set[int] = set()
        self._stream_context_id: dict[tuple[str, str], int] = {}
        self._datasets: dict[str, h5py.Dataset] = {}
        self._device_map: dict[str, Json] = {}
        self._sub: zmq.Socket | None = None
        self._poller: zmq.Poller | None = None
        self._buf: deque[Json] | None = None
        self._event_buf: deque[tuple[str, Json]] | None = None
        self._rpc_router: zmq.Socket | None = None
        self._rpc_endpoint: str | None = None
        self._dropped_local = 0
        self._dropped_local_by_topic: dict[str, int] = {}
        self._dropped_events = 0
        self._drop_policy: Literal["drop_newest", "drop_oldest"] = "drop_newest"

        self._stream_readers: dict[tuple[str, str], ShmRingReader] = {}
        self._stream_last_seq: dict[tuple[str, str], int] = {}
        self._stream_buffers: dict[
            tuple[str, str],
            dict[str, list[Any]],
        ] = {}
        self._stream_datasets: dict[
            tuple[str, str, int],
            dict[str, h5py.Dataset],
        ] = {}
        self._stream_schema: dict[tuple[str, str], dict[str, Any]] = {}
        self._stream_dropped_total: dict[tuple[str, str], int] = {}
        self._stream_expected_nbytes: dict[tuple[str, str], int] = {}
        self._pending_stream_metadata: dict[tuple[str, str], dict[str, Any]] = {}
        self._stream_sessions: dict[tuple[str, str], int] = {}
        self._stream_active_session: dict[tuple[str, str], int] = {}

        self._process_id: str | None = None
        self._heartbeat_endpoint: str | None = None
        self._heartbeat_period_s: float = 1.0

        self._pending = 0
        self._last_flush = 0.0
        self._next_write = 0.0

        self._error_counts: dict[str, int] = {}

    @staticmethod
    def _normalize_device_id(raw: Any) -> str | None:
        text = str(raw).strip()
        return text if text else None

    @classmethod
    def _normalize_device_id_list(cls, raw: Any) -> list[str]:
        if isinstance(raw, str):
            items: list[Any] = [raw]
        elif isinstance(raw, list):
            items = raw
        else:
            raise TypeError("device_ids must be a string or list[str]")
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            did = cls._normalize_device_id(item)
            if did is None or did in seen:
                continue
            seen.add(did)
            out.append(did)
        return out

    @classmethod
    def _normalize_device_ids(cls, raw: Any) -> set[str]:
        return set(cls._normalize_device_id_list(raw))

    @staticmethod
    def _normalize_event_log_mode(raw: Any) -> EventLogMode:
        mode = str(raw or "all").strip().lower()
        if mode not in EVENT_LOG_MODES:
            raise ValueError(
                f"event_log_mode must be one of {', '.join(EVENT_LOG_MODES)}"
            )
        return cast(EventLogMode, mode)

    @staticmethod
    def _normalize_schema_path(raw: Any) -> str | None:
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None

    @staticmethod
    def _normalize_autostart_writing(raw: Any, *, schema_configured: bool) -> bool:
        # Default behavior: if a measurement schema is configured, start in idle mode
        # and require explicit rotate to open the first file.
        if raw is None:
            return not schema_configured
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError("autostart_writing must be a bool-like value")

    def _load_measurement_schema(self) -> None:
        self._measurement_schema = None
        self._measurement_schema_source = None
        self._measurement_schema_error = None
        if self._measurement_schema_path is None:
            return
        try:
            raw = load_yaml_file(self._measurement_schema_path)
            self._measurement_schema = measurement_schema_from_json(raw)
            self._measurement_schema_source = str(
                Path(self._measurement_schema_path).expanduser().resolve()
            )
        except Exception as e:
            self._measurement_schema_error = str(e)

    def _measurement_schema_state(self) -> tuple[bool, bool, str | None]:
        configured = self._measurement_schema_path is not None
        available = self._measurement_schema is not None
        if self._measurement_schema_error:
            return configured, available, self._measurement_schema_error
        if configured and not available:
            return configured, available, "measurement schema unavailable"
        return configured, available, None

    @staticmethod
    def _normalize_log_severity(raw: Any) -> str:
        return normalize_log_severity(raw, default="info")

    def _known_devices(self) -> list[str]:
        known = set(self._device_map)
        known.update(self._datasets)
        known.update(self._latest_device_config)
        return sorted(known)

    def _is_device_enabled(self, device_id: str) -> bool:
        did = self._normalize_device_id(device_id)
        return bool(did) and did not in self._disabled_devices

    def _bump_error(self, key: str) -> None:
        self._error_counts[key] = self._error_counts.get(key, 0) + 1

    def _resolve_output_path(
        self, filename: str | None, *, use_default_filename: bool
    ) -> Path:
        name: str | None = None
        if filename is not None:
            raw = str(filename).strip()
            if not raw:
                raise ValueError("filename must be a non-empty string")
            name = raw
        elif not use_default_filename and self._filename is not None:
            raw = str(self._filename).strip()
            if raw:
                name = raw
        if name is None:
            name = _default_filename()
        return self._out_dir / name

    def _ensure_output_path_unused(self, path: Path) -> None:
        if path.exists():
            raise FileExistsError(f"target file already exists: {path}")

    def _fetch_schema_with_backoff(
        self,
        *,
        timeout_s: float = 15.0,
        initial_delay_s: float = 0.2,
        max_delay_s: float = 2.0,
    ) -> Json | None:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        delay = max(0.01, float(initial_delay_s))
        max_delay = max(delay, float(max_delay_s))
        while True:
            try:
                return _schema_rpc(
                    self._ctx, self._manager_rpc, timeout_ms=self._rpc_timeout_ms
                )
            except Exception:
                self._bump_error("schema.rpc")
                if time.monotonic() >= deadline:
                    return None
                time.sleep(delay)
                delay = min(delay * 2.0, max_delay)

    def _fetch_config_with_backoff(
        self,
        *,
        timeout_s: float = 15.0,
        initial_delay_s: float = 0.2,
        max_delay_s: float = 2.0,
    ) -> list[Json] | None:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        delay = max(0.01, float(initial_delay_s))
        max_delay = max(delay, float(max_delay_s))
        while True:
            try:
                return _config_rpc(
                    self._ctx, self._manager_rpc, timeout_ms=self._rpc_timeout_ms
                )
            except Exception:
                self._bump_error("config.rpc")
                if time.monotonic() >= deadline:
                    return None
                time.sleep(delay)
                delay = min(delay * 2.0, max_delay)

    @staticmethod
    def _normalize_metadata_dict(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, Any] = {}
        for key, value in raw.items():
            name = str(key).strip()
            if not name:
                continue
            out[name] = value
        return out

    @classmethod
    def _normalize_stream_metadata_dict(
        cls, raw: Any
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for stream_raw, attrs_raw in raw.items():
            stream = str(stream_raw).strip()
            if not stream or not isinstance(attrs_raw, dict):
                continue
            out[stream] = cls._normalize_metadata_dict(attrs_raw)
        return out

    def _call_optional_device_action(
        self,
        *,
        device_id: str,
        action: str,
        timeout_ms: int = 1200,
    ) -> Any | None:
        try:
            resp = _manager_rpc(
                self._ctx,
                self._manager_rpc,
                {
                    "type": "command",
                    "device_id": str(device_id),
                    "action": str(action),
                    "params": {},
                },
                timeout_ms=max(200, int(timeout_ms)),
            )
        except Exception:
            self._bump_error(f"metadata.rpc.{action}")
            return None
        if not isinstance(resp, dict):
            return None
        if not bool(resp.get("ok")):
            return None
        return resp.get("result")

    @staticmethod
    def _is_remote_config(config: Json) -> bool:
        source_kind = str(config.get("source_kind", "")).strip().lower()
        return bool(config.get("is_remote")) or source_kind == "federated"

    def _capture_run_metadata_for_configs(self, configs: list[Json]) -> None:
        seen: set[str] = set()
        timeout_ms = min(max(200, int(self._rpc_timeout_ms)), 1500)
        for config in configs:
            device_id = self._normalize_device_id(config.get("device_id"))
            if device_id is None or device_id in seen:
                continue
            seen.add(device_id)
            if not self._is_device_enabled(device_id):
                continue
            if self._is_remote_config(config):
                continue
            run_metadata = self._call_optional_device_action(
                device_id=device_id,
                action="collect_run_metadata",
                timeout_ms=timeout_ms,
            )
            if run_metadata is None:
                continue
            if not isinstance(run_metadata, dict):
                self._bump_error("run_metadata.invalid")
                continue
            self._handle_run_metadata(
                {
                    "device_id": device_id,
                    "run_metadata": run_metadata,
                }
            )

    def _build_measurement_metadata(
        self,
        *,
        profile_id: str | None,
        values: object,
        require_profile: bool,
    ) -> Json:
        measurement_type = "unspecified"
        header_values: Json = {}
        schema_version = 1

        configured, available, error = self._measurement_schema_state()
        if configured and not available and require_profile:
            message = error or "measurement schema unavailable"
            raise ValueError(message)

        if self._measurement_schema is not None:
            schema_version = int(self._measurement_schema.version)
            if profile_id is None or not str(profile_id).strip():
                if require_profile:
                    raise ValueError(
                        "measurement_profile is required when measurement schema is configured"
                    )
            else:
                normalized_profile = str(profile_id).strip()
                _profile, _flat, nested = normalize_measurement_values(
                    self._measurement_schema,
                    profile_id=normalized_profile,
                    values=values,
                )
                measurement_type = normalized_profile
                header_values = nested
        else:
            if profile_id is not None and str(profile_id).strip():
                measurement_type = str(profile_id).strip()
            if values is None:
                header_values = {}
            elif isinstance(values, dict):
                header_values = values
            else:
                raise ValueError("measurement_values must be a dict")

        started_wall_ns = int(time.time_ns())
        header_payload: Json = {"values": header_values}
        return {
            "measurement_id": str(uuid.uuid4()),
            "measurement_type": measurement_type,
            "schema_version": int(schema_version),
            "started_wall_ns": started_wall_ns,
            "header_json": json.dumps(header_payload),
        }

    @staticmethod
    def _mark_measurement_group_ended(group: h5py.Group | None) -> int | None:
        if group is None:
            return None
        ended_wall_ns = int(time.time_ns())
        try:
            group.attrs["ended_wall_ns"] = ended_wall_ns
        except Exception:
            return None
        return ended_wall_ns

    def _mark_active_measurement_ended(self) -> None:
        if self._measurement_ended_wall_ns is not None:
            return
        ended_wall_ns = self._mark_measurement_group_ended(self._measurement_group)
        if ended_wall_ns is not None:
            self._measurement_ended_wall_ns = int(ended_wall_ns)

    def _init_measurement_group(self, h5: h5py.File, *, measurement_meta: Json) -> None:
        group = h5.require_group("measurement")
        group.attrs["measurement_id"] = str(measurement_meta.get("measurement_id", ""))
        group.attrs["measurement_type"] = str(
            measurement_meta.get("measurement_type", "unspecified")
        )
        group.attrs["schema_version"] = int(measurement_meta.get("schema_version", 1))
        group.attrs["started_wall_ns"] = int(measurement_meta.get("started_wall_ns", 0))
        source = self._measurement_schema_source or self._measurement_schema_path
        if source:
            group.attrs["schema_source"] = str(source)
        header_ds = group.require_dataset(
            "header_json",
            shape=(),
            dtype=h5py.string_dtype("utf-8"),
        )
        header_ds[()] = str(measurement_meta.get("header_json", "{}"))
        notes_ds = group.require_dataset(
            "notes",
            shape=(0,),
            maxshape=(None,),
            dtype=_measurement_note_dtype(),
            chunks=(256,),
        )

        self._measurement_group = group
        self._measurement_header_ds = header_ds
        self._measurement_notes_ds = notes_ds
        self._measurement_id = str(group.attrs.get("measurement_id", "") or "")
        self._measurement_type = str(group.attrs.get("measurement_type", "") or "")
        try:
            self._measurement_schema_version = int(group.attrs.get("schema_version", 1))
        except Exception:
            self._measurement_schema_version = 1
        try:
            self._measurement_started_wall_ns = int(group.attrs.get("started_wall_ns", 0))
        except Exception:
            self._measurement_started_wall_ns = None
        ended_raw = group.attrs.get("ended_wall_ns", None)
        if ended_raw is None:
            self._measurement_ended_wall_ns = None
        else:
            try:
                self._measurement_ended_wall_ns = int(ended_raw)
            except Exception:
                self._measurement_ended_wall_ns = None

    def _append_measurement_note_row(
        self,
        *,
        author: str,
        kind: str,
        message: str,
        payload_json: str,
    ) -> tuple[int, float, float]:
        if self._measurement_notes_ds is None:
            raise RuntimeError("measurement notes dataset unavailable")
        t_wall = float(time.time())
        t_mono = float(time.monotonic())
        row = np.zeros(1, dtype=self._measurement_notes_ds.dtype)
        row[0]["t_wall"] = t_wall
        row[0]["t_mono"] = t_mono
        row[0]["author"] = str(author)
        row[0]["kind"] = str(kind)
        row[0]["message"] = str(message)
        row[0]["payload_json"] = str(payload_json)
        old = int(self._measurement_notes_ds.shape[0])
        self._measurement_notes_ds.resize((old + 1,))
        self._measurement_notes_ds[old] = row[0]
        self._pending += 1
        return old, t_wall, t_mono

    def _flush_active_file(self) -> None:
        if self._h5 is None:
            return
        self._h5.attrs["dropped_local_messages_total"] = int(self._dropped_local)
        self._h5.attrs["dropped_event_messages_total"] = int(self._dropped_events)
        self._h5.flush()
        self._pending = 0
        self._last_flush = time.monotonic()

    def _drain_pending_to_file(self) -> None:
        self._write_buffered_rows()
        self._write_event_rows()
        self._write_stream_buffers()
        self._flush_active_file()

    def _clear_buffered_for_disabled(self, disabled: set[str]) -> None:
        if not disabled:
            return
        if self._buf is not None and len(self._buf) > 0:
            kept = [
                msg
                for msg in self._buf
                if self._normalize_device_id(msg.get("device_id")) not in disabled
            ]
            self._buf.clear()
            self._buf.extend(kept)
        if self._event_buf is not None and len(self._event_buf) > 0:
            kept_events: list[tuple[str, Json]] = []
            for topic, msg in self._event_buf:
                if topic != "manager.command":
                    kept_events.append((topic, msg))
                    continue
                did = self._normalize_device_id(msg.get("device_id"))
                if did in disabled:
                    continue
                kept_events.append((topic, msg))
            self._event_buf.clear()
            self._event_buf.extend(kept_events)
        for key in list(self._stream_buffers.keys()):
            if key[0] in disabled:
                self._stream_buffers.pop(key, None)

    def _snapshot_file_state(self) -> dict[str, Any]:
        return {
            "h5": self._h5,
            "telemetry_group": self._telemetry_group,
            "streams_group": self._streams_group,
            "config_group": self._config_group,
            "run_meta_group": self._run_meta_group,
            "events_group": self._events_group,
            "events_ds": self._events_ds,
            "sequencer_group": self._sequencer_group,
            "sequencer_events_ds": self._sequencer_events_ds,
            "sequencer_yaml_ds": self._sequencer_yaml_ds,
            "sequencer_yaml_next_id": self._sequencer_yaml_next_id,
            "measurement_group": self._measurement_group,
            "measurement_header_ds": self._measurement_header_ds,
            "measurement_notes_ds": self._measurement_notes_ds,
            "measurement_id": self._measurement_id,
            "measurement_type": self._measurement_type,
            "measurement_schema_version": self._measurement_schema_version,
            "measurement_started_wall_ns": self._measurement_started_wall_ns,
            "measurement_ended_wall_ns": self._measurement_ended_wall_ns,
            "context_table_group": self._context_table_group,
            "context_table_ds": self._context_table_ds,
            "context_columns_group": self._context_columns_group,
            "context_columns_datasets": self._context_columns_datasets,
            "context_columns_types": self._context_columns_types,
            "context_columns_missing": self._context_columns_missing,
            "context_columns_source": self._context_columns_source,
            "context_columns_ready": self._context_columns_ready,
            "context_columns_fetch_attempted": self._context_columns_fetch_attempted,
            "seen_context_ids": self._seen_context_ids,
            "stream_context_id": self._stream_context_id,
            "datasets": self._datasets,
            "device_map": self._device_map,
            "stream_datasets": self._stream_datasets,
            "stream_schema": self._stream_schema,
            "stream_dropped_total": self._stream_dropped_total,
            "stream_expected_nbytes": self._stream_expected_nbytes,
            "pending_stream_metadata": self._pending_stream_metadata,
            "stream_sessions": self._stream_sessions,
            "stream_active_session": self._stream_active_session,
            "pending": self._pending,
            "last_flush": self._last_flush,
            "next_write": self._next_write,
        }

    def _restore_file_state(self, state: dict[str, Any]) -> None:
        self._h5 = state["h5"]
        self._telemetry_group = state["telemetry_group"]
        self._streams_group = state["streams_group"]
        self._config_group = state["config_group"]
        self._run_meta_group = state["run_meta_group"]
        self._events_group = state["events_group"]
        self._events_ds = state["events_ds"]
        self._sequencer_group = state["sequencer_group"]
        self._sequencer_events_ds = state["sequencer_events_ds"]
        self._sequencer_yaml_ds = state["sequencer_yaml_ds"]
        self._sequencer_yaml_next_id = int(state["sequencer_yaml_next_id"])
        self._measurement_group = state["measurement_group"]
        self._measurement_header_ds = state["measurement_header_ds"]
        self._measurement_notes_ds = state["measurement_notes_ds"]
        self._measurement_id = state["measurement_id"]
        self._measurement_type = state["measurement_type"]
        self._measurement_schema_version = state["measurement_schema_version"]
        self._measurement_started_wall_ns = state["measurement_started_wall_ns"]
        self._measurement_ended_wall_ns = state["measurement_ended_wall_ns"]
        self._context_table_group = state["context_table_group"]
        self._context_table_ds = state["context_table_ds"]
        self._context_columns_group = state["context_columns_group"]
        self._context_columns_datasets = state["context_columns_datasets"]
        self._context_columns_types = state["context_columns_types"]
        self._context_columns_missing = state["context_columns_missing"]
        self._context_columns_source = state["context_columns_source"]
        self._context_columns_ready = state["context_columns_ready"]
        self._context_columns_fetch_attempted = state["context_columns_fetch_attempted"]
        self._seen_context_ids = state["seen_context_ids"]
        self._stream_context_id = state["stream_context_id"]
        self._datasets = state["datasets"]
        self._device_map = state["device_map"]
        self._stream_datasets = state["stream_datasets"]
        self._stream_schema = state["stream_schema"]
        self._stream_dropped_total = state["stream_dropped_total"]
        self._stream_expected_nbytes = state["stream_expected_nbytes"]
        self._pending_stream_metadata = state["pending_stream_metadata"]
        self._stream_sessions = state["stream_sessions"]
        self._stream_active_session = state["stream_active_session"]
        self._pending = int(state["pending"])
        self._last_flush = float(state["last_flush"])
        self._next_write = float(state["next_write"])

    def _reset_per_file_state(self) -> None:
        self._telemetry_group = None
        self._streams_group = None
        self._config_group = None
        self._run_meta_group = None
        self._events_group = None
        self._events_ds = None
        self._sequencer_group = None
        self._sequencer_events_ds = None
        self._sequencer_yaml_ds = None
        self._sequencer_yaml_next_id = 0
        self._measurement_group = None
        self._measurement_header_ds = None
        self._measurement_notes_ds = None
        self._measurement_id = None
        self._measurement_type = None
        self._measurement_schema_version = None
        self._measurement_started_wall_ns = None
        self._measurement_ended_wall_ns = None
        self._context_table_group = None
        self._context_table_ds = None
        self._context_columns_group = None
        self._context_columns_datasets = {}
        self._context_columns_types = {}
        self._context_columns_missing = {}
        self._context_columns_source = None
        self._context_columns_ready = False
        self._context_columns_fetch_attempted = False
        self._seen_context_ids = set()
        self._stream_context_id = {}
        self._datasets = {}
        self._device_map = {}
        self._stream_datasets = {}
        self._stream_schema = {}
        self._stream_dropped_total = {}
        self._stream_expected_nbytes = {}
        self._pending_stream_metadata = {}
        self._stream_sessions = {}
        self._stream_active_session = {}

    def _configure_active_file(
        self,
        h5: h5py.File,
        *,
        write_every_s: float,
        load_manager_state: bool,
        measurement_meta: Json,
    ) -> None:
        self._h5 = h5
        self._reset_per_file_state()

        h5.attrs["timezone"] = self._timezone
        h5.attrs["schema_version"] = 4
        h5.attrs["created_at_wall"] = time.time()
        h5.attrs["manager_rpc_endpoint"] = self._manager_rpc
        h5.attrs["manager_pub_endpoint"] = self._manager_pub
        h5.attrs["zmq_rcvhwm"] = int(self._rcvhwm)
        h5.attrs["buffer_max_messages"] = int(max(1, self._buffer_max_messages))
        h5.attrs["write_every_s"] = float(write_every_s)
        h5.attrs["drop_policy"] = str(self._drop_policy)
        h5.attrs["event_log_mode"] = str(self._event_log_mode)
        h5.attrs["dropped_local_messages_total"] = 0
        h5.attrs["dropped_event_messages_total"] = 0
        h5.attrs["disabled_devices_json"] = json.dumps(sorted(self._disabled_devices))

        self._telemetry_group = h5.require_group("telemetry")
        self._streams_group = h5.require_group("streams")
        self._config_group = h5.require_group("config")
        self._run_meta_group = h5.require_group("run_metadata")
        self._events_group = h5.require_group("events")
        self._events_ds = self._events_group.require_dataset(
            "data",
            shape=(0,),
            maxshape=(None,),
            dtype=_event_dtype(),
            chunks=(1024,),
        )
        self._sequencer_group = h5.require_group("sequencer")
        self._sequencer_events_ds = self._sequencer_group.require_dataset(
            "events",
            shape=(0,),
            maxshape=(None,),
            dtype=_sequencer_event_dtype(),
            chunks=(256,),
        )
        self._sequencer_yaml_ds = self._sequencer_group.require_dataset(
            "yaml_snapshots",
            shape=(0,),
            maxshape=(None,),
            dtype=_sequencer_yaml_dtype(),
            chunks=(32,),
        )
        self._sequencer_yaml_next_id = int(self._sequencer_yaml_ds.shape[0])
        self._init_measurement_group(h5, measurement_meta=measurement_meta)
        self._context_table_group = h5.require_group("context_table")
        self._context_table_ds = self._context_table_group.require_dataset(
            "data",
            shape=(0,),
            maxshape=(None,),
            dtype=_context_table_dtype(),
            chunks=(1024,),
        )

        if load_manager_state:
            schema = self._fetch_schema_with_backoff(timeout_s=5.0)
            if schema is not None and self._telemetry_group is not None:
                self._device_map = _ingest_schema(
                    schema,
                    self._telemetry_group,
                    self._datasets,
                    write_enabled=self._is_device_enabled,
                )

            configs: list[Json] = []
            if self._latest_device_config:
                configs = [copy.deepcopy(v) for v in self._latest_device_config.values()]
            else:
                fetched = self._fetch_config_with_backoff(timeout_s=5.0)
                if fetched is not None:
                    configs = fetched
            for config in configs:
                device_id = self._normalize_device_id(config.get("device_id"))
                if device_id is not None:
                    self._latest_device_config[device_id] = copy.deepcopy(config)
                self._handle_device_config(config, cache=False)
            self._capture_run_metadata_for_configs(configs)

        self._pending = 0
        now = time.monotonic()
        self._last_flush = now
        self._next_write = now + write_every_s

    def _clear_stream_buffer(self, buf: dict[str, list[Any]]) -> None:
        for key in ("data", "seq", "t0_mono_ns", "t0_wall_ns", "context_id"):
            values = buf.get(key)
            if isinstance(values, list):
                values.clear()

    def _rotate_file(
        self,
        *,
        filename: str | None,
        disabled_devices: set[str] | None = None,
        measurement_profile: str | None = None,
        measurement_values: object = None,
    ) -> tuple[str | None, str]:
        if self._h5 is None:
            new_file = self._start_writing_file(
                filename=filename,
                disabled_devices=disabled_devices,
                measurement_profile=measurement_profile,
                measurement_values=measurement_values,
            )
            return None, new_file

        old_file = str(self._h5.filename)
        old_disabled = set(self._disabled_devices)

        new_disabled = (
            set(disabled_devices)
            if disabled_devices is not None
            else set(self._disabled_devices)
        )
        self._out_dir.mkdir(parents=True, exist_ok=True)
        path = self._resolve_output_path(filename, use_default_filename=True)
        self._ensure_output_path_unused(path)
        measurement_meta = self._build_measurement_metadata(
            profile_id=measurement_profile,
            values=measurement_values,
            require_profile=self._measurement_schema is not None,
        )
        self._drain_pending_to_file()
        old_state = self._snapshot_file_state()
        self._disabled_devices = new_disabled
        self._clear_buffered_for_disabled(new_disabled)
        new_h5 = h5py.File(path, "w")
        try:
            self._configure_active_file(
                new_h5,
                write_every_s=max(0.1, float(self._write_every_s)),
                load_manager_state=bool(self._process_id),
                measurement_meta=measurement_meta,
            )
            self._clear_buffered_for_disabled(self._disabled_devices)
        except Exception:
            try:
                new_h5.close()
            except Exception:
                self._bump_error("rotate.close_new")
            self._restore_file_state(old_state)
            self._disabled_devices = old_disabled
            raise

        old_h5 = old_state.get("h5")
        if old_h5 is not None and old_h5 is not new_h5:
            old_measurement_group = old_state.get("measurement_group")
            if isinstance(old_measurement_group, h5py.Group):
                _ = self._mark_measurement_group_ended(old_measurement_group)
            try:
                old_h5.flush()
            except Exception:
                self._bump_error("rotate.old_flush")
            try:
                old_h5.close()
            except Exception:
                self._bump_error("rotate.old_close")

        return old_file, str(new_h5.filename)

    def _start_writing_file(
        self,
        *,
        filename: str | None,
        disabled_devices: set[str] | None = None,
        measurement_profile: str | None = None,
        measurement_values: object = None,
    ) -> str:
        if self._h5 is not None:
            raise RuntimeError("HDF writer is already writing")

        self._out_dir.mkdir(parents=True, exist_ok=True)
        path = self._resolve_output_path(filename, use_default_filename=False)
        self._ensure_output_path_unused(path)

        new_disabled = (
            set(disabled_devices)
            if disabled_devices is not None
            else set(self._disabled_devices)
        )
        self._disabled_devices = new_disabled
        self._clear_buffered_for_disabled(new_disabled)

        measurement_meta = self._build_measurement_metadata(
            profile_id=measurement_profile,
            values=measurement_values,
            require_profile=self._measurement_schema is not None,
        )
        h5 = h5py.File(path, "w")
        try:
            self._configure_active_file(
                h5,
                write_every_s=max(0.1, float(self._write_every_s)),
                load_manager_state=bool(self._process_id),
                measurement_meta=measurement_meta,
            )
            self._clear_buffered_for_disabled(self._disabled_devices)
        except Exception:
            try:
                h5.close()
            except Exception:
                self._bump_error("start_writing.close_new")
            raise

        return str(h5.filename)

    def close(self) -> None:
        self._stop_evt.set()

        t = self._heartbeat_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)

        for reader in list(self._stream_readers.values()):
            try:
                reader.close()
            except Exception:
                self._bump_error("close.reader")
        self._stream_readers.clear()

        for sock in (self._sub, self._heartbeat_pub):
            if sock is None:
                continue
            try:
                sock.setsockopt(zmq.LINGER, 0)
            except Exception:
                self._bump_error("close.socket_setopt")
            try:
                sock.close(0)
            except Exception:
                self._bump_error("close.socket")
        if self._rpc_router is not None:
            try:
                self._rpc_router.setsockopt(zmq.LINGER, 0)
            except Exception:
                self._bump_error("close.rpc_setopt")
            try:
                self._rpc_router.close(0)
            except Exception:
                self._bump_error("close.rpc")

        self._sub = None
        self._heartbeat_pub = None
        self._rpc_router = None

        try:
            self._ctx.term()
        except Exception:
            self._bump_error("close.ctx")

        h5 = self._h5
        self._h5 = None
        if h5 is not None:
            self._mark_active_measurement_ended()
            try:
                h5.flush()
            except Exception:
                self._bump_error("close.h5_flush")
            try:
                h5.close()
            except Exception:
                self._bump_error("close.h5_close")

    def run(self) -> None:
        self._out_dir.mkdir(parents=True, exist_ok=True)
        write_every_s = max(0.1, self._write_every_s)
        flush_every_n = max(1, self._flush_every_n)
        flush_every_s = max(0.1, self._flush_every_s)
        buffer_max_messages = max(1, self._buffer_max_messages)

        self._stop_evt.clear()

        try:
            if self._autostart_writing:
                try:
                    path = self._resolve_output_path(None, use_default_filename=False)
                    self._ensure_output_path_unused(path)
                    h5 = h5py.File(path, "w")
                    measurement_meta = self._build_measurement_metadata(
                        profile_id=None,
                        values=None,
                        require_profile=False,
                    )
                    self._configure_active_file(
                        h5,
                        write_every_s=write_every_s,
                        load_manager_state=bool(self._process_id),
                        measurement_meta=measurement_meta,
                    )
                    self._clear_buffered_for_disabled(self._disabled_devices)
                except FileExistsError:
                    self._bump_error("autostart.file_exists")
                    self._h5 = None
                    self._reset_per_file_state()
                    self._pending = 0
                    now = time.monotonic()
                    self._last_flush = now
                    self._next_write = now + write_every_s
            else:
                self._h5 = None
                self._reset_per_file_state()
                self._pending = 0
                now = time.monotonic()
                self._last_flush = now
                self._next_write = now + write_every_s
            self._start_heartbeat_thread()
            if self._process_id:
                try:
                    self._init_rpc_router()
                    if self._rpc_endpoint is not None:
                        _manager_rpc(
                            self._ctx,
                            self._manager_rpc,
                            {
                                "type": "process.rpc.advertise",
                                "process_id": self._process_id,
                                "rpc_endpoint": self._rpc_endpoint,
                            },
                            timeout_ms=self._rpc_timeout_ms,
                        )
                except Exception:
                    self._bump_error("rpc.advertise")

                sub = self._ctx.socket(zmq.SUB)
                self._sub = sub
                sub.setsockopt(zmq.RCVHWM, int(self._rcvhwm))
                sub.setsockopt(zmq.SUBSCRIBE, b"manager.telemetry_update")
                sub.setsockopt(zmq.SUBSCRIBE, b"manager.chunk_ready")
                sub.setsockopt(zmq.SUBSCRIBE, b"manager.device_config")
                sub.setsockopt(zmq.SUBSCRIBE, b"manager.run_metadata")
                sub.setsockopt(zmq.SUBSCRIBE, b"manager.command")
                sub.setsockopt(zmq.SUBSCRIBE, b"manager.log")
                sub.setsockopt(zmq.SUBSCRIBE, b"sequencer.lifecycle")
                sub.connect(self._manager_pub)

                self._buf = deque(maxlen=buffer_max_messages)
                self._event_buf = deque(maxlen=buffer_max_messages)
                self._dropped_local = 0
                self._dropped_local_by_topic = {}
                self._dropped_events = 0
                self._clear_buffered_for_disabled(self._disabled_devices)

                poller = zmq.Poller()
                self._poller = poller
                poller.register(sub, zmq.POLLIN)
                if self._rpc_router is not None:
                    poller.register(self._rpc_router, zmq.POLLIN)

                while not self._stop_evt.is_set():
                    now = time.monotonic()
                    timeout_s = min(
                        self._next_write - now,
                        (self._last_flush + flush_every_s) - now,
                    )
                    timeout_ms = int(max(0.0, timeout_s) * 1000)
                    events = dict(poller.poll(timeout_ms))

                    if events.get(sub) == zmq.POLLIN:
                        self._drain_socket()
                    if (
                        self._rpc_router is not None
                        and events.get(self._rpc_router) == zmq.POLLIN
                    ):
                        self._drain_rpc()

                    now = time.monotonic()
                    if now >= self._next_write:
                        self._write_buffered_rows()
                        self._write_event_rows()
                        self._write_stream_buffers()
                        self._next_write = now + write_every_s

                    if (
                        self._pending >= flush_every_n
                        or (now - self._last_flush) >= flush_every_s
                    ):
                        self._flush_active_file()
        except KeyboardInterrupt:
            self._stop_evt.set()
        finally:
            self.close()

    def _buffer_append(self, *, topic: str, msg: Json) -> None:
        if self._buf is None:
            return

        if len(self._buf) == self._buf.maxlen:
            self._dropped_local += 1
            self._dropped_local_by_topic[topic] = (
                self._dropped_local_by_topic.get(topic, 0) + 1
            )
            if self._drop_policy == "drop_newest":
                return

        self._buf.append(msg)

    def _buffer_event(self, *, topic: str, msg: Json) -> None:
        if self._event_buf is None:
            return

        if len(self._event_buf) == self._event_buf.maxlen:
            self._dropped_events += 1
            if self._drop_policy == "drop_newest":
                return

        self._event_buf.append((topic, msg))

    def _should_keep_event(self, *, topic: str, msg: Json) -> bool:
        if self._event_log_mode == "none":
            return False
        if self._event_log_mode == "all":
            return True
        if topic == "manager.command":
            return msg.get("ok") is not True
        if topic == "manager.log":
            severity = self._normalize_log_severity(msg.get("severity"))
            return severity in {"warning", "error", "critical"}
        return True

    def _append_sequencer_event_row(
        self,
        *,
        t_wall: float,
        t_mono: float,
        process_id: str,
        event: str,
        source: str,
        ok: bool,
        message: str,
        payload_json: str,
        yaml_snapshot_id: int = -1,
    ) -> None:
        if self._sequencer_events_ds is None:
            return
        row = np.zeros(1, dtype=self._sequencer_events_ds.dtype)
        row[0]["t_wall"] = float(t_wall)
        row[0]["t_mono"] = float(t_mono)
        row[0]["process_id"] = str(process_id)
        row[0]["event"] = str(event)
        row[0]["source"] = str(source)
        row[0]["ok"] = bool(ok)
        row[0]["message"] = str(message)
        row[0]["payload_json"] = str(payload_json)
        row[0]["yaml_snapshot_id"] = int(yaml_snapshot_id)
        old = self._sequencer_events_ds.shape[0]
        self._sequencer_events_ds.resize((old + 1,))
        self._sequencer_events_ds[old] = row[0]
        self._pending += 1

    def _capture_sequencer_yaml_snapshot(self) -> tuple[int, str | None]:
        if self._sequencer_yaml_ds is None:
            return -1, "sequencer yaml dataset unavailable"
        try:
            resp = _manager_rpc(
                self._ctx,
                self._manager_rpc,
                {
                    "type": "process.rpc",
                    "process_id": self._sequencer_process_id,
                    "request": {
                        "type": "sequencer.loaded_yaml",
                        "request_id": "hdf-sequencer-loaded-yaml",
                    },
                },
                timeout_ms=min(max(300, int(self._rpc_timeout_ms)), 1200),
            )
        except Exception as e:
            return -1, f"loaded_yaml rpc failed: {e}"
        if not isinstance(resp, dict):
            return -1, "loaded_yaml rpc invalid response"
        if resp.get("ok") is False:
            err = resp.get("error")
            if isinstance(err, dict):
                return -1, str(err.get("message") or err.get("code") or "rpc error")
            return -1, str(err or "rpc error")
        result = resp.get("result")
        if not isinstance(result, dict):
            return -1, "loaded_yaml rpc missing result"
        if result.get("loaded") is not True:
            return -1, "no sequence loaded"
        text_raw = result.get("text")
        if text_raw is None:
            return -1, "loaded sequence text unavailable"
        source = str(result.get("source") or "")
        text = str(text_raw)
        snapshot_id = int(self._sequencer_yaml_next_id)
        row = np.zeros(1, dtype=self._sequencer_yaml_ds.dtype)
        row[0]["snapshot_id"] = snapshot_id
        row[0]["t_wall"] = float(time.time())
        row[0]["t_mono"] = float(time.monotonic())
        row[0]["process_id"] = str(self._sequencer_process_id)
        row[0]["source"] = source
        row[0]["text"] = text
        old = self._sequencer_yaml_ds.shape[0]
        self._sequencer_yaml_ds.resize((old + 1,))
        self._sequencer_yaml_ds[old] = row[0]
        self._sequencer_yaml_next_id = snapshot_id + 1
        self._pending += 1
        return snapshot_id, None

    def _handle_sequencer_lifecycle(self, msg: Json) -> None:
        ts_raw = msg.get("ts")
        ts = ts_raw if isinstance(ts_raw, dict) else {}
        t_wall = float(ts.get("t_wall", np.nan))
        t_mono = float(ts.get("t_mono", np.nan))
        process_id = str(msg.get("process_id") or self._sequencer_process_id)
        event = str(msg.get("event") or "unknown")
        source = str(msg.get("source") or "unknown")
        ok = bool(msg.get("ok", True))
        message = str(msg.get("message") or "")
        payload_json = ""
        try:
            payload_json = json.dumps(msg)
        except Exception:
            payload_json = str(msg)

        yaml_snapshot_id = -1
        if event == "start" and ok:
            yaml_snapshot_id, snapshot_error = self._capture_sequencer_yaml_snapshot()
            if snapshot_error:
                if message:
                    message = f"{message}; {snapshot_error}"
                else:
                    message = snapshot_error

        self._append_sequencer_event_row(
            t_wall=t_wall,
            t_mono=t_mono,
            process_id=process_id,
            event=event,
            source=source,
            ok=ok,
            message=message,
            payload_json=payload_json,
            yaml_snapshot_id=yaml_snapshot_id,
        )

    def _drain_socket(self) -> None:
        if self._sub is None or self._buf is None:
            return
        while True:
            try:
                topic_b, payload_b = self._sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break

            topic = topic_b.decode("utf-8", errors="replace")
            msg = safe_json_loads(payload_b)
            if not isinstance(msg, dict):
                continue

            if topic == "manager.telemetry_update":
                device_id = self._normalize_device_id(msg.get("device_id"))
                if device_id is None or not self._is_device_enabled(device_id):
                    continue
                self._buffer_append(topic=topic, msg=msg)
                continue

            if topic == "manager.chunk_ready":
                self._handle_chunk_ready(msg)
                continue

            if topic == "manager.device_config":
                self._handle_device_config(msg)
                continue

            if topic == "manager.run_metadata":
                device_id = self._normalize_device_id(msg.get("device_id"))
                if device_id is None or not self._is_device_enabled(device_id):
                    continue
                self._handle_run_metadata(msg)
                continue

            if topic in {"manager.command", "manager.log"}:
                if topic == "manager.command":
                    device_id = self._normalize_device_id(msg.get("device_id"))
                    if device_id is None or not self._is_device_enabled(device_id):
                        continue
                if not self._should_keep_event(topic=topic, msg=msg):
                    continue
                self._buffer_event(topic=topic, msg=msg)
                continue

            if topic == "sequencer.lifecycle":
                self._handle_sequencer_lifecycle(msg)
                continue

    def _ensure_device(self, device_id: str) -> bool:
        if not self._is_device_enabled(device_id):
            return False
        if device_id in self._datasets:
            return True
        if self._telemetry_group is None:
            return False
        try:
            schema = _schema_rpc(self._ctx, self._manager_rpc)
            self._device_map = _ingest_schema(
                schema,
                self._telemetry_group,
                self._datasets,
                write_enabled=self._is_device_enabled,
            )
        except Exception:
            self._bump_error("schema.rpc")
            return False
        return device_id in self._datasets

    def _write_event_rows(self) -> None:
        if self._event_buf is None:
            return
        if self._h5 is None or self._events_ds is None:
            self._event_buf.clear()
            return

        rows: list[np.void] = []
        while self._event_buf:
            topic, msg = self._event_buf.popleft()
            ts = msg.get("ts", {})
            t_wall = float(ts.get("t_wall", np.nan))
            t_mono = float(ts.get("t_mono", np.nan))

            if topic == "manager.command":
                device_id = self._normalize_device_id(msg.get("device_id"))
                if device_id is None or not self._is_device_enabled(device_id):
                    continue
                row = np.zeros(1, dtype=self._events_ds.dtype)
                row[0]["t_wall"] = t_wall
                row[0]["t_mono"] = t_mono
                row[0]["kind"] = "command"
                row[0]["severity"] = "info"
                row[0]["device_id"] = device_id
                row[0]["action"] = str(msg.get("action", ""))
                row[0]["params_json"] = str(msg.get("params_json", ""))
                row[0]["ok"] = bool(msg.get("ok", False))
                row[0]["error"] = str(msg.get("error", "") or "")
                row[0]["result_json"] = str(msg.get("result_json", ""))
                row[0]["topic"] = topic
                row[0]["message"] = ""
                row[0]["payload_json"] = ""
                rows.append(row[0])
            elif topic == "manager.log":
                row = np.zeros(1, dtype=self._events_ds.dtype)
                row[0]["t_wall"] = t_wall
                row[0]["t_mono"] = t_mono
                row[0]["kind"] = "event"
                row[0]["severity"] = str(msg.get("severity", ""))
                row[0]["device_id"] = str(msg.get("device_id", "") or "")
                row[0]["action"] = ""
                row[0]["params_json"] = ""
                row[0]["ok"] = False
                row[0]["error"] = str(msg.get("error", "") or "")
                row[0]["result_json"] = ""
                row[0]["topic"] = str(msg.get("topic", "") or "")
                row[0]["message"] = str(msg.get("message", "") or "")
                row[0]["payload_json"] = str(msg.get("payload_json", "") or "")
                rows.append(row[0])

        if not rows:
            return
        n = len(rows)
        old = self._events_ds.shape[0]
        self._events_ds.resize((old + n,))
        self._events_ds[old : old + n] = np.array(rows, dtype=self._events_ds.dtype)
        self._pending += n

    def _handle_rpc(self, req: Json) -> Json:
        request_id = req.get("request_id")
        rtype = req.get("type", "")
        common = self._handle_common_rpc(req)
        if common is not None:
            return common

        if rtype == "process.capabilities":
            members = [
                method("hdf.status", params=None, doc="Get writer status."),
                method("hdf.devices.get", params=None, doc="Get HDF device write filter."),
                method(
                    "hdf.devices.disable",
                    params=[
                        param(
                            "device_ids",
                            required=True,
                            default=None,
                            annotation="list[str]",
                        ),
                    ],
                    doc="Disable writing for device_ids.",
                ),
                method(
                    "hdf.devices.enable",
                    params=[
                        param(
                            "device_ids",
                            required=True,
                            default=None,
                            annotation="list[str]",
                        ),
                    ],
                    doc="Enable writing for device_ids.",
                ),
                method(
                    "hdf.rotate",
                    params=[
                        param(
                            "filename",
                            required=False,
                            default=None,
                            annotation="str",
                        ),
                        param(
                            "disabled_devices",
                            required=False,
                            default=None,
                            annotation="list[str]",
                        ),
                        param(
                            "measurement_profile",
                            required=False,
                            default=None,
                            annotation="str",
                        ),
                        param(
                            "measurement_values",
                            required=False,
                            default=None,
                            annotation="dict",
                        ),
                    ],
                    doc="Rotate writer output to a new file.",
                ),
                method(
                    "hdf.measurement.schema.get",
                    params=None,
                    doc="Get configured measurement schema (profiles + note fields).",
                ),
                method(
                    "hdf.measurement.note",
                    params=[
                        param("author", required=True, default=None, annotation="str"),
                        param("kind", required=False, default="note", annotation="str"),
                        param("message", required=True, default=None, annotation="str"),
                        param("payload", required=False, default=None, annotation="dict"),
                    ],
                    doc="Append a measurement note row to /measurement/notes.",
                ),
            ]
            members = self._with_common_capabilities(members)
            return {"request_id": request_id, "ok": True, "result": capabilities_payload(members)}

        def _filter_state() -> Json:
            known = self._known_devices()
            disabled = sorted(self._disabled_devices)
            disabled_set = set(disabled)
            enabled_known = [did for did in known if did not in disabled_set]
            return {
                "disabled_devices": disabled,
                "known_devices": known,
                "enabled_known_devices": enabled_known,
            }

        if rtype == "hdf.status":
            schema_configured, schema_available, schema_error = self._measurement_schema_state()
            result = {
                "file": str(self._h5.filename) if self._h5 is not None else None,
                "writing_active": self._h5 is not None,
                "autostart_writing": bool(self._autostart_writing),
                "pending": int(self._pending),
                "dropped": int(self._dropped_local),
                "dropped_events": int(self._dropped_events),
                "event_log_mode": str(self._event_log_mode),
                "measurement_id": self._measurement_id,
                "measurement_type": self._measurement_type,
                "measurement_schema_version": self._measurement_schema_version,
                "measurement_started_wall_ns": self._measurement_started_wall_ns,
                "measurement_ended_wall_ns": self._measurement_ended_wall_ns,
                "measurement_notes_rows": int(self._measurement_notes_ds.shape[0])
                if self._measurement_notes_ds is not None
                else 0,
                "measurement_schema_configured": bool(schema_configured),
                "measurement_schema_available": bool(schema_available),
                "measurement_schema_path": self._measurement_schema_source
                or self._measurement_schema_path,
                "measurement_schema_error": schema_error,
                "sequencer_event_rows": int(self._sequencer_events_ds.shape[0])
                if self._sequencer_events_ds is not None
                else 0,
                "sequencer_yaml_snapshots": int(self._sequencer_yaml_ds.shape[0])
                if self._sequencer_yaml_ds is not None
                else 0,
            }
            result.update(_filter_state())
            return {"request_id": request_id, "ok": True, "result": result}

        if rtype == "hdf.measurement.schema.get":
            configured, available, error = self._measurement_schema_state()
            if not configured:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "measurement_schema_not_configured",
                        "message": "measurement schema path is not configured",
                    },
                }
            if not available or self._measurement_schema is None:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "measurement_schema_unavailable",
                        "message": error or "measurement schema unavailable",
                    },
                }
            return {
                "request_id": request_id,
                "ok": True,
                "result": {
                    "schema": measurement_schema_to_json(self._measurement_schema),
                    "path": self._measurement_schema_source or self._measurement_schema_path,
                },
            }

        if rtype == "hdf.measurement.note":
            params = req.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": "params must be a dict"},
                }
            configured, available, error = self._measurement_schema_state()
            if not configured:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "measurement_schema_not_configured",
                        "message": "measurement schema path is not configured",
                    },
                }
            if not available or self._measurement_schema is None:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "measurement_schema_unavailable",
                        "message": error or "measurement schema unavailable",
                    },
                }
            try:
                core, payload = normalize_measurement_note_values(
                    self._measurement_schema,
                    values=params,
                )
            except Exception as e:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": str(e)},
                }
            try:
                payload_json = json.dumps(payload)
            except Exception as e:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": str(e)},
                }
            try:
                index, t_wall, t_mono = self._append_measurement_note_row(
                    author=str(core.get("author", "")),
                    kind=str(core.get("kind", "note")),
                    message=str(core.get("message", "")),
                    payload_json=payload_json,
                )
            except Exception as e:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "note_write_failed", "message": str(e)},
                }
            return {
                "request_id": request_id,
                "ok": True,
                "result": {
                    "index": int(index),
                    "t_wall": float(t_wall),
                    "t_mono": float(t_mono),
                    "author": str(core.get("author", "")),
                    "kind": str(core.get("kind", "note")),
                },
            }

        if rtype == "hdf.devices.get":
            return {"request_id": request_id, "ok": True, "result": _filter_state()}

        if rtype in {"hdf.devices.disable", "hdf.devices.enable"}:
            params = req.get("params", {})
            if not isinstance(params, dict):
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": "params must be a dict"},
                }
            try:
                ids = self._normalize_device_id_list(params.get("device_ids"))
            except Exception as e:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": str(e)},
                }
            if not ids:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "invalid_params",
                        "message": "device_ids must contain at least one id",
                    },
                }

            known = set(self._known_devices())
            unknown = sorted([did for did in ids if did not in known])
            changed: list[str] = []
            if rtype == "hdf.devices.disable":
                for did in ids:
                    if did not in self._disabled_devices:
                        self._disabled_devices.add(did)
                        changed.append(did)
                if changed:
                    self._clear_buffered_for_disabled(set(changed))
            else:
                for did in ids:
                    if did in self._disabled_devices:
                        self._disabled_devices.remove(did)
                        changed.append(did)
                if changed and self._telemetry_group is not None:
                    try:
                        schema = _schema_rpc(self._ctx, self._manager_rpc)
                        self._device_map = _ingest_schema(
                            schema,
                            self._telemetry_group,
                            self._datasets,
                            write_enabled=self._is_device_enabled,
                        )
                    except Exception:
                        self._bump_error("schema.rpc")

            if self._h5 is not None:
                try:
                    self._h5.attrs["disabled_devices_json"] = json.dumps(
                        sorted(self._disabled_devices)
                    )
                except Exception:
                    self._bump_error("h5.attrs.disabled_devices")

            return {
                "request_id": request_id,
                "ok": True,
                "result": {
                    "changed": changed,
                    "unknown": unknown,
                    **_filter_state(),
                },
            }

        if rtype == "hdf.rotate":
            params = req.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": "params must be a dict"},
                }
            filename_raw = params.get("filename")
            filename: str | None = None
            if filename_raw is not None:
                filename = str(filename_raw).strip()
                if not filename:
                    return {
                        "request_id": request_id,
                        "ok": False,
                        "error": {
                            "code": "invalid_params",
                            "message": "filename must be a non-empty string",
                        },
                    }

            disabled_override: set[str] | None = None
            unknown: list[str] = []
            if "disabled_devices" in params:
                try:
                    disabled_override = self._normalize_device_ids(
                        params.get("disabled_devices")
                    )
                except Exception as e:
                    return {
                        "request_id": request_id,
                        "ok": False,
                        "error": {"code": "invalid_params", "message": str(e)},
                    }
                known = set(self._known_devices())
                unknown = sorted(
                    [did for did in disabled_override if did not in known]
                )

            measurement_profile: str | None = None
            if "measurement_profile" in params:
                raw_profile = params.get("measurement_profile")
                if raw_profile is not None:
                    profile_text = str(raw_profile).strip()
                    if not profile_text:
                        return {
                            "request_id": request_id,
                            "ok": False,
                            "error": {
                                "code": "invalid_params",
                                "message": "measurement_profile must be a non-empty string",
                            },
                        }
                    measurement_profile = profile_text

            measurement_values: object = params.get("measurement_values", {})
            if measurement_values is None:
                measurement_values = {}
            if not isinstance(measurement_values, dict):
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "invalid_params",
                        "message": "measurement_values must be a dict",
                    },
                }

            try:
                old_file, new_file = self._rotate_file(
                    filename=filename,
                    disabled_devices=disabled_override,
                    measurement_profile=measurement_profile,
                    measurement_values=measurement_values,
                )
            except FileExistsError as e:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "file_exists",
                        "message": str(e),
                    },
                }
            except Exception as e:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "rotate_failed",
                        "message": str(e),
                    },
                }

            return {
                "request_id": request_id,
                "ok": True,
                "result": {
                    "old_file": old_file,
                    "new_file": new_file,
                    "measurement_id": self._measurement_id,
                    "measurement_type": self._measurement_type,
                    "unknown": unknown,
                    **_filter_state(),
                },
            }

        return {
            "request_id": request_id,
            "ok": False,
            "error": {"code": "unknown_request"},
        }

    def _write_buffered_rows(self) -> None:
        if self._buf is None:
            return
        if self._h5 is None or self._telemetry_group is None:
            self._buf.clear()
            return
        rows_by_device: dict[str, list[np.void]] = {}

        while self._buf:
            msg = self._buf.popleft()
            device_id = self._normalize_device_id(msg.get("device_id"))
            if device_id is None:
                continue
            if not self._ensure_device(device_id):
                continue

            ds = self._datasets[device_id]
            info = self._device_map[device_id]
            signals = info["signals"]
            dtypes = info["dtypes"]

            ts = msg.get("ts", {})
            t_wall = float(ts.get("t_wall", np.nan))
            t_mono = float(ts.get("t_mono", np.nan))
            seq = int(msg.get("seq", -1))
            sigs = msg.get("signals", {})
            if not isinstance(sigs, dict):
                sigs = {}

            row = np.zeros(1, dtype=ds.dtype)
            row[0]["t_wall"] = t_wall
            row[0]["t_mono"] = t_mono
            row[0]["seq"] = seq

            for name, dtype_str in zip(signals, dtypes, strict=True):
                raw = sigs.get(name, {})
                value = raw.get("value") if isinstance(raw, dict) else None
                row[0][name] = _convert_value(value, dtype_str)

            rows_by_device.setdefault(device_id, []).append(row[0])

        for device_id, rows in rows_by_device.items():
            ds = self._datasets[device_id]
            n = len(rows)
            if n == 0:
                continue
            old = ds.shape[0]
            ds.resize((old + n,))
            ds[old : old + n] = np.array(rows, dtype=ds.dtype)
            self._pending += n

    def _handle_chunk_ready(self, msg: Json) -> None:
        device_id = self._normalize_device_id(msg.get("device_id"))
        stream = self._normalize_device_id(msg.get("stream"))
        shm_name = msg.get("shm_name")
        if not device_id or not stream or not shm_name:
            return

        key = (device_id, stream)
        if not self._is_device_enabled(device_id):
            seq_raw = msg.get("seq")
            try:
                seq = int(seq_raw)
            except Exception:
                seq = None
            if seq is not None:
                prev = int(self._stream_last_seq.get(key, 0))
                if seq > prev:
                    self._stream_last_seq[key] = seq
            self._stream_buffers.pop(key, None)
            return

        ctx_id: int | None = None
        context_id = msg.get("context_id")
        if context_id is not None:
            try:
                ctx_id = int(context_id)
                fields = msg.get("context_fields")
                if isinstance(fields, dict):
                    self._record_context(ctx_id, fields)
            except Exception:
                ctx_id = None

        reader = self._stream_readers.get(key)
        if reader is None or reader.name != shm_name:
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    self._bump_error("stream.reader_close")
            try:
                reader = ShmRingReader.attach(str(shm_name))
            except Exception:
                self._bump_error("stream.attach")
                return
            self._stream_readers[key] = reader
            session = self._next_stream_session(device_id, stream)
            self._stream_sessions[key] = session
            self._stream_active_session[key] = session
            self._stream_last_seq[key] = 0
            self._stream_dropped_total[key] = 0
            self._stream_buffers.pop(key, None)
            self._stream_schema.pop(key, None)
            self._stream_expected_nbytes.pop(key, None)
            self._stream_context_id.pop(key, None)

        if ctx_id is not None:
            self._stream_context_id[key] = ctx_id

        last_seq = self._stream_last_seq.get(key, 0)
        try:
            events = reader.read_events(last_seq)
        except Exception:
            try:
                reader.close()
            except Exception:
                self._bump_error("stream.reader_close")
            self._bump_error("stream.drain")
            self._stream_readers.pop(key, None)
            self._stream_last_seq.pop(key, None)
            self._stream_buffers.pop(key, None)
            self._stream_schema.pop(key, None)
            self._stream_context_id.pop(key, None)
            return
        if not events:
            return

        buf = self._stream_buffers.setdefault(
            key,
            {
                "data": [],
                "seq": [],
                "t0_mono_ns": [],
                "t0_wall_ns": [],
                "context_id": [],
            },
        )

        dropped = self._stream_dropped_total.get(key, 0)
        current_context_id = self._stream_context_id.get(key, -1)
        for ev in events:
            seq = int(ev["seq"])
            if last_seq and seq > last_seq + 1:
                dropped += seq - last_seq - 1
            last_seq = seq

            buf["data"].append(ev["payload"])
            buf["seq"].append(seq)
            buf["t0_mono_ns"].append(int(ev["t0_mono_ns"]))
            buf["t0_wall_ns"].append(int(ev["t0_wall_ns"]))
            buf["context_id"].append(int(current_context_id))

        self._stream_last_seq[key] = last_seq
        self._stream_dropped_total[key] = dropped

        if key not in self._stream_schema:
            self._stream_schema[key] = {
                "dtype": str(reader.layout.dtype),
                "shape": reader.layout.shape,
            }

    def _next_stream_session(self, device_id: str, stream: str) -> int:
        key = (device_id, stream)
        base = self._stream_sessions.get(key, 0) + 1
        if self._streams_group is None:
            return base
        try:
            device_group = self._streams_group.require_group(device_id)
            stream_group = device_group.require_group(stream)
        except Exception:
            self._bump_error("stream.group")
            return base

        max_session = 0
        for name in stream_group.keys():
            if not name.startswith("session_"):
                continue
            suffix = name.split("session_", 1)[-1]
            try:
                value = int(suffix)
            except ValueError:
                continue
            if value > max_session:
                max_session = value
        return max(max_session + 1, base)

    def _ensure_context_columns(self, fields: dict[str, Any]) -> None:
        if self._context_columns_ready:
            return

        spec = self._fetch_context_columns_spec()
        if spec is not None:
            self._init_context_columns_from_spec(spec, source="explicit")
            return

        if fields:
            spec = self._infer_context_columns_from_fields(fields)
            self._init_context_columns_from_spec(spec, source="auto")

    def _fetch_context_columns_spec(self) -> dict[str, str] | None:
        if self._context_columns_fetch_attempted:
            return None
        self._context_columns_fetch_attempted = True
        try:
            resp = _manager_rpc(
                self._ctx,
                self._manager_rpc,
                {
                    "type": "process.rpc",
                    "process_id": self._sequencer_process_id,
                    "request": {
                        "type": "sequencer.status",
                        "request_id": "context_columns",
                    },
                },
                timeout_ms=500,
            )
        except Exception:
            return None
        if not isinstance(resp, dict):
            return None
        if resp.get("ok") is False:
            return None
        result = resp.get("result")
        if not isinstance(result, dict):
            return None
        return self._normalize_context_columns_spec(result.get("context_columns"))

    def _normalize_context_columns_spec(self, raw: Any) -> dict[str, str] | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        spec: dict[str, str] = {}
        for key, value in raw.items():
            name = str(key)
            dtype = str(value).lower()
            if dtype not in {"float64", "int64", "bool"}:
                continue
            spec[name] = dtype
        return spec

    def _infer_context_columns_from_fields(
        self, fields: dict[str, Any]
    ) -> dict[str, str]:
        spec: dict[str, str] = {}
        for key, value in fields.items():
            if isinstance(value, (bool, np.bool_)):
                spec[str(key)] = "bool"
                continue
            if isinstance(value, (int, float, np.integer, np.floating)):
                spec[str(key)] = "float64"
        return spec

    def _init_context_columns_from_spec(
        self, spec: dict[str, str], *, source: str
    ) -> None:
        if self._context_table_group is None or self._context_table_ds is None:
            return
        if self._context_columns_ready:
            return

        self._context_columns_ready = True
        self._context_columns_source = source
        self._context_columns_types = dict(spec)
        self._context_columns_group = self._context_table_group.require_group("columns")
        self._context_columns_group.attrs["source"] = source
        try:
            self._context_table_group.attrs["context_columns_json"] = json.dumps(spec)
        except Exception:
            pass

        current_len = int(self._context_table_ds.shape[0])
        for name, dtype in spec.items():
            if not name:
                continue
            if dtype == "int64":
                ds_dtype = np.dtype("int64")
                missing = -1
            elif dtype == "bool":
                ds_dtype = np.dtype("uint8")
                missing = np.uint8(255)
            else:
                ds_dtype = np.dtype("float64")
                missing = np.nan
            ds = self._context_columns_group.require_dataset(
                name,
                shape=(current_len,),
                maxshape=(None,),
                dtype=ds_dtype,
                chunks=(1024,),
            )
            if current_len:
                ds[...] = missing
            ds.attrs["dtype"] = dtype
            ds.attrs["missing"] = int(missing) if dtype == "bool" else missing
            if dtype == "bool":
                ds.attrs["encoding"] = "0=false,1=true,255=missing"
            self._context_columns_datasets[name] = ds
            self._context_columns_missing[name] = missing

    def _append_context_columns(self, index: int, fields: dict[str, Any]) -> None:
        if not self._context_columns_datasets:
            return
        for name, ds in self._context_columns_datasets.items():
            ds.resize((index + 1,))
            raw = fields.get(name)
            ds[index] = self._coerce_context_value(name, raw)

    def _coerce_context_value(self, name: str, value: Any) -> Any:
        missing = self._context_columns_missing.get(name, np.nan)
        dtype = self._context_columns_types.get(name, "float64")
        if value is None:
            return missing
        try:
            if dtype == "int64":
                if isinstance(value, (bool, np.bool_)):
                    return int(bool(value))
                if isinstance(value, (int, np.integer)):
                    return int(value)
                if isinstance(value, (float, np.floating)):
                    return int(value)
                return missing
            if dtype == "bool":
                if isinstance(value, (bool, np.bool_)):
                    return np.uint8(1 if bool(value) else 0)
                if isinstance(value, (int, np.integer)) and int(value) in {0, 1}:
                    return np.uint8(int(value))
                return missing
            if isinstance(value, (bool, np.bool_)):
                return float(bool(value))
            if isinstance(value, (int, float, np.integer, np.floating)):
                return float(value)
        except Exception:
            return missing
        return missing

    def _record_context(self, context_id: int, fields: dict[str, Any]) -> None:
        if self._context_table_ds is None:
            return
        if context_id in self._seen_context_ids:
            return
        self._seen_context_ids.add(context_id)
        self._ensure_context_columns(fields)
        row = np.zeros(1, dtype=self._context_table_ds.dtype)
        row[0]["context_id"] = int(context_id)
        row[0]["ts_wall_ns"] = int(time.time_ns())
        row[0]["ts_mono_ns"] = int(time.monotonic_ns())
        try:
            fields_json = json.dumps(fields)
        except Exception:
            fields_json = str(fields)
        row[0]["fields_json"] = fields_json
        old = self._context_table_ds.shape[0]
        self._context_table_ds.resize((old + 1,))
        self._context_table_ds[old] = row[0]
        self._append_context_columns(old, fields)

    def _write_stream_buffers(self) -> None:
        if self._h5 is None or self._streams_group is None:
            for _key, buf in self._stream_buffers.items():
                self._clear_stream_buffer(buf)
            return
        for key, buf in list(self._stream_buffers.items()):
            data_list = buf.get("data", [])
            if not data_list:
                continue

            device_id, stream = key
            if not self._is_device_enabled(device_id):
                self._clear_stream_buffer(buf)
                continue
            schema = self._stream_schema.get(key)
            reader = self._stream_readers.get(key)

            dtype_raw = None if schema is None else schema.get("dtype")
            shape_raw = None if schema is None else schema.get("shape")
            if dtype_raw is None and reader is not None:
                dtype_raw = str(reader.layout.dtype)
            if shape_raw is None and reader is not None:
                shape_raw = tuple(reader.layout.shape)

            if dtype_raw is None or shape_raw is None:
                self._clear_stream_buffer(buf)
                continue

            dtype_str = str(dtype_raw)
            shape = tuple(int(x) for x in shape_raw)
            session = self._stream_active_session.get(key, 1)
            datasets = self._ensure_stream_dataset(
                device_id, stream, dtype_str, shape, session=session
            )

            n = len(data_list)
            seq_list = list(buf["seq"])
            t0_mono_list = list(buf["t0_mono_ns"])
            t0_wall_list = list(buf["t0_wall_ns"])
            context_list = list(buf.get("context_id", []))
            if context_list and len(context_list) != n:
                context_list = [-1] * n
                self._bump_error("stream.context_alignment_repair")

            data_ds = datasets["data"]
            seq_ds = datasets["seq"]
            t0_mono_ds = datasets["t0_mono_ns"]
            t0_wall_ds = datasets["t0_wall_ns"]
            context_ds = datasets.get("context_id")

            dtype_obj = data_ds.dtype
            shape_obj = tuple(data_ds.shape[1:])
            expected_nbytes = self._stream_expected_nbytes.get(key)
            if expected_nbytes is None:
                expected_nbytes = int(dtype_obj.itemsize * np.prod(shape_obj))
                self._stream_expected_nbytes[key] = expected_nbytes
            elif expected_nbytes != int(dtype_obj.itemsize * np.prod(shape_obj)):
                expected_nbytes = int(dtype_obj.itemsize * np.prod(shape_obj))
                self._stream_expected_nbytes[key] = expected_nbytes
            if any(len(payload) != expected_nbytes for payload in data_list):
                self._clear_stream_buffer(buf)
                continue

            if n == 1:
                data_arr = np.frombuffer(data_list[0], dtype=dtype_obj).reshape(
                    (n,) + shape_obj
                )
            else:
                data_arr = np.empty((n,) + shape_obj, dtype=dtype_obj)
                for idx, payload in enumerate(data_list):
                    data_arr[idx] = np.frombuffer(payload, dtype=dtype_obj).reshape(
                        shape_obj
                    )

            old = data_ds.shape[0]
            data_ds.resize((old + n,) + data_ds.shape[1:])
            seq_ds.resize((old + n,))
            t0_mono_ds.resize((old + n,))
            t0_wall_ds.resize((old + n,))
            if context_ds is not None:
                context_ds.resize((old + n,))

            data_ds[old : old + n] = data_arr
            seq_ds[old : old + n] = np.asarray(seq_list, dtype=seq_ds.dtype)
            t0_mono_ds[old : old + n] = np.asarray(t0_mono_list, dtype=t0_mono_ds.dtype)
            t0_wall_ds[old : old + n] = np.asarray(t0_wall_list, dtype=t0_wall_ds.dtype)
            if context_ds is not None:
                if len(context_list) != n:
                    context_list = [-1] * n
                context_ds[old : old + n] = np.asarray(
                    context_list, dtype=context_ds.dtype
                )

            dropped = int(self._stream_dropped_total.get(key, 0))
            data_ds.attrs["dropped_total"] = dropped

            self._clear_stream_buffer(buf)

    def _active_stream_dataset_key(
        self, device_id: str, stream: str
    ) -> tuple[str, str, int] | None:
        base = (device_id, stream)
        session = self._stream_active_session.get(base)
        if session is not None:
            key = (device_id, stream, int(session))
            if key in self._stream_datasets:
                return key

        best: tuple[str, str, int] | None = None
        for d, s, sess in self._stream_datasets.keys():
            if d == device_id and s == stream:
                if best is None or sess > best[2]:
                    best = (d, s, sess)
        return best

    def _ensure_stream_dataset(
        self,
        device_id: str,
        stream: str,
        dtype_str: str,
        shape: tuple[int, ...],
        *,
        session: int,
    ) -> dict[str, h5py.Dataset]:
        key = (device_id, stream, session)
        if key in self._stream_datasets:
            return self._stream_datasets[key]
        if self._streams_group is None:
            raise RuntimeError("Streams group not initialized")

        device_group = self._streams_group.require_group(device_id)
        stream_group = device_group.require_group(stream)
        session_group = stream_group.require_group(f"session_{session:03d}")

        dtype_obj = np.dtype(dtype_str)
        chunk_n = 64
        data_chunks = (chunk_n,) + tuple(shape)
        data_ds = session_group.create_dataset(
            "data",
            shape=(0,) + tuple(shape),
            maxshape=(None,) + tuple(shape),
            dtype=dtype_obj,
            chunks=data_chunks,
            compression=DEFAULT_NUMERIC_COMPRESSION,
            shuffle=DEFAULT_NUMERIC_SHUFFLE,
        )
        seq_ds = session_group.create_dataset(
            "seq",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint64,
            chunks=(chunk_n,),
            compression="lzf",
            shuffle=True,
        )
        t0_mono_ds = session_group.create_dataset(
            "t0_mono_ns",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint64,
            chunks=(chunk_n,),
            compression="lzf",
            shuffle=True,
        )
        t0_wall_ds = session_group.create_dataset(
            "t0_wall_ns",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint64,
            chunks=(chunk_n,),
            compression="lzf",
            shuffle=True,
        )
        context_ds = session_group.create_dataset(
            "context_id",
            shape=(0,),
            maxshape=(None,),
            dtype=np.int64,
            chunks=(chunk_n,),
            compression="lzf",
            shuffle=True,
        )

        pending = self._pending_stream_metadata.get((device_id, stream), None)
        if pending:
            for attr_key, value in pending.items():
                data_ds.attrs[attr_key] = value
            self._pending_stream_metadata.pop((device_id, stream), None)
        data_ds.attrs["session"] = int(session)

        datasets = {
            "data": data_ds,
            "seq": seq_ds,
            "t0_mono_ns": t0_mono_ds,
            "t0_wall_ns": t0_wall_ds,
            "context_id": context_ds,
        }
        self._stream_datasets[key] = datasets
        return datasets

    def _handle_device_config(self, msg: Json, *, cache: bool = True) -> None:
        device_id = self._normalize_device_id(msg.get("device_id"))
        if device_id is None:
            return
        if cache:
            self._latest_device_config[device_id] = copy.deepcopy(msg)

        can_write_device = self._is_device_enabled(device_id)
        if self._config_group is None and not can_write_device:
            return

        yaml_text = msg.get("yaml_text")
        stream_metadata = self._normalize_stream_metadata_dict(msg.get("stream_metadata"))
        stream_calls = msg.get("stream_calls", [])
        run_meta_calls = msg.get("run_meta_calls", [])

        if can_write_device and self._config_group is not None:
            device_group = self._config_group.require_group(device_id)
            if yaml_text is not None:
                ds = device_group.require_dataset(
                    "yaml",
                    shape=(),
                    dtype=h5py.string_dtype("utf-8"),
                )
                ds[()] = str(yaml_text)
            device_metadata = self._normalize_metadata_dict(msg.get("device_metadata"))
            device_meta_ds = device_group.require_dataset(
                "device_metadata_json",
                shape=(),
                dtype=h5py.string_dtype("utf-8"),
            )
            device_meta_ds[()] = json.dumps(device_metadata)
            stream_meta_ds = device_group.require_dataset(
                "stream_metadata_json",
                shape=(),
                dtype=h5py.string_dtype("utf-8"),
            )
            stream_meta_ds[()] = json.dumps(stream_metadata)
            run_meta_schema: list[Any] = run_meta_calls if isinstance(run_meta_calls, list) else []
            run_meta_calls_ds = device_group.require_dataset(
                "run_meta_calls_json",
                shape=(),
                dtype=h5py.string_dtype("utf-8"),
            )
            run_meta_calls_ds[()] = json.dumps(run_meta_schema)

        stream_call_attrs: dict[str, dict[str, Any]] = {}
        if isinstance(stream_calls, list):
            for call in stream_calls:
                if not isinstance(call, dict):
                    continue
                for out in call.get("outputs", []) or []:
                    if not isinstance(out, dict):
                        continue
                    stream = str(out.get("stream"))
                    dtype = str(out.get("dtype"))
                    shape_raw = out.get("shape", []) or []
                    shape = tuple(int(x) for x in shape_raw)
                    key = (device_id, stream)
                    self._stream_schema[key] = {"dtype": dtype, "shape": shape}
                    try:
                        expected = int(np.dtype(dtype).itemsize * np.prod(shape))
                        self._stream_expected_nbytes[key] = expected
                    except Exception:
                        self._stream_expected_nbytes.pop(key, None)
                    merged = stream_call_attrs.setdefault(stream, {})
                    units = out.get("units")
                    if units is not None:
                        merged["units"] = units
                    description = out.get("description")
                    if description is not None:
                        merged["description"] = description
                    attrs = out.get("attrs")
                    if isinstance(attrs, dict):
                        merged.update(attrs)

        combined_stream: dict[str, dict[str, Any]] = {}
        for stream_name, attrs in stream_call_attrs.items():
            combined_stream[stream_name] = dict(attrs)

        for stream_name, attrs in stream_metadata.items():
            current = dict(combined_stream.get(stream_name, {}))
            current.update(attrs)
            combined_stream[stream_name] = current

        for stream_name, attrs in combined_stream.items():
            base_key = (device_id, stream_name)
            if not can_write_device:
                self._pending_stream_metadata[base_key] = dict(attrs)
                continue
            active_key = self._active_stream_dataset_key(device_id, stream_name)
            if active_key is not None:
                data_ds = self._stream_datasets[active_key]["data"]
                for attr_key, value in attrs.items():
                    data_ds.attrs[attr_key] = value
            else:
                self._pending_stream_metadata[base_key] = dict(attrs)

    def _handle_run_metadata(self, msg: Json) -> None:
        if self._run_meta_group is None:
            return
        device_id = self._normalize_device_id(msg.get("device_id"))
        if device_id is None or not self._is_device_enabled(device_id):
            return
        run_metadata = msg.get("run_metadata", {})
        if not isinstance(run_metadata, dict):
            return
        device_group = self._run_meta_group.require_group(device_id)
        ds = device_group.require_dataset(
            "json",
            shape=(),
            dtype=h5py.string_dtype("utf-8"),
        )
        ds[()] = json.dumps(run_metadata)


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    writer = HdfWriter(
        out_dir=ns.out_dir,
        filename=ns.filename,
        manager_rpc=ns.manager_rpc,
        manager_pub=ns.manager_pub,
        rpc_timeout_ms=ns.rpc_timeout_ms,
        timezone=ns.timezone,
        rcvhwm=ns.rcvhwm,
        write_every_s=ns.write_every_s,
        buffer_max_messages=ns.buffer_max_messages,
        flush_every_n=ns.flush_every_n,
        flush_every_s=ns.flush_every_s,
        disabled_devices=ns.disabled_devices,
        measurement_schema_path=ns.measurement_schema_path,
        autostart_writing=ns.autostart_writing,
        event_log_mode=ns.event_log_mode,
    )
    writer._process_id = ns.process_id
    writer._heartbeat_endpoint = ns.heartbeat_endpoint
    writer._heartbeat_period_s = ns.heartbeat_period_s
    writer.run()


if __name__ == "__main__":
    main()
