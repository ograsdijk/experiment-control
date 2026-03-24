from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, TextIO

import zmq

from .federation import FederationConfig
from .federation.hub import FederationHub
from .utils.config_parsing import (
    ConfigError,
    optional_dict,
    require_dict,
    require_str,
)
from .utils.manager_network import derive_local_connect_endpoint
from .utils.command_journal import CommandJournal, CommandJournalSettings
from .utils.command_interceptors import apply_command_interceptor_chain
from .manager_device_routing import route_device_request
from .manager_driver_pub import handle_driver_pub as shared_handle_driver_pub
from .manager_driver_pub import ingest_chunk_ready as shared_ingest_chunk_ready
from .manager_driver_pub import ingest_heartbeat as shared_ingest_heartbeat
from .manager_driver_pub import ingest_telemetry as shared_ingest_telemetry
from .manager_lifecycle import shutdown_cleanup as shared_shutdown_cleanup
from .manager_lifecycle import startup_sequence as shared_startup_sequence
from .manager_log_events import maybe_emit_manager_log_sink as shared_maybe_emit_manager_log_sink
from .manager_log_events import (
    maybe_publish_log_event as shared_maybe_publish_log_event,
)
from .manager_process_spec import process_spec_kwargs_from_yaml
from .manager_rpc_calls import call_device_rpc as shared_call_device_rpc
from .manager_rpc_calls import call_process_rpc as shared_call_process_rpc
from .types import (
    DeviceState,
    DriverState,
    RunMetaCall,
    StreamCall,
    TelemetryCall,
    TelemetryQuality,
    Timestamp,
)
from .schemas.run_meta import run_meta_calls_from_json, run_meta_calls_to_json
from .schemas.stream import stream_calls_from_json, stream_calls_to_json
from .schemas.telemetry import telemetry_calls_from_json, telemetry_calls_to_json
from .utils.process_lifecycle import ProcessGuardian
from .utils.process_lifecycle import cleanup_orphan_children
from .utils.instance_lock import (
    derive_lock_effective_status,
    lock_effective_status_help,
    read_instance_lock_status,
)
from .utils.logging_levels import (
    is_valid_log_severity,
    normalize_log_severity,
    severity_rank,
)
from .utils.yaml_helpers import load_yaml_file
from .utils.zmq_helpers import json_dumps, safe_json_loads

Json = dict[str, Any]
_LOG_LEVEL_PREFIX_RE = re.compile(
    r"^\s*(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b", re.IGNORECASE
)
_LOG_LEVEL_BRACKET_PREFIX_RE = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)+(?:\[\s*)?(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)(?:\s*\])?\b",
    re.IGNORECASE,
)
_LOG_LEVEL_INLINE_RE = re.compile(
    r"\s-\s(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s-\s", re.IGNORECASE
)
_LOG_LEVEL_TABLE_RE = re.compile(
    r"\s{2,}(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s{2,}", re.IGNORECASE
)
_EXCEPTION_LINE_RE = re.compile(
    r"^[A-Za-z_][\w.]*?(Error|Exception|Exit|Interrupt|Fault|Failure)\s*:\s*"
)


def _module_name_from_path(path: Path) -> tuple[str | None, Path | None]:
    parts: list[str] = []
    cur = path.parent
    while (cur / "__init__.py").exists():
        parts.append(cur.name)
        cur = cur.parent
    if not parts:
        return None, None
    module_name = ".".join(list(reversed(parts)) + [path.stem])
    return module_name, cur


def _load_module(
    *,
    module_name: str | None,
    file_path: str | Path,
) -> Any:
    if module_name:
        return importlib.import_module(module_name)
    path = Path(file_path).expanduser().resolve()
    inferred_name, root = _module_name_from_path(path)
    if inferred_name and root is not None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return importlib.import_module(inferred_name)
    module_name = f"_ec_driver_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {str(path)!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _coerce_telemetry_calls(raw: object) -> list[TelemetryCall]:
    if raw is None:
        return []
    if isinstance(raw, list) and raw and all(isinstance(x, TelemetryCall) for x in raw):
        return list(raw)
    if isinstance(raw, list) and not raw:
        return []
    return telemetry_calls_from_json(raw)


def _coerce_stream_calls(raw: object) -> list[StreamCall] | None:
    if raw is None:
        return []
    if isinstance(raw, list) and raw and all(isinstance(x, StreamCall) for x in raw):
        return list(raw)
    if isinstance(raw, list) and not raw:
        return []
    return stream_calls_from_json(raw)


def _coerce_device_metadata(raw: object) -> dict[str, Any]:
    meta = optional_dict(raw, path=["device_metadata"])
    out: dict[str, Any] = {}
    for key, value in meta.items():
        name = str(key).strip()
        if not name:
            raise ConfigError("device_metadata", "keys must be non-empty strings")
        out[name] = value
    return out


def _coerce_stream_metadata(raw: object) -> dict[str, dict[str, Any]]:
    meta = optional_dict(raw, path=["stream_metadata"])
    out: dict[str, dict[str, Any]] = {}
    for stream_raw, attrs_raw in meta.items():
        stream = str(stream_raw).strip()
        if not stream:
            raise ConfigError("stream_metadata", "stream names must be non-empty")
        attrs = require_dict(attrs_raw, path=["stream_metadata", stream])
        normalized_attrs: dict[str, Any] = {}
        for attr_key, attr_value in attrs.items():
            name = str(attr_key).strip()
            if not name:
                raise ConfigError(
                    f"stream_metadata.{stream}",
                    "attribute keys must be non-empty strings",
                )
            normalized_attrs[name] = attr_value
        out[stream] = normalized_attrs
    return out


@dataclass(frozen=True)
class ConnectCheckSpec:
    enabled: bool = False
    identity: dict[str, Any] = field(default_factory=dict)
    on_fail: str = "disconnect"


def _coerce_connect_check(raw: object) -> ConnectCheckSpec:
    if raw is None:
        return ConnectCheckSpec()

    obj = require_dict(raw, path=["connect_check"])
    enabled_raw = obj.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise ConfigError("connect_check.enabled", "must be a bool")
    enabled = bool(enabled_raw)

    identity_raw = obj.get("identity", {})
    if identity_raw is None:
        identity_raw = {}
    identity_obj = require_dict(identity_raw, path=["connect_check", "identity"])
    identity: dict[str, Any] = {}
    for key, value in identity_obj.items():
        field_name = str(key).strip()
        if not field_name:
            raise ConfigError(
                "connect_check.identity", "identity keys must be non-empty strings"
            )
        identity[field_name] = copy.deepcopy(value)

    on_fail_raw = str(obj.get("on_fail", "disconnect")).strip().lower()
    if not on_fail_raw:
        on_fail_raw = "disconnect"
    if on_fail_raw not in {"disconnect", "keep_connected"}:
        raise ConfigError(
            "connect_check.on_fail",
            "must be 'disconnect' or 'keep_connected'",
        )

    if enabled and not identity:
        raise ConfigError(
            "connect_check.identity",
            "must be non-empty when connect_check.enabled is true",
        )

    return ConnectCheckSpec(
        enabled=enabled,
        identity=identity,
        on_fail=on_fail_raw,
    )


def _load_driver_defaults(
    *,
    module_name: str | None,
    file_path: str | Path,
    class_name: str,
) -> dict[str, object]:
    try:
        module = _load_module(module_name=module_name, file_path=file_path)
    except Exception:
        return {}

    defaults: dict[str, object] = {}
    class_suffix = class_name.upper()
    telemetry_name = f"DEFAULT_TELEMETRY_CALLS_{class_suffix}"
    stream_name = f"DEFAULT_STREAM_CALLS_{class_suffix}"

    if hasattr(module, telemetry_name):
        defaults["telemetry_calls"] = getattr(module, telemetry_name)
    if hasattr(module, stream_name):
        defaults["stream_calls"] = getattr(module, stream_name)
    return defaults


class Liveness(StrEnum):
    OFFLINE = "OFFLINE"  # heartbeat stale
    DISCONNECTED = "DISCONNECTED"  # heartbeat fresh but device unreachable
    ONLINE = "ONLINE"


@dataclass
class TelemetrySignal:
    value: Any | None
    units: str | None
    quality: TelemetryQuality
    ts: Timestamp | None  # None => use bundle timestamp (avoid false precision)
    quality_source: str = "device"


@dataclass
class Heartbeat:
    pid: int
    seq: int
    driver_state: DriverState
    device_reachable: bool
    device_state: DeviceState
    device_health: str | None  # driver-specific (optional)
    last_error: str | None
    last_ok_wall: float | None
    last_ok_mono: float | None
    loop_lag_s: float | None
    ts: Timestamp


@dataclass(frozen=True)
class DriverRegistration:
    device_id: str
    rpc_endpoint: str
    pub_endpoint: str
    capabilities: Json | None = None  # optional to send at register-time


@dataclass
class DeviceSpec:
    device_id: str
    device_class_path: str | Path
    device_class_name: str
    device_init_kwargs: dict[str, Any]

    telemetry_calls: list[TelemetryCall]
    stream_calls: list[StreamCall] | None = None
    run_meta_calls: list[RunMetaCall] | None = None
    device_metadata: dict[str, Any] | None = None
    stream_metadata: dict[str, dict[str, Any]] | None = None
    connect_check: ConnectCheckSpec = field(default_factory=ConnectCheckSpec)
    config_yaml_text: str | None = None
    telemetry_period_s: float = 1.0
    heartbeat_period_s: float = 1.0
    command_poll_period_s: float = 0.01
    driver_stop_timeout_s: float = 3.0
    driver_kill_timeout_s: float = 3.0
    driver_restart_backoff_s: float = 0.5
    driver_max_restarts: int | None = None


class RestartPolicy(StrEnum):
    NEVER = "NEVER"
    ALWAYS = "ALWAYS"
    ON_FAILURE = "ON_FAILURE"


class ManagedProcessState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    EXITED = "EXITED"
    FAILED = "FAILED"
    CRASHLOOP = "CRASHLOOP"


@dataclass
class DeviceHandle:
    spec: DeviceSpec
    process: subprocess.Popen[str] | None = None
    rpc_endpoint: str | None = None
    rpc_sock: zmq.Socket | None = None
    rpc_fail_count: int = 0
    rpc_last_fail_t_mono: float | None = None
    pub_endpoint: str | None = None
    capabilities: Json | None = None
    last_hb_recv_mono: float | None = None
    last_hb: Heartbeat | None = None
    driver_process_state: ManagedProcessState = ManagedProcessState.STOPPED
    driver_pid: int | None = None
    driver_last_exit_code: int | None = None
    driver_restart_count: int = 0
    driver_last_restart_t_mono: float | None = None
    driver_last_error: str | None = None
    driver_stop_requested_t_mono: float | None = None
    driver_next_restart_t_mono: float | None = None
    connect_check_last: dict[str, Any] | None = None
    config_published: bool = False


@dataclass
class TelemetryBundle:
    device_id: str
    ts: Timestamp
    signals: dict[str, TelemetrySignal]


@dataclass
class ProcessSpec:
    process_id: str
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    heartbeat_period_s: float = 1.0
    heartbeat_timeout_s: float = 3.0
    shutdown_timeout_s: float = 3.0
    restart_policy: RestartPolicy = RestartPolicy.NEVER
    restart_backoff_s: float = 0.5
    max_restarts: int | None = None
    heartbeat_endpoint: str | None = None
    process_data_endpoint: str | None = None


@dataclass
class ProcessHandle:
    spec: ProcessSpec
    popen: subprocess.Popen[str] | None = None
    state: ManagedProcessState = ManagedProcessState.STOPPED
    pid: int | None = None
    rpc_endpoint: str | None = None
    rpc_sock: zmq.Socket | None = None
    rpc_fail_count: int = 0
    rpc_last_fail_t_mono: float | None = None
    last_start_t_wall: float | None = None
    last_start_t_mono: float | None = None
    last_hb_t_wall: float | None = None
    last_hb_t_mono: float | None = None
    last_exit_code: int | None = None
    restart_count: int = 0
    last_restart_t_mono: float | None = None
    last_error: str | None = None
    heartbeat_endpoint: str = ""
    process_data_endpoint: str = ""
    stop_requested_t_mono: float | None = None
    next_restart_t_mono: float | None = None
    startup_collision_retry_done: bool = False


@dataclass(frozen=True)
class CommandInterceptorRoute:
    process_id: str
    device_id: str
    action: str
    order: int


def device_spec_from_yaml(path: str | Path) -> DeviceSpec:
    raw, yaml_text = load_yaml_file(path, return_text=True)
    try:
        raw_obj = require_dict(raw, path=[])
        device_id = require_str(raw_obj.get("device_id"), path=["device_id"])
        driver = require_dict(raw_obj.get("driver"), path=["driver"])
        driver_file = driver.get("file")
        driver_module = driver.get("module")
        if driver_file and driver_module:
            raise ConfigError("driver", "file and module are mutually exclusive")
        if not driver_file and not driver_module:
            raise ConfigError("driver", "file or module must be provided")
        module_name = None
        if driver_module:
            module_name = require_str(driver_module, path=["driver", "module"])
            spec = importlib.util.find_spec(module_name)
            if spec is None or spec.origin is None:
                raise ConfigError(
                    "driver.module", f"module not found: {module_name!r}"
                )
            device_class_path = spec.origin
        else:
            device_class_path = require_str(driver_file, path=["driver", "file"])
        device_class_name = require_str(
            driver.get("class_name"), path=["driver", "class_name"]
        )
        init_kwargs = optional_dict(raw_obj.get("init_kwargs"), path=["init_kwargs"])
        defaults = _load_driver_defaults(
            module_name=module_name,
            file_path=device_class_path,
            class_name=device_class_name,
        )
        if "telemetry_calls" in raw_obj:
            telemetry_calls = _coerce_telemetry_calls(raw_obj.get("telemetry_calls"))
        else:
            telemetry_calls = _coerce_telemetry_calls(defaults.get("telemetry_calls"))
        if "stream_calls" in raw_obj:
            stream_calls = _coerce_stream_calls(raw_obj.get("stream_calls"))
        else:
            stream_calls = _coerce_stream_calls(defaults.get("stream_calls"))
        run_meta_calls = run_meta_calls_from_json(raw_obj.get("run_meta_calls"))
        device_metadata = _coerce_device_metadata(raw_obj.get("device_metadata"))
        stream_metadata = _coerce_stream_metadata(raw_obj.get("stream_metadata"))
        connect_check = _coerce_connect_check(raw_obj.get("connect_check"))
        telemetry_period_s = float(raw_obj.get("telemetry_period_s", 1.0))
        heartbeat_period_s = float(raw_obj.get("heartbeat_period_s", 1.0))
        command_poll_period_s = float(raw_obj.get("command_poll_period_s", 0.01))
    except ConfigError as e:
        raise TypeError(str(e)) from None

    return DeviceSpec(
        device_id=device_id,
        device_class_path=device_class_path,
        device_class_name=device_class_name,
        device_init_kwargs=init_kwargs,
        telemetry_calls=telemetry_calls,
        stream_calls=stream_calls,
        run_meta_calls=run_meta_calls,
        device_metadata=device_metadata,
        stream_metadata=stream_metadata,
        connect_check=connect_check,
        config_yaml_text=yaml_text,
        telemetry_period_s=telemetry_period_s,
        heartbeat_period_s=heartbeat_period_s,
        command_poll_period_s=command_poll_period_s,
    )


def process_spec_from_yaml(
    path: str | Path,
    *,
    manager_rpc: str,
    manager_pub: str,
) -> ProcessSpec:
    return ProcessSpec(
        **process_spec_kwargs_from_yaml(
            path,
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            restart_policy_enum=RestartPolicy,
        )
    )


class Manager:
    """
    Manager process responsibilities (implementation-facing summary):

    - Start drivers (one OS process per device) and accept registration via a
      well-known registry socket.
    - Maintain device registry and per-device RPC REQ sockets (serial command
      execution per device).
    - Subscribe to all driver PUB sockets on a single SUB socket and update
      caches (telemetry, heartbeat, chunk descriptors).
    - Provide internal RPC for the device_router/processes and publish state
      snapshots/updates for external subscribers.
    """

    def __init__(
        self,
        *,
        instance_id: str | None = None,
        federation_config: FederationConfig | None = None,
        registry_bind: str = "tcp://127.0.0.1:5555",
        internal_rpc_bind: str = "tcp://127.0.0.1:6002",
        external_rpc_bind: str = "tcp://127.0.0.1:6000",
        external_pub_bind: str = "tcp://127.0.0.1:6001",
        external_pub_connect_local: str | None = None,
        process_hb_bind_base: str = "tcp://127.0.0.1:6100",
        process_data_bind_base: str = "tcp://127.0.0.1:6200",
        heartbeat_timeout_s: float = 3.0,
        telemetry_stale_s: float = 10.0,
        device_rpc_timeout_ms: int = 1500,
        interceptor_rpc_timeout_ms: int = 500,
        auto_connect_on_register: bool = True,
        log_history_size: int = 10000,
        command_journal_enabled: bool = False,
        command_journal_path: str | Path | None = None,
        command_journal_queue_max: int = 10_000,
        command_journal_batch_size: int = 200,
        command_journal_flush_interval_ms: int = 200,
        command_journal_retention_max_rows: int | None = 1_000_000,
        command_journal_retention_max_age_days: float | None = None,
        manager_log_stderr: bool | None = None,
        manager_log_file: str | Path | None = None,
        manager_log_min_level: str | None = None,
    ) -> None:
        instance_id_text = str(
            instance_id or os.environ.get("EXPERIMENT_CONTROL_INSTANCE_ID", "")
        ).strip()
        self._instance_id = instance_id_text or "unknown"
        self._started_t_wall = time.time()
        self._started_t_mono = time.monotonic()
        self._ctx = zmq.Context.instance()

        # Driver registry (REP): drivers register endpoints here
        self._registry_bind = registry_bind
        self._registry_rep = self._ctx.socket(zmq.REP)
        self._registry_rep.bind(self._registry_bind)

        # Telemetry subscriber (SUB): connects to all driver PUB endpoints
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.SUBSCRIBE, b"")  # subscribe all topics

        # Process heartbeat subscriber (SUB): connects to managed process PUB endpoints
        self._process_hb_sub = self._ctx.socket(zmq.SUB)
        self._process_hb_sub.setsockopt(zmq.SUBSCRIBE, b"")
        # Process data/event subscriber (SUB): high-rate managed-process events
        self._process_data_sub = self._ctx.socket(zmq.SUB)
        self._process_data_sub.setsockopt(zmq.SUBSCRIBE, b"")

        # Internal RPC router (ROUTER): device_router/processes forward requests here
        self._internal_rpc_bind = internal_rpc_bind
        self._internal_rpc = self._ctx.socket(zmq.ROUTER)
        self._internal_rpc.bind(self._internal_rpc_bind)
        self._internal_rpc_endpoint = self._internal_rpc.getsockopt_string(
            zmq.LAST_ENDPOINT
        )
        self._external_rpc_bind = external_rpc_bind

        # External publisher (PUB): manager broadcasts state snapshots/updates
        self._external_pub_bind = external_pub_bind
        self._external_pub_connect_local = (
            str(external_pub_connect_local).strip()
            if isinstance(external_pub_connect_local, str)
            and str(external_pub_connect_local).strip()
            else derive_local_connect_endpoint(external_pub_bind, 6001)
        )
        self._external_pub = self._ctx.socket(zmq.PUB)
        self._external_pub.bind(external_pub_bind)

        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._telemetry_stale_s = telemetry_stale_s
        self._device_rpc_timeout_ms = device_rpc_timeout_ms
        self._interceptor_rpc_timeout_ms = int(interceptor_rpc_timeout_ms)
        self._federation_config = federation_config or FederationConfig()

        self._process_hb_bind_base = process_hb_bind_base
        self._process_hb_connected: set[str] = set()
        self._process_hb_port_offset = 0
        self._process_data_bind_base = process_data_bind_base
        self._process_data_connected: set[str] = set()
        self._process_data_port_offset = 0

        self._auto_connect_on_register = auto_connect_on_register

        self._devices: dict[str, DeviceHandle] = {}
        self._processes: dict[str, ProcessHandle] = {}

        # Latest telemetry cache: (device_id -> signal_name -> TelemetrySignal + bundle ts)
        self._telemetry_latest: dict[
            str, dict[str, tuple[Timestamp, TelemetrySignal]]
        ] = {}
        self._telemetry_last_bundle_ts: dict[str, Timestamp] = {}
        self._last_liveness: dict[str, Liveness] = {}
        self._log_history_size = max(100, int(log_history_size))
        self._log_history: deque[Json] = deque(maxlen=self._log_history_size)
        self._supervisor_log_queue: queue.Queue[Json] = queue.Queue(maxsize=5000)
        self._supervisor_log_dropped = 0
        self._supervisor_log_threads: dict[tuple[str, str, int, str], threading.Thread] = {}
        self._supervisor_pending_blocks: dict[tuple[str, str, int, str], Json] = {}
        self._last_orphan_cleanup: Json | None = None
        self._command_journal_enabled = bool(command_journal_enabled)
        self._command_journal: CommandJournal | None = None
        path_raw = (
            str(command_journal_path).strip()
            if command_journal_path is not None
            else ""
        )
        if path_raw:
            self._command_journal_path: Path | None = Path(path_raw).expanduser()
        else:
            self._command_journal_path = (
                Path(".state") / self._instance_id / "command_journal.sqlite3"
            )
        self._command_journal_start_error: str | None = None
        if self._command_journal_enabled:
            try:
                settings = CommandJournalSettings(
                    path=self._command_journal_path,
                    queue_max=int(command_journal_queue_max),
                    batch_size=int(command_journal_batch_size),
                    flush_interval_ms=int(command_journal_flush_interval_ms),
                    retention_max_rows=(
                        None
                        if command_journal_retention_max_rows is None
                        else int(command_journal_retention_max_rows)
                    ),
                    retention_max_age_days=(
                        None
                        if command_journal_retention_max_age_days is None
                        else float(command_journal_retention_max_age_days)
                    ),
                )
                self._command_journal = CommandJournal(
                    settings=settings,
                    instance_id=self._instance_id,
                )
                self._command_journal.start()
            except Exception as e:
                self._command_journal = None
                self._command_journal_enabled = False
                self._command_journal_start_error = str(e)

        self._manager_log_stderr_enabled = self._resolve_manager_log_stderr_enabled(
            manager_log_stderr
        )
        self._manager_log_min_level = self._resolve_manager_log_min_level(
            manager_log_min_level
        )
        self._manager_log_min_level_rank = self._severity_rank(
            self._manager_log_min_level
        )
        self._manager_log_file_path = self._resolve_manager_log_file_path(
            manager_log_file
        )
        self._manager_log_file: TextIO | None = None
        self._manager_log_sink_recent: dict[str, float] = {}
        self._manager_log_sink_recent_window_s = 0.5
        self._manager_log_sink_recent_max = 256
        self._open_manager_log_sink_file()

        # Latest fast-data descriptor cache: (device_id -> stream_name -> descriptor json)
        self._latest_chunk_desc: dict[str, dict[str, Json]] = {}
        self._command_interceptor_routes: list[CommandInterceptorRoute] = []
        self._command_interceptor_order = 0
        self._command_interceptor_cache_max = 2048
        self._command_interceptor_cache: dict[tuple[str, str], list[CommandInterceptorRoute]] = {}
        self._runtime_device_metadata_overrides: dict[str, dict[str, Any]] = {}
        self._runtime_stream_metadata_overrides: dict[
            str, dict[str, dict[str, Any]]
        ] = {}
        self._runtime_metadata_revision: dict[str, int] = {}

        # Optional hooks for in-process consumers (handy for unit tests / local GUI)
        self._event_hooks: list[Callable[[str, Json], None]] = []
        self._rpc_seq = 0
        self._stop = False
        self._shutdown_requested = False
        self._process_guard = ProcessGuardian()
        self._process_guard_attach_failures = 0
        self._process_guard_last_error: str | None = None
        self._process_guard_init_error = self._process_guard.init_error
        if self._process_guard_init_error:
            self._emit_log(
                severity="warning",
                topic="manager.process_guard.init_failed",
                message=self._process_guard_init_error,
                source_kind="manager",
                source_id="manager",
                stream="event",
            )

        self._poller = zmq.Poller()
        self._poller.register(self._registry_rep, zmq.POLLIN)
        self._poller.register(self._sub, zmq.POLLIN)
        self._poller.register(self._internal_rpc, zmq.POLLIN)
        self._poller.register(self._process_hb_sub, zmq.POLLIN)
        self._poller.register(self._process_data_sub, zmq.POLLIN)

        self._federation_hub = FederationHub(
            ctx=self._ctx,
            poller=self._poller,
            manager=self,
            config=self._federation_config,
            instance_id=self._instance_id,
        )

        self._router_process_id = "device_router"
        self._ensure_router_handle()

    # -----------------------------
    # Public API
    # -----------------------------

    def add_device(self, spec: DeviceSpec) -> None:
        if spec.device_id in self._devices:
            raise ValueError(f"Duplicate device_id {spec.device_id!r}")
        self._devices[spec.device_id] = DeviceHandle(spec=spec)

    def start_driver(self, device_id: str) -> None:
        handle = self._devices.get(device_id)
        if handle is None:
            raise KeyError(f"Unknown device_id {device_id!r}")
        if handle.process is not None and handle.process.poll() is None:
            return
        if handle.driver_process_state == ManagedProcessState.CRASHLOOP:
            return

        cmd = self._build_driver_cmd(handle.spec)
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        # Always inject runtime-resolved local endpoints for driver subprocesses.
        # Composite/virtual drivers can consume these without hardcoded ports.
        env["EXPERIMENT_CONTROL_ROUTER_RPC"] = derive_local_connect_endpoint(
            self._external_rpc_bind, 6000
        )
        env["EXPERIMENT_CONTROL_MANAGER_PUB"] = self._external_pub_connect_local
        env["EXPERIMENT_CONTROL_INSTANCE_ID"] = self._instance_id
        try:
            handle.process = subprocess.Popen(
                cmd,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            handle.process = None
            handle.driver_pid = None
            handle.driver_process_state = ManagedProcessState.FAILED
            handle.driver_last_error = str(e)
            self._publish_driver_event("manager.driver.failed", handle)
            self._emit_log(
                severity="error",
                topic="manager.driver.spawn_error",
                message=str(e),
                source_kind="driver",
                source_id=device_id,
                device_id=device_id,
                stream="event",
                payload={"device_id": device_id, "cmd": cmd},
            )
            raise
        self._adopt_with_process_guard(
            handle.process, target_kind="driver", target_id=device_id
        )
        handle.driver_pid = handle.process.pid
        handle.driver_process_state = ManagedProcessState.STARTING
        handle.driver_last_exit_code = None
        handle.driver_stop_requested_t_mono = None
        handle.driver_last_error = None
        handle.driver_next_restart_t_mono = None
        self._start_child_log_readers(
            popen=handle.process,
            source_kind="driver",
            source_id=device_id,
            device_id=device_id,
            process_id=None,
        )

        self._publish_driver_event("manager.driver.starting", handle)

    def stop_driver(self, device_id: str, *, force: bool = False) -> None:
        handle = self._devices.get(device_id)
        if handle is None:
            raise KeyError(f"Unknown device_id {device_id!r}")

        self._mark_device_offline(device_id, handle)

        if handle.process is None or handle.process.poll() is not None:
            handle.process = None
            handle.driver_pid = None
            handle.driver_process_state = ManagedProcessState.STOPPED
            self._close_device_rpc(handle)
            handle.rpc_endpoint = None
            handle.pub_endpoint = None
            self._publish_driver_event("manager.driver.stopped", handle)
            return

        if handle.rpc_endpoint is not None:
            try:
                self._call_device_rpc(
                    device_id=device_id,
                    action="shutdown",
                    params={},
                    timeout_ms=1000,
                )
            except Exception:
                pass

        if force:
            try:
                handle.process.terminate()
            except Exception as e:
                handle.driver_last_error = str(e)
            try:
                handle.process.kill()
            except Exception as e:
                handle.driver_last_error = str(e)

        handle.driver_process_state = ManagedProcessState.STOPPING
        handle.driver_stop_requested_t_mono = time.monotonic()
        self._close_device_rpc(handle)
        self._publish_driver_event("manager.driver.stopping", handle)

    def _mark_device_offline(self, device_id: str, handle: DeviceHandle) -> None:
        age = self._heartbeat_timeout_s + 1.0
        handle.last_hb_recv_mono = time.monotonic() - age
        self._last_liveness[device_id] = Liveness.OFFLINE
        self._publish_manager_event(
            "manager.liveness",
            {"device_id": device_id, "liveness": Liveness.OFFLINE, "age_s": age},
        )

    def _driver_is_started(self, handle: DeviceHandle) -> bool:
        if handle.process is not None and handle.process.poll() is None:
            return True
        return handle.driver_process_state in {
            ManagedProcessState.STARTING,
            ManagedProcessState.RUNNING,
            ManagedProcessState.STOPPING,
        }

    def _driver_is_stopped(self, handle: DeviceHandle) -> bool:
        if handle.process is None or handle.process.poll() is not None:
            return True
        return handle.driver_process_state in {
            ManagedProcessState.STOPPED,
            ManagedProcessState.EXITED,
            ManagedProcessState.FAILED,
        }

    def restart_driver(self, device_id: str, *, force: bool = False) -> None:
        handle = self._devices.get(device_id)
        if handle is None:
            raise KeyError(f"Unknown device_id {device_id!r}")

        self._publish_driver_event("manager.driver.restart_requested", handle)
        try:
            self.disconnect_device(device_id)
        except Exception:
            pass

        self.stop_driver(device_id, force=force)
        handle.driver_next_restart_t_mono = (
            time.monotonic() + handle.spec.driver_restart_backoff_s
        )
        self._publish_driver_event("manager.driver.restart_scheduled", handle)

    def recover_device(
        self, device_id: str, *, reconnect: bool = True, force: bool = False
    ) -> None:
        handle = self._devices.get(device_id)
        if handle is None:
            raise KeyError(f"Unknown device_id {device_id!r}")

        try:
            self.disconnect_device(device_id)
        except Exception:
            pass

        self.restart_driver(device_id, force=force)
        self._publish_manager_event(
            "manager.device.recover_sent",
            {
                "device_id": device_id,
                "reconnect": reconnect,
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            },
        )

    # -----------------------------
    # Managed process public API
    # -----------------------------

    def add_process(self, spec: ProcessSpec) -> None:
        if spec.process_id in self._processes:
            raise ValueError(f"Duplicate process_id {spec.process_id!r}")
        hb_endpoint = self._resolve_process_heartbeat_endpoint(spec)
        data_endpoint = self._resolve_process_data_endpoint(spec)
        handle = ProcessHandle(
            spec=spec,
            heartbeat_endpoint=hb_endpoint,
            process_data_endpoint=data_endpoint,
        )
        self._processes[spec.process_id] = handle
        self._connect_process_heartbeat(hb_endpoint)
        self._connect_process_data(data_endpoint)
        self._publish_process_event(
            "manager.process.added",
            handle,
        )

    def _build_router_spec(self) -> ProcessSpec:
        router_path = (
            Path(__file__).resolve().parent / "processes" / "device_router.py"
        )
        # Router startup does a manager RPC advertisement before heartbeats begin.
        # Keep startup supervision timeout comfortably above that RPC timeout.
        router_heartbeat_timeout_s = max(
            3.0,
            (float(self._device_rpc_timeout_ms) / 1000.0) + 2.0,
        )
        init_kwargs = {
            "external_rpc_bind": self._external_rpc_bind,
            "device_rpc_timeout_ms": self._device_rpc_timeout_ms,
            "interceptor_rpc_timeout_ms": self._interceptor_rpc_timeout_ms,
            "federation_mirrors": self._federation_hub.mirror_route_entries(),
            "origin_instance_id": self._instance_id,
        }
        argv = [
            sys.executable,
            "-m",
            "experiment_control.cli.start_process",
            "--process-class-path",
            str(router_path),
            "--process-class-name",
            "DeviceRouter",
            "--process-init-json",
            json.dumps(init_kwargs),
            "--manager-rpc",
            self._internal_rpc_endpoint,
            "--manager-pub",
            self._external_pub_connect_local,
        ]
        return ProcessSpec(
            process_id=self._router_process_id,
            argv=argv,
            heartbeat_period_s=1.0,
            heartbeat_timeout_s=router_heartbeat_timeout_s,
            shutdown_timeout_s=3.0,
            restart_policy=RestartPolicy.ALWAYS,
            restart_backoff_s=0.5,
            max_restarts=None,
        )

    def _ensure_router_handle(self) -> ProcessHandle:
        handle = self._processes.get(self._router_process_id)
        if handle is None:
            spec = self._build_router_spec()
            self.add_process(spec)
            handle = self._processes[self._router_process_id]
        return handle

    def _ensure_router_running(self, *, timeout_s: float, poll_ms: int) -> None:
        handle = self._ensure_router_handle()
        self._start_process_handle(handle)
        deadline = time.monotonic() + timeout_s
        while handle.state != ManagedProcessState.RUNNING:
            if time.monotonic() > deadline:
                # Flush pending stdout/stderr so startup failures include the real cause.
                self._drain_supervisor_logs(max_items=5000)
                self._flush_stale_supervisor_blocks(force=True)
                if handle.state in {
                    ManagedProcessState.FAILED,
                    ManagedProcessState.EXITED,
                    ManagedProcessState.CRASHLOOP,
                }:
                    raise RuntimeError(self._format_router_startup_failure(handle))
                if handle.popen is not None and handle.popen.poll() is not None:
                    raise RuntimeError(self._format_router_startup_failure(handle))
                raise TimeoutError("Timed out waiting for device_router RUNNING")
            self._pump_once(poll_ms=poll_ms)

    def _recent_process_logs(self, *, process_id: str, limit: int = 6) -> list[str]:
        pid = process_id.strip()
        if not pid or limit <= 0:
            return []
        out: list[str] = []
        for entry in reversed(self._log_history):
            if not isinstance(entry, dict):
                continue
            source_kind = str(entry.get("source_kind", "") or "").strip().lower()
            if source_kind != "process":
                continue
            source_id = self._normalize_id(entry.get("source_id"))
            entry_process_id = self._normalize_id(entry.get("process_id"))
            if source_id != pid and entry_process_id != pid:
                continue
            message = str(entry.get("message", "") or "").strip()
            if not message:
                continue
            if len(message) > 220:
                message = message[:217] + "..."
            severity = self._normalize_log_severity(entry.get("severity"))
            stream = str(entry.get("stream", "event") or "event").strip()
            out.append(f"{severity}/{stream}: {message}")
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def _format_router_startup_failure(self, handle: ProcessHandle) -> str:
        process_id = handle.spec.process_id
        exit_code = handle.last_exit_code
        if exit_code is None and handle.popen is not None:
            try:
                polled = handle.popen.poll()
                if polled is not None:
                    exit_code = int(polled)
            except Exception:
                exit_code = None

        details: list[str] = []
        if exit_code is not None:
            details.append(f"exit_code={exit_code}")
        if handle.last_error:
            details.append(f"last_error={handle.last_error}")

        recent_logs = self._recent_process_logs(process_id=process_id, limit=6)
        if recent_logs:
            details.append("recent_logs=" + " | ".join(recent_logs))

        if not details:
            return f"{process_id} exited during startup"
        return f"{process_id} exited during startup ({'; '.join(details)})"

    def _cleanup_orphans_summary(
        self,
        *,
        dry_run: bool,
        stale_only: bool = True,
        timeout_s: float = 2.0,
    ) -> Json:
        summary = cleanup_orphan_children(
            instance_id=self._instance_id,
            exclude_pids={os.getpid()},
            current_parent_pid=os.getpid(),
            timeout_s=float(timeout_s),
            stale_only=bool(stale_only),
            dry_run=bool(dry_run),
        )
        return {
            "instance_id": self._instance_id,
            "dry_run": bool(summary.get("dry_run", dry_run)),
            "stale_only": bool(summary.get("stale_only", stale_only)),
            "matched": int(summary.get("matched", 0) or 0),
            "terminated": list(summary.get("terminated", [])),
            "failed": list(summary.get("failed", [])),
            "skipped_live_parent": list(summary.get("skipped_live_parent", [])),
            "candidates": list(summary.get("candidates", [])),
        }

    def _record_orphan_cleanup(self, *, source: str, summary: Json) -> None:
        self._last_orphan_cleanup = {
            "source": str(source),
            "ts": {
                "t_wall": float(time.time()),
                "t_mono": float(time.monotonic()),
            },
            "result": summary,
        }

    @staticmethod
    def _is_endpoint_collision_process_start_failure(handle: ProcessHandle) -> bool:
        err = str(handle.last_error or "").lower()
        if "already in use" in err or "bind failed" in err:
            return True
        return False

    def _maybe_recover_process_start_collision(self, handle: ProcessHandle) -> bool:
        if handle.state != ManagedProcessState.STARTING:
            return False
        if handle.startup_collision_retry_done:
            return False
        if not self._is_endpoint_collision_process_start_failure(handle):
            recent = " ".join(
                self._recent_process_logs(process_id=handle.spec.process_id, limit=8)
            ).lower()
            markers = (
                "already in use",
                "bind failed",
                "endpoint is likely already in use",
                "address already in use",
            )
            if not any(marker in recent for marker in markers):
                return False
        handle.startup_collision_retry_done = True
        summary = self._cleanup_orphans_summary(dry_run=False, stale_only=True)
        self._record_orphan_cleanup(
            source="startup_collision_recovery",
            summary=summary,
        )
        self._emit_log(
            severity="warning",
            topic="manager.process.collision_recover",
            message=(
                f"startup collision cleanup for {handle.spec.process_id}: "
                f"matched={summary.get('matched', 0)} "
                f"terminated={len(summary.get('terminated', []))} "
                f"failed={len(summary.get('failed', []))}"
            ),
            source_kind="process",
            source_id=handle.spec.process_id,
            process_id=handle.spec.process_id,
            stream="event",
            payload=summary,
        )
        self._publish_manager_event(
            "manager.process.collision_recover",
            {
                "process_id": handle.spec.process_id,
                "summary": summary,
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            },
        )
        try:
            self._start_process_handle(handle, reset_collision_retry=False)
            return True
        except Exception as e:
            handle.state = ManagedProcessState.FAILED
            handle.last_error = f"collision cleanup retry failed: {e}"
            self._publish_process_event("manager.process.failed", handle)
            return False

    def remove_process(self, process_id: str) -> None:
        handle = self._require_process(process_id)
        if handle.popen is not None and handle.popen.poll() is None:
            raise RuntimeError(f"Process {process_id!r} is still running")
        self._drop_command_interceptor_routes(process_id)
        self._processes.pop(process_id)
        self._publish_process_event("manager.process.removed", handle)

    def start_process(self, process_id: str) -> None:
        handle = self._require_process(process_id)
        self._start_process_handle(handle)

    def stop_process(self, process_id: str) -> None:
        handle = self._require_process(process_id)
        self._stop_process_handle(handle)

    def restart_process(self, process_id: str) -> None:
        handle = self._require_process(process_id)
        self._stop_process_handle(handle)
        handle.next_restart_t_mono = time.monotonic() + handle.spec.restart_backoff_s
        self._publish_process_event("manager.process.restart_scheduled", handle)

    def list_processes(self) -> list[Json]:
        return [self._process_snapshot(h) for h in self._processes.values()]

    def get_process(self, process_id: str) -> Json:
        handle = self._require_process(process_id)
        return self._process_snapshot(handle)

    def start_all_processes(self) -> None:
        for handle in self._processes.values():
            self._start_process_handle(handle)

    def stop_all_processes(self) -> None:
        for handle in self._processes.values():
            self._stop_process_handle(handle)

    def start_all_drivers(self) -> None:
        for device_id in self._devices.keys():
            self.start_driver(device_id)

    def stop_all_drivers(self, *, force: bool = False) -> None:
        for device_id in self._devices.keys():
            self.stop_driver(device_id, force=force)

    def _build_driver_cmd(self, spec: DeviceSpec) -> list[str]:
        stream_calls_json = json.dumps(
            stream_calls_to_json(list(spec.stream_calls or []))
        )
        run_meta_calls_json = json.dumps(
            run_meta_calls_to_json(list(spec.run_meta_calls or []))
        )
        return [
            sys.executable,
            "-m",
            "experiment_control.cli.start_driver",
            "--registry",
            self._registry_bind,
            "--device-id",
            spec.device_id,
            "--device-class-path",
            str(spec.device_class_path),
            "--device-class-name",
            spec.device_class_name,
            "--device-init-json",
            json.dumps(spec.device_init_kwargs),
            "--telemetry-period-s",
            str(spec.telemetry_period_s),
            "--heartbeat-period-s",
            str(spec.heartbeat_period_s),
            "--command-poll-period-s",
            str(spec.command_poll_period_s),
            "--telemetry-calls-json",
            json.dumps(telemetry_calls_to_json(spec.telemetry_calls)),
            "--stream-calls-json",
            stream_calls_json,
            "--run-meta-calls-json",
            run_meta_calls_json,
            "--instance-id",
            self._instance_id,
            "--parent-pid",
            str(os.getpid()),
        ]

    def _adopt_with_process_guard(
        self,
        popen: subprocess.Popen[str] | None,
        *,
        target_kind: str,
        target_id: str,
    ) -> None:
        if popen is None:
            return
        if not hasattr(self, "_process_guard_attach_failures"):
            self._process_guard_attach_failures = 0
        if not hasattr(self, "_process_guard_last_error"):
            self._process_guard_last_error = None
        try:
            self._process_guard.adopt_popen(popen)
        except Exception as exc:
            self._process_guard_attach_failures += 1
            self._process_guard_last_error = str(exc)
            self._emit_log(
                severity="warning",
                topic="manager.process_guard.adopt_failed",
                message=str(exc),
                source_kind="manager",
                source_id="manager",
                stream="event",
                payload={
                    "target_kind": str(target_kind or "unknown"),
                    "target_id": str(target_id or ""),
                    "pid": int(getattr(popen, "pid", -1) or -1),
                    "attach_failures": int(self._process_guard_attach_failures),
                },
            )

    def _require_process(self, process_id: str) -> ProcessHandle:
        handle = self._processes.get(process_id)
        if handle is None:
            raise KeyError(f"Unknown process_id {process_id!r}")
        return handle

    def _resolve_process_heartbeat_endpoint(self, spec: ProcessSpec) -> str:
        if spec.heartbeat_endpoint is not None:
            return spec.heartbeat_endpoint

        scheme_host, _, port_str = self._process_hb_bind_base.rpartition(":")
        if not scheme_host or not port_str.isdigit():
            raise ValueError(
                f"process_hb_bind_base must be tcp://host:port, got {self._process_hb_bind_base!r}"
            )
        base_port = int(port_str)
        port = base_port + self._process_hb_port_offset
        self._process_hb_port_offset += 1
        return f"{scheme_host}:{port}"

    def _resolve_process_data_endpoint(self, spec: ProcessSpec) -> str:
        if spec.process_data_endpoint is not None:
            return spec.process_data_endpoint

        scheme_host, _, port_str = self._process_data_bind_base.rpartition(":")
        if not scheme_host or not port_str.isdigit():
            raise ValueError(
                "process_data_bind_base must be tcp://host:port, "
                f"got {self._process_data_bind_base!r}"
            )
        base_port = int(port_str)
        port = base_port + self._process_data_port_offset
        self._process_data_port_offset += 1
        return f"{scheme_host}:{port}"

    def _connect_process_heartbeat(self, endpoint: str) -> None:
        if endpoint in self._process_hb_connected:
            return
        self._process_hb_sub.connect(endpoint)
        self._process_hb_connected.add(endpoint)

    def _connect_process_data(self, endpoint: str) -> None:
        if endpoint in self._process_data_connected:
            return
        self._process_data_sub.connect(endpoint)
        self._process_data_connected.add(endpoint)

    def _expand_process_argv(self, argv: list[str], handle: ProcessHandle) -> list[str]:
        out: list[str] = []
        for arg in argv:
            if isinstance(arg, str):
                arg = arg.replace("{process_id}", handle.spec.process_id)
                arg = arg.replace("{heartbeat_endpoint}", handle.heartbeat_endpoint)
                arg = arg.replace("{process_data_endpoint}", handle.process_data_endpoint)
            out.append(arg)
        return out

    def _start_process_handle(
        self,
        handle: ProcessHandle,
        *,
        reset_collision_retry: bool = True,
    ) -> None:
        if handle.popen is not None and handle.popen.poll() is None:
            return
        if handle.state == ManagedProcessState.CRASHLOOP:
            return

        if not handle.heartbeat_endpoint:
            handle.heartbeat_endpoint = self._resolve_process_heartbeat_endpoint(
                handle.spec
            )
        self._connect_process_heartbeat(handle.heartbeat_endpoint)
        if not handle.process_data_endpoint:
            handle.process_data_endpoint = self._resolve_process_data_endpoint(
                handle.spec
            )
        self._connect_process_data(handle.process_data_endpoint)

        argv = self._expand_process_argv(list(handle.spec.argv), handle)
        argv += [
            "--process-id",
            handle.spec.process_id,
            "--heartbeat-endpoint",
            handle.heartbeat_endpoint,
            "--process-data-endpoint",
            handle.process_data_endpoint,
            "--instance-id",
            self._instance_id,
            "--parent-pid",
            str(os.getpid()),
        ]

        env = os.environ.copy()
        if handle.spec.env:
            env.update(handle.spec.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["EXPERIMENT_CONTROL_INSTANCE_ID"] = self._instance_id

        try:
            handle.popen = subprocess.Popen(
                argv,
                cwd=handle.spec.cwd,
                env=env,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            handle.popen = None
            handle.pid = None
            handle.state = ManagedProcessState.FAILED
            handle.last_error = str(e)
            self._publish_process_event("manager.process.failed", handle)
            self._emit_log(
                severity="error",
                topic="manager.process.spawn_error",
                message=str(e),
                source_kind="process",
                source_id=handle.spec.process_id,
                process_id=handle.spec.process_id,
                stream="event",
                payload={"process_id": handle.spec.process_id, "argv": argv},
            )
            raise
        self._adopt_with_process_guard(
            handle.popen, target_kind="process", target_id=handle.spec.process_id
        )
        handle.pid = handle.popen.pid
        handle.state = ManagedProcessState.STARTING
        handle.rpc_endpoint = None
        self._close_process_rpc(handle)
        handle.last_start_t_wall = time.time()
        handle.last_start_t_mono = time.monotonic()
        handle.last_hb_t_wall = None
        handle.last_hb_t_mono = None
        handle.last_exit_code = None
        handle.stop_requested_t_mono = None
        handle.next_restart_t_mono = None
        handle.last_error = None
        if reset_collision_retry:
            handle.startup_collision_retry_done = False
        self._start_child_log_readers(
            popen=handle.popen,
            source_kind="process",
            source_id=handle.spec.process_id,
            device_id=None,
            process_id=handle.spec.process_id,
        )

        self._publish_process_event("manager.process.started", handle)

    def _stop_process_handle(self, handle: ProcessHandle) -> None:
        if handle.popen is None:
            handle.state = ManagedProcessState.STOPPED
            return
        if handle.popen.poll() is not None:
            handle.state = ManagedProcessState.EXITED
            handle.last_exit_code = handle.popen.poll()
            handle.popen = None
            handle.rpc_endpoint = None
            self._close_process_rpc(handle)
            self._publish_process_event("manager.process.exited", handle)
            return

        graceful_requested = False
        if handle.rpc_endpoint is not None:
            req: Json = {
                "type": "process.stop",
                "params": {},
                "request_id": f"mgr-stop-{int(time.time() * 1000)}",
            }
            timeout_ms = max(100, min(int(self._device_rpc_timeout_ms), 500))
            try:
                resp = self._call_process_rpc(
                    process_id=handle.spec.process_id,
                    request=req,
                    timeout_ms=timeout_ms,
                )
                graceful_requested = bool(
                    isinstance(resp, dict) and resp.get("ok", False)
                )
            except Exception:
                graceful_requested = False

        if not graceful_requested:
            try:
                handle.popen.terminate()
            except Exception as e:
                handle.last_error = str(e)
        handle.state = ManagedProcessState.STOPPING
        handle.stop_requested_t_mono = time.monotonic()
        handle.rpc_endpoint = None
        self._close_process_rpc(handle)
        self._publish_process_event("manager.process.stopping", handle)

    def _maybe_schedule_restart(self, handle: ProcessHandle, now_mono: float) -> None:
        if handle.next_restart_t_mono is not None:
            return

        policy = handle.spec.restart_policy
        if policy == RestartPolicy.NEVER:
            return
        if policy == RestartPolicy.ON_FAILURE:
            if handle.last_exit_code is None:
                return
            if handle.last_exit_code == 0:
                return

        delay = max(handle.spec.restart_backoff_s, 0.0)
        handle.next_restart_t_mono = now_mono + delay
        self._publish_process_event("manager.process.restart_scheduled", handle)

    def _try_restart_process(self, handle: ProcessHandle) -> None:
        if (
            handle.spec.max_restarts is not None
            and handle.restart_count >= handle.spec.max_restarts
        ):
            handle.state = ManagedProcessState.CRASHLOOP
            handle.next_restart_t_mono = None
            self._publish_process_event("manager.process.crashloop", handle)
            return

        handle.restart_count += 1
        handle.last_restart_t_mono = time.monotonic()
        handle.next_restart_t_mono = None
        handle.stop_requested_t_mono = None
        self._start_process_handle(handle)

    def _process_snapshot(self, handle: ProcessHandle) -> Json:
        hb_age_s: float | None = None
        now_mono = time.monotonic()
        if handle.last_hb_t_mono is not None:
            hb_age_s = now_mono - handle.last_hb_t_mono
        return {
            "process_id": handle.spec.process_id,
            "argv": handle.spec.argv,
            "cwd": handle.spec.cwd,
            "env": handle.spec.env,
            "heartbeat_period_s": handle.spec.heartbeat_period_s,
            "heartbeat_timeout_s": handle.spec.heartbeat_timeout_s,
            "shutdown_timeout_s": handle.spec.shutdown_timeout_s,
            "restart_policy": handle.spec.restart_policy,
            "restart_backoff_s": handle.spec.restart_backoff_s,
            "max_restarts": handle.spec.max_restarts,
            "state": handle.state,
            "pid": handle.pid,
            "last_start_t_wall": handle.last_start_t_wall,
            "last_start_t_mono": handle.last_start_t_mono,
            "last_hb_t_wall": handle.last_hb_t_wall,
            "last_hb_t_mono": handle.last_hb_t_mono,
            "hb_age_s": hb_age_s,
            "last_exit_code": handle.last_exit_code,
            "restart_count": handle.restart_count,
            "last_restart_t_mono": handle.last_restart_t_mono,
            "last_error": handle.last_error,
            "heartbeat_endpoint": handle.heartbeat_endpoint,
            "process_data_endpoint": handle.process_data_endpoint,
            "rpc_endpoint": handle.rpc_endpoint,
            "registered": handle.rpc_endpoint is not None,
        }

    def _start_child_log_readers(
        self,
        *,
        popen: subprocess.Popen[str],
        source_kind: str,
        source_id: str,
        device_id: str | None,
        process_id: str | None,
    ) -> None:
        pid = int(popen.pid or -1)
        if pid <= 0:
            return
        for stream in ("stdout", "stderr"):
            pipe = getattr(popen, stream, None)
            if pipe is None:
                continue
            key = (source_kind, source_id, pid, stream)
            existing = self._supervisor_log_threads.get(key)
            if existing is not None and existing.is_alive():
                continue

            def _reader(
                *,
                pipe_obj: Any,
                stream_name: str,
                source_kind_name: str,
                source_id_name: str,
                pid_value: int,
                device_id_value: str | None,
                process_id_value: str | None,
            ) -> None:
                try:
                    for line in iter(pipe_obj.readline, ""):
                        text = str(line).rstrip("\r\n")
                        if not text:
                            continue
                        self._queue_supervisor_log(
                            {
                                "source_kind": source_kind_name,
                                "source_id": source_id_name,
                                "stream": stream_name,
                                "pid": pid_value,
                                "device_id": device_id_value,
                                "process_id": process_id_value,
                                "message": text,
                            }
                        )
                except Exception as e:
                    self._queue_supervisor_log(
                        {
                            "source_kind": source_kind_name,
                            "source_id": source_id_name,
                            "stream": stream_name,
                            "pid": pid_value,
                            "device_id": device_id_value,
                            "process_id": process_id_value,
                            "message": f"log stream read failed: {e}",
                            "reader_error": True,
                        }
                    )
                finally:
                    try:
                        pipe_obj.close()
                    except Exception:
                        pass

            thread = threading.Thread(
                target=_reader,
                kwargs={
                    "pipe_obj": pipe,
                    "stream_name": stream,
                    "source_kind_name": source_kind,
                    "source_id_name": source_id,
                    "pid_value": pid,
                    "device_id_value": device_id,
                    "process_id_value": process_id,
                },
                daemon=True,
                name=f"ec-log-{source_kind}-{source_id}-{pid}-{stream}",
            )
            self._supervisor_log_threads[key] = thread
            thread.start()

    def _queue_supervisor_log(self, item: Json) -> None:
        try:
            self._supervisor_log_queue.put_nowait(item)
        except queue.Full:
            self._supervisor_log_dropped += 1

    @staticmethod
    def _supervisor_key(item: Json) -> tuple[str, str, int, str]:
        source_kind = str(item.get("source_kind", "manager") or "manager")
        source_id = str(item.get("source_id", "") or "")
        stream = str(item.get("stream", "stdout") or "stdout")
        pid = -1
        try:
            pid = int(item.get("pid", -1))
        except Exception:
            pid = -1
        return (source_kind, source_id, pid, stream)

    @staticmethod
    def _supervisor_block_start(message: str) -> bool:
        lower = message.strip().lower()
        return (
            lower.startswith("traceback (most recent call last):")
            or lower.startswith("call stack:")
            or lower.startswith("--- logging error ---")
        )

    @staticmethod
    def _supervisor_block_continuation(message: str) -> bool:
        if not message.strip():
            return False
        if message.startswith((" ", "\t")):
            return True
        lower = message.strip().lower()
        if lower.startswith(("traceback (most recent call last):", "call stack:")):
            return True
        if lower.startswith("--- logging error ---"):
            return True
        if lower.startswith(("message:", "arguments:")):
            return True
        if lower.startswith(
            (
                "during handling of the above exception",
                "the above exception was the direct cause of the following exception",
            )
        ):
            return True
        if _EXCEPTION_LINE_RE.match(message) is not None:
            return True
        return False

    def _supervisor_infer_severity(
        self, *, stream: str, message: str, reader_error: bool
    ) -> str:
        if reader_error:
            return "error"

        match = _LOG_LEVEL_PREFIX_RE.match(message)
        if match is None:
            match = _LOG_LEVEL_BRACKET_PREFIX_RE.match(message)
        if match is None:
            match = _LOG_LEVEL_INLINE_RE.search(message)
        if match is None:
            match = _LOG_LEVEL_TABLE_RE.search(message)
        if match is not None:
            return self._normalize_log_severity(match.group(1))

        lower = message.lower()
        if "traceback (most recent call last):" in lower:
            return "error"
        if _EXCEPTION_LINE_RE.match(message.strip()) is not None:
            return "error"
        if "fatal" in lower and "error" in lower:
            return "critical"
        if stream == "stderr":
            return "warning"
        return "info"

    def _emit_supervisor_item(self, item: Json) -> None:
        if not isinstance(item, dict):
            return
        stream = str(item.get("stream", "") or "stdout")
        reader_error = bool(item.get("reader_error", False))
        source_kind = str(item.get("source_kind", "manager") or "manager")
        source_id = str(item.get("source_id", "") or "")
        message = str(item.get("message", "") or "")
        if not message:
            return
        device_id_raw = item.get("device_id")
        process_id_raw = item.get("process_id")
        pid_raw = item.get("pid")
        severity = self._supervisor_infer_severity(
            stream=stream, message=message, reader_error=reader_error
        )
        payload: Json = {}
        try:
            payload["pid"] = int(pid_raw)
        except Exception:
            pass
        self._emit_log(
            severity=severity,
            topic=f"manager.supervisor.{source_kind}.{stream}",
            message=message,
            source_kind=source_kind,
            source_id=source_id or None,
            device_id=str(device_id_raw) if device_id_raw is not None else None,
            process_id=str(process_id_raw) if process_id_raw is not None else None,
            stream=stream,
            payload=payload if payload else None,
        )

    def _flush_stale_supervisor_blocks(
        self, *, max_age_s: float = 0.25, force: bool = False
    ) -> None:
        now = time.monotonic()
        stale_keys: list[tuple[str, str, int, str]] = []
        for key, item in self._supervisor_pending_blocks.items():
            last_update_raw = item.get("last_update_mono", now)
            try:
                last_update = float(last_update_raw)
            except Exception:
                last_update = now
            if force or (now - last_update) >= max_age_s:
                stale_keys.append(key)
        for key in stale_keys:
            item = self._supervisor_pending_blocks.pop(key, None)
            if isinstance(item, dict):
                item.pop("last_update_mono", None)
                self._emit_supervisor_item(item)

    def _prune_supervisor_log_threads(self) -> None:
        stale = [key for key, thread in self._supervisor_log_threads.items() if not thread.is_alive()]
        for key in stale:
            self._supervisor_log_threads.pop(key, None)

    def _drain_supervisor_logs(self, *, max_items: int = 250) -> None:
        if self._supervisor_log_dropped > 0:
            dropped = int(self._supervisor_log_dropped)
            self._supervisor_log_dropped = 0
            self._emit_log(
                severity="warning",
                topic="manager.supervisor.drop",
                message=f"Dropped {dropped} supervisor log lines",
                source_kind="manager",
                source_id="manager",
                stream="event",
                payload={"dropped": dropped},
            )
        self._flush_stale_supervisor_blocks()
        for _ in range(max_items):
            try:
                item = self._supervisor_log_queue.get_nowait()
            except queue.Empty:
                break
            if not isinstance(item, dict):
                continue
            message = str(item.get("message", "") or "")
            if not message:
                continue
            key = self._supervisor_key(item)
            pending = self._supervisor_pending_blocks.get(key)
            if pending is not None:
                if self._supervisor_block_continuation(message):
                    pending_message = str(pending.get("message", "") or "")
                    pending["message"] = (
                        f"{pending_message}\n{message}" if pending_message else message
                    )
                    pending["last_update_mono"] = time.monotonic()
                    continue
                pending.pop("last_update_mono", None)
                self._emit_supervisor_item(pending)
                self._supervisor_pending_blocks.pop(key, None)

            if self._supervisor_block_start(message):
                pending_item = dict(item)
                pending_item["message"] = message
                pending_item["last_update_mono"] = time.monotonic()
                self._supervisor_pending_blocks[key] = pending_item
                continue

            self._emit_supervisor_item(item)
        self._flush_stale_supervisor_blocks()
        self._prune_supervisor_log_threads()

    def connect_device(self, device_id: str) -> Json:
        handle = self._require_running_driver(device_id)
        connect_resp = self._call_device_rpc(
            device_id=device_id, action="connect_device", params={}
        )
        if not self._device_rpc_status_ok(connect_resp):
            handle.connect_check_last = {
                "ok": False,
                "checked_at": {"t_wall": time.time(), "t_mono": time.monotonic()},
                "message": f"connect RPC failed: {self._device_rpc_error_text(connect_resp)}",
            }
            return connect_resp

        check = handle.spec.connect_check
        if not check.enabled:
            handle.connect_check_last = None
            return connect_resp

        identity_resp = self._call_device_rpc(
            device_id=device_id,
            action="identity",
            params={},
        )
        if not self._device_rpc_status_ok(identity_resp):
            message = (
                "connect_check failed: identity RPC failed: "
                f"{self._device_rpc_error_text(identity_resp)}"
            )
            details = {
                "device_id": device_id,
                "identity_error": identity_resp.get("error"),
            }
            return self._connect_check_failed_response(
                handle=handle,
                message=message,
                details=details,
            )

        identity_result = identity_resp.get("result")
        if not isinstance(identity_result, dict):
            message = (
                "connect_check failed: identity RPC must return an object/dict"
            )
            details = {
                "device_id": device_id,
                "actual_type": type(identity_result).__name__,
            }
            return self._connect_check_failed_response(
                handle=handle,
                message=message,
                details=details,
            )

        mismatches: list[dict[str, Any]] = []
        for field_name, expected in check.identity.items():
            actual = identity_result.get(field_name)
            if actual != expected:
                mismatches.append(
                    {
                        "field": field_name,
                        "expected": expected,
                        "actual": actual,
                    }
                )

        if mismatches:
            mismatch = mismatches[0]
            message = (
                f"connect_check failed for {device_id}: identity.{mismatch['field']} "
                f"expected {mismatch['expected']!r}, got {mismatch['actual']!r}"
            )
            details = {
                "device_id": device_id,
                "checks": mismatches,
                "identity": identity_result,
            }
            return self._connect_check_failed_response(
                handle=handle,
                message=message,
                details=details,
            )

        handle.connect_check_last = {
            "ok": True,
            "checked_at": {"t_wall": time.time(), "t_mono": time.monotonic()},
            "message": "identity check passed",
            "details": {
                "expected": copy.deepcopy(check.identity),
                "identity": copy.deepcopy(identity_result),
            },
        }
        self._publish_manager_event(
            "manager.connect_check.passed",
            {
                "device_id": device_id,
                "expected": copy.deepcopy(check.identity),
                "identity": copy.deepcopy(identity_result),
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            },
        )
        return connect_resp

    def disconnect_device(self, device_id: str) -> Json:
        self._require_running_driver(device_id)
        return self._call_device_rpc(
            device_id=device_id, action="disconnect_device", params={}
        )

    @staticmethod
    def _device_rpc_status_ok(resp: Any) -> bool:
        if not isinstance(resp, dict):
            return False
        status = resp.get("status")
        if isinstance(status, str):
            status_norm = status.strip().upper()
            if status_norm == "OK":
                return True
            if status_norm == "ERROR":
                return False
        ok_value = resp.get("ok")
        if isinstance(ok_value, bool):
            return ok_value
        return False

    @staticmethod
    def _device_rpc_error_text(resp: Any) -> str:
        if not isinstance(resp, dict):
            return str(resp)
        err = resp.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip()
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            code = err.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip()
        return "device rpc error"

    def _connect_check_failed_response(
        self,
        *,
        handle: DeviceHandle,
        message: str,
        details: dict[str, Any],
    ) -> Json:
        device_id = handle.spec.device_id
        now = {"t_wall": time.time(), "t_mono": time.monotonic()}
        self._publish_manager_event(
            "manager.connect_check.failed",
            {
                "device_id": device_id,
                "message": message,
                "details": details,
                "ts": now,
            },
        )
        handle.connect_check_last = {
            "ok": False,
            "checked_at": now,
            "message": message,
            "details": details,
        }
        if handle.spec.connect_check.on_fail != "keep_connected":
            try:
                self._call_device_rpc(
                    device_id=device_id,
                    action="disconnect_device",
                    params={},
                )
            except Exception:
                pass
        return {
            "status": "ERROR",
            "error": message,
            "error_code": "connect_check_failed",
            "error_details": details,
        }

    def _require_running_driver(self, device_id: str) -> DeviceHandle:
        handle = self._devices.get(device_id)
        if handle is None:
            raise RuntimeError(f"Device {device_id!r} is not configured")
        if handle.process is None or handle.process.poll() is not None:
            raise RuntimeError(f"Driver process for {device_id!r} is not running")
        return handle

    def connect_all_devices(self) -> dict[str, Json]:
        results: dict[str, Json] = {}
        for device_id, handle in self._devices.items():
            if handle.rpc_endpoint is None:
                continue
            results[device_id] = self.connect_device(device_id)
        return results

    def startup_sequence(
        self,
        *,
        start_drivers: bool = True,
        start_processes: bool = True,
        wait_processes_running: bool | None = None,
        connect: bool | None = None,
        wait_for_registered: bool = True,
        wait_for_online: bool = True,
        timeout_s: float = 10.0,
        poll_ms: int = 50,
    ) -> None:
        return shared_startup_sequence(
            self,
            start_drivers=start_drivers,
            start_processes=start_processes,
            wait_processes_running=wait_processes_running,
            connect=connect,
            wait_for_registered=wait_for_registered,
            wait_for_online=wait_for_online,
            timeout_s=timeout_s,
            poll_ms=poll_ms,
            managed_process_running=ManagedProcessState.RUNNING,
            driver_state_ok=DriverState.OK,
        )

    def _pump_once(self, poll_ms: int = 50) -> None:
        """Run one iteration of the manager poll loop."""
        self._drain_supervisor_logs()
        self._check_timeouts()

        events = dict(self._poller.poll(poll_ms))
        if events.get(self._registry_rep) == zmq.POLLIN:
            self._handle_registry()

        if events.get(self._sub) == zmq.POLLIN:
            self._handle_driver_pub()

        if events.get(self._process_hb_sub) == zmq.POLLIN:
            self._handle_process_pub()
        if events.get(self._process_data_sub) == zmq.POLLIN:
            self._handle_process_data_pub()
        self._federation_hub.handle_poll_events(events)

        if events.get(self._internal_rpc) == zmq.POLLIN:
            self._handle_internal_rpc()
        self._drain_supervisor_logs()

    def run_forever(self, poll_ms: int = 50) -> None:
        """
        Single-threaded poll loop:
        - accepts driver registrations
        - receives telemetry/heartbeat/chunk_ready
        - handles internal RPC from device_router/processes
        - broadcasts state updates to external PUB
        """
        self._ensure_router_running(timeout_s=5.0, poll_ms=poll_ms)
        self._federation_hub.activate()
        while not self._stop:
            self._pump_once(poll_ms)

            if self._shutdown_requested:
                self._shutdown_requested = False
                self._stop = True
                self._shutdown_cleanup()
                break

    def shutdown(self) -> None:
        self._shutdown_requested = True

    def _shutdown_cleanup(self) -> None:
        shared_shutdown_cleanup(self)

    def add_event_hook(self, hook: Callable[[str, Json], None]) -> None:
        self._event_hooks.append(hook)

    # -----------------------------
    # Registry + wiring
    # -----------------------------

    def _handle_registry(self) -> None:
        msg = self._recv_json(self._registry_rep)
        try:
            reg = self._parse_registration(msg)
            handle = self._devices.get(reg.device_id)
            if handle is None:
                raise KeyError(
                    f"Unknown device_id {reg.device_id!r} (not configured in manager)"
                )

            if handle.rpc_endpoint != reg.rpc_endpoint:
                self._close_device_rpc(handle)
            handle.rpc_endpoint = reg.rpc_endpoint
            handle.pub_endpoint = reg.pub_endpoint
            handle.capabilities = reg.capabilities
            handle.driver_process_state = ManagedProcessState.RUNNING
            handle.driver_pid = handle.process.pid if handle.process else None

            # Connect SUB to driver PUB (one SUB can connect to many PUBs)
            self._sub.connect(reg.pub_endpoint)

            # Acknowledge registration promptly (important for REP socket)
            self._send_json(self._registry_rep, {"ok": True})

            self._publish_manager_event(
                "manager.driver_registered",
                {
                    "device_id": reg.device_id,
                    "rpc_endpoint": reg.rpc_endpoint,
                    "pub_endpoint": reg.pub_endpoint,
                    "capabilities": reg.capabilities,
                },
            )
            self._publish_driver_event("manager.driver.running", handle)

            if not handle.config_published:
                self._publish_device_config(handle)
                handle.config_published = True

        except Exception as e:
            self._send_json(self._registry_rep, {"ok": False, "error": str(e)})
            self._publish_manager_event(
                "manager.driver_registration_error", {"error": str(e), "raw": msg}
            )
            return

        # Auto-connect policy
        if self._auto_connect_on_register:
            try:
                resp = self.connect_device(reg.device_id)
                if self._device_rpc_status_ok(resp):
                    self._publish_manager_event(
                        "manager.connect_device_sent",
                        {"device_id": reg.device_id, "response": resp},
                    )
                else:
                    self._publish_manager_event(
                        "manager.connect_device_failed",
                        {
                            "device_id": reg.device_id,
                            "error": self._device_rpc_error_text(resp),
                            "response": resp,
                        },
                    )
            except Exception as e:
                self._publish_manager_event(
                    "manager.connect_device_failed",
                    {"device_id": reg.device_id, "error": str(e)},
                )

    @staticmethod
    def _parse_registration(msg: Json) -> DriverRegistration:
        # expected: {"type":"register", "device_id":..., "rpc_endpoint":..., "pub_endpoint":..., "capabilities": {...}}
        if msg.get("type") != "register":
            raise ValueError(
                f"Registry received non-register message: {msg.get('type')!r}"
            )
        device_id = str(msg["device_id"])
        return DriverRegistration(
            device_id=device_id,
            rpc_endpoint=str(msg["rpc_endpoint"]),
            pub_endpoint=str(msg["pub_endpoint"]),
            capabilities=msg.get("capabilities"),
        )

    @staticmethod
    def _parse_process_spec(raw: Json) -> ProcessSpec:
        process_id = raw.get("process_id")
        if not isinstance(process_id, str) or not process_id:
            raise TypeError("process_id must be a non-empty string")

        argv = raw.get("argv")
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise TypeError("argv must be a list[str]")

        restart_policy = raw.get("restart_policy", RestartPolicy.NEVER)
        if isinstance(restart_policy, str):
            restart_policy = RestartPolicy(restart_policy)
        if not isinstance(restart_policy, RestartPolicy):
            raise TypeError("restart_policy must be a RestartPolicy or string")

        return ProcessSpec(
            process_id=process_id,
            argv=argv,
            cwd=raw.get("cwd"),
            env=raw.get("env"),
            heartbeat_period_s=float(raw.get("heartbeat_period_s", 1.0)),
            heartbeat_timeout_s=float(raw.get("heartbeat_timeout_s", 3.0)),
            shutdown_timeout_s=float(raw.get("shutdown_timeout_s", 3.0)),
            restart_policy=restart_policy,
            restart_backoff_s=float(raw.get("restart_backoff_s", 0.5)),
            max_restarts=raw.get("max_restarts"),
            heartbeat_endpoint=raw.get("heartbeat_endpoint"),
            process_data_endpoint=raw.get("process_data_endpoint"),
        )

    # -----------------------------
    # Receiving driver PUB plane
    # -----------------------------

    def _handle_driver_pub(self) -> None:
        shared_handle_driver_pub(self)

    def _handle_process_pub(self) -> None:
        topic_b, payload_b = self._process_hb_sub.recv_multipart()
        topic = self._normalize_topic(topic_b.decode("utf-8", errors="replace"))
        try:
            msg = safe_json_loads(payload_b)
            if not isinstance(msg, dict):
                self._publish_manager_event(
                    "manager.process.unknown_pub", {"topic": topic}
                )
                return

            if not topic.startswith("process/") or not topic.endswith("/heartbeat"):
                self._publish_manager_event(
                    "manager.process.unknown_pub", {"topic": topic, "raw": msg}
                )
                return

            self._ingest_process_heartbeat(topic, msg)
        except Exception as e:
            self._publish_manager_event(
                "manager.process.heartbeat_error",
                {"topic": topic, "error": str(e)},
            )

    def _handle_process_data_pub(self) -> None:
        topic_b, payload_b = self._process_data_sub.recv_multipart()
        topic = self._normalize_topic(topic_b.decode("utf-8", errors="replace"))
        try:
            msg = safe_json_loads(payload_b)
            if not isinstance(msg, dict):
                self._publish_manager_event(
                    "manager.process.unknown_pub", {"topic": topic}
                )
                return

            if not topic.startswith("manager."):
                self._publish_manager_event(
                    "manager.process.unknown_pub", {"topic": topic, "raw": msg}
                )
                return

            if topic == "manager.log":
                self._emit_log_from_payload(msg, default_topic=topic)
                return

            self._publish_manager_event(topic, msg)
        except Exception as e:
            self._publish_manager_event(
                "manager.process.data_error",
                {"topic": topic, "error": str(e)},
            )

    def _ingest_telemetry(self, msg: Json) -> None:
        shared_ingest_telemetry(
            self,
            msg,
            telemetry_signal_cls=TelemetrySignal,
            timestamp_cls=Timestamp,
            telemetry_quality_enum=TelemetryQuality,
        )

    def _ingest_heartbeat(self, msg: Json) -> None:
        shared_ingest_heartbeat(
            self,
            msg,
            heartbeat_cls=Heartbeat,
            timestamp_cls=Timestamp,
            driver_state_enum=DriverState,
            device_state_enum=DeviceState,
        )

    def _ingest_chunk_ready(self, msg: Json) -> None:
        shared_ingest_chunk_ready(self, msg)

    def _ingest_process_heartbeat(self, topic: str, msg: Json) -> None:
        process_id = str(msg["process_id"])
        topic_pid = topic.split("/")[1] if "/" in topic else ""
        if topic_pid and topic_pid != process_id:
            self._publish_manager_event(
                "manager.process.heartbeat_error",
                {"topic": topic, "error": "process_id mismatch", "payload": msg},
            )
            return
        pid = int(msg["pid"])
        ts = msg.get("ts")
        if not isinstance(ts, dict):
            raise ValueError("process heartbeat missing ts")

        handle = self._processes.get(process_id)
        if handle is None:
            self._publish_manager_event(
                "manager.process.unknown", {"process_id": process_id, "topic": topic}
            )
            return

        handle.pid = pid
        handle.last_hb_t_wall = float(ts["t_wall"])
        handle.last_hb_t_mono = float(ts["t_mono"])
        if handle.state == ManagedProcessState.STARTING:
            handle.state = ManagedProcessState.RUNNING
            handle.startup_collision_retry_done = False
        hb_rpc_endpoint = str(msg.get("rpc_endpoint") or "").strip()
        if hb_rpc_endpoint:
            if handle.rpc_endpoint != hb_rpc_endpoint:
                self._close_process_rpc(handle)
                handle.rpc_endpoint = hb_rpc_endpoint
                self._publish_manager_event(
                    "manager.process.rpc_update",
                    {
                        "process_id": process_id,
                        "rpc_endpoint": hb_rpc_endpoint,
                        "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
                    },
                )

        payload = {
            "process_id": process_id,
            "pid": pid,
            "state": handle.state,
            "heartbeat_endpoint": handle.heartbeat_endpoint,
            "process_data_endpoint": handle.process_data_endpoint,
            "ts": {"t_wall": handle.last_hb_t_wall, "t_mono": handle.last_hb_t_mono},
        }
        if "state" in msg:
            payload["process_state"] = msg["state"]
        if "metrics" in msg:
            payload["metrics"] = msg["metrics"]

        self._publish_manager_event("manager.process.heartbeat", payload)

    # -----------------------------
    # External command routing
    # -----------------------------

    def _command_interceptor_routes_snapshot(self) -> list[Json]:
        return [
            {
                "process_id": r.process_id,
                "device_id": r.device_id,
                "action": r.action,
                "order": r.order,
            }
            for r in sorted(self._command_interceptor_routes, key=lambda r: r.order)
        ]

    def _publish_interceptor_routes_update(
        self, *, process_id: str, routes: list[Json], replace: bool
    ) -> None:
        self._publish_manager_event(
            "manager.command_interceptor.routes_updated",
            {
                "process_id": process_id,
                "routes": routes,
                "replace": replace,
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            },
        )

    def _invalidate_command_interceptor_cache(self) -> None:
        self._command_interceptor_cache.clear()

    def _drop_command_interceptor_routes(self, process_id: str) -> None:
        before = len(self._command_interceptor_routes)
        self._command_interceptor_routes = [
            r for r in self._command_interceptor_routes if r.process_id != process_id
        ]
        if len(self._command_interceptor_routes) != before:
            self._invalidate_command_interceptor_cache()
            self._publish_interceptor_routes_update(
                process_id=process_id, routes=[], replace=True
            )

    def _register_command_interceptor_routes(
        self, process_id: str, routes_raw: Any, *, replace: bool
    ) -> list[Json]:
        if process_id not in self._processes:
            raise KeyError(f"Unknown process_id {process_id!r}")
        if not isinstance(routes_raw, list):
            raise TypeError("routes must be a list")

        if replace:
            self._command_interceptor_routes = [
                r for r in self._command_interceptor_routes if r.process_id != process_id
            ]
            self._invalidate_command_interceptor_cache()

        seen: set[tuple[str, str, str]] = set()
        added: list[Json] = []
        for route in routes_raw:
            if not isinstance(route, dict):
                raise TypeError("route must be an object")
            device_id = str(route.get("device_id", "")).strip()
            action = str(route.get("action", "")).strip()
            if not device_id or not action:
                raise ValueError("route.device_id and route.action are required")
            key = (process_id, device_id, action)
            if key in seen:
                continue
            seen.add(key)
            self._command_interceptor_order += 1
            entry = CommandInterceptorRoute(
                process_id=process_id,
                device_id=device_id,
                action=action,
                order=self._command_interceptor_order,
            )
            self._command_interceptor_routes.append(entry)
            added.append(
                {
                    "process_id": process_id,
                    "device_id": device_id,
                    "action": action,
                    "order": entry.order,
                }
            )

        self._publish_interceptor_routes_update(
            process_id=process_id, routes=added, replace=replace
        )
        self._invalidate_command_interceptor_cache()
        return added

    @staticmethod
    def _match_command_interceptor_route(
        route: CommandInterceptorRoute, device_id: str, action: str
    ) -> bool:
        if route.device_id != "*" and route.device_id != device_id:
            return False
        if route.action != "*" and route.action != action:
            return False
        return True

    def _command_interceptor_chain(
        self, device_id: str, action: str
    ) -> list[CommandInterceptorRoute]:
        key = (device_id, action)
        cached = self._command_interceptor_cache.get(key)
        if cached is not None:
            # Touch entry so eviction behaves as LRU.
            self._command_interceptor_cache.pop(key, None)
            self._command_interceptor_cache[key] = cached
            return list(cached)
        matches = [
            r
            for r in self._command_interceptor_routes
            if self._match_command_interceptor_route(r, device_id, action)
        ]
        matches.sort(key=lambda r: r.order)
        ordered: list[CommandInterceptorRoute] = []
        seen: set[str] = set()
        for r in matches:
            if r.process_id in seen:
                continue
            seen.add(r.process_id)
            ordered.append(r)
        self._command_interceptor_cache[key] = list(ordered)
        max_items = max(32, int(getattr(self, "_command_interceptor_cache_max", 2048)))
        while len(self._command_interceptor_cache) > max_items:
            oldest = next(iter(self._command_interceptor_cache))
            self._command_interceptor_cache.pop(oldest, None)
        return ordered

    def _apply_command_interceptors(
        self, cmd: Json, *, request_id: str | None, caller_process_id: str | None
    ) -> tuple[bool, Json | None, Json | None]:
        device_id = str(cmd.get("device_id", ""))
        action = str(cmd.get("action", ""))
        chain = self._command_interceptor_chain(device_id, action)

        def _is_route_available(process_id: str) -> bool:
            handle = self._processes.get(process_id)
            if handle is None:
                return False
            if handle.state not in {
                ManagedProcessState.STARTING,
                ManagedProcessState.RUNNING,
                ManagedProcessState.STOPPING,
            }:
                return False
            return handle.rpc_endpoint is not None

        def _call(process_id: str, request: Json) -> tuple[str, Json | None, str | None]:
            try:
                resp = self._call_process_rpc(
                    process_id=process_id,
                    request=request,
                    timeout_ms=self._interceptor_rpc_timeout_ms,
                )
                return "ok", resp, None
            except zmq.Again:
                return "timeout", None, None
            except Exception as exc:
                return "unavailable", None, str(exc)

        return apply_command_interceptor_chain(
            initial_command={
                "device_id": device_id,
                "action": action,
                "params": cmd.get("params", {}),
            },
            chain=chain,
            request_id=request_id,
            caller_process_id=caller_process_id,
            is_route_available=_is_route_available,
            call_interceptor=_call,
            publish_event=self._publish_manager_event,
            distinct_ok_false_message=True,
        )

    def _handle_internal_rpc(self) -> None:
        """
        Internal entities (device_router/processes) connect and send requests.
        Manager routes to device/process RPC endpoints or handles manager actions.
        """
        identity, payload_bytes = self._internal_rpc.recv_multipart()
        try:
            req = safe_json_loads(payload_bytes)
            if not isinstance(req, dict):
                raise TypeError("Request must be a JSON object")
            resp = self._route_internal_request(req)
        except Exception as e:
            resp = {"ok": False, "error": repr(e)}

        self._internal_rpc.send_multipart([identity, json_dumps(resp)])

    def _route_internal_request(self, req: Json) -> Json:
        action = req.get("action")
        if action == "telemetry.schema.list":
            return {"ok": True, "result": self._telemetry_schema_list()}

        rtype = req.get("type")
        if rtype == "list_devices":
            return {"ok": True, "devices": self._list_devices_snapshot()}
        if rtype == "telemetry.snapshot":
            return {"ok": True, "result": self._telemetry_snapshot()}
        if rtype == "get_telemetry":
            device_id = str(req["device_id"])
            return {
                "ok": True,
                "telemetry": self._get_device_telemetry_snapshot(device_id),
            }
        device_resp = self._route_device_request(rtype, req)
        if device_resp is not None:
            return device_resp

        process_resp = self._route_process_request(rtype, req)
        if process_resp is not None:
            return process_resp

        manager_resp = self._route_manager_request(rtype, req)
        if manager_resp is not None:
            return manager_resp

        raise ValueError(f"Unknown external request type {rtype!r}")

    def _route_device_request(self, rtype: Any, req: Json) -> Json | None:
        return route_device_request(self, rtype, req)

    def _publish_process_command_response(
        self,
        *,
        process_id: str,
        action: str,
        params: Json,
        response: Json,
        request_id: Any,
        caller_process_id: Any,
        source_kind: str,
        source_id: str,
    ) -> Json:
        self._publish_process_command_event(
            process_id=process_id,
            action=action,
            params=params,
            response=response,
            request_id=request_id,
            caller_process_id=caller_process_id,
            source_kind=source_kind,
            source_id=source_id,
        )
        return response

    def _route_process_request(self, rtype: Any, req: Json) -> Json | None:
        handlers: dict[str, Callable[[Json], Json]] = {
            "process.list_status": self._route_process_list_status,
            "process.get": self._route_process_get,
            "process.start": self._route_process_start,
            "process.stop": self._route_process_stop,
            "process.restart": self._route_process_restart,
            "process.add": self._route_process_add,
            "process.remove": self._route_process_remove,
            "process.rpc.advertise": self._route_process_rpc_advertise,
            "process.rpc": self._route_process_rpc,
            "command_interceptor.register": self._route_command_interceptor_register,
            "command_interceptor.list": self._route_command_interceptor_list,
        }
        handler = handlers.get(str(rtype))
        if handler is None:
            return None
        return handler(req)

    def _route_process_list_status(self, req: Json) -> Json:
        del req
        return {"ok": True, "result": self.list_processes()}

    def _route_process_get(self, req: Json) -> Json:
        process_id = str(req["process_id"])
        return {"ok": True, "result": self.get_process(process_id)}

    def _route_process_control(
        self,
        req: Json,
        *,
        action: str,
        runner: Callable[[str], None],
    ) -> Json:
        process_id = str(req["process_id"])
        request_id = req.get("request_id")
        caller_process_id = req.get("caller_process_id")
        source_kind, source_id = self._normalize_command_source(
            source_kind=req.get("source_kind"),
            source_id=req.get("source_id"),
            caller_process_id=caller_process_id,
        )
        runner(process_id)
        resp = {"ok": True, "result": {"process_id": process_id}}
        return self._publish_process_command_response(
            process_id=process_id,
            action=action,
            params={"process_id": process_id},
            response=resp,
            request_id=request_id,
            caller_process_id=caller_process_id,
            source_kind=source_kind,
            source_id=source_id,
        )

    def _route_process_start(self, req: Json) -> Json:
        return self._route_process_control(req, action="process.start", runner=self.start_process)

    def _route_process_stop(self, req: Json) -> Json:
        return self._route_process_control(req, action="process.stop", runner=self.stop_process)

    def _route_process_restart(self, req: Json) -> Json:
        return self._route_process_control(
            req, action="process.restart", runner=self.restart_process
        )

    def _route_process_add(self, req: Json) -> Json:
        spec_raw = req.get("spec")
        if not isinstance(spec_raw, dict):
            raise TypeError("spec must be a dict")
        spec = self._parse_process_spec(spec_raw)
        self.add_process(spec)
        return {"ok": True, "result": {"process_id": spec.process_id}}

    def _route_process_remove(self, req: Json) -> Json:
        process_id = str(req["process_id"])
        self.remove_process(process_id)
        return {"ok": True, "result": {"process_id": process_id}}

    def _route_process_rpc_advertise(self, req: Json) -> Json:
        process_id = str(req.get("process_id", ""))
        rpc_endpoint = str(req.get("rpc_endpoint", ""))
        if not process_id or not rpc_endpoint:
            return {
                "ok": False,
                "error": {"code": "invalid_advertise", "message": "missing fields"},
            }
        handle = self._processes.get(process_id)
        if handle is None:
            return {"ok": False, "error": {"code": "unknown_process"}}
        if handle.rpc_endpoint != rpc_endpoint:
            self._close_process_rpc(handle)
        handle.rpc_endpoint = rpc_endpoint
        self._publish_manager_event(
            "manager.process.rpc_update",
            {
                "process_id": process_id,
                "rpc_endpoint": rpc_endpoint,
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            },
        )
        return {"ok": True, "result": {"process_id": process_id}}

    def _route_process_rpc(self, req: Json) -> Json:
        process_id = str(req.get("process_id", ""))
        request = req.get("request")
        request_id = req.get("request_id")
        caller_process_id = req.get("caller_process_id")
        source_kind, source_id = self._normalize_command_source(
            source_kind=req.get("source_kind"),
            source_id=req.get("source_id"),
            caller_process_id=caller_process_id,
        )
        process_action = "process.rpc"
        process_params: Json = {}
        if isinstance(request, dict):
            process_action = str(request.get("type", "process.rpc") or "process.rpc")
            raw_params = request.get("params", {})
            if isinstance(raw_params, dict):
                process_params = raw_params
        if not process_id or not isinstance(request, dict):
            resp = {
                "ok": False,
                "error": {"code": "invalid_process_rpc", "message": "bad request"},
            }
            return self._publish_process_command_response(
                process_id=process_id or "unknown",
                action=process_action,
                params=process_params,
                response=resp,
                request_id=request_id,
                caller_process_id=caller_process_id,
                source_kind=source_kind,
                source_id=source_id,
            )
        handle = self._processes.get(process_id)
        if handle is None:
            resp = {"ok": False, "error": {"code": "unknown_process"}}
            return self._publish_process_command_response(
                process_id=process_id,
                action=process_action,
                params=process_params,
                response=resp,
                request_id=request_id,
                caller_process_id=caller_process_id,
                source_kind=source_kind,
                source_id=source_id,
            )
        if handle.state not in {
            ManagedProcessState.STARTING,
            ManagedProcessState.RUNNING,
            ManagedProcessState.STOPPING,
        }:
            resp = {"ok": False, "error": {"code": "process_not_running"}}
            return self._publish_process_command_response(
                process_id=process_id,
                action=process_action,
                params=process_params,
                response=resp,
                request_id=request_id,
                caller_process_id=caller_process_id,
                source_kind=source_kind,
                source_id=source_id,
            )
        if handle.rpc_endpoint is None:
            if (
                process_action == "process.capabilities"
                and handle.state == ManagedProcessState.STARTING
            ):
                resp = {
                    "ok": False,
                    "error": {
                        "code": "process_starting",
                        "message": "process is starting; RPC endpoint not advertised yet",
                        "retry_after_ms": 500,
                    },
                }
            else:
                resp = {"ok": False, "error": {"code": "process_rpc_not_ready"}}
            return self._publish_process_command_response(
                process_id=process_id,
                action=process_action,
                params=process_params,
                response=resp,
                request_id=request_id,
                caller_process_id=caller_process_id,
                source_kind=source_kind,
                source_id=source_id,
            )
        try:
            resp = self._call_process_rpc(
                process_id=process_id,
                request=request,
            )
        except Exception as e:
            resp = {
                "ok": False,
                "error": {"code": "process_rpc_failed", "message": str(e)},
            }
            return self._publish_process_command_response(
                process_id=process_id,
                action=process_action,
                params=process_params,
                response=resp,
                request_id=request_id,
                caller_process_id=caller_process_id,
                source_kind=source_kind,
                source_id=source_id,
            )
        return self._publish_process_command_response(
            process_id=process_id,
            action=process_action,
            params=process_params,
            response=resp,
            request_id=request_id,
            caller_process_id=caller_process_id,
            source_kind=source_kind,
            source_id=source_id,
        )

    def _route_command_interceptor_register(self, req: Json) -> Json:
        process_id = str(req.get("process_id", ""))
        routes_raw = req.get("routes", [])
        replace = bool(req.get("replace", False))
        if not process_id:
            return {
                "ok": False,
                "error": {"code": "invalid_register", "message": "missing process_id"},
            }
        try:
            routes = self._register_command_interceptor_routes(
                process_id, routes_raw, replace=replace
            )
        except Exception as e:
            return {"ok": False, "error": {"code": "register_failed", "message": str(e)}}
        return {"ok": True, "result": {"routes": routes}}

    def _route_command_interceptor_list(self, req: Json) -> Json:
        del req
        return {"ok": True, "result": {"routes": self._command_interceptor_routes_snapshot()}}

    def _route_manager_request(self, rtype: Any, req: Json) -> Json | None:
        handlers: dict[str, Callable[[Json], Json]] = {
            "manager.shutdown": self._route_manager_shutdown,
            "manager.identity": self._route_manager_identity,
            "manager.cleanup_orphans": self._route_manager_cleanup_orphans,
            "manager.log.publish": self._route_manager_log_publish,
            "manager.log.tail": self._route_manager_log_tail,
            "manager.command_journal.status": self._route_manager_command_journal_status,
            "manager.command_journal.tail": self._route_manager_command_journal_tail,
            "manager.event.publish": self._route_manager_event_publish,
        }
        handler = handlers.get(str(rtype))
        if handler is None:
            return None
        return handler(req)

    def _route_manager_shutdown(self, req: Json) -> Json:
        del req
        self.shutdown()
        return {"ok": True, "result": {"status": "shutting_down"}}

    def _route_manager_identity(self, req: Json) -> Json:
        del req
        manager_pid = int(os.getpid())
        lock_status = read_instance_lock_status(self._instance_id)
        lock_effective_status = derive_lock_effective_status(
            lock_status=lock_status,
            manager_pid=manager_pid,
            manager_reachable=True,
            reported_effective_status=None,
        )
        process_guard = getattr(self, "_process_guard", None)
        process_guard_enabled = bool(
            getattr(process_guard, "available", False) if process_guard is not None else False
        )
        process_guard_init_error = getattr(self, "_process_guard_init_error", None)
        if process_guard_init_error is None and process_guard is not None:
            process_guard_init_error = getattr(process_guard, "init_error", None)
        process_guard_attach_failures = int(
            getattr(self, "_process_guard_attach_failures", 0) or 0
        )
        process_guard_last_error = getattr(self, "_process_guard_last_error", None)
        return {
            "ok": True,
            "result": {
                "version": 1,
                "instance_id": self._instance_id,
                "manager_pid": manager_pid,
                "started_ts": {
                    "t_wall": float(self._started_t_wall),
                    "t_mono": float(self._started_t_mono),
                },
                "lock_status": lock_status,
                "lock_effective_status": lock_effective_status,
                "lock_effective_help": lock_effective_status_help(lock_effective_status),
                "last_orphan_cleanup": self._last_orphan_cleanup,
                "process_guard": {
                    "enabled": process_guard_enabled,
                    "init_error": process_guard_init_error,
                    "attach_failures": process_guard_attach_failures,
                    "last_attach_error": process_guard_last_error,
                },
            },
        }

    def _route_manager_cleanup_orphans(self, req: Json) -> Json:
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {
                "ok": False,
                "error": {"code": "invalid_params", "message": "params must be a dict"},
            }
        try:
            dry_run = bool(params.get("dry_run", False))
            stale_only = bool(params.get("stale_only", True))
            timeout_s = float(params.get("timeout_s", 2.0))
            if timeout_s <= 0:
                raise ValueError("timeout_s must be > 0")
        except Exception as e:
            return {
                "ok": False,
                "error": {"code": "invalid_params", "message": str(e)},
            }
        result = self._cleanup_orphans_summary(
            dry_run=dry_run,
            stale_only=stale_only,
            timeout_s=timeout_s,
        )
        self._record_orphan_cleanup(source="rpc", summary=result)
        self._publish_manager_event(
            "manager.orphan_cleanup",
            {
                "result": result,
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            },
        )
        return {"ok": True, "result": result}

    def _route_manager_log_publish(self, req: Json) -> Json:
        payload = req.get("payload")
        if not isinstance(payload, dict):
            return {"ok": False, "error": {"code": "invalid_payload"}}
        entry = self._emit_log_from_payload(payload, default_topic="manager.log.publish")
        return {"ok": True, "result": {"status": "published", "entry": entry}}

    def _route_manager_log_tail(self, req: Json) -> Json:
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {
                "ok": False,
                "error": {
                    "code": "invalid_params",
                    "message": "params must be a dict",
                },
            }
        try:
            result = self._log_tail(params)
        except Exception as e:
            return {
                "ok": False,
                "error": {"code": "invalid_params", "message": str(e)},
            }
        return {"ok": True, "result": result}

    def _route_manager_command_journal_status(self, req: Json) -> Json:
        del req
        return {"ok": True, "result": self._command_journal_status_payload()}

    def _route_manager_command_journal_tail(self, req: Json) -> Json:
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {
                "ok": False,
                "error": {"code": "invalid_params", "message": "params must be a dict"},
            }
        journal = self._command_journal
        if journal is None:
            return {
                "ok": False,
                "error": {
                    "code": "journal_disabled",
                    "message": "command journal is disabled",
                },
            }
        try:
            result = journal.tail(params)
        except Exception as e:
            return {
                "ok": False,
                "error": {"code": "invalid_params", "message": str(e)},
            }
        return {"ok": True, "result": result}

    def _route_manager_event_publish(self, req: Json) -> Json:
        topic = req.get("topic")
        payload = req.get("payload")
        if not isinstance(topic, str) or not topic.strip():
            return {"ok": False, "error": {"code": "invalid_topic"}}
        if not isinstance(payload, dict):
            return {"ok": False, "error": {"code": "invalid_payload"}}
        normalized_topic = self._normalize_topic(topic)
        if normalized_topic == "manager.log":
            self._emit_log_from_payload(payload, default_topic=normalized_topic)
        else:
            self._publish_manager_event(normalized_topic, payload)
        return {"ok": True, "result": {"status": "published"}}

    def _call_device_rpc(
        self,
        *,
        device_id: str,
        action: str,
        params: Json,
        timeout_ms: int | None = None,
        request_id: Any = None,
        caller_process_id: Any = None,
        source_kind: Any = None,
        source_id: Any = None,
        is_remote_target: bool = False,
    ) -> Json:
        return shared_call_device_rpc(
            self,
            device_id=device_id,
            action=action,
            params=params,
            timeout_ms=timeout_ms,
            request_id=request_id,
            caller_process_id=caller_process_id,
            source_kind=source_kind,
            source_id=source_id,
            is_remote_target=is_remote_target,
        )

    def _call_process_rpc(
        self,
        *,
        process_id: str,
        request: Json,
        timeout_ms: int | None = None,
    ) -> Json:
        return shared_call_process_rpc(
            self,
            process_id=process_id,
            request=request,
            timeout_ms=timeout_ms,
        )

    def _close_device_rpc(self, handle: DeviceHandle) -> None:
        sock = handle.rpc_sock
        if sock is None:
            return
        try:
            sock.close(linger=0)
        except Exception:
            pass
        handle.rpc_sock = None
        handle.rpc_fail_count = 0
        handle.rpc_last_fail_t_mono = None

    def _close_process_rpc(self, handle: ProcessHandle) -> None:
        sock = handle.rpc_sock
        if sock is None:
            return
        try:
            sock.close(linger=0)
        except Exception:
            pass
        handle.rpc_sock = None
        handle.rpc_fail_count = 0
        handle.rpc_last_fail_t_mono = None

    # -----------------------------
    # Timeouts + derived states
    # -----------------------------

    def _update_device_liveness(self, now_mono: float) -> None:
        for dev_id, handle in self._devices.items():
            if handle.last_hb_recv_mono is None:
                continue
            age = now_mono - handle.last_hb_recv_mono
            if age > self._heartbeat_timeout_s:
                liveness = Liveness.OFFLINE
            else:
                hb = handle.last_hb
                if hb is not None and not hb.device_reachable:
                    liveness = Liveness.DISCONNECTED
                else:
                    liveness = Liveness.ONLINE

            if self._last_liveness.get(dev_id) != liveness:
                self._last_liveness[dev_id] = liveness
                self._publish_manager_event(
                    "manager.liveness",
                    {"device_id": dev_id, "liveness": liveness, "age_s": age},
                )

    def _mark_stale_telemetry(self, now_mono: float) -> None:
        # Telemetry staleness: driver emits OK/BAD/MISSING; manager derives STALE.
        # Mark staleness per-signal so partial updates do not mask old values.
        for dev_id, signals in self._telemetry_latest.items():
            stale_names: list[str] = []
            stale_ages: list[float] = []
            for sig_name, (sig_bundle_ts, sig) in list(signals.items()):
                age = now_mono - sig_bundle_ts.t_mono
                if age <= self._telemetry_stale_s:
                    continue
                if sig.quality in {
                    TelemetryQuality.OK,
                    TelemetryQuality.BAD,
                    TelemetryQuality.MISSING,
                }:
                    stale_sig = TelemetrySignal(
                        value=sig.value,
                        units=sig.units,
                        quality=TelemetryQuality.STALE,
                        ts=sig.ts,
                        quality_source="manager",
                    )
                    signals[sig_name] = (sig_bundle_ts, stale_sig)
                    stale_names.append(sig_name)
                    stale_ages.append(age)

            if stale_names:
                age_s = max(stale_ages) if stale_ages else None
                self._publish_manager_event(
                    "manager.telemetry_stale",
                    {
                        "version": 1,
                        "device_id": dev_id,
                        "signals": stale_names,
                        "age_s": age_s,
                        "ts": {"t_wall": time.time(), "t_mono": now_mono},
                    },
                )

    def _update_device_driver_exit_state(self, handle: DeviceHandle, rc: int) -> None:
        handle.driver_last_exit_code = int(rc)
        handle.process = None
        handle.driver_pid = None
        if (
            handle.driver_process_state == ManagedProcessState.STOPPING
            and handle.driver_stop_requested_t_mono is not None
        ):
            handle.driver_process_state = ManagedProcessState.STOPPED
            self._publish_driver_event("manager.driver.stopped", handle)
            return
        if rc == 0:
            handle.driver_process_state = ManagedProcessState.STOPPED
            self._publish_driver_event("manager.driver.exited", handle)
            return
        handle.driver_process_state = ManagedProcessState.FAILED
        handle.driver_last_error = handle.driver_last_error or "driver exited"
        self._publish_driver_event("manager.driver.failed", handle)

    def _enforce_device_driver_stop_timeout(
        self, handle: DeviceHandle, now_mono: float
    ) -> None:
        if handle.driver_process_state != ManagedProcessState.STOPPING:
            return
        if (
            handle.driver_stop_requested_t_mono is None
            or handle.process is None
            or handle.process.poll() is not None
        ):
            return
        if now_mono - handle.driver_stop_requested_t_mono > handle.spec.driver_stop_timeout_s:
            try:
                handle.process.kill()
                self._publish_driver_event("manager.driver.killing", handle)
            except Exception as e:
                handle.driver_last_error = str(e)
        if (
            now_mono - handle.driver_stop_requested_t_mono
            > handle.spec.driver_stop_timeout_s + handle.spec.driver_kill_timeout_s
            and handle.process is not None
            and handle.process.poll() is None
        ):
            handle.driver_process_state = ManagedProcessState.FAILED
            handle.driver_last_error = "kill timeout"
            self._publish_driver_event("manager.driver.kill_timeout", handle)

    def _maybe_restart_device_driver(
        self, device_id: str, handle: DeviceHandle, now_mono: float
    ) -> None:
        if (
            handle.driver_next_restart_t_mono is None
            or now_mono < handle.driver_next_restart_t_mono
        ):
            return
        if (
            handle.spec.driver_max_restarts is not None
            and handle.driver_restart_count >= handle.spec.driver_max_restarts
        ):
            handle.driver_process_state = ManagedProcessState.CRASHLOOP
            handle.driver_next_restart_t_mono = None
            self._publish_driver_event("manager.driver.crashloop", handle)
            return
        handle.driver_restart_count += 1
        handle.driver_last_restart_t_mono = now_mono
        handle.driver_next_restart_t_mono = None
        self._publish_driver_event("manager.driver.restarting", handle)
        self.start_driver(device_id)

    def _supervise_device_drivers(self, now_mono: float) -> None:
        for device_id, handle in self._devices.items():
            proc = handle.process
            if proc is not None:
                rc = proc.poll()
                if rc is not None:
                    self._update_device_driver_exit_state(handle, int(rc))
            self._enforce_device_driver_stop_timeout(handle, now_mono)
            self._maybe_restart_device_driver(device_id, handle, now_mono)

    def _update_managed_process_exit_state(self, handle: ProcessHandle, rc: int) -> bool:
        handle.last_exit_code = int(rc)
        handle.popen = None
        handle.pid = None
        handle.rpc_endpoint = None
        self._close_process_rpc(handle)
        if handle.state == ManagedProcessState.STOPPING:
            handle.state = ManagedProcessState.EXITED
            self._publish_process_event("manager.process.exited", handle)
            return False
        if rc == 0:
            handle.state = ManagedProcessState.STOPPED
            self._publish_process_event("manager.process.exited", handle)
            return False
        if self._maybe_recover_process_start_collision(handle):
            return True
        handle.state = ManagedProcessState.FAILED
        handle.last_error = handle.last_error or "process exited"
        self._publish_process_event("manager.process.failed", handle)
        return False

    def _enforce_managed_process_heartbeat_timeout(
        self, handle: ProcessHandle, now_mono: float
    ) -> None:
        if handle.state not in {
            ManagedProcessState.STARTING,
            ManagedProcessState.RUNNING,
        }:
            return
        hb_age: float | None = None
        if handle.last_hb_t_mono is not None:
            hb_age = now_mono - handle.last_hb_t_mono
        elif handle.last_start_t_mono is not None:
            hb_age = now_mono - handle.last_start_t_mono
        if hb_age is None or hb_age <= handle.spec.heartbeat_timeout_s:
            return
        handle.state = ManagedProcessState.FAILED
        handle.last_error = "heartbeat stale"
        if handle.popen is not None and handle.popen.poll() is None:
            try:
                handle.popen.terminate()
                handle.stop_requested_t_mono = now_mono
            except Exception as e:
                handle.last_error = f"heartbeat stale; terminate failed: {e}"
        self._publish_process_event("manager.process.failed", handle)

    def _enforce_managed_process_stop_timeout(
        self, handle: ProcessHandle, now_mono: float
    ) -> None:
        if (
            handle.state != ManagedProcessState.STOPPING
            or handle.stop_requested_t_mono is None
            or handle.popen is None
            or handle.popen.poll() is not None
        ):
            return
        if now_mono - handle.stop_requested_t_mono <= handle.spec.shutdown_timeout_s:
            return
        try:
            handle.popen.kill()
        except Exception as e:
            handle.last_error = str(e)

    def _maybe_restart_managed_process(self, handle: ProcessHandle, now_mono: float) -> None:
        if handle.state in {ManagedProcessState.FAILED, ManagedProcessState.EXITED}:
            if handle.stop_requested_t_mono is None:
                self._maybe_schedule_restart(handle, now_mono)
        if (
            handle.next_restart_t_mono is not None
            and now_mono >= handle.next_restart_t_mono
        ):
            self._try_restart_process(handle)

    def _supervise_managed_processes(self, now_mono: float) -> None:
        for _process_id, handle in self._processes.items():
            popen = handle.popen
            if popen is not None:
                rc = popen.poll()
                if rc is not None and self._update_managed_process_exit_state(handle, int(rc)):
                    continue
            self._enforce_managed_process_heartbeat_timeout(handle, now_mono)
            self._enforce_managed_process_stop_timeout(handle, now_mono)
            self._maybe_restart_managed_process(handle, now_mono)

    def _check_timeouts(self) -> None:
        now_mono = time.monotonic()
        self._update_device_liveness(now_mono)
        self._federation_hub.check_timeouts(now_mono)
        self._mark_stale_telemetry(now_mono)
        self._supervise_device_drivers(now_mono)
        self._supervise_managed_processes(now_mono)

    # -----------------------------
    # Snapshots for external consumers
    # -----------------------------

    def _list_devices_snapshot(self) -> list[Json]:
        out: list[Json] = []
        for dev_id, h in self._devices.items():
            out.append(
                {
                    "device_id": dev_id,
                    "registered": (
                        h.rpc_endpoint is not None and h.pub_endpoint is not None
                    ),
                    "rpc_endpoint": h.rpc_endpoint,
                    "pub_endpoint": h.pub_endpoint,
                    "capabilities": h.capabilities,
                    "source_kind": "local",
                    "is_remote": False,
                    "owner_peer_id": None,
                    "remote_device_id": None,
                }
            )
        out.extend(self._federation_hub.list_devices_snapshot())
        out.sort(key=lambda item: str(item.get("device_id", "")))
        return out

    def _get_device_telemetry_snapshot(self, device_id: str) -> Json:
        device_cache = self._telemetry_latest.get(device_id, {})
        snap: Json = {}
        for name, (bundle_ts, sig) in device_cache.items():
            # Per-signal ts None => use bundle ts (newer spec)
            ts = sig.ts or bundle_ts
            snap[name] = {
                "value": sig.value,
                "units": sig.units,
                "quality": sig.quality,
                "quality_source": sig.quality_source,
                "ts": {"t_wall": ts.t_wall, "t_mono": ts.t_mono},
            }
        return snap

    def _telemetry_snapshot(self) -> Json:
        devices: Json = {}
        for device_id in sorted(self._telemetry_latest.keys()):
            devices[device_id] = self._get_device_telemetry_snapshot(device_id)
        return {
            "generated_ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            "devices": devices,
        }

    def _device_status_snapshot(self, device_id: str) -> Json:
        if self._federation_hub.is_mirrored_device(device_id):
            return self._federation_hub.device_status_snapshot(device_id)
        handle = self._devices.get(device_id)
        if handle is None:
            raise KeyError(f"Unknown device_id {device_id!r}")

        now_mono = time.monotonic()
        hb_age_s: float | None = None
        liveness = Liveness.OFFLINE
        if handle.last_hb_recv_mono is not None:
            hb_age_s = now_mono - handle.last_hb_recv_mono
            if hb_age_s > self._heartbeat_timeout_s:
                liveness = Liveness.OFFLINE
            else:
                if handle.last_hb is not None and not handle.last_hb.device_reachable:
                    liveness = Liveness.DISCONNECTED
                else:
                    liveness = Liveness.ONLINE

        telemetry_age_s: float | None = None
        latest_ts = self._telemetry_last_bundle_ts.get(device_id)
        if latest_ts is None:
            device_cache = self._telemetry_latest.get(device_id, {})
            for bundle_ts, _sig in device_cache.values():
                if latest_ts is None or bundle_ts.t_mono > latest_ts.t_mono:
                    latest_ts = bundle_ts
        if latest_ts is not None:
            telemetry_age_s = now_mono - latest_ts.t_mono

        hb = handle.last_hb
        return {
            "device_id": device_id,
            "registered": (
                handle.rpc_endpoint is not None and handle.pub_endpoint is not None
            ),
            "rpc_endpoint": handle.rpc_endpoint,
            "pub_endpoint": handle.pub_endpoint,
            "liveness": liveness,
            "hb_age_s": hb_age_s,
            "telemetry_age_s": telemetry_age_s,
            "driver_state": hb.driver_state if hb else None,
            "device_state": hb.device_state if hb else None,
            "device_reachable": hb.device_reachable if hb else None,
            "last_error": hb.last_error if hb else None,
            "driver_process": {
                "state": handle.driver_process_state,
                "pid": handle.driver_pid,
                "restart_count": handle.driver_restart_count,
                "last_exit_code": handle.driver_last_exit_code,
                "last_error": handle.driver_last_error,
            },
            "connect_check": copy.deepcopy(handle.connect_check_last),
            "source_kind": "local",
            "is_remote": False,
            "owner_peer_id": None,
            "remote_device_id": None,
        }

    def _list_devices_status_snapshot(self) -> list[Json]:
        device_ids = sorted(set(self._devices) | set(self._federation_hub.mirrored_device_ids()))
        return [self._device_status_snapshot(did) for did in device_ids]

    def _telemetry_schema_list(self) -> Json:
        devices: list[Json] = []
        for device_id in sorted(self._devices.keys()):
            handle = self._devices[device_id]
            signals: list[str] = []
            dtypes: list[str] = []
            units: list[str] = []
            seen: set[str] = set()

            for call in handle.spec.telemetry_calls:
                for out in call.outputs or []:
                    if out.signal in seen:
                        # Configuration error: duplicate signal names for a device.
                        raise ValueError(
                            f"Duplicate telemetry signal {out.signal!r} for device {device_id!r}"
                        )
                    seen.add(out.signal)
                    signals.append(out.signal)
                    dtypes.append(out.dtype)
                    units.append(out.units or "")

            devices.append(
                {
                    "device_id": device_id,
                    "signals": signals,
                    "dtypes": dtypes,
                    "units": units,
                    "source_kind": "local",
                    "is_remote": False,
                    "owner_peer_id": None,
                    "remote_device_id": None,
                }
            )
        devices.extend(self._federation_hub.telemetry_schema_devices())
        devices.sort(key=lambda item: str(item.get("device_id", "")))

        ts = {"t_wall": time.time(), "t_mono": time.monotonic()}
        return {"schema_version": 1, "generated_ts": ts, "devices": devices}

    # -----------------------------
    # Manager -> external PUB
    # -----------------------------

    def _publish_manager_event(self, topic: str, payload: Json) -> None:
        # External subscribers can filter topics at SUBSCRIBE level.
        self._external_pub.send_multipart(
            [topic.encode("utf-8"), json_dumps(payload)]
        )
        if topic == "manager.command":
            self._append_command_journal_entry(payload)
        for hook in self._event_hooks:
            hook(topic, payload)
        if topic != "manager.log":
            self._maybe_publish_log_event(topic, payload)
        self._maybe_emit_manager_log_sink(topic, payload)

    @staticmethod
    def _safe_json(value: Any, *, max_len: int = 4000) -> str:
        try:
            text = json.dumps(value)
        except Exception:
            text = str(value)
        if len(text) > max_len:
            return text[:max_len] + "...(truncated)"
        return text

    @staticmethod
    def _should_journal_command_action(action: Any) -> bool:
        text = str(action or "").strip().lower()
        if not text:
            return True
        if text.startswith("stream__"):
            return False
        if text.startswith("telemetry__"):
            return False
        if text == "capabilities" or text.endswith(".capabilities"):
            return False
        if text.endswith(".status"):
            return False
        if text.endswith(".list_status"):
            return False
        if text in {"device.get_status", "device.list_status", "process.list_status"}:
            return False
        return True

    @staticmethod
    def _normalize_command_source(
        *,
        source_kind: Any,
        source_id: Any,
        caller_process_id: Any,
    ) -> tuple[str, str | None]:
        source_kind_text = str(source_kind or "").strip().lower()
        source_id_text = str(source_id or "").strip()
        caller_text = str(caller_process_id or "").strip()

        if not source_kind_text:
            if caller_text:
                source_kind_text = "process"
                if not source_id_text:
                    source_id_text = caller_text
            else:
                source_kind_text = "manager"
                if not source_id_text:
                    source_id_text = "rpc"

        if not source_id_text and source_kind_text == "process" and caller_text:
            source_id_text = caller_text

        if not source_id_text:
            return source_kind_text, None
        return source_kind_text, source_id_text

    def _append_command_journal_entry(self, payload: Json) -> None:
        journal = self._command_journal
        if journal is None:
            return
        action_text = str(payload.get("action", "") or "")
        if not self._should_journal_command_action(action_text):
            return

        ts = payload.get("ts")
        t_wall = time.time()
        t_mono = time.monotonic()
        if isinstance(ts, dict):
            try:
                t_wall = float(ts.get("t_wall", t_wall))
            except Exception:
                pass
            try:
                t_mono = float(ts.get("t_mono", t_mono))
            except Exception:
                pass

        error_value = payload.get("error")
        error_json = ""
        if error_value is not None:
            error_json = self._safe_json(error_value)

        journal.append(
            {
                "t_wall": t_wall,
                "t_mono": t_mono,
                "instance_id": self._instance_id,
                "device_id": str(payload.get("device_id", "") or ""),
                "action": action_text,
                "params_json": str(payload.get("params_json", "") or ""),
                "ok": bool(payload.get("ok")),
                "status": payload.get("status"),
                "error_json": error_json,
                "result_json": str(payload.get("result_json", "") or ""),
                "request_id": payload.get("request_id"),
                "caller_process_id": payload.get("caller_process_id"),
                "source_kind": payload.get("source_kind"),
                "source_id": payload.get("source_id"),
                "is_remote_target": bool(payload.get("is_remote_target")),
            }
        )

    def _command_journal_status_payload(self) -> Json:
        journal = self._command_journal
        if journal is None:
            return {
                "enabled": False,
                "path": (
                    str(self._command_journal_path)
                    if self._command_journal_path is not None
                    else None
                ),
                "start_error": self._command_journal_start_error,
            }
        return journal.status()

    def _publish_process_command_event(
        self,
        *,
        process_id: str,
        action: str,
        params: Json,
        response: Json,
        request_id: Any = None,
        caller_process_id: Any = None,
        source_kind: Any = None,
        source_id: Any = None,
    ) -> None:
        caller_process_id_text = self._normalize_id(caller_process_id)
        source_kind_text, source_id_text = self._normalize_command_source(
            source_kind=source_kind,
            source_id=source_id,
            caller_process_id=caller_process_id_text,
        )
        status = response.get("status")
        ok: bool | None
        if status in {"OK", "ERROR"}:
            ok = status == "OK"
        elif "ok" in response:
            ok = bool(response.get("ok"))
        else:
            ok = None

        cmd_payload: Json = {
            "version": 1,
            "device_id": f"process:{process_id}",
            "process_id": process_id,
            "action": str(action or ""),
            "params_json": self._safe_json(params),
            "ok": ok,
            "status": status,
            "error": response.get("error"),
            "result_json": self._safe_json(response.get("result")),
            "source_kind": source_kind_text,
            "source_id": source_id_text,
            "is_remote_target": False,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        if request_id is not None:
            cmd_payload["request_id"] = request_id
        if caller_process_id_text is not None:
            cmd_payload["caller_process_id"] = caller_process_id_text
        error_obj = response.get("error")
        error_code = (
            str(error_obj.get("code", "")).strip()
            if isinstance(error_obj, dict)
            else ""
        )
        if (
            str(action or "").strip() == "process.capabilities"
            and error_code in {"process_rpc_not_ready", "process_starting"}
        ):
            handle = self._processes.get(str(process_id))
            if handle is not None and handle.state == ManagedProcessState.STARTING:
                return
        self._publish_manager_event("manager.command", cmd_payload)

    @staticmethod
    def _normalize_log_severity(raw: Any) -> str:
        return normalize_log_severity(raw, default="info")

    @staticmethod
    def _parse_boolish(raw: Any, *, default: bool) -> bool:
        if raw is None:
            return bool(default)
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if not text:
            return bool(default)
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def _resolve_manager_log_stderr_enabled(self, raw: Any) -> bool:
        if raw is None:
            return self._parse_boolish(
                os.environ.get("MANAGER_LOG_STDERR"), default=True
            )
        return self._parse_boolish(raw, default=True)

    def _resolve_manager_log_file_path(self, raw: Any) -> Path | None:
        value = raw
        if value is None:
            value = os.environ.get("MANAGER_LOG_FILE")
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return Path(text).expanduser()

    def _resolve_manager_log_min_level(self, raw: Any) -> str:
        value = raw
        if value is None:
            value = os.environ.get("MANAGER_LOG_MIN_LEVEL")
        text = str(value or "").strip().lower()
        if not text:
            return "error"
        if not is_valid_log_severity(text):
            return "error"
        return normalize_log_severity(text, default="error")

    @staticmethod
    def _severity_rank(raw: Any) -> int:
        return severity_rank(raw, default="info")

    def _open_manager_log_sink_file(self) -> None:
        path = self._manager_log_file_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._manager_log_file = path.open("a", encoding="utf-8", buffering=1)
        except Exception as e:
            self._manager_log_file = None
            if self._manager_log_stderr_enabled:
                try:
                    sys.stderr.write(
                        f"[manager][warning] MANAGER_LOG_FILE open failed: {path} ({e})\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass

    def _close_manager_log_sink_file(self) -> None:
        handle = self._manager_log_file
        self._manager_log_file = None
        if handle is None:
            return
        try:
            handle.close()
        except Exception:
            pass

    def _manager_log_sink_event(
        self, topic: str, payload: Json
    ) -> tuple[str, str, str, str | None, str]:
        if topic == "manager.log":
            severity = self._normalize_log_severity(payload.get("severity"))
            line_topic = self._normalize_topic(str(payload.get("topic") or "manager.log"))
        elif topic.startswith("manager.") and topic.endswith("_error"):
            severity = "error"
            line_topic = self._normalize_topic(topic)
        else:
            raise ValueError("not sink-eligible")

        source_kind = self._normalize_id(payload.get("source_kind")) or "manager"
        source_id = self._normalize_id(payload.get("source_id"))
        message = payload.get("message")
        if message is None:
            message = payload.get("error")
        text = str(message or "").strip()
        if not text:
            payload_json = payload.get("payload_json")
            if isinstance(payload_json, str) and payload_json.strip():
                text = payload_json.strip()
            else:
                text = self._safe_json(payload)
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        return severity, line_topic, source_kind, source_id, text

    def _manager_log_sink_is_duplicate(self, fingerprint: str) -> bool:
        now = time.monotonic()
        recent = getattr(self, "_manager_log_sink_recent", None)
        if not isinstance(recent, dict):
            recent = {}
            self._manager_log_sink_recent = recent
        window_s = float(getattr(self, "_manager_log_sink_recent_window_s", 0.5))
        max_items = int(getattr(self, "_manager_log_sink_recent_max", 256))
        prev = recent.get(fingerprint)
        if prev is not None and (now - prev) <= window_s:
            return True
        recent[fingerprint] = now
        if len(recent) > max_items:
            cutoff = now - window_s
            drop = [key for key, ts in recent.items() if ts < cutoff]
            for key in drop:
                recent.pop(key, None)
            if len(recent) > max_items:
                overflow = len(recent) - max_items
                for key in list(recent.keys())[:overflow]:
                    recent.pop(key, None)
        return False

    def _maybe_emit_manager_log_sink(self, topic: str, payload: Json) -> None:
        shared_maybe_emit_manager_log_sink(self, topic, payload)

    @staticmethod
    def _normalize_id(raw: Any) -> str | None:
        if raw is None:
            return None
        text = str(raw).strip()
        return text if text else None

    def _normalize_log_ts(self, raw: Any) -> Json:
        now_wall = time.time()
        now_mono = time.monotonic()
        if not isinstance(raw, dict):
            return {"t_wall": now_wall, "t_mono": now_mono}
        try:
            t_wall = float(raw.get("t_wall", now_wall))
        except Exception:
            t_wall = now_wall
        try:
            t_mono = float(raw.get("t_mono", now_mono))
        except Exception:
            t_mono = now_mono
        return {"t_wall": t_wall, "t_mono": t_mono}

    def _emit_log(
        self,
        *,
        severity: Any,
        topic: Any,
        message: Any,
        source_kind: Any = "manager",
        source_id: Any = None,
        device_id: Any = None,
        process_id: Any = None,
        stream: Any = "event",
        payload: Json | None = None,
        payload_json: Any = None,
        ts: Any = None,
    ) -> Json:
        sev = self._normalize_log_severity(severity)
        normalized_topic = self._normalize_topic(str(topic or "manager.log"))
        source_kind_text = self._normalize_id(source_kind) or "manager"
        source_id_text = self._normalize_id(source_id)
        device_id_text = self._normalize_id(device_id)
        process_id_text = self._normalize_id(process_id)
        stream_text = self._normalize_id(stream) or "event"
        msg_text = str(message or "")

        if payload_json is None:
            payload_json_text = self._safe_json(payload) if payload is not None else ""
        else:
            payload_json_text = str(payload_json)
            if len(payload_json_text) > 4000:
                payload_json_text = payload_json_text[:4000] + "...(truncated)"

        entry: Json = {
            "version": 1,
            "severity": sev,
            "topic": normalized_topic,
            "source_kind": source_kind_text,
            "source_id": source_id_text,
            "device_id": device_id_text,
            "process_id": process_id_text,
            "stream": stream_text,
            "message": msg_text,
            "payload_json": payload_json_text,
            "ts": self._normalize_log_ts(ts),
        }
        self._log_history.append(entry)
        self._publish_manager_event("manager.log", entry)
        return entry

    def _emit_log_from_payload(
        self, payload: Json, *, default_topic: str = "manager.log"
    ) -> Json:
        source_kind = payload.get("source_kind")
        source_id = payload.get("source_id")
        device_id = payload.get("device_id")
        process_id = payload.get("process_id")

        if source_kind is None:
            if process_id is not None:
                source_kind = "process"
                if source_id is None:
                    source_id = process_id
            elif device_id is not None:
                source_kind = "driver"
                if source_id is None:
                    source_id = device_id
            else:
                source_kind = "manager"

        message = payload.get("message")
        if message is None:
            message = payload.get("error", "")

        raw_payload: Json | None = None
        payload_value = payload.get("payload")
        if isinstance(payload_value, dict):
            raw_payload = payload_value

        return self._emit_log(
            severity=payload.get("severity", "info"),
            topic=payload.get("topic", default_topic),
            message=message,
            source_kind=source_kind,
            source_id=source_id,
            device_id=device_id,
            process_id=process_id,
            stream=payload.get("stream", "event"),
            payload=raw_payload,
            payload_json=payload.get("payload_json"),
            ts=payload.get("ts"),
        )

    @staticmethod
    def _normalize_filter_set(raw: Any, *, field: str) -> set[str] | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            return {text}
        if isinstance(raw, list):
            out: set[str] = set()
            for item in raw:
                text = str(item).strip()
                if text:
                    out.add(text)
            return out if out else None
        raise TypeError(f"{field} must be a string or list[str]")

    @staticmethod
    def _parse_log_tail_limit(raw: Any) -> int:
        try:
            limit = int(raw)
        except Exception as e:
            raise TypeError(f"limit must be int: {e}") from e
        return max(1, min(limit, 5000))

    @staticmethod
    def _parse_log_tail_since_t_mono(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw)
        except Exception as e:
            raise TypeError(f"since_t_mono must be float: {e}") from e

    def _log_tail_filters(self, params: Json) -> dict[str, Any]:
        severity_min_raw = params.get("severity_min")
        severity_min_rank: int | None = None
        if severity_min_raw is not None:
            severity_min_rank = self._severity_rank(severity_min_raw)

        severity_set = self._normalize_filter_set(params.get("severity"), field="severity")
        if severity_set is not None:
            severity_set = {self._normalize_log_severity(item) for item in severity_set}

        source_kind_set = self._normalize_filter_set(
            params.get("source_kind"), field="source_kind"
        )
        if source_kind_set is not None:
            source_kind_set = {item.lower() for item in source_kind_set}

        return {
            "since_t_mono": self._parse_log_tail_since_t_mono(params.get("since_t_mono")),
            "severity_min_rank": severity_min_rank,
            "severity_set": severity_set,
            "source_kind_set": source_kind_set,
            "device_set": self._normalize_filter_set(
                params.get("device_ids"), field="device_ids"
            ),
            "process_set": self._normalize_filter_set(
                params.get("process_ids"), field="process_ids"
            ),
            "source_id_set": self._normalize_filter_set(
                params.get("source_ids"), field="source_ids"
            ),
            "topic_contains": str(params.get("topic_contains", "") or "").strip().lower(),
            "text_contains": str(params.get("text_contains", "") or "").strip().lower(),
        }

    @staticmethod
    def _log_tail_entry_t_mono(entry: Json) -> float | None:
        ts = entry.get("ts")
        if not isinstance(ts, dict):
            return None
        try:
            return float(ts.get("t_mono"))
        except Exception:
            return None

    def _log_tail_matches_time(self, entry: Json, *, filters: dict[str, Any]) -> bool:
        since_t_mono = filters.get("since_t_mono")
        if since_t_mono is not None:
            t_mono = self._log_tail_entry_t_mono(entry)
            if t_mono is None or t_mono < float(since_t_mono):
                return False
        return True

    def _log_tail_matches_severity(self, entry: Json, *, filters: dict[str, Any]) -> bool:
        severity = self._normalize_log_severity(entry.get("severity"))
        severity_min_rank = filters.get("severity_min_rank")
        if severity_min_rank is not None and self._severity_rank(severity) < int(
            severity_min_rank
        ):
            return False
        severity_set = filters.get("severity_set")
        if isinstance(severity_set, set) and severity not in severity_set:
            return False
        return True

    @staticmethod
    def _log_tail_matches_source_kind(entry: Json, *, filters: dict[str, Any]) -> bool:
        source_kind = str(entry.get("source_kind", "") or "").lower()
        source_kind_set = filters.get("source_kind_set")
        if isinstance(source_kind_set, set) and source_kind not in source_kind_set:
            return False
        return True

    def _log_tail_matches_ids(self, entry: Json, *, filters: dict[str, Any]) -> bool:
        device_set = filters.get("device_set")
        device_id = self._normalize_id(entry.get("device_id"))
        if isinstance(device_set, set) and (device_id is None or device_id not in device_set):
            return False

        process_set = filters.get("process_set")
        process_id = self._normalize_id(entry.get("process_id"))
        if isinstance(process_set, set) and (
            process_id is None or process_id not in process_set
        ):
            return False

        source_id_set = filters.get("source_id_set")
        source_id = self._normalize_id(entry.get("source_id"))
        if isinstance(source_id_set, set) and (
            source_id is None or source_id not in source_id_set
        ):
            return False
        return True

    @staticmethod
    def _log_tail_matches_contains(entry: Json, *, filters: dict[str, Any]) -> bool:
        topic_contains = str(filters.get("topic_contains", "") or "")
        if topic_contains:
            topic = str(entry.get("topic", "") or "").lower()
            if topic_contains not in topic:
                return False

        text_contains = str(filters.get("text_contains", "") or "")
        if text_contains:
            message = str(entry.get("message", "") or "").lower()
            payload_json = str(entry.get("payload_json", "") or "").lower()
            if text_contains not in message and text_contains not in payload_json:
                return False

        return True

    def _log_tail_entry_matches(self, entry: Json, *, filters: dict[str, Any]) -> bool:
        if not self._log_tail_matches_time(entry, filters=filters):
            return False
        if not self._log_tail_matches_severity(entry, filters=filters):
            return False
        if not self._log_tail_matches_source_kind(entry, filters=filters):
            return False
        if not self._log_tail_matches_ids(entry, filters=filters):
            return False
        return self._log_tail_matches_contains(entry, filters=filters)

    def _log_tail(self, params: Json) -> Json:
        limit = self._parse_log_tail_limit(params.get("limit", 200))
        filters = self._log_tail_filters(params)

        filtered: list[Json] = []
        for entry in list(self._log_history):
            if self._log_tail_entry_matches(entry, filters=filters):
                filtered.append(entry)

        total = len(filtered)
        if total > limit:
            filtered = filtered[-limit:]

        latest_t_mono: float | None = None
        if filtered:
            latest_t_mono = self._log_tail_entry_t_mono(filtered[-1])

        return {
            "entries": filtered,
            "count": len(filtered),
            "total_matched": total,
            "limit": limit,
            "latest_t_mono": latest_t_mono,
        }

    def _maybe_publish_log_event(self, topic: str, payload: Json) -> None:
        shared_maybe_publish_log_event(self, topic, payload)

    def _publish_process_event(self, topic: str, handle: ProcessHandle) -> None:
        payload = {
            "version": 1,
            "process_id": handle.spec.process_id,
            "state": handle.state,
            "pid": handle.pid,
            "exit_code": handle.last_exit_code,
            "heartbeat_endpoint": handle.heartbeat_endpoint,
            "process_data_endpoint": handle.process_data_endpoint,
            "error": handle.last_error,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        self._publish_manager_event(topic, payload)

    def _publish_driver_event(self, topic: str, handle: DeviceHandle) -> None:
        payload = {
            "version": 1,
            "device_id": handle.spec.device_id,
            "state": handle.driver_process_state,
            "pid": handle.driver_pid,
            "exit_code": handle.driver_last_exit_code,
            "error": handle.driver_last_error,
            "restart_count": handle.driver_restart_count,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        self._publish_manager_event(topic, payload)

    @staticmethod
    def _normalize_runtime_metadata_dict(
        raw: object,
        *,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TypeError(f"{label} must be an object/dict")
        out: dict[str, Any] = {}
        for key, value in raw.items():
            name = str(key).strip()
            if not name:
                raise ValueError(f"{label} keys must be non-empty strings")
            out[name] = copy.deepcopy(value)
        return out

    @classmethod
    def _normalize_runtime_stream_metadata_dict(
        cls,
        raw: object,
        *,
        label: str,
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            raise TypeError(f"{label} must be an object/dict")
        out: dict[str, dict[str, Any]] = {}
        for stream_raw, attrs_raw in raw.items():
            stream = str(stream_raw).strip()
            if not stream:
                raise ValueError(f"{label} stream names must be non-empty strings")
            attrs = cls._normalize_runtime_metadata_dict(
                attrs_raw,
                label=f"{label}.{stream}",
            )
            out[stream] = attrs
        return out

    @staticmethod
    def _merge_stream_metadata_dicts(
        base: dict[str, dict[str, Any]],
        overlay: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for stream, attrs in base.items():
            merged[stream] = dict(attrs)
        for stream, attrs in overlay.items():
            cur = dict(merged.get(stream, {}))
            cur.update(attrs)
            merged[stream] = cur
        return merged

    def _effective_metadata_for_device(
        self, device_id: str, spec: DeviceSpec
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        base_device = copy.deepcopy(spec.device_metadata or {})
        base_stream = copy.deepcopy(spec.stream_metadata or {})
        override_device = copy.deepcopy(
            self._runtime_device_metadata_overrides.get(device_id, {})
        )
        override_stream = copy.deepcopy(
            self._runtime_stream_metadata_overrides.get(device_id, {})
        )
        effective_device = dict(base_device)
        effective_device.update(override_device)
        effective_stream = self._merge_stream_metadata_dicts(base_stream, override_stream)
        return effective_device, effective_stream

    def _runtime_metadata_state(self, device_id: str, handle: DeviceHandle) -> Json:
        base_device = copy.deepcopy(handle.spec.device_metadata or {})
        base_stream = copy.deepcopy(handle.spec.stream_metadata or {})
        override_device = copy.deepcopy(
            self._runtime_device_metadata_overrides.get(device_id, {})
        )
        override_stream = copy.deepcopy(
            self._runtime_stream_metadata_overrides.get(device_id, {})
        )
        effective_device, effective_stream = self._effective_metadata_for_device(
            device_id, handle.spec
        )
        return {
            "device_id": device_id,
            "revision": int(self._runtime_metadata_revision.get(device_id, 0)),
            "base": {
                "device_metadata": base_device,
                "stream_metadata": base_stream,
            },
            "overrides": {
                "device_metadata": override_device,
                "stream_metadata": override_stream,
            },
            "effective": {
                "device_metadata": effective_device,
                "stream_metadata": effective_stream,
            },
        }

    def _touch_runtime_metadata_revision(self, device_id: str) -> int:
        current = int(self._runtime_metadata_revision.get(device_id, 0))
        next_rev = current + 1
        self._runtime_metadata_revision[device_id] = next_rev
        return next_rev

    def _publish_device_config(self, handle: DeviceHandle) -> None:
        payload: Json = self._device_config_payload(handle)
        self._publish_manager_event("manager.device_config", payload)

    def _device_config_payload(self, handle: DeviceHandle) -> Json:
        yaml_text = handle.spec.config_yaml_text
        if yaml_text is None:
            yaml_text = self._serialize_spec_yaml(handle.spec)
        device_metadata, stream_metadata = self._effective_metadata_for_device(
            handle.spec.device_id, handle.spec
        )
        return {
            "version": 1,
            "device_id": handle.spec.device_id,
            "yaml_text": yaml_text,
            "device_metadata": device_metadata,
            "stream_metadata": stream_metadata,
            "connect_check": {
                "enabled": bool(handle.spec.connect_check.enabled),
                "identity": copy.deepcopy(handle.spec.connect_check.identity),
                "on_fail": str(handle.spec.connect_check.on_fail),
            },
            "telemetry_calls": telemetry_calls_to_json(handle.spec.telemetry_calls),
            "stream_calls": stream_calls_to_json(list(handle.spec.stream_calls or [])),
            "run_meta_calls": run_meta_calls_to_json(
                list(handle.spec.run_meta_calls or [])
            ),
            "metadata_revision": int(
                self._runtime_metadata_revision.get(handle.spec.device_id, 0)
            ),
            "source_kind": "local",
            "is_remote": False,
            "owner_peer_id": None,
            "remote_device_id": None,
        }

    def _serialize_spec_yaml(self, spec: DeviceSpec) -> str:
        payload = {
            "device_id": spec.device_id,
            "driver": {
                "file": str(spec.device_class_path),
                "class_name": spec.device_class_name,
            },
            "init_kwargs": spec.device_init_kwargs,
            "telemetry_calls": telemetry_calls_to_json(spec.telemetry_calls),
            "stream_calls": stream_calls_to_json(list(spec.stream_calls or [])),
            "run_meta_calls": run_meta_calls_to_json(list(spec.run_meta_calls or [])),
            "device_metadata": spec.device_metadata or {},
            "stream_metadata": spec.stream_metadata or {},
            "connect_check": {
                "enabled": bool(spec.connect_check.enabled),
                "identity": copy.deepcopy(spec.connect_check.identity),
                "on_fail": str(spec.connect_check.on_fail),
            },
        }
        try:
            import yaml  # type: ignore[import-not-found]

            return yaml.safe_dump(payload, sort_keys=False)
        except Exception:
            return json.dumps(payload, indent=2, sort_keys=False)

    # -----------------------------
    # JSON helpers
    # -----------------------------

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        return topic.strip()

    @staticmethod
    def _parse_timestamp(raw: Json) -> Timestamp:
        return Timestamp(t_wall=float(raw["t_wall"]), t_mono=float(raw["t_mono"]))

    @staticmethod
    def _coerce_enum(enum_cls: Any, value: Any, default: Any) -> Any:
        if isinstance(value, enum_cls):
            return value
        try:
            return enum_cls(value)
        except Exception:
            return default

    @staticmethod
    def _recv_json(sock: zmq.Socket) -> Json:
        data = sock.recv()
        msg = safe_json_loads(data)
        if not isinstance(msg, dict):
            raise TypeError("JSON message must be an object")
        return msg

    @staticmethod
    def _send_json(sock: zmq.Socket, msg: Json) -> None:
        sock.send(json_dumps(msg))
