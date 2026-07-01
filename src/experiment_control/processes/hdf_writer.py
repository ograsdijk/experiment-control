from __future__ import annotations

import argparse
import copy
import json
import queue
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Literal, cast

import h5py
import numpy as np
import zmq

from ..capabilities import capabilities_payload, method, param
from ..contracts.messages import ChunkReadyMessage, RpcActionRequest
from ..schemas.measurement import (
    MeasurementSchema,
    measurement_schema_from_json,
    measurement_schema_to_json,
    normalize_measurement_note_values,
    normalize_measurement_values,
)
from ..shm.shm_ring import ShmRingReader
from ..types import MemberSpec
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.value_coercion import coerce_scalar
from ..utils.logging_levels import normalize_log_severity
from ..utils.rpc_dispatch import RpcDispatchRegistry
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import json_dumps, json_loads, safe_json_loads
from .hdf_writer_bg import (
    _BG_SENTINEL as _BG_SENTINEL,
    _BgRequest as _BgRequest,
    _BgSentinel as _BgSentinel,
    _CaptureRunMetadataRequest as _CaptureRunMetadataRequest,
    _DevicesToggleRequest as _DevicesToggleRequest,
    _FlushBatch as _FlushBatch,
    _MeasurementNoteRequest as _MeasurementNoteRequest,
    _RotateRequest as _RotateRequest,
    _StartWritingRequest as _StartWritingRequest,
    _StopWritingRequest as _StopWritingRequest,
)
from .hdf_writer_context import coerce_context_value
from .hdf_writer_dtypes import (
    DEFAULT_NUMERIC_COMPRESSION,
    DEFAULT_NUMERIC_SHUFFLE,
    DEFAULT_TELEMETRY_CHUNK_ROWS,
    DTYPE_MAP,
    _context_table_dtype,
    _event_dtype,
    _measurement_note_dtype,
    _sequencer_event_dtype,
    _sequencer_yaml_dtype,
)
from .hdf_writer_topics import build_hdf_topic_handlers
from .process_base import ManagedProcessBase

Json = dict[str, Any]
EventLogMode = Literal["all", "failures_only", "none"]
EVENT_LOG_MODES: tuple[EventLogMode, ...] = ("all", "failures_only", "none")


def _default_filename() -> str:
    return time.strftime("%Y_%m_%d-%H_%M_%S.h5", time.localtime())





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
    p.add_argument("--write-every-s", type=float, default=5.0)
    p.add_argument("--buffer-max-messages", type=int, default=200_000)
    p.add_argument("--flush-every-n", type=int, default=2000)
    p.add_argument("--flush-every-s", type=float, default=15.0)
    p.add_argument("--context-resolve-ttl-s", type=float, default=5.0)
    p.add_argument("--context-pending-max-per-stream", type=int, default=10_000)
    p.add_argument("--context-map-max-per-stream", type=int, default=20_000)
    p.add_argument("--disabled-devices", nargs="*", default=None)
    p.add_argument("--disabled-processes", nargs="*", default=None)
    p.add_argument("--measurement-schema-path", default=None)
    p.add_argument("--autostart-writing", default=None)
    p.add_argument(
        "--event-log-mode",
        choices=list(EVENT_LOG_MODES),
        default="all",
    )
    p.add_argument("--bg-join-timeout-s", type=float, default=2.0)
    return p.parse_args(argv)


def _schema_rpc(ctx: zmq.Context, endpoint: str, timeout_ms: int = 2000) -> Json:
    sock = ctx.socket(zmq.DEALER)
    sock.connect(endpoint)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        msg = {"action": "manager.telemetry.schema.list"}
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
    chunk_size: int = DEFAULT_TELEMETRY_CHUNK_ROWS,
) -> h5py.Dataset:
    device_group = telemetry_group.require_group(device_id)

    fields: list[tuple[str, Any]] = [("t_wall", np.float64), ("t_mono", np.float64)]
    # Wall clock of the manager when the bundle was ingested (NaN if absent).
    # For a federated device this is the consuming host's clock, so
    # `t_wall_recv - t_wall` = clock_skew + transport_latency; pair it with a
    # round-trip skew measurement (cli/clock_skew_probe.py) to recover per-sample
    # one-way latency offline. For a local device it is just pipeline latency.
    fields.append(("t_wall_recv", np.float64))
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
    chunk_size: int = DEFAULT_TELEMETRY_CHUNK_ROWS,
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


def _ingest_process_schema(
    schema: Json,
    process_group: h5py.Group,
    datasets: dict[str, h5py.Dataset],
    device_map: dict[str, Json],
    *,
    write_enabled: Callable[[str], bool] | None = None,
    chunk_size: int = DEFAULT_TELEMETRY_CHUNK_ROWS,
) -> set[str]:
    """Create ``/process_telemetry/<process_id>`` datasets from a process
    telemetry schema (manager.process_telemetry.schema.list).

    Process telemetry reuses the device telemetry write path: entries are
    merged into the SAME ``datasets``/``device_map`` (keyed by ``process_id``)
    so ``_write_buffered_rows_batch`` writes them unchanged — but the datasets
    live in a SEPARATE ``/process_telemetry`` group and carry a
    ``source_kind="process"`` attribute, keeping the distinction in the file.

    ``write_enabled`` gates dataset creation (a disabled process gets no
    dataset, mirroring ``_ingest_schema`` for devices). Returns the set of
    process_ids present in the schema (known processes) regardless of enabled
    state, so the caller can track them for the process write-filter.
    """
    processes_raw = schema.get("processes", [])
    if not isinstance(processes_raw, list):
        return set()
    seen: set[str] = set()
    for proc in processes_raw:
        if not isinstance(proc, dict):
            continue
        process_id = str(proc.get("process_id", ""))
        signals = list(proc.get("signals", []))
        dtypes = list(proc.get("dtypes", []))
        units = list(proc.get("units", []))
        if not process_id or not signals:
            continue
        seen.add(process_id)
        device_map[process_id] = {
            "signals": signals,
            "dtypes": dtypes,
            "units": units,
        }
        can_write = True if write_enabled is None else bool(write_enabled(process_id))
        if can_write and process_id not in datasets:
            ds = _create_device_dataset(
                process_group,
                process_id,
                signals,
                dtypes,
                units,
                chunk_size=chunk_size,
            )
            ds.attrs["source_kind"] = "process"
            datasets[process_id] = ds
    return seen


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
        context_resolve_ttl_s: float = 5.0,
        context_pending_max_per_stream: int = 10_000,
        context_map_max_per_stream: int = 20_000,
        disabled_devices: list[str] | None = None,
        disabled_processes: list[str] | None = None,
        measurement_schema_path: str | None = None,
        autostart_writing: bool | str | None = None,
        event_log_mode: EventLogMode = "all",
        bg_join_timeout_s: float = 2.0,
    ) -> None:
        # bg_join_timeout_s bounds how long close() waits for the background
        # flush thread to drain its queue and close the HDF5 file on shutdown.
        # Must be < the process-level shutdown_timeout_s the manager
        # supervisor enforces, otherwise the manager will SIGKILL the worker
        # mid-close. Default 2.0s leaves 1s headroom under the typical 3.0s
        # supervisor budget.
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
        # Size the per-device write-staging buffer so a full write cycle is
        # typically one contiguous h5py append. Decoupled from `flush_every_n`
        # (which is now purely a flush-to-disk trigger) with a generous floor
        # and ceiling so the larger 5 s write cadence still coalesces cleanly.
        batch_rows = min(max(int(self._flush_every_n), 1024), 8192)
        self._telemetry_batch_rows = int(batch_rows)
        self._event_batch_rows = int(batch_rows)
        self._context_resolve_ttl_s = max(0.1, float(context_resolve_ttl_s))
        self._context_pending_max_per_stream = max(
            1, int(context_pending_max_per_stream)
        )
        self._context_map_max_per_stream = max(1, int(context_map_max_per_stream))
        self._context_map_ttl_s = 60.0
        self._context_map_written_margin = 512
        self._disabled_devices = self._normalize_device_ids(disabled_devices or [])
        # Process-telemetry write filter, a SEPARATE namespace from
        # `_disabled_devices` so a device id and a process id never collide.
        # `_known_process_ids` tracks which ids are processes (seen via the
        # process telemetry schema or a process telemetry_update) so the filter
        # state can present processes distinctly from devices.
        self._disabled_processes = self._normalize_device_ids(disabled_processes or [])
        self._known_process_ids: set[str] = set()
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
        self._process_telemetry_group: h5py.Group | None = None
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
        # Context-table rows buffered on the main drain thread (dedup'd via
        # `_seen_context_ids`) and written to h5py by the bg thread. Keeps the
        # one remaining h5py write off the high-frequency drain path so the SUB
        # socket never stalls behind a slow context-table write / RPC.
        self._pending_context_rows: list[dict[str, Any]] = []
        self._datasets: dict[str, h5py.Dataset] = {}
        self._device_map: dict[str, Json] = {}
        self._sub: zmq.Socket | None = None
        self._poller: zmq.Poller | None = None
        self._buf: deque[Json] | None = None
        self._event_buf: deque[tuple[str, Json]] | None = None
        self._telemetry_batch_buffers: dict[str, np.ndarray[Any, Any]] = {}
        self._event_batch_buffer: np.ndarray[Any, Any] | None = None
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
        self._stream_context_by_seq: dict[tuple[str, str], dict[int, tuple[int, float]]] = {}
        self._stream_pending_by_seq: dict[tuple[str, str], dict[int, Json]] = {}
        self._stream_last_written_seq: dict[tuple[str, str], int] = {}
        self._context_resolved_exact = 0
        self._context_late_resolved = 0
        self._context_written_minus1_missing = 0
        self._context_evicted_pending_overflow = 0
        self._context_evicted_map_overflow = 0

        self._process_id: str | None = None
        self._heartbeat_endpoint: str | None = None
        self._heartbeat_period_s: float = 1.0

        self._pending = 0
        self._last_flush = 0.0
        self._next_write = 0.0

        self._error_counts: dict[str, int] = {}
        # _bump_error fires on both the main thread (drain handlers, RPC
        # handlers) and the bg flush thread. CPython dict mutation is
        # NOT atomic across threads in the way `+=` on an attribute
        # suggests â€” the get + assignment can interleave with another
        # thread's bump on the same key, losing counts. Guard with a
        # small lock; reads via the public _error_counts attribute (used
        # by the bg-thread-failure tests) take a momentary snapshot to
        # avoid mid-mutation reads of any single bucket.
        self._error_counts_lock = threading.Lock()

        # Background-flush plumbing. The bg thread, once spawned in run(),
        # consumes _FlushBatch (fire-and-forget) and _BgRequest (synchronous)
        # items from _bg_queue. The bounded queue caps in-flight handoffs;
        # on overflow a FlushBatch is dropped and _dropped_flush_batches is
        # incremented. The two cached attrs (_active_h5_filename,
        # _writing_active) are written by the bg thread on every file
        # open/close/rotate and read by status RPC handlers without a lock
        # (single-reference attribute reads are atomic in CPython).
        bg_qsize = max(32, self._flush_every_n // 100)
        self._bg_queue: "queue.Queue[_FlushBatch | _BgRequest | _BgSentinel]" = (
            queue.Queue(maxsize=bg_qsize)
        )
        self._bg_thread: threading.Thread | None = None
        self._bg_thread_dead = False
        self._dropped_flush_batches = 0
        # Counter for *deferred* flush batches â€” non-force_flush calls that
        # found the bg queue full and chose to leave rows/events in the
        # in-memory deques rather than snapshot-and-drop. Exposed alongside
        # `_dropped_flush_batches` in the status payload so operators can
        # distinguish a backlog (deferred) from real data loss (dropped).
        self._deferred_flush_batches = 0
        # Monotonic timestamp of the last `hdf.flush_batch_dropped` event
        # we published. Used to rate-limit the overflow notification so a
        # sustained backlog doesn't flood the process data PUB bus.
        self._last_flush_drop_event_mono: float = 0.0
        # Same rate-limit semantics for `hdf.flush_batch_deferred`.
        self._last_flush_defer_event_mono: float = 0.0
        # Rate-limit for the `hdf.backpressure` alarm (reservoir high-water).
        self._last_backpressure_event_mono: float = 0.0
        # Reservoir high-water marks (rows). Soft = loud alarm only; hard =
        # last-resort drop-with-accounting so a genuine sustained overload
        # can never grow memory without bound. Sized off the deque cap.
        self._reservoir_soft_rows = int(self._buffer_max_messages)
        self._reservoir_hard_rows = int(self._buffer_max_messages) * 4
        self._bg_join_timeout_s = max(0.1, float(bg_join_timeout_s))
        self._active_h5_filename: str | None = None
        self._writing_active = False
        # writing_active is also published as PROCESS telemetry (signal
        # "writing_active") so an interlock can gate device reconfig RPCs
        # on whether a run is being recorded. hdf_writer has no persistent
        # ManagerClient, so we publish via the same manager.events.publish
        # path ManagerClient.publish_telemetry uses (see
        # _publish_writing_active_telemetry). Periodic so the value stays
        # fresh for the interlock's max_age check; schema advertised once.
        self._next_writing_active_publish_mono: float = 0.0
        self._writing_active_schema_advertised = False
        self._writing_active_publish_period_s = 1.0
        # The publish does a synchronous manager RPC; run it on a dedicated
        # single-worker executor so a slow/wedged manager can never stall
        # the writer's main loop (which drains sockets + enqueues flushes).
        # skip-if-inflight prevents pile-up. Bounded RPC timeout caps how
        # long a stuck publish (and thus shutdown) can take.
        self._telemetry_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="hdf-telemetry"
        )
        self._telemetry_future: Future | None = None
        self._writing_active_rpc_timeout_ms = min(self._rpc_timeout_ms, 1000)
        # Coarse RLock serialising _h5 and stream-state access between the
        # main loop (drain handlers, RPC handlers, file rotation) and the
        # bg flush thread. Held briefly by the main loop on per-message
        # processing and for the full write cycle by the bg thread.
        # Subsequent commits will narrow the lock's scope by moving more
        # state to bg-thread-exclusive ownership.
        #
        # Invariant: every site that mutates `_h5`, any dataset/group on it,
        # or any of the cached dataset handles MUST hold `_h5_lock`. The
        # `_assert_h5_locked()` helper enforces this in debug mode at the
        # top of every dataset-touching helper so future regressions surface
        # immediately instead of producing intermittent HDF corruption.
        self._h5_lock = threading.RLock()

        self._rpc_registry = self._build_rpc_registry()
        self._topic_handlers = self._build_topic_handlers()

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
        # Processes ride the device write path (device_map/datasets keyed by
        # process_id) but are a separate filter namespace — exclude them so
        # the device filter lists only real devices.
        known.difference_update(self._known_process_ids)
        return sorted(known)

    def _is_device_enabled(self, device_id: str) -> bool:
        did = self._normalize_device_id(device_id)
        return bool(did) and did not in self._disabled_devices

    def _known_processes(self) -> list[str]:
        return sorted(self._known_process_ids)

    def _is_process_enabled(self, process_id: str) -> bool:
        pid = self._normalize_device_id(process_id)
        return bool(pid) and pid not in self._disabled_processes

    def _register_known_process(self, process_id: str) -> None:
        pid = self._normalize_device_id(process_id)
        if pid:
            self._known_process_ids.add(pid)

    def _stream_buffer_snapshot(self) -> tuple[list[Json], int, int]:
        items: list[Json] = []
        total_samples = 0
        total_data_bytes = 0
        keys = set(self._stream_buffers.keys())
        keys.update(self._stream_pending_by_seq.keys())
        keys.update(self._stream_context_by_seq.keys())
        for key in sorted(keys):
            device_id, stream = key
            buf = self._stream_buffers.get(key, {})
            data_raw = buf.get("data") if isinstance(buf, dict) else []
            data_list = data_raw if isinstance(data_raw, list) else []
            sample_count = len(data_list)
            data_bytes = 0
            for payload in data_list:
                if isinstance(payload, (bytes, bytearray, memoryview)):
                    data_bytes += len(payload)
            total_samples += sample_count
            total_data_bytes += data_bytes
            pending = self._stream_pending_by_seq.get(key, {})
            pending_count = len(pending)
            context_map = self._stream_context_by_seq.get(key, {})
            context_count = len(context_map)
            if (
                sample_count <= 0
                and data_bytes <= 0
                and pending_count <= 0
                and context_count <= 0
            ):
                continue
            item: Json = {
                "device_id": device_id,
                "stream": stream,
                "buffered_samples": int(sample_count),
                "buffered_data_bytes": int(data_bytes),
            }
            if pending_count:
                item["pending_context_samples"] = int(pending_count)
            if context_count:
                item["context_map_entries"] = int(context_count)
            last_seq = self._stream_last_seq.get(key)
            if last_seq is not None:
                item["last_seq"] = int(last_seq)
            dropped_total = self._stream_dropped_total.get(key)
            if dropped_total is not None:
                item["dropped_total"] = int(dropped_total)
            session = self._stream_active_session.get(key)
            if session is not None:
                item["session"] = int(session)
            items.append(item)
        return items, total_samples, total_data_bytes

    def _stream_pending_context_count(self) -> int:
        return sum(len(items) for items in self._stream_pending_by_seq.values())

    def _stream_context_map_count(self) -> int:
        return sum(len(items) for items in self._stream_context_by_seq.values())

    def _bump_error(self, key: str) -> None:
        with self._error_counts_lock:
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

    def _fetch_process_schema_best_effort(self) -> Json | None:
        """Best-effort fetch of manager.process_telemetry.schema.list.

        Single attempt: process telemetry is optional, so a missing/empty
        result simply means no /process_telemetry datasets this file."""
        try:
            resp = _manager_rpc(
                self._ctx,
                self._manager_rpc,
                {"action": "manager.process_telemetry.schema.list"},
                timeout_ms=self._rpc_timeout_ms,
            )
        except Exception:
            self._bump_error("process_schema.rpc")
            return None
        if not isinstance(resp, dict) or not resp.get("ok"):
            return None
        result = resp.get("result")
        return result if isinstance(result, dict) else None

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
        # Device commands are routed through the manager and return the raw
        # driver-runner envelope on success: {"id", "status": "OK", "result"}.
        # Only manager-level failures (unknown_device, driver_not_running, ...)
        # use the {"ok": False, "error"} shape. Accept either success form —
        # checking only ``ok`` silently treated every successful
        # collect_run_metadata as a failure, so /run_metadata was never
        # written.
        ok = resp.get("ok")
        if ok is None:
            ok = str(resp.get("status", "")).upper() == "OK"
        if not ok:
            return None
        return resp.get("result")

    @staticmethod
    def _is_remote_config(config: Json) -> bool:
        source_kind = str(config.get("source_kind", "")).strip().lower()
        return bool(config.get("is_remote")) or source_kind == "federated"

    def _enqueue_run_metadata_capture(self, configs: list[Json]) -> None:
        """Hand run-metadata capture to the bg thread so its slow per-device
        RPCs don't stall the main SUB-drain loop during file-open. Falls back
        to inline capture when the bg thread can't take it (autostart runs
        file-open before the bg thread is spawned, or the thread has died),
        so metadata is never silently skipped."""
        req = _CaptureRunMetadataRequest(
            configs=[copy.deepcopy(c) for c in configs],
            measurement_id=self._measurement_id or "",
        )
        if self._bg_thread is None or self._bg_thread_dead:
            self._capture_run_metadata_for_configs(
                req.configs, expected_measurement_id=req.measurement_id
            )
            return
        try:
            self._bg_queue.put_nowait(req)
        except queue.Full:
            self._capture_run_metadata_for_configs(
                req.configs, expected_measurement_id=req.measurement_id
            )

    def _capture_run_metadata_for_configs(
        self,
        configs: list[Json],
        *,
        expected_measurement_id: str | None = None,
    ) -> None:
        seen: set[str] = set()
        targets: list[str] = []
        for config in configs:
            device_id = self._normalize_device_id(config.get("device_id"))
            if device_id is None or device_id in seen:
                continue
            seen.add(device_id)
            if not self._is_device_enabled(device_id):
                continue
            if self._is_remote_config(config):
                continue
            targets.append(device_id)
        if not targets:
            return

        timeout_ms = min(max(200, int(self._rpc_timeout_ms)), 1500)
        # Fan the blocking collect_run_metadata RPCs out in parallel: each
        # _manager_rpc opens its own DEALER socket on the shared thread-safe
        # zmq.Context, so this turns N*1.5s serial into ~1.5s. NOTE: this may
        # run on the bg thread (deferred) or inline (fallback) — the RPCs never
        # touch h5, so no lock is needed here.
        results: list[tuple[str, Json]] = []
        max_workers = min(8, len(targets))
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="hdf-runmeta"
        ) as pool:
            futures = {
                pool.submit(
                    self._call_optional_device_action,
                    device_id=device_id,
                    action="collect_run_metadata",
                    timeout_ms=timeout_ms,
                ): device_id
                for device_id in targets
            }
            for fut, device_id in futures.items():
                try:
                    run_metadata = fut.result()
                except Exception:
                    self._bump_error("metadata.rpc.collect_run_metadata")
                    continue
                if run_metadata is None:
                    continue
                if not isinstance(run_metadata, dict):
                    self._bump_error("run_metadata.invalid")
                    continue
                results.append((device_id, run_metadata))
        if not results:
            return

        # h5py is not thread-safe: serialise the writes under _h5_lock. Re-check
        # the active file first — a stop/rotate between enqueue and now may have
        # closed or replaced it, in which case this capture is stale.
        with self._h5_lock:
            if self._h5 is None:
                return
            if (
                expected_measurement_id is not None
                and (self._measurement_id or "") != expected_measurement_id
            ):
                self._bump_error("run_metadata.stale_skip")
                return
            for device_id, run_metadata in results:
                self._handle_run_metadata_locked(
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
        # Invoked from the RPC handler thread (main loop _drain_rpc). Held
        # under _h5_lock so the bg flush thread can't be mid-write on the
        # same h5py.File â€” h5py is not safe to share across threads even
        # for writes to distinct datasets.
        with self._h5_lock:
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
        self._assert_h5_locked()
        if self._h5 is None:
            return
        self._h5.attrs["dropped_local_messages_total"] = int(self._dropped_local)
        self._h5.attrs["dropped_event_messages_total"] = int(self._dropped_events)
        self._h5.flush()
        self._pending = 0
        self._last_flush = time.monotonic()

    # ------------------------------------------------------------------
    # Background-flush thread.
    #
    # Commit 1 only wires the thread up and handles the shutdown sentinel.
    # Commit 2 will add _FlushBatch + _BgRequest dispatch; for now any
    # other queue item raises and is logged via _record_exception.
    # ------------------------------------------------------------------

    def _start_bg_thread(self) -> None:
        if self._bg_thread is not None:
            return
        self._bg_thread_dead = False
        self._bg_thread = threading.Thread(
            target=self._bg_thread_run,
            name="hdf-bg-flush",
            daemon=True,
        )
        self._bg_thread.start()

    def _bg_thread_run(self) -> None:
        try:
            while not self._stop_evt.is_set():
                try:
                    req = self._bg_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if isinstance(req, _BgSentinel):
                    return
                try:
                    self._dispatch_bg_request(req)
                except Exception as exc:
                    self._record_exception(
                        exc, phase=f"bg.{type(req).__name__}"
                    )
                    self._bump_error(f"bg.{type(req).__name__}.failed")
                    if isinstance(req, _BgRequest):
                        try:
                            req.response.put(exc, timeout=0.1)
                        except queue.Full:
                            pass
        except Exception as exc:
            # Anything that escapes the outer loop is fatal â€” set the
            # watchdog flag and stop the process so the supervisor's
            # restart policy can take over.
            self._record_exception(exc, phase="bg_thread_fatal")
            self._bump_error("bg_thread_fatal")
            self._bg_thread_dead = True
            self._stop_evt.set()

    def _dispatch_bg_request(
        self, req: "_FlushBatch | _BgRequest"
    ) -> None:
        if isinstance(req, _FlushBatch):
            self._handle_flush_batch(req)
            return
        if isinstance(req, _CaptureRunMetadataRequest):
            # Slow per-device collect_run_metadata RPCs, run off the main
            # drain loop so file-open never stalls telemetry draining.
            self._capture_run_metadata_for_configs(
                req.configs, expected_measurement_id=req.measurement_id
            )
            return
        # Synchronous RPC request types are wired in a follow-up step;
        # for now anything else surfaces as an explicit error to the
        # caller via the response queue.
        raise NotImplementedError(
            f"bg request type not yet handled: {type(req).__name__}"
        )

    def _handle_flush_batch(self, batch: "_FlushBatch") -> None:
        # The bg thread runs this under _h5_lock â€” same lock that
        # serialises h5 + stream-state access from the main-loop drain
        # handlers, RPC handlers, and file rotation. The batch carries
        # snapshots of the deque contents and per-stream buffers the
        # main loop swapped out at handoff time, so the write helpers
        # operate on private copies and don't race with concurrent
        # main-loop appends.
        with self._h5_lock:
            if batch.context_rows:
                self._write_context_rows_batch(batch.context_rows)
            self._write_buffered_rows_batch(batch.buffered_rows)
            self._write_event_rows_batch(batch.event_rows)
            if batch.stream_batches:
                self._write_stream_buffers_batch(
                    batch.stream_batches, stream_meta=batch.stream_meta
                )
            if batch.force_flush:
                self._flush_active_file()

    def _drain_bg_queue_locked(self) -> None:
        """Write any `_FlushBatch` items still queued when the bg thread
        exited (it stops pulling as soon as `_stop_evt` is set). Runs on the
        main thread during close(), bg thread already joined, under _h5_lock.
        Non-batch items (sentinels / unused request types) are discarded."""
        self._assert_h5_locked()
        while True:
            try:
                req = self._bg_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(req, _FlushBatch):
                try:
                    self._handle_flush_batch(req)
                except Exception:
                    self._bump_error("close.bg_queue_batch")

    def _shutdown_bg_thread(self) -> None:
        thread = self._bg_thread
        if thread is None:
            return
        try:
            self._bg_queue.put(_BG_SENTINEL, timeout=1.0)
        except queue.Full:
            pass
        if thread.is_alive():
            thread.join(timeout=self._bg_join_timeout_s)
            if thread.is_alive():
                # Bg thread is stuck (likely a slow h5.close() or a wedged
                # write). Force-close the file from the main thread so we
                # don't leak the handle, then let close() proceed.
                self._bump_error("close.bg_thread_hang")
                if self._h5 is not None:
                    try:
                        self._h5.close()
                    except Exception:
                        self._bump_error("close.bg_thread_hang.h5_close")
                    self._h5 = None
                    self._publish_h5_state_cache()
        self._bg_thread = None

    def _assert_h5_locked(self) -> None:
        """Debug invariant: caller must hold `_h5_lock` when the bg thread is live.

        Called from every helper that touches `_h5`, a cached dataset
        handle, or stream-state mutated by the bg flush thread. The
        assertion only fires when the bg flush thread is actually running
        (production), because the lock's whole purpose is to mediate
        main-thread vs bg-thread access. Tests that drive HdfWriter
        directly without the bg thread are exercising single-threaded
        code paths and don't need the lock.

        Skipped in optimised (-O) runs.
        """
        if not __debug__:
            return
        bg = self._bg_thread
        if bg is None or not bg.is_alive():
            return
        if not bool(cast(Any, self._h5_lock)._is_owned()):
            raise AssertionError(
                "hdf_writer mutation site invoked without _h5_lock while "
                "the bg flush thread is live; this is a thread-safety "
                "regression â€” see the lock comment in HdfWriter.__init__"
            )

    def _drain_pending_to_file(self) -> None:
        # Synchronous drain of the LIVE reservoir, used by main-thread file
        # ops (rotate / stop / close). Callers first `_quiesce_bg_writes()`
        # (rotate/stop) or shut the bg thread down (close), so the bg thread
        # is not writing concurrently. Held under _h5_lock for the h5py work.
        with self._h5_lock:
            if self._pending_context_rows:
                self._write_context_rows_batch(self._pending_context_rows)
                self._pending_context_rows = []
            self._write_buffered_rows()
            self._write_event_rows()
            self._write_stream_buffers()
            self._flush_active_file()

    def _quiesce_bg_writes(self, *, timeout_s: float | None = None) -> None:
        """Flush the entire reservoir + bg queue to disk before a file op
        closes/rotates the file, closing the in-flight-batch loss gap.

        Snapshots the current reservoir into a final force-flush batch, then
        waits (bounded) for the bg queue to fully drain. Must be called
        WITHOUT `_h5_lock` held so the bg thread can acquire it to write.
        No-op when the bg thread isn't running (tests / pre-start) — the
        caller's subsequent `_drain_pending_to_file` handles those cases.
        """
        thread = self._bg_thread
        if thread is None or not thread.is_alive() or self._stop_evt.is_set():
            return
        budget = self._bg_join_timeout_s * 10 if timeout_s is None else max(0.0, timeout_s)
        deadline = time.monotonic() + budget
        # Push the current reservoir; retry briefly if the queue is momentarily
        # full (the bg thread is actively draining it).
        while not self._enqueue_flush_batch(force_flush=True):
            if time.monotonic() >= deadline:
                self._bump_error("bg.quiesce_enqueue_timeout")
                break
            time.sleep(0.005)
        # Wait for the bg thread to empty the queue. qsize()==0 plus the
        # caller's subsequent `_h5_lock` acquisition guarantees the last batch
        # has finished writing (acquiring the lock blocks until the bg thread
        # releases it after its current write).
        while self._bg_queue.qsize() > 0:
            if time.monotonic() >= deadline:
                self._bump_error("bg.quiesce_drain_timeout")
                break
            time.sleep(0.005)

    def _publish_h5_state_cache(self) -> None:
        """Refresh cached filename/writing_active from current `self._h5`.

        Status RPC reads use these cached fields instead of accessing
        `self._h5.filename` directly, so the status handler doesn't have
        to take `_h5_lock` (read path stays free of contention with the
        bg flush thread). CPython attribute writes/reads on a single
        reference are atomic.

        We pair the two writes under `_h5_lock` so a concurrent reader
        never sees the inconsistent intermediate state
        (`filename=None, writing_active=True` or vice versa) that would
        arise if the two `=` ran across a context-switch boundary. The
        reader path stays lock-free as documented; the lock here is
        only for writer-side atomicity, and since `_h5_lock` is an
        RLock most callers already hold it (this re-acquire is a
        no-op).
        """
        with self._h5_lock:
            h5 = self._h5
            if h5 is None:
                self._active_h5_filename = None
                self._writing_active = False
            else:
                try:
                    self._active_h5_filename = str(h5.filename)
                except Exception:
                    self._active_h5_filename = None
                self._writing_active = True

    def process_telemetry_schema(self) -> list[dict[str, Any]] | None:
        """Process telemetry signals published by the HDF writer.

        ``writing_active`` (bool) drives the PXIe reconfig interlock and is
        also recorded under ``/process_telemetry/hdf_writer``.
        """
        return [{"name": "writing_active", "dtype": "bool", "units": ""}]

    def _schedule_writing_active_publish(self) -> None:
        """Submit a writing_active publish to the telemetry executor.

        Fire-and-forget off the main loop. Skips if a prior publish is
        still in flight (e.g. a slow manager) so calls never pile up.
        """
        if not self._process_id:
            return
        fut = self._telemetry_future
        if fut is not None and not fut.done():
            return
        try:
            self._telemetry_future = self._telemetry_executor.submit(
                self._publish_writing_active_telemetry
            )
        except Exception:
            # Executor shut down (teardown) or rejected — best-effort.
            self._bump_error("telemetry.writing_active_schedule")

    def _publish_writing_active_telemetry(self) -> None:
        """Publish ``writing_active`` as process telemetry.

        Lets an interlock gate device reconfig RPCs on whether a run is
        being recorded. hdf_writer keeps no persistent ManagerClient, so
        we go through the same ``manager.events.publish`` ->
        ``manager.process_telemetry_update`` path that
        ``ManagerClient.publish_telemetry`` uses, via the ephemeral
        ``_manager_rpc`` helper. Quality is the canonical ``"OK"`` so the
        interlock resolver's quality check passes. Best-effort: a publish
        failure bumps an error counter but never disrupts writing.
        """
        if not self._process_id:
            return
        ts = {"t_wall": time.time(), "t_mono": time.monotonic()}
        try:
            if not self._writing_active_schema_advertised:
                _manager_rpc(
                    self._ctx,
                    self._manager_rpc,
                    {
                        "type": "manager.process_telemetry.schema.advertise",
                        "process_id": self._process_id,
                        "schema": self.process_telemetry_schema(),
                    },
                    timeout_ms=self._writing_active_rpc_timeout_ms,
                )
                self._writing_active_schema_advertised = True
            _manager_rpc(
                self._ctx,
                self._manager_rpc,
                {
                    "type": "manager.events.publish",
                    "topic": "manager.process_telemetry_update",
                    "payload": {
                        "process_id": self._process_id,
                        "version": 1,
                        "signals": {
                            "writing_active": {
                                "value": bool(self._writing_active),
                                "units": "",
                                "quality": "OK",
                                "ts": ts,
                            }
                        },
                        "ts": ts,
                    },
                },
                timeout_ms=self._writing_active_rpc_timeout_ms,
            )
        except Exception:
            self._bump_error("telemetry.writing_active")

    def _snapshot_main_loop_buffers(
        self,
    ) -> tuple[
        list[Json],
        list[tuple[str, Json]],
        dict[tuple[str, str], dict[str, list[Any]]],
        dict[tuple[str, str], dict[str, Any]],
        list[dict[str, Any]],
    ]:
        """Extract main-loop drain buffers + replace with fresh containers.

        Runs on the main loop thread (the only thread that touches these
        buffers), so the swap needs no lock. The returned snapshot is
        transferred to the bg thread via the queue and is never mutated by
        the main loop afterwards. It is *self-contained*: `stream_meta`
        carries the dtype/shape/session for each buffered stream so the bg
        write never reads the main-owned `_stream_schema` /
        `_stream_active_session` maps.
        """
        rows: list[Json] = []
        if self._buf is not None:
            while self._buf:
                rows.append(self._buf.popleft())

        event_rows: list[tuple[str, Json]] = []
        if self._event_buf is not None:
            while self._event_buf:
                event_rows.append(self._event_buf.popleft())

        stream_buffers = self._stream_buffers
        self._stream_buffers = {}

        stream_meta: dict[tuple[str, str], dict[str, Any]] = {}
        for key in stream_buffers:
            schema = self._stream_schema.get(key)
            if schema is None:
                continue
            stream_meta[key] = {
                "dtype": schema.get("dtype"),
                "shape": schema.get("shape"),
                "session": int(self._stream_active_session.get(key, 1)),
            }

        context_rows = self._pending_context_rows
        self._pending_context_rows = []

        return rows, event_rows, stream_buffers, stream_meta, context_rows

    def _stream_buffered_rows(self) -> int:
        total = 0
        for buf in self._stream_buffers.values():
            data = buf.get("data") if isinstance(buf, dict) else None
            if isinstance(data, list):
                total += len(data)
        return total

    def _reservoir_row_count(self) -> int:
        """Total rows currently held in the in-memory reservoirs (telemetry +
        events + stream frames + pending context rows). Drives the early-enqueue
        trigger and the backpressure alarm. Cheap; runs on the main loop."""
        total = 0
        if self._buf is not None:
            total += len(self._buf)
        if self._event_buf is not None:
            total += len(self._event_buf)
        total += self._stream_buffered_rows()
        total += len(self._pending_context_rows)
        return total

    def _check_reservoir_backpressure(self, *, now_mono: float) -> None:
        """Alarm (and, only at the hard ceiling, drop-with-accounting) when the
        reservoir grows unbounded because the bg thread can't keep up. For a
        finite burst this never fires; it exists so a genuine sustained overload
        is loud and any unavoidable loss is recorded (never silent)."""
        total = self._reservoir_row_count()
        if total < self._reservoir_soft_rows:
            return
        stream_rows = self._stream_buffered_rows()
        if now_mono - self._last_backpressure_event_mono >= 1.0:
            self._last_backpressure_event_mono = now_mono
            self._bump_error("bg.reservoir_backpressure")
            try:
                self._publish_process_event(
                    topic="hdf.backpressure",
                    payload={
                        "buffered_rows_total": int(total),
                        "buffered_stream_rows": int(stream_rows),
                        "soft_cap": int(self._reservoir_soft_rows),
                        "hard_cap": int(self._reservoir_hard_rows),
                        "queue_depth": self._bg_queue.qsize(),
                        "queue_max": self._bg_queue.maxsize,
                    },
                )
            except Exception:
                pass
        if stream_rows > self._reservoir_hard_rows:
            self._drop_oldest_stream_frames(
                target_drop=stream_rows - self._reservoir_hard_rows
            )

    def _drop_oldest_stream_frames(self, *, target_drop: int) -> None:
        """Last-resort: drop the oldest buffered stream frames, largest buffers
        first, and bump `_stream_dropped_total` so the drop is recorded in the
        file's per-stream `dropped_total` attr. Only reached above the hard
        reservoir ceiling."""
        remaining = int(target_drop)
        if remaining <= 0:
            return
        keys = sorted(
            self._stream_buffers.keys(),
            key=lambda k: len(self._stream_buffers[k].get("data", []) or []),
            reverse=True,
        )
        for key in keys:
            if remaining <= 0:
                break
            buf = self._stream_buffers.get(key)
            if not isinstance(buf, dict):
                continue
            data = buf.get("data")
            n = len(data) if isinstance(data, list) else 0
            if n <= 0:
                continue
            drop = min(remaining, n)
            for col in ("data", "seq", "t0_mono_ns", "t0_wall_ns", "context_id"):
                values = buf.get(col)
                if isinstance(values, list):
                    del values[:drop]
            self._stream_dropped_total[key] = (
                int(self._stream_dropped_total.get(key, 0)) + drop
            )
            remaining -= drop
            self._bump_error("bg.reservoir_drop")

    def _enqueue_flush_batch(self, *, force_flush: bool) -> bool:
        """Snapshot main-loop drain state and hand it to the bg flush thread.

        **Non-dropping.** If the bg queue is saturated we leave *all* data in
        the in-memory reservoirs (the deques, the stream buffers, and the
        pending context rows) and return False — nothing is discarded. The
        next call ships one larger batch once the bg thread drains a slot.
        Returns True if a batch was queued (or there was nothing to queue).

        `force_flush` only asks the bg thread to `h5.flush()` after writing;
        because deferral is now lossless it never bypasses the reservoir, so
        the old "force flush stampede drops the batch" trap is gone. The
        flush cadence self-regulates: the bg thread resets `_last_flush` in
        `_flush_active_file`, so `force_flush` stops being requested until the
        next `flush_every_s` interval elapses.
        """
        if self._bg_queue.full():
            # Reservoir stays intact; just record the backlog (rate-limited)
            # so a sustained overflow is visible without polling status.
            self._deferred_flush_batches += 1
            self._bump_error("bg.flush_batch.deferred")
            now_mono = time.monotonic()
            if now_mono - self._last_flush_defer_event_mono >= 1.0:
                self._last_flush_defer_event_mono = now_mono
                try:
                    self._publish_process_event(
                        topic="hdf.flush_batch_deferred",
                        payload={
                            "queue_depth": self._bg_queue.qsize(),
                            "queue_max": self._bg_queue.maxsize,
                            "deferred_total": self._deferred_flush_batches,
                            "buffered_rows": len(self._buf) if self._buf else 0,
                            "buffered_events": (
                                len(self._event_buf) if self._event_buf else 0
                            ),
                            "buffered_streams": len(self._stream_buffers),
                        },
                    )
                except Exception:
                    # Never let event-publish failure mask the defer or
                    # destabilise the main loop.
                    pass
            return False

        rows, event_rows, stream_buffers, stream_meta, context_rows = (
            self._snapshot_main_loop_buffers()
        )
        if (
            not rows
            and not event_rows
            and not stream_buffers
            and not context_rows
            and not force_flush
        ):
            return True
        batch = _FlushBatch(
            buffered_rows=rows,
            event_rows=event_rows,
            stream_batches=stream_buffers,
            stream_meta=stream_meta,
            context_rows=context_rows,
            force_flush=force_flush,
        )
        # Single producer (the main loop) and we checked not-full above, so
        # put_nowait cannot raise — the bg thread's get() only frees space.
        self._bg_queue.put_nowait(batch)
        return True

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
        for key in list(self._stream_pending_by_seq.keys()):
            if key[0] in disabled:
                self._stream_pending_by_seq.pop(key, None)
        for key in list(self._stream_context_by_seq.keys()):
            if key[0] in disabled:
                self._stream_context_by_seq.pop(key, None)
        for device_id in list(self._telemetry_batch_buffers.keys()):
            if device_id in disabled:
                self._telemetry_batch_buffers.pop(device_id, None)

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
            "datasets": self._datasets,
            "device_map": self._device_map,
            "stream_datasets": self._stream_datasets,
            "stream_schema": self._stream_schema,
            "stream_dropped_total": self._stream_dropped_total,
            "stream_expected_nbytes": self._stream_expected_nbytes,
            "pending_stream_metadata": self._pending_stream_metadata,
            "stream_sessions": self._stream_sessions,
            "stream_active_session": self._stream_active_session,
            "stream_context_by_seq": self._stream_context_by_seq,
            "stream_pending_by_seq": self._stream_pending_by_seq,
            "stream_last_written_seq": self._stream_last_written_seq,
            "context_resolved_exact": self._context_resolved_exact,
            "context_late_resolved": self._context_late_resolved,
            "context_written_minus1_missing": self._context_written_minus1_missing,
            "context_evicted_pending_overflow": self._context_evicted_pending_overflow,
            "context_evicted_map_overflow": self._context_evicted_map_overflow,
            "pending": self._pending,
            "last_flush": self._last_flush,
            "next_write": self._next_write,
        }

    def _restore_file_state(self, state: dict[str, Any]) -> None:
        self._h5 = state["h5"]
        self._publish_h5_state_cache()
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
        self._datasets = state["datasets"]
        self._device_map = state["device_map"]
        self._stream_datasets = state["stream_datasets"]
        self._stream_schema = state["stream_schema"]
        self._stream_dropped_total = state["stream_dropped_total"]
        self._stream_expected_nbytes = state["stream_expected_nbytes"]
        self._pending_stream_metadata = state["pending_stream_metadata"]
        self._stream_sessions = state["stream_sessions"]
        self._stream_active_session = state["stream_active_session"]
        self._stream_context_by_seq = state["stream_context_by_seq"]
        self._stream_pending_by_seq = state["stream_pending_by_seq"]
        self._stream_last_written_seq = state["stream_last_written_seq"]
        self._context_resolved_exact = int(state["context_resolved_exact"])
        self._context_late_resolved = int(state["context_late_resolved"])
        self._context_written_minus1_missing = int(
            state["context_written_minus1_missing"]
        )
        self._context_evicted_pending_overflow = int(
            state["context_evicted_pending_overflow"]
        )
        self._context_evicted_map_overflow = int(
            state["context_evicted_map_overflow"]
        )
        self._pending = int(state["pending"])
        self._last_flush = float(state["last_flush"])
        self._next_write = float(state["next_write"])

    def _reset_per_file_state(self) -> None:
        self._telemetry_group = None
        self._process_telemetry_group = None
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
        self._pending_context_rows = []
        self._datasets = {}
        self._device_map = {}
        self._stream_datasets = {}
        self._stream_schema = {}
        self._stream_dropped_total = {}
        self._stream_expected_nbytes = {}
        self._pending_stream_metadata = {}
        self._stream_sessions = {}
        self._stream_active_session = {}
        self._stream_context_by_seq = {}
        self._stream_pending_by_seq = {}
        self._stream_last_written_seq = {}
        self._context_resolved_exact = 0
        self._context_late_resolved = 0
        self._context_written_minus1_missing = 0
        self._context_evicted_pending_overflow = 0
        self._context_evicted_map_overflow = 0
        self._telemetry_batch_buffers = {}
        self._event_batch_buffer = None

    def _configure_active_file(
        self,
        h5: h5py.File,
        *,
        write_every_s: float,
        load_manager_state: bool,
        measurement_meta: Json,
    ) -> None:
        # Reassigns `self._h5` and creates/loads datasets. Callers are
        # _rotate_file, _start_writing_file, and the autostart branch of
        # run(); all three now hold `_h5_lock` (run()'s case is harmless
        # since the bg thread isn't running yet, but the lock keeps the
        # contract uniform). The debug assertion catches future regressions.
        self._assert_h5_locked()
        self._h5 = h5
        self._publish_h5_state_cache()
        self._reset_per_file_state()

        h5.attrs["timezone"] = self._timezone
        # 5: added per-row `t_wall_recv` column to telemetry datasets.
        h5.attrs["schema_version"] = 5
        h5.attrs["created_at_wall"] = time.time()
        h5.attrs["manager_rpc_endpoint"] = self._manager_rpc
        h5.attrs["manager_pub_endpoint"] = self._manager_pub
        h5.attrs["zmq_rcvhwm"] = int(self._rcvhwm)
        h5.attrs["buffer_max_messages"] = int(max(1, self._buffer_max_messages))
        h5.attrs["write_every_s"] = float(write_every_s)
        h5.attrs["drop_policy"] = str(self._drop_policy)
        h5.attrs["event_log_mode"] = str(self._event_log_mode)
        h5.attrs["context_resolve_ttl_s"] = float(self._context_resolve_ttl_s)
        h5.attrs["context_pending_max_per_stream"] = int(
            self._context_pending_max_per_stream
        )
        h5.attrs["context_map_max_per_stream"] = int(self._context_map_max_per_stream)
        h5.attrs["dropped_local_messages_total"] = 0
        h5.attrs["dropped_event_messages_total"] = 0
        h5.attrs["disabled_devices_json"] = json.dumps(sorted(self._disabled_devices))
        h5.attrs["disabled_processes_json"] = json.dumps(sorted(self._disabled_processes))

        self._telemetry_group = h5.require_group("telemetry")
        self._process_telemetry_group = h5.require_group("process_telemetry")
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

            # Process telemetry schema (manager.process_telemetry.schema.list):
            # best-effort, merged into the same datasets/device_map keyed by
            # process_id but written into the /process_telemetry group.
            if self._process_telemetry_group is not None:
                proc_schema = self._fetch_process_schema_best_effort()
                if proc_schema is not None:
                    seen_processes = _ingest_process_schema(
                        proc_schema,
                        self._process_telemetry_group,
                        self._datasets,
                        self._device_map,
                        write_enabled=self._is_process_enabled,
                    )
                    self._known_process_ids.update(seen_processes)

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
                # Already under _h5_lock (via _configure_active_file); call the
                # locked impl directly to avoid a redundant re-acquire.
                self._handle_device_config_locked(config, cache=False)
            self._enqueue_run_metadata_capture(configs)

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
        # Reassigning `self._h5` and configuring the new file is guarded by
        # `_h5_lock` so the bg flush thread can't observe a half-built file
        # mid-write, and so concurrent attrs/dataset writes on the old
        # handle can't race the close. Held across the full setup +
        # old-handle teardown so the swap is atomic from observers'
        # perspective.
        if self._h5 is None:
            new_file = self._start_writing_file(
                filename=filename,
                disabled_devices=disabled_devices,
                measurement_profile=measurement_profile,
                measurement_values=measurement_values,
            )
            return None, new_file

        # Drain the reservoir + bg queue into the OLD file before rotating, so
        # no in-flight batch is written to the new file or lost.
        self._quiesce_bg_writes()

        with self._h5_lock:
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
            new_path_str = str(new_h5.filename)
            try:
                self._configure_active_file(
                    new_h5,
                    write_every_s=max(0.1, float(self._write_every_s)),
                    load_manager_state=bool(self._process_id),
                    measurement_meta=measurement_meta,
                )
                self._clear_buffered_for_disabled(self._disabled_devices)
            except Exception:
                # _configure_active_file failed after we created the new
                # h5py.File: close the handle AND delete the truncated
                # file from disk so a same-name rotate next round isn't
                # blocked by `_ensure_output_path_unused`.
                try:
                    new_h5.close()
                except Exception:
                    self._bump_error("rotate.close_new")
                try:
                    Path(new_path_str).unlink(missing_ok=True)
                except Exception:
                    self._bump_error("rotate.unlink_new")
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

            return old_file, new_path_str

    def _start_writing_file(
        self,
        *,
        filename: str | None,
        disabled_devices: set[str] | None = None,
        measurement_profile: str | None = None,
        measurement_values: object = None,
    ) -> str:
        # _configure_active_file assigns `self._h5` and creates datasets;
        # held under `_h5_lock` so the bg flush thread can't observe a
        # half-built file. The lock is held across new-file creation +
        # configuration so the swap is atomic.
        with self._h5_lock:
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
            file_path_str = str(h5.filename)
            try:
                self._configure_active_file(
                    h5,
                    write_every_s=max(0.1, float(self._write_every_s)),
                    load_manager_state=bool(self._process_id),
                    measurement_meta=measurement_meta,
                )
                self._clear_buffered_for_disabled(self._disabled_devices)
            except Exception:
                # `_configure_active_file` assigns `self._h5` before the work
                # that can fail, so a failed start would otherwise leave the
                # writer wedged: `self._h5` points at a (doomed) handle, so the
                # next start is rejected with "already writing" while status
                # reports not-writing. Reset writer state so a retry works.
                self._h5 = None
                self._publish_h5_state_cache()
                self._reset_per_file_state()
                try:
                    h5.close()
                except Exception:
                    self._bump_error("start_writing.close_new")
                # Delete the just-created file from disk so a same-name
                # start next round isn't blocked by ensure_output_path_unused.
                try:
                    Path(file_path_str).unlink(missing_ok=True)
                except Exception:
                    self._bump_error("start_writing.unlink_new")
                raise

            return file_path_str

    def _stop_writing_file(self) -> str | None:
        # The `self._h5 = None` reassignment and the surrounding flush +
        # close are guarded by `_h5_lock` so the bg flush thread can't
        # interleave a write between flush and close, and so observers
        # (status RPC, etc.) see a coherent file-active state.
        #
        # Quiesce the bg thread FIRST (outside the lock) so every buffered +
        # in-flight batch lands in this file before it closes — this is the
        # "stop finishes writing the buffer before stopping" guarantee.
        self._quiesce_bg_writes()
        with self._h5_lock:
            h5 = self._h5
            if h5 is None:
                return None
            file_path = str(h5.filename)
            errors: list[str] = []
            try:
                self._drain_pending_to_file()
            except Exception as e:
                self._bump_error("stop_writing.drain")
                errors.append(f"drain_pending failed: {e}")
            try:
                self._mark_active_measurement_ended()
            except Exception as e:
                self._bump_error("stop_writing.measurement_end")
                errors.append(f"mark_measurement_ended failed: {e}")
            try:
                h5.flush()
            except Exception as e:
                self._bump_error("stop_writing.flush")
                errors.append(f"flush failed: {e}")
            try:
                h5.close()
            except Exception as e:
                self._bump_error("stop_writing.close")
                errors.append(f"close failed: {e}")
            self._h5 = None
            self._publish_h5_state_cache()
            self._reset_per_file_state()
            self._pending = 0
            now = time.monotonic()
            self._last_flush = now
            self._next_write = now + max(0.1, float(self._write_every_s))
            if errors:
                raise RuntimeError("; ".join(errors))
            return file_path

    def close(self) -> None:
        # Best-effort: while the bg thread is still running (close() called
        # directly, not via run()'s finally), push the reservoir through and
        # let it drain. No-op if _stop_evt is already set (run() teardown) —
        # the post-shutdown main-thread drain below is the reliable backstop.
        try:
            self._quiesce_bg_writes()
        except Exception:
            self._bump_error("close.quiesce")

        self._stop_evt.set()

        # Shut down the bg flush thread BEFORE tearing down sockets or h5
        # so a final flush attempt has a chance to land. The shutdown
        # helper is a no-op if the thread was never started.
        self._shutdown_bg_thread()

        # Stop the telemetry executor before super().close() terminates the
        # zmq context — an in-flight publish holds a socket on self._ctx and
        # term() would block on it. A wedged publish is bounded by the short
        # writing-active RPC timeout. Guarded for object.__new__ test instances.
        executor = getattr(self, "_telemetry_executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=True)
            except Exception:
                self._bump_error("close.telemetry_executor")

        # HdfWriter-specific resources that the base class doesn't know
        # about. The SUB socket and any stream-data readers must be torn
        # down before super().close() terminates the zmq context.
        for reader in list(self._stream_readers.values()):
            try:
                reader.close()
            except Exception:
                self._bump_error("close.reader")
        self._stream_readers.clear()

        if self._sub is not None:
            try:
                self._sub.setsockopt(zmq.LINGER, 0)
            except Exception:
                self._bump_error("close.socket_setopt")
            try:
                self._sub.close(0)
            except Exception:
                self._bump_error("close.socket")
            self._sub = None

        # Delegate manager client + heartbeat thread/pub + rpc router
        # teardown to the base class. Previously this method open-coded
        # all of that EXCEPT _manager.close(), leaking the ManagerClient's
        # DEALER + SUB sockets on every shutdown.
        try:
            super().close()
        except Exception:
            self._bump_error("close.super")

        try:
            self._ctx.term()
        except Exception:
            self._bump_error("close.ctx")

        # File teardown happens last and under _h5_lock so a late bg
        # thread (e.g. one that survived _shutdown_bg_thread's timeout)
        # can't race the h5.close().
        with self._h5_lock:
            h5 = self._h5
            if h5 is not None:
                # The bg thread has stopped; write anything it left behind so
                # shutdown is lossless: (1) batches still sitting in the bg
                # queue when it exited, then (2) the live reservoir.
                try:
                    self._drain_bg_queue_locked()
                except Exception:
                    self._bump_error("close.bg_queue_drain")
                try:
                    self._drain_pending_to_file()
                except Exception:
                    self._bump_error("close.drain")
                self._mark_active_measurement_ended()
            self._h5 = None
            self._publish_h5_state_cache()
            if h5 is not None:
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
                    # Bg thread isn't started yet so the race isn't live,
                    # but take the lock anyway so `_configure_active_file`'s
                    # debug invariant is satisfied and the contract is
                    # uniform across call sites.
                    with self._h5_lock:
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
                    self._publish_h5_state_cache()
                    self._reset_per_file_state()
                    self._pending = 0
                    now = time.monotonic()
                    self._last_flush = now
                    self._next_write = now + write_every_s
            else:
                self._h5 = None
                self._publish_h5_state_cache()
                self._reset_per_file_state()
                self._pending = 0
                now = time.monotonic()
                self._last_flush = now
                self._next_write = now + write_every_s
            self._start_heartbeat_thread()
            self._start_bg_thread()
            if self._process_id:
                try:
                    self._init_rpc_router()
                    if self._rpc_endpoint is not None:
                        _manager_rpc(
                            self._ctx,
                            self._manager_rpc,
                            {
                                "type": "manager.processes.rpc.advertise",
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
                sub.setsockopt(zmq.SUBSCRIBE, b"manager.process_telemetry_update")
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

                # Register the local sub socket on the shared base-class poller.
                # `include_sub=False` because we connect our own SUB (the manager
                # helper isn't used here); the RPC router is auto-drained by
                # `_poll_and_drain`.
                self._init_poller(
                    include_sub=False,
                    include_rpc=True,
                    extra=[(sub, zmq.POLLIN)],
                )

                while not self._stop_evt.is_set():
                    # Bail out cleanly if the bg flush thread died â€” the
                    # supervisor's restart policy will bring us back up.
                    if self._bg_thread_dead:
                        self._stop_evt.set()
                        break

                    now = time.monotonic()
                    timeout_s = min(
                        self._next_write - now,
                        self._next_writing_active_publish_mono - now,
                    )
                    timeout_ms = int(max(0.0, timeout_s) * 1000)
                    events = self._poll_and_drain(timeout_ms)

                    if events.get(sub) == zmq.POLLIN:
                        # Drain messages from ZMQ into the in-memory reservoirs.
                        # LOCK-FREE: the high-frequency drain path (telemetry /
                        # events / chunk_ready) touches only main-owned Python
                        # state and never h5py, so it can run while the bg
                        # thread holds _h5_lock for a slow write — this is what
                        # keeps the SUB socket drained and prevents ZMQ HWM
                        # drops. Low-frequency handlers that DO write h5py
                        # (device_config / run_metadata / sequencer) take
                        # _h5_lock internally.
                        self._drain_socket()

                    # Context-buffer housekeeping (pure in-memory) moved off the
                    # bg write onto the main thread. Runs every iteration (not
                    # just when messages arrived) so pending-context TTL expiry
                    # still progresses during idle gaps.
                    self._sweep_context_buffers(now_mono=time.monotonic())

                    now = time.monotonic()
                    # Enqueue on the write cadence, or early if the reservoir is
                    # filling faster than that (keeps memory bounded under load).
                    # `force_flush` is purely the fsync-cadence flag; deferral is
                    # lossless so it never drops (no force-flush trap).
                    buffered = self._reservoir_row_count()
                    want_flush = (now - self._last_flush) >= flush_every_s
                    if now >= self._next_write or buffered >= flush_every_n:
                        self._enqueue_flush_batch(force_flush=want_flush)
                        self._next_write = now + write_every_s
                        self._check_reservoir_backpressure(now_mono=now)

                    # Periodically publish writing_active so the interlock's
                    # max_age freshness check holds while idle (fresh false
                    # -> allow reconfig) and trips quickly once recording
                    # starts (fresh true -> block).
                    if now >= self._next_writing_active_publish_mono:
                        self._schedule_writing_active_publish()
                        self._next_writing_active_publish_mono = (
                            now + self._writing_active_publish_period_s
                        )
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
        self._assert_h5_locked()
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
        self._assert_h5_locked()
        if self._sequencer_yaml_ds is None:
            return -1, "sequencer yaml dataset unavailable"
        try:
            resp = _manager_rpc(
                self._ctx,
                self._manager_rpc,
                {
                    "type": "manager.processes.rpc",
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
        # Low-frequency handler from the lock-free main drain; writes h5py
        # (sequencer events + yaml snapshot) so it takes _h5_lock.
        with self._h5_lock:
            self._handle_sequencer_lifecycle_locked(msg)

    def _handle_sequencer_lifecycle_locked(self, msg: Json) -> None:
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
        handlers = self._ensure_topic_handlers()
        while True:
            try:
                topic_b, payload_b = self._sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break

            topic = topic_b.decode("utf-8", errors="replace")
            msg = safe_json_loads(payload_b)
            if not isinstance(msg, dict):
                continue
            handler = handlers.get(topic)
            if handler is None:
                continue
            handler(msg)

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

    def _append_dataset_rows(
        self,
        *,
        ds: h5py.Dataset,
        batch: np.ndarray[Any, Any],
        count: int,
    ) -> None:
        self._assert_h5_locked()
        n = int(count)
        if n <= 0:
            return
        old = int(ds.shape[0])
        ds.resize((old + n,))
        ds[old : old + n] = batch[:n]
        self._pending += n

    def _ensure_telemetry_batch_buffer(
        self,
        *,
        device_id: str,
        dtype: np.dtype[Any],
    ) -> np.ndarray[Any, Any]:
        batch = self._telemetry_batch_buffers.get(device_id)
        if (
            batch is None
            or batch.shape[0] != self._telemetry_batch_rows
            or batch.dtype != dtype
        ):
            batch = np.zeros(self._telemetry_batch_rows, dtype=dtype)
            self._telemetry_batch_buffers[device_id] = batch
        return batch

    def _flush_telemetry_batch(
        self,
        *,
        device_id: str,
        ds: h5py.Dataset,
        used: int,
    ) -> None:
        batch = self._telemetry_batch_buffers.get(device_id)
        if batch is None:
            return
        self._append_dataset_rows(ds=ds, batch=batch, count=used)

    def _prune_telemetry_batch_buffers(self) -> None:
        for device_id in list(self._telemetry_batch_buffers.keys()):
            if device_id not in self._datasets:
                self._telemetry_batch_buffers.pop(device_id, None)

    def _ensure_event_batch_buffer(self, *, dtype: np.dtype[Any]) -> np.ndarray[Any, Any]:
        batch = self._event_batch_buffer
        if (
            batch is None
            or batch.shape[0] != self._event_batch_rows
            or batch.dtype != dtype
        ):
            batch = np.zeros(self._event_batch_rows, dtype=dtype)
            self._event_batch_buffer = batch
        return batch

    def _write_event_rows(self) -> None:
        if self._event_buf is None:
            return
        rows: list[tuple[str, Json]] = []
        while self._event_buf:
            rows.append(self._event_buf.popleft())
        self._write_event_rows_batch(rows)

    def _write_event_rows_batch(self, rows: list[tuple[str, Json]]) -> None:
        self._assert_h5_locked()
        if self._h5 is None or self._events_ds is None:
            return

        batch = self._ensure_event_batch_buffer(dtype=self._events_ds.dtype)
        used = 0

        def _flush() -> None:
            nonlocal used
            if used <= 0:
                return
            self._append_dataset_rows(ds=self._events_ds, batch=batch, count=used)
            used = 0

        for topic, msg in rows:
            ts = msg.get("ts", {})
            t_wall = float(ts.get("t_wall", np.nan))
            t_mono = float(ts.get("t_mono", np.nan))

            if topic == "manager.command":
                device_id = self._normalize_device_id(msg.get("device_id"))
                if device_id is None or not self._is_device_enabled(device_id):
                    continue
                if used >= batch.shape[0]:
                    _flush()
                row = batch[used]
                row["t_wall"] = t_wall
                row["t_mono"] = t_mono
                row["kind"] = "command"
                row["severity"] = "info"
                row["device_id"] = device_id
                row["action"] = str(msg.get("action", ""))
                row["params_json"] = str(msg.get("params_json", ""))
                row["ok"] = bool(msg.get("ok", False))
                row["error"] = str(msg.get("error", "") or "")
                row["result_json"] = str(msg.get("result_json", ""))
                row["topic"] = topic
                row["message"] = ""
                row["payload_json"] = ""
                used += 1
            elif topic == "manager.log":
                if used >= batch.shape[0]:
                    _flush()
                row = batch[used]
                row["t_wall"] = t_wall
                row["t_mono"] = t_mono
                row["kind"] = "event"
                row["severity"] = str(msg.get("severity", ""))
                row["device_id"] = str(msg.get("device_id", "") or "")
                row["action"] = ""
                row["params_json"] = ""
                row["ok"] = False
                row["error"] = str(msg.get("error", "") or "")
                row["result_json"] = ""
                row["topic"] = str(msg.get("topic", "") or "")
                row["message"] = str(msg.get("message", "") or "")
                row["payload_json"] = str(msg.get("payload_json", "") or "")
                used += 1
        _flush()

    def _build_rpc_registry(self) -> RpcDispatchRegistry:
        return RpcDispatchRegistry(
            handlers={
                "hdf.status": self._rpc_hdf_status,
                "hdf.writing.start": self._rpc_hdf_writing_start,
                "hdf.writing.stop": self._rpc_hdf_writing_stop,
                "hdf.measurement.schema.get": self._rpc_hdf_measurement_schema_get,
                "hdf.measurement.note": self._rpc_hdf_measurement_note,
                "hdf.devices.get": self._rpc_hdf_devices_get,
                "hdf.devices.disable": self._rpc_hdf_devices_disable,
                "hdf.devices.enable": self._rpc_hdf_devices_enable,
                "hdf.processes.get": self._rpc_hdf_processes_get,
                "hdf.processes.disable": self._rpc_hdf_processes_disable,
                "hdf.processes.enable": self._rpc_hdf_processes_enable,
                "hdf.rotate": self._rpc_hdf_rotate,
            },
            aliases={
                "hdf.get_status": "hdf.status",
                "hdf.get_measurement_schema": "hdf.measurement.schema.get",
                "hdf.add_measurement_note": "hdf.measurement.note",
                "hdf.get_devices": "hdf.devices.get",
                "hdf.disable_devices": "hdf.devices.disable",
                "hdf.enable_devices": "hdf.devices.enable",
                "hdf.get_processes": "hdf.processes.get",
                "hdf.disable_processes": "hdf.processes.disable",
                "hdf.enable_processes": "hdf.processes.enable",
                "hdf.rotate_file": "hdf.rotate",
            },
        )

    def _build_topic_handlers(self) -> dict[str, Callable[[Json], None]]:
        return build_hdf_topic_handlers(self)

    def _ensure_topic_handlers(self) -> dict[str, Callable[[Json], None]]:
        handlers = getattr(self, "_topic_handlers", None)
        if isinstance(handlers, dict):
            return handlers
        handlers = self._build_topic_handlers()
        self._topic_handlers = handlers
        return handlers

    # ``rpc_ok`` is inherited verbatim from ``ManagedProcessBase``;
    # the prior explicit override duplicated the base body, narrowed
    # ``_rpc_request_id`` to dict-only (an LSP violation against the
    # base's polymorphic ``dict | Any`` accept), and was patched by no
    # test. Inheriting keeps the wire envelope under a single source
    # of truth so any future change to ``rpc_ok`` applies to
    # HdfWriter automatically.

    def _rpc_error(self, req: Json, *, code: str, message: str | None = None) -> Json:
        err: Json = {"code": str(code)}
        if message is not None:
            err["message"] = str(message)
        return {
            "request_id": self._rpc_request_id(req),
            "ok": False,
            "error": err,
        }

    def _hdf_capability_members(self) -> list[MemberSpec]:
        members: list[MemberSpec] = [
            method("hdf.status", params=None, doc="Get writer status."),
            method(
                "hdf.writing.start",
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
                doc="Start writing to a new file when currently inactive.",
            ),
            method(
                "hdf.writing.stop",
                params=None,
                doc="Stop writing and close the active file while keeping process alive.",
            ),
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
                "hdf.processes.get", params=None, doc="Get HDF process write filter."
            ),
            method(
                "hdf.processes.disable",
                params=[
                    param(
                        "process_ids",
                        required=True,
                        default=None,
                        annotation="list[str]",
                    ),
                ],
                doc="Disable writing for process telemetry process_ids.",
            ),
            method(
                "hdf.processes.enable",
                params=[
                    param(
                        "process_ids",
                        required=True,
                        default=None,
                        annotation="list[str]",
                    ),
                ],
                doc="Enable writing for process telemetry process_ids.",
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
        ]
        if self._measurement_schema_path is not None:
            members.extend(
                [
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
            )
        return self._with_common_capabilities(members)

    def _hdf_device_filter_state(self) -> Json:
        known = self._known_devices()
        disabled = sorted(self._disabled_devices)
        disabled_set = set(disabled)
        enabled_known = [did for did in known if did not in disabled_set]
        return {
            "disabled_devices": disabled,
            "known_devices": known,
            "enabled_known_devices": enabled_known,
        }

    def _hdf_process_filter_state(self) -> Json:
        known = self._known_processes()
        disabled = sorted(self._disabled_processes)
        disabled_set = set(disabled)
        enabled_known = [pid for pid in known if pid not in disabled_set]
        return {
            "disabled_processes": disabled,
            "known_processes": known,
            "enabled_known_processes": enabled_known,
        }

    def _rpc_hdf_status(self, req: Json) -> Json:
        schema_configured, schema_available, schema_error = self._measurement_schema_state()
        stream_buffered, stream_buffered_samples, stream_buffered_data_bytes = (
            self._stream_buffer_snapshot()
        )
        telemetry_buffer_depth = len(self._buf) if self._buf is not None else 0
        telemetry_buffer_capacity = (
            int(self._buf.maxlen)
            if self._buf is not None and self._buf.maxlen is not None
            else None
        )
        event_buffer_depth = len(self._event_buf) if self._event_buf is not None else 0
        event_buffer_capacity = (
            int(self._event_buf.maxlen)
            if self._event_buf is not None and self._event_buf.maxlen is not None
            else None
        )
        result = {
            # Read cached state (refreshed by bg thread / file rotation
            # via _publish_h5_state_cache) instead of `self._h5.filename`
            # so this RPC handler doesn't need to take _h5_lock while the
            # bg thread is in the middle of a write.
            "file": self._active_h5_filename,
            "writing_active": bool(self._writing_active),
            "autostart_writing": bool(self._autostart_writing),
            "pending": int(self._pending),
            "dropped": int(self._dropped_local),
            "dropped_by_topic": dict(self._dropped_local_by_topic),
            "dropped_events": int(self._dropped_events),
            "telemetry_buffer_depth": int(telemetry_buffer_depth),
            "telemetry_buffer_capacity": telemetry_buffer_capacity,
            "event_buffer_depth": int(event_buffer_depth),
            "event_buffer_capacity": event_buffer_capacity,
            "stream_buffered_streams": int(len(stream_buffered)),
            "stream_buffered_samples": int(stream_buffered_samples),
            "stream_buffered_data_bytes": int(stream_buffered_data_bytes),
            "stream_buffered": stream_buffered,
            "stream_pending_context_samples": int(self._stream_pending_context_count()),
            "stream_context_map_entries": int(self._stream_context_map_count()),
            "stream_context_entries": int(self._stream_context_map_count()),
            "context_resolved_exact": int(self._context_resolved_exact),
            "context_late_resolved": int(self._context_late_resolved),
            "context_written_minus1_missing": int(self._context_written_minus1_missing),
            "context_evicted_pending_overflow": int(
                self._context_evicted_pending_overflow
            ),
            "context_evicted_map_overflow": int(self._context_evicted_map_overflow),
            "seen_context_ids_count": int(len(self._seen_context_ids)),
            "bg_thread": {
                "alive": self._bg_thread is not None and self._bg_thread.is_alive(),
                "dead": bool(self._bg_thread_dead),
                "queue_depth": self._bg_queue.qsize(),
                "queue_capacity": self._bg_queue.maxsize,
            },
            "dropped_flush_batches": int(self._dropped_flush_batches),
            "deferred_flush_batches": int(self._deferred_flush_batches),
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
        result.update(self._hdf_device_filter_state())
        result.update(self._hdf_process_filter_state())
        return self.rpc_ok(req, result=result)

    def _rpc_hdf_measurement_schema_get(self, req: Json) -> Json:
        configured, available, error = self._measurement_schema_state()
        if not configured:
            return self._rpc_error(
                req,
                code="measurement_schema_not_configured",
                message="measurement schema path is not configured",
            )
        if not available or self._measurement_schema is None:
            return self._rpc_error(
                req,
                code="measurement_schema_unavailable",
                message=error or "measurement schema unavailable",
            )
        return self.rpc_ok(
            req,
            result={
                "schema": measurement_schema_to_json(self._measurement_schema),
                "path": self._measurement_schema_source or self._measurement_schema_path,
            },
        )

    def _rpc_hdf_measurement_note(self, req: Json) -> Json:
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._rpc_error(
                req, code="invalid_params", message="params must be a dict"
            )
        configured, available, error = self._measurement_schema_state()
        if not configured:
            return self._rpc_error(
                req,
                code="measurement_schema_not_configured",
                message="measurement schema path is not configured",
            )
        if not available or self._measurement_schema is None:
            return self._rpc_error(
                req,
                code="measurement_schema_unavailable",
                message=error or "measurement schema unavailable",
            )
        try:
            core, payload = normalize_measurement_note_values(
                self._measurement_schema,
                values=params,
            )
        except Exception as e:
            return self._rpc_error(req, code="invalid_params", message=str(e))
        try:
            payload_json = json.dumps(payload)
        except Exception as e:
            return self._rpc_error(req, code="invalid_params", message=str(e))
        try:
            index, t_wall, t_mono = self._append_measurement_note_row(
                author=str(core.get("author", "")),
                kind=str(core.get("kind", "note")),
                message=str(core.get("message", "")),
                payload_json=payload_json,
            )
        except Exception as e:
            return self._rpc_error(req, code="note_write_failed", message=str(e))
        return self.rpc_ok(
            req,
            result={
                "index": int(index),
                "t_wall": float(t_wall),
                "t_mono": float(t_mono),
                "author": str(core.get("author", "")),
                "kind": str(core.get("kind", "note")),
            },
        )

    def _rpc_hdf_devices_get(self, req: Json) -> Json:
        return self.rpc_ok(req, result=self._hdf_device_filter_state())

    def _rpc_hdf_devices_disable(self, req: Json) -> Json:
        return self._rpc_hdf_devices_toggle(req, disable=True)

    def _rpc_hdf_devices_enable(self, req: Json) -> Json:
        return self._rpc_hdf_devices_toggle(req, disable=False)

    def _rpc_hdf_devices_toggle(self, req: Json, *, disable: bool) -> Json:
        params = req.get("params", {})
        if not isinstance(params, dict):
            return self._rpc_error(req, code="invalid_params", message="params must be a dict")
        try:
            ids = self._normalize_device_id_list(params.get("device_ids"))
        except Exception as e:
            return self._rpc_error(req, code="invalid_params", message=str(e))
        if not ids:
            return self._rpc_error(
                req,
                code="invalid_params",
                message="device_ids must contain at least one id",
            )

        known = set(self._known_devices())
        unknown = sorted([did for did in ids if did not in known])
        changed: list[str] = []
        if disable:
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
            # RPC handler runs on the main thread; the bg flush thread
            # holds _h5_lock while writing. Take the lock so attrs[]
            # assignment can't interleave with a concurrent flush.
            with self._h5_lock:
                if self._h5 is not None:
                    try:
                        self._h5.attrs["disabled_devices_json"] = json.dumps(
                            sorted(self._disabled_devices)
                        )
                    except Exception:
                        self._bump_error("h5.attrs.disabled_devices")

        return self.rpc_ok(
            req,
            result={
                "changed": changed,
                "unknown": unknown,
                **self._hdf_device_filter_state(),
            },
        )

    def _rpc_hdf_processes_get(self, req: Json) -> Json:
        return self.rpc_ok(req, result=self._hdf_process_filter_state())

    def _rpc_hdf_processes_disable(self, req: Json) -> Json:
        return self._rpc_hdf_processes_toggle(req, disable=True)

    def _rpc_hdf_processes_enable(self, req: Json) -> Json:
        return self._rpc_hdf_processes_toggle(req, disable=False)

    def _rpc_hdf_processes_toggle(self, req: Json, *, disable: bool) -> Json:
        params = req.get("params", {})
        if not isinstance(params, dict):
            return self._rpc_error(req, code="invalid_params", message="params must be a dict")
        try:
            ids = self._normalize_device_id_list(params.get("process_ids"))
        except Exception as e:
            return self._rpc_error(req, code="invalid_params", message=str(e))
        if not ids:
            return self._rpc_error(
                req,
                code="invalid_params",
                message="process_ids must contain at least one id",
            )

        known = set(self._known_processes())
        unknown = sorted([pid for pid in ids if pid not in known])
        changed: list[str] = []
        if disable:
            for pid in ids:
                if pid not in self._disabled_processes:
                    self._disabled_processes.add(pid)
                    changed.append(pid)
            if changed:
                # Buffered process telemetry rows carry device_id=process_id.
                self._clear_buffered_for_disabled(set(changed))
        else:
            for pid in ids:
                if pid in self._disabled_processes:
                    self._disabled_processes.remove(pid)
                    changed.append(pid)
            # Re-ingest the process schema so re-enabled processes get their
            # /process_telemetry datasets recreated (mirrors device enable).
            if changed and self._process_telemetry_group is not None:
                proc_schema = self._fetch_process_schema_best_effort()
                if proc_schema is not None:
                    with self._h5_lock:
                        if self._process_telemetry_group is not None:
                            seen = _ingest_process_schema(
                                proc_schema,
                                self._process_telemetry_group,
                                self._datasets,
                                self._device_map,
                                write_enabled=self._is_process_enabled,
                            )
                            self._known_process_ids.update(seen)

        if self._h5 is not None:
            with self._h5_lock:
                if self._h5 is not None:
                    try:
                        self._h5.attrs["disabled_processes_json"] = json.dumps(
                            sorted(self._disabled_processes)
                        )
                    except Exception:
                        self._bump_error("h5.attrs.disabled_processes")

        return self.rpc_ok(
            req,
            result={
                "changed": changed,
                "unknown": unknown,
                **self._hdf_process_filter_state(),
            },
        )

    def _rpc_hdf_rotate(self, req: Json) -> Json:
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._rpc_error(req, code="invalid_params", message="params must be a dict")

        filename_raw = params.get("filename")
        filename: str | None = None
        if filename_raw is not None:
            filename = str(filename_raw).strip()
            if not filename:
                return self._rpc_error(
                    req,
                    code="invalid_params",
                    message="filename must be a non-empty string",
                )

        disabled_override: set[str] | None = None
        unknown: list[str] = []
        if "disabled_devices" in params:
            try:
                disabled_override = self._normalize_device_ids(
                    params.get("disabled_devices")
                )
            except Exception as e:
                return self._rpc_error(req, code="invalid_params", message=str(e))
            known = set(self._known_devices())
            unknown = sorted([did for did in disabled_override if did not in known])

        measurement_profile: str | None = None
        if "measurement_profile" in params:
            raw_profile = params.get("measurement_profile")
            if raw_profile is not None:
                profile_text = str(raw_profile).strip()
                if not profile_text:
                    return self._rpc_error(
                        req,
                        code="invalid_params",
                        message="measurement_profile must be a non-empty string",
                    )
                measurement_profile = profile_text

        measurement_values: object = params.get("measurement_values", {})
        if measurement_values is None:
            measurement_values = {}
        if not isinstance(measurement_values, dict):
            return self._rpc_error(
                req,
                code="invalid_params",
                message="measurement_values must be a dict",
            )

        try:
            old_file, new_file = self._rotate_file(
                filename=filename,
                disabled_devices=disabled_override,
                measurement_profile=measurement_profile,
                measurement_values=measurement_values,
            )
        except FileExistsError as e:
            return self._rpc_error(req, code="file_exists", message=str(e))
        except Exception as e:
            return self._rpc_error(req, code="rotate_failed", message=str(e))

        return self.rpc_ok(
            req,
            result={
                "old_file": old_file,
                "new_file": new_file,
                "measurement_id": self._measurement_id,
                "measurement_type": self._measurement_type,
                "unknown": unknown,
                **self._hdf_device_filter_state(),
            },
        )

    def _rpc_hdf_writing_stop(self, req: Json) -> Json:
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._rpc_error(req, code="invalid_params", message="params must be a dict")
        if self._h5 is None:
            return self.rpc_ok(
                req,
                result={
                    "already_stopped": True,
                    "old_file": None,
                    **self._hdf_device_filter_state(),
                },
            )
        try:
            old_file = self._stop_writing_file()
        except Exception as e:
            return self._rpc_error(req, code="stop_failed", message=str(e))
        return self.rpc_ok(
            req,
            result={
                "already_stopped": False,
                "old_file": old_file,
                **self._hdf_device_filter_state(),
            },
        )

    def _rpc_hdf_writing_start(self, req: Json) -> Json:
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._rpc_error(req, code="invalid_params", message="params must be a dict")
        if self._h5 is not None:
            return self._rpc_error(
                req, code="already_writing", message="HDF writer is already writing"
            )

        filename_raw = params.get("filename")
        filename: str | None = None
        if filename_raw is not None:
            filename = str(filename_raw).strip()
            if not filename:
                return self._rpc_error(
                    req,
                    code="invalid_params",
                    message="filename must be a non-empty string",
                )

        disabled_override: set[str] | None = None
        unknown: list[str] = []
        if "disabled_devices" in params:
            try:
                disabled_override = self._normalize_device_ids(
                    params.get("disabled_devices")
                )
            except Exception as e:
                return self._rpc_error(req, code="invalid_params", message=str(e))
            known = set(self._known_devices())
            unknown = sorted([did for did in disabled_override if did not in known])

        measurement_profile: str | None = None
        if "measurement_profile" in params:
            raw_profile = params.get("measurement_profile")
            if raw_profile is not None:
                profile_text = str(raw_profile).strip()
                if not profile_text:
                    return self._rpc_error(
                        req,
                        code="invalid_params",
                        message="measurement_profile must be a non-empty string",
                    )
                measurement_profile = profile_text

        measurement_values: object = params.get("measurement_values", {})
        if measurement_values is None:
            measurement_values = {}
        if not isinstance(measurement_values, dict):
            return self._rpc_error(
                req,
                code="invalid_params",
                message="measurement_values must be a dict",
            )

        try:
            new_file = self._start_writing_file(
                filename=filename,
                disabled_devices=disabled_override,
                measurement_profile=measurement_profile,
                measurement_values=measurement_values,
            )
        except FileExistsError as e:
            return self._rpc_error(req, code="file_exists", message=str(e))
        except Exception as e:
            return self._rpc_error(req, code="start_failed", message=str(e))

        return self.rpc_ok(
            req,
            result={
                "new_file": new_file,
                "measurement_id": self._measurement_id,
                "measurement_type": self._measurement_type,
                "unknown": unknown,
                **self._hdf_device_filter_state(),
            },
        )

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common

        rpc = RpcActionRequest.parse(
            req,
            action_field="type",
            request_id_field="request_id",
        )
        if rpc is None:
            return self._rpc_error(req, code="invalid_request", message="Malformed request")
        dispatch_req = rpc.as_dispatch_payload(request_id_field="request_id")
        if rpc.action == "process.capabilities":
            return self.rpc_ok(
                dispatch_req,
                result=capabilities_payload(self._hdf_capability_members()),
            )

        dispatched = self._rpc_registry.dispatch(dispatch_req)
        if dispatched is not None:
            return dispatched

        return self._rpc_error(dispatch_req, code="unknown_request")

    def _write_buffered_rows(self) -> None:
        # Backward-compatible entry point used by the existing test suite.
        # Drains the live deque into a list and delegates to the batch-aware
        # variant. Once commit 2b lands and the main loop hands batches off
        # to the bg thread, the bg thread will call `_write_buffered_rows_batch`
        # directly with a pre-built list.
        if self._buf is None:
            return
        rows: list[Json] = []
        while self._buf:
            rows.append(self._buf.popleft())
        self._write_buffered_rows_batch(rows)

    def _write_buffered_rows_batch(self, rows: list[Json]) -> None:
        self._assert_h5_locked()
        if self._h5 is None or self._telemetry_group is None:
            return
        used_by_device: dict[str, int] = {}

        for msg in rows:
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
            t_wall_recv = float(ts.get("t_wall_recv", np.nan))
            seq = int(msg.get("seq", -1))
            sigs = msg.get("signals", {})
            if not isinstance(sigs, dict):
                sigs = {}

            batch = self._ensure_telemetry_batch_buffer(device_id=device_id, dtype=ds.dtype)
            used = int(used_by_device.get(device_id, 0))
            if used >= batch.shape[0]:
                self._flush_telemetry_batch(
                    device_id=device_id,
                    ds=ds,
                    used=used,
                )
                used = 0
            row = batch[used]
            row["t_wall"] = t_wall
            row["t_mono"] = t_mono
            row["t_wall_recv"] = t_wall_recv
            row["seq"] = seq

            for name, dtype_str in zip(signals, dtypes, strict=True):
                raw = sigs.get(name, {})
                value = raw.get("value") if isinstance(raw, dict) else None
                row[name] = _convert_value(value, dtype_str)
            used += 1
            if used >= batch.shape[0]:
                self._flush_telemetry_batch(
                    device_id=device_id,
                    ds=ds,
                    used=used,
                )
                used = 0
            used_by_device[device_id] = used

        for device_id, used in used_by_device.items():
            if used <= 0:
                continue
            ds = self._datasets.get(device_id)
            if ds is None:
                continue
            self._flush_telemetry_batch(
                device_id=device_id,
                ds=ds,
                used=used,
            )
        self._prune_telemetry_batch_buffers()

    def _handle_chunk_ready(
        self,
        msg: Json,
        *,
        parsed: ChunkReadyMessage | None = None,
    ) -> None:
        chunk = parsed if parsed is not None else ChunkReadyMessage.parse(msg)
        if chunk is None:
            return
        device_id = chunk.device_id
        stream = chunk.stream
        key = (device_id, stream)
        now_mono = time.monotonic()
        if not self._is_device_enabled(device_id):
            self._handle_chunk_ready_disabled_device(key=key, seq=chunk.seq)
            return

        ctx_id = chunk.context_id
        if ctx_id is not None and chunk.context_fields is not None:
            self._record_context(ctx_id, chunk.context_fields)
        if ctx_id is not None and chunk.seq is not None:
            seq = int(chunk.seq)
            self._store_context_for_seq(
                key=key,
                seq=seq,
                context_id=int(ctx_id),
                now_mono=now_mono,
            )
            self._resolve_pending_stream_event(
                key=key,
                seq=seq,
                context_id=int(ctx_id),
            )
        elif ctx_id is not None:
            self._bump_error("stream.context_seq_missing")

        reader = self._ensure_chunk_ready_reader(
            key=key,
            device_id=device_id,
            stream=stream,
            shm_name=chunk.shm_name,
        )
        if reader is None:
            return

        last_seq = int(self._stream_last_seq.get(key, 0))
        events = self._read_chunk_ready_events(key=key, reader=reader, last_seq=last_seq)
        if not events:
            self._expire_pending_context(key=key, now_mono=now_mono)
            self._trim_context_map(key=key, now_mono=now_mono)
            return
        self._append_chunk_ready_events(
            key=key,
            reader=reader,
            events=events,
            initial_last_seq=last_seq,
            now_mono=now_mono,
        )

    def _handle_chunk_ready_disabled_device(
        self,
        *,
        key: tuple[str, str],
        seq: int | None,
    ) -> None:
        if seq is not None:
            prev = int(self._stream_last_seq.get(key, 0))
            if seq > prev:
                self._stream_last_seq[key] = seq
        self._stream_buffers.pop(key, None)
        self._stream_pending_by_seq.pop(key, None)
        self._stream_context_by_seq.pop(key, None)

    def _reset_stream_runtime_state(self, key: tuple[str, str]) -> None:
        self._stream_readers.pop(key, None)
        self._stream_last_seq.pop(key, None)
        self._stream_buffers.pop(key, None)
        self._stream_schema.pop(key, None)
        self._stream_expected_nbytes.pop(key, None)
        self._stream_pending_by_seq.pop(key, None)
        self._stream_context_by_seq.pop(key, None)
        self._stream_last_written_seq.pop(key, None)

    def _ensure_chunk_ready_reader(
        self,
        *,
        key: tuple[str, str],
        device_id: str,
        stream: str,
        shm_name: str,
    ) -> ShmRingReader | None:
        reader = self._stream_readers.get(key)
        if reader is not None and reader.name == shm_name:
            return reader
        if reader is not None:
            try:
                reader.close()
            except Exception:
                self._bump_error("stream.reader_close")
        try:
            reader = ShmRingReader.attach(str(shm_name))
        except Exception:
            self._bump_error("stream.attach")
            return None
        self._stream_readers[key] = reader
        session = self._next_stream_session(device_id, stream)
        self._stream_sessions[key] = session
        self._stream_active_session[key] = session
        self._stream_last_seq[key] = 0
        self._stream_dropped_total[key] = 0
        self._stream_buffers.pop(key, None)
        self._stream_schema.pop(key, None)
        self._stream_expected_nbytes.pop(key, None)
        self._stream_pending_by_seq.pop(key, None)
        self._stream_context_by_seq.pop(key, None)
        self._stream_last_written_seq[key] = 0
        return reader

    def _read_chunk_ready_events(
        self,
        *,
        key: tuple[str, str],
        reader: ShmRingReader,
        last_seq: int,
    ) -> list[Json] | None:
        try:
            return reader.read_events(last_seq)
        except Exception:
            try:
                reader.close()
            except Exception:
                self._bump_error("stream.reader_close")
            self._bump_error("stream.drain")
            self._reset_stream_runtime_state(key)
            return None

    def _append_chunk_ready_events(
        self,
        *,
        key: tuple[str, str],
        reader: ShmRingReader,
        events: list[Json],
        initial_last_seq: int,
        now_mono: float,
    ) -> None:
        if not events:
            return
        dropped = int(self._stream_dropped_total.get(key, 0))
        last_seq = int(initial_last_seq)
        for ev in events:
            seq = int(ev["seq"])
            if last_seq and seq > last_seq + 1:
                dropped += seq - last_seq - 1
            last_seq = seq
            context_id = self._resolve_context_for_seq(key=key, seq=seq)
            if context_id is not None:
                event = self._pending_event_payload(ev=ev, now_mono=now_mono)
                self._append_resolved_stream_event(
                    key=key,
                    event=event,
                    context_id=context_id,
                )
                self._context_resolved_exact += 1
                continue
            self._queue_pending_stream_event(
                key=key,
                ev=ev,
                now_mono=now_mono,
            )

        self._stream_last_seq[key] = last_seq
        self._stream_dropped_total[key] = dropped
        if key not in self._stream_schema:
            self._stream_schema[key] = {
                "dtype": str(reader.layout.dtype),
                "shape": reader.layout.shape,
            }
        self._enforce_pending_context_cap(key=key)
        self._expire_pending_context(key=key, now_mono=now_mono)
        self._trim_context_map(key=key, now_mono=now_mono)

    def _stream_buffer_for_key(self, key: tuple[str, str]) -> dict[str, list[Any]]:
        return self._stream_buffers.setdefault(
            key,
            {
                "data": [],
                "seq": [],
                "t0_mono_ns": [],
                "t0_wall_ns": [],
                "context_id": [],
            },
        )

    def _append_resolved_stream_event(
        self,
        *,
        key: tuple[str, str],
        event: Json,
        context_id: int,
    ) -> None:
        buf = self._stream_buffer_for_key(key)
        buf["data"].append(event["payload"])
        buf["seq"].append(int(event["seq"]))
        buf["t0_mono_ns"].append(int(event["t0_mono_ns"]))
        buf["t0_wall_ns"].append(int(event["t0_wall_ns"]))
        buf["context_id"].append(int(context_id))

    def _store_context_for_seq(
        self,
        *,
        key: tuple[str, str],
        seq: int,
        context_id: int,
        now_mono: float,
    ) -> None:
        context_map = self._stream_context_by_seq.setdefault(key, {})
        context_map[int(seq)] = (int(context_id), float(now_mono))

    def _resolve_context_for_seq(
        self, *, key: tuple[str, str], seq: int
    ) -> int | None:
        context_map = self._stream_context_by_seq.get(key)
        if not context_map:
            return None
        hit = context_map.pop(int(seq), None)
        if not context_map:
            self._stream_context_by_seq.pop(key, None)
        if hit is None:
            return None
        return int(hit[0])

    @staticmethod
    def _pending_event_payload(*, ev: Json, now_mono: float) -> Json:
        return {
            "seq": int(ev["seq"]),
            "payload": ev["payload"],
            "t0_mono_ns": int(ev["t0_mono_ns"]),
            "t0_wall_ns": int(ev["t0_wall_ns"]),
            "first_seen_mono": float(now_mono),
        }

    def _queue_pending_stream_event(
        self,
        *,
        key: tuple[str, str],
        ev: Json,
        now_mono: float,
    ) -> None:
        seq = int(ev["seq"])
        pending = self._stream_pending_by_seq.setdefault(key, {})
        pending[seq] = self._pending_event_payload(ev=ev, now_mono=now_mono)

    def _resolve_pending_stream_event(
        self,
        *,
        key: tuple[str, str],
        seq: int,
        context_id: int,
    ) -> None:
        pending = self._stream_pending_by_seq.get(key)
        if not pending:
            return
        event = pending.pop(int(seq), None)
        if not pending:
            self._stream_pending_by_seq.pop(key, None)
        if event is None:
            return
        self._append_resolved_stream_event(
            key=key,
            event=event,
            context_id=int(context_id),
        )
        self._context_late_resolved += 1
        context_map = self._stream_context_by_seq.get(key)
        if context_map is not None:
            context_map.pop(int(seq), None)
            if not context_map:
                self._stream_context_by_seq.pop(key, None)

    def _flush_pending_as_unknown(
        self,
        *,
        key: tuple[str, str],
        seqs: list[int],
        count_overflow: bool,
    ) -> None:
        if not seqs:
            return
        pending = self._stream_pending_by_seq.get(key)
        if not pending:
            return
        for seq in seqs:
            event = pending.pop(int(seq), None)
            if event is None:
                continue
            self._append_resolved_stream_event(key=key, event=event, context_id=-1)
            self._context_written_minus1_missing += 1
            if count_overflow:
                self._context_evicted_pending_overflow += 1
        if not pending:
            self._stream_pending_by_seq.pop(key, None)

    def _enforce_pending_context_cap(self, *, key: tuple[str, str]) -> None:
        pending = self._stream_pending_by_seq.get(key)
        if not pending:
            return
        cap = int(self._context_pending_max_per_stream)
        if len(pending) <= cap:
            return
        overflow = len(pending) - cap
        oldest = sorted(pending.keys())[:overflow]
        self._flush_pending_as_unknown(
            key=key,
            seqs=oldest,
            count_overflow=True,
        )

    def _expire_pending_context(self, *, key: tuple[str, str], now_mono: float) -> None:
        pending = self._stream_pending_by_seq.get(key)
        if not pending:
            return
        ttl_s = float(self._context_resolve_ttl_s)
        if ttl_s <= 0.0:
            return
        expired = [
            seq
            for seq, event in pending.items()
            if (now_mono - float(event.get("first_seen_mono", now_mono))) >= ttl_s
        ]
        if not expired:
            return
        expired.sort()
        self._flush_pending_as_unknown(
            key=key,
            seqs=expired,
            count_overflow=False,
        )

    def _trim_context_map(self, *, key: tuple[str, str], now_mono: float) -> None:
        context_map = self._stream_context_by_seq.get(key)
        if context_map is None:
            return
        if not context_map:
            self._stream_context_by_seq.pop(key, None)
            return
        last_written = int(self._stream_last_written_seq.get(key, 0))
        written_floor = max(0, last_written - int(self._context_map_written_margin))
        if written_floor > 0:
            stale_written = [seq for seq in context_map if seq <= written_floor]
            for seq in stale_written:
                context_map.pop(seq, None)
        ttl_s = float(self._context_map_ttl_s)
        if ttl_s > 0.0:
            stale_ttl = [
                seq
                for seq, (_, seen_mono) in context_map.items()
                if (now_mono - float(seen_mono)) >= ttl_s
            ]
            for seq in stale_ttl:
                context_map.pop(seq, None)
        cap = int(self._context_map_max_per_stream)
        if len(context_map) > cap:
            overflow = len(context_map) - cap
            oldest = sorted(context_map.keys())[:overflow]
            for seq in oldest:
                context_map.pop(seq, None)
                self._context_evicted_map_overflow += 1
        if not context_map:
            self._stream_context_by_seq.pop(key, None)

    def _sweep_context_buffers(self, *, now_mono: float) -> None:
        keys = set(self._stream_pending_by_seq.keys())
        keys.update(self._stream_context_by_seq.keys())
        for key in keys:
            self._enforce_pending_context_cap(key=key)
            self._expire_pending_context(key=key, now_mono=now_mono)
            self._trim_context_map(key=key, now_mono=now_mono)

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
                    "type": "manager.processes.rpc",
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
        self._assert_h5_locked()
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
            ds_dtype: np.dtype[Any]
            missing: object
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
        self._assert_h5_locked()
        if not self._context_columns_datasets:
            return
        for name, ds in self._context_columns_datasets.items():
            ds.resize((index + 1,))
            raw = fields.get(name)
            ds[index] = self._coerce_context_value(name, raw)

    def _coerce_context_value(self, name: str, value: Any) -> Any:
        return coerce_context_value(
            name=name,
            value=value,
            missing_values=self._context_columns_missing,
            dtype_map=self._context_columns_types,
        )

    def _record_context(self, context_id: int, fields: dict[str, Any]) -> None:
        # Runs on the main drain thread (lock-free, h5py-free). Buffer the
        # context row — deduplicated via `_seen_context_ids` — and let the bg
        # thread perform the actual h5py write in `_write_context_rows_batch`.
        # Deferring keeps the SUB drain from ever stalling behind a
        # context-table write or the (blocking) columns-spec RPC.
        if self._context_table_ds is None:
            return
        cid = int(context_id)
        if cid in self._seen_context_ids:
            return
        self._seen_context_ids.add(cid)
        self._pending_context_rows.append(
            {
                "context_id": cid,
                "fields": fields,
                "ts_wall_ns": int(time.time_ns()),
                "ts_mono_ns": int(time.monotonic_ns()),
            }
        )

    def _write_context_rows_batch(self, rows: list[dict[str, Any]]) -> None:
        # Bg thread, under _h5_lock. Writes the context-table rows the main
        # thread buffered (timestamps captured at observation time so they
        # reflect when the context was seen, not when it was persisted).
        self._assert_h5_locked()
        if not rows or self._context_table_ds is None:
            return
        for row_info in rows:
            fields = row_info.get("fields") or {}
            self._ensure_context_columns(fields)
            row = np.zeros(1, dtype=self._context_table_ds.dtype)
            row[0]["context_id"] = int(row_info.get("context_id", -1))
            row[0]["ts_wall_ns"] = int(row_info.get("ts_wall_ns", 0))
            row[0]["ts_mono_ns"] = int(row_info.get("ts_mono_ns", 0))
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
        # Backward-compatible entry point. Mirrors the wrapper pattern
        # introduced for `_write_buffered_rows` and `_write_event_rows`:
        # callers that operate on the live `self._stream_buffers` dict go
        # through this wrapper, while the bg-thread path (added in the next
        # commit) will call `_write_stream_buffers_batch` directly with a
        # snapshot dict the main loop swapped out.
        self._write_stream_buffers_batch(self._stream_buffers)

    def _write_stream_buffers_batch(
        self,
        stream_buffers: dict[tuple[str, str], dict[str, list[Any]]],
        *,
        stream_meta: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        # `stream_meta` (dtype/shape/session per key) is supplied by the bg
        # flush path so the write is self-contained; the synchronous drain
        # path (file rotate/stop/close, main thread with the bg quiesced)
        # passes None and reads the live `_stream_schema`/`_stream_readers`/
        # `_stream_active_session` maps instead. Context-buffer sweeps live on
        # the main drain thread now, so they are NOT performed here.
        self._assert_h5_locked()
        if self._h5 is None or self._streams_group is None:
            for _key, buf in stream_buffers.items():
                self._clear_stream_buffer(buf)
            return
        for key, buf in list(stream_buffers.items()):
            data_list = buf.get("data", [])
            if not data_list:
                continue

            device_id, stream = key
            if not self._is_device_enabled(device_id):
                self._clear_stream_buffer(buf)
                continue
            meta = stream_meta.get(key) if stream_meta else None
            dtype_raw = meta.get("dtype") if meta else None
            shape_raw = meta.get("shape") if meta else None
            if dtype_raw is None or shape_raw is None:
                schema = self._stream_schema.get(key)
                reader = self._stream_readers.get(key)
                if dtype_raw is None:
                    dtype_raw = None if schema is None else schema.get("dtype")
                if shape_raw is None:
                    shape_raw = None if schema is None else schema.get("shape")
                if dtype_raw is None and reader is not None:
                    dtype_raw = reader.layout.dtype
                if shape_raw is None and reader is not None:
                    shape_raw = tuple(reader.layout.shape)

            if dtype_raw is None or shape_raw is None:
                self._clear_stream_buffer(buf)
                continue

            dtype_obj = np.dtype(dtype_raw)
            shape = tuple(int(x) for x in shape_raw)
            if meta and meta.get("session") is not None:
                session = int(meta["session"])
            else:
                session = self._stream_active_session.get(key, 1)
            datasets = self._ensure_stream_dataset(
                device_id, stream, dtype_obj, shape, session=session
            )

            n = len(data_list)
            seq_list = list(buf["seq"])
            t0_mono_list = list(buf["t0_mono_ns"])
            t0_wall_list = list(buf["t0_wall_ns"])
            context_list = list(buf.get("context_id", []))
            if n > 1 and any(seq_list[idx] > seq_list[idx + 1] for idx in range(n - 1)):
                order = sorted(range(n), key=lambda idx: seq_list[idx])
                data_list = [data_list[idx] for idx in order]
                seq_list = [seq_list[idx] for idx in order]
                t0_mono_list = [t0_mono_list[idx] for idx in order]
                t0_wall_list = [t0_wall_list[idx] for idx in order]
                context_list = [context_list[idx] for idx in order]
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
                expected_nbytes = int(dtype_obj.itemsize * int(np.prod(shape_obj, dtype=np.int64)))
                self._stream_expected_nbytes[key] = expected_nbytes
            elif expected_nbytes != int(dtype_obj.itemsize * int(np.prod(shape_obj, dtype=np.int64))):
                expected_nbytes = int(dtype_obj.itemsize * int(np.prod(shape_obj, dtype=np.int64)))
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

            if seq_list:
                self._stream_last_written_seq[key] = max(
                    int(self._stream_last_written_seq.get(key, 0)),
                    int(max(seq_list)),
                )
                self._trim_context_map(key=key, now_mono=time.monotonic())
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
        dtype: str | np.dtype[Any],
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

        dtype_obj = np.dtype(dtype)
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
            compression=DEFAULT_NUMERIC_COMPRESSION,
            shuffle=DEFAULT_NUMERIC_SHUFFLE,
        )
        t0_mono_ds = session_group.create_dataset(
            "t0_mono_ns",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint64,
            chunks=(chunk_n,),
            compression=DEFAULT_NUMERIC_COMPRESSION,
            shuffle=DEFAULT_NUMERIC_SHUFFLE,
        )
        t0_wall_ds = session_group.create_dataset(
            "t0_wall_ns",
            shape=(0,),
            maxshape=(None,),
            dtype=np.uint64,
            chunks=(chunk_n,),
            compression=DEFAULT_NUMERIC_COMPRESSION,
            shuffle=DEFAULT_NUMERIC_SHUFFLE,
        )
        context_ds = session_group.create_dataset(
            "context_id",
            shape=(0,),
            maxshape=(None,),
            dtype=np.int64,
            chunks=(chunk_n,),
            compression=DEFAULT_NUMERIC_COMPRESSION,
            shuffle=DEFAULT_NUMERIC_SHUFFLE,
        )

        pending = self._pending_stream_metadata.get((device_id, stream), None)
        if pending:
            for attr_key, value in pending.items():
                data_ds.attrs[attr_key] = value
            self._pending_stream_metadata.pop((device_id, stream), None)
        data_ds.attrs["session"] = int(session)
        data_ds.attrs["stream_kind"] = "records" if dtype_obj.fields else "frame"
        if dtype_obj.fields:
            data_ds.attrs["record_fields_json"] = json.dumps(
                [
                    {"name": name, "dtype": str(dtype_obj.fields[name][0])}
                    for name in (dtype_obj.names or ())
                ]
            )

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
        # Low-frequency handler invoked from the (lock-free) main drain: it
        # writes h5py and mutates bg-shared stream state, so it takes _h5_lock.
        # RLock re-entry keeps the already-locked internal caller
        # (_configure_active_file) correct.
        with self._h5_lock:
            self._handle_device_config_locked(msg, cache=cache)

    def _handle_device_config_locked(self, msg: Json, *, cache: bool = True) -> None:
        self._assert_h5_locked()
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
                    kind = str(out.get("kind", "frame") or "frame")
                    fields_raw = out.get("fields")
                    if kind == "records" and isinstance(fields_raw, list):
                        field_specs: list[tuple[str, str]] = []
                        for field in fields_raw:
                            if not isinstance(field, dict):
                                continue
                            name = str(field.get("name") or "").strip()
                            dtype_text = str(field.get("dtype") or "").strip()
                            if name and dtype_text:
                                field_specs.append((name, dtype_text))
                        dtype = np.dtype(field_specs)
                        stream_shape: tuple[int, ...] = ()
                    else:
                        dtype = np.dtype(str(out.get("dtype")))
                        shape_raw = out.get("shape", []) or []
                        stream_shape = tuple(int(x) for x in shape_raw)
                    key = (device_id, stream)
                    self._stream_schema[key] = {"dtype": dtype, "shape": stream_shape}
                    try:
                        expected = int(dtype.itemsize * int(np.prod(stream_shape, dtype=np.int64)))
                        self._stream_expected_nbytes[key] = expected
                    except Exception:
                        self._stream_expected_nbytes.pop(key, None)
                    merged = stream_call_attrs.setdefault(stream, {})
                    merged["stream_kind"] = "records" if dtype.fields else "frame"
                    if dtype.fields:
                        merged["record_fields_json"] = json.dumps(
                            [
                                {"name": name, "dtype": str(dtype.fields[name][0])}
                                for name in (dtype.names or ())
                            ]
                        )
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
        # Low-frequency handler from the lock-free main drain; takes _h5_lock
        # for the h5py write (RLock re-entry keeps locked callers correct).
        with self._h5_lock:
            self._handle_run_metadata_locked(msg)

    def _handle_run_metadata_locked(self, msg: Json) -> None:
        self._assert_h5_locked()
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
        context_resolve_ttl_s=ns.context_resolve_ttl_s,
        context_pending_max_per_stream=ns.context_pending_max_per_stream,
        context_map_max_per_stream=ns.context_map_max_per_stream,
        disabled_devices=ns.disabled_devices,
        disabled_processes=ns.disabled_processes,
        measurement_schema_path=ns.measurement_schema_path,
        autostart_writing=ns.autostart_writing,
        event_log_mode=ns.event_log_mode,
        bg_join_timeout_s=ns.bg_join_timeout_s,
    )
    writer._process_id = ns.process_id
    writer._heartbeat_endpoint = ns.heartbeat_endpoint
    writer._heartbeat_period_s = ns.heartbeat_period_s
    writer.run()


if __name__ == "__main__":
    main()

