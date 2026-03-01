from __future__ import annotations

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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

import zmq

from .federation import FederationConfig
from .federation.hub import FederationHub
from .utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    optional_str,
    require_dict,
    require_str,
)
from .utils.manager_network import derive_local_connect_endpoint
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
    fixed_metadata: dict[str, Any] | None = None
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
        fixed_metadata = optional_dict(
            raw_obj.get("fixed_metadata"), path=["fixed_metadata"]
        )
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
        fixed_metadata=fixed_metadata,
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
    raw, yaml_text = load_yaml_file(path, return_text=True)
    try:
        raw_obj = require_dict(raw, path=[])
        process_id = require_str(raw_obj.get("process_id"), path=["process_id"])
        process_raw = raw_obj.get("process")
        argv_raw = raw_obj.get("argv")
        if process_raw is None and argv_raw is None:
            raise ConfigError("<root>", "process or argv must be provided")
        if process_raw is not None and argv_raw is not None:
            raise ConfigError("<root>", "process and argv are mutually exclusive")

        heartbeat_period_s_raw = raw_obj.get("heartbeat_period_s")
        heartbeat_period_s = (
            float(heartbeat_period_s_raw)
            if heartbeat_period_s_raw is not None
            else None
        )
        init_kwargs = optional_dict(raw_obj.get("init_kwargs"), path=["init_kwargs"])
        forbidden = {
            "process_id",
            "manager_rpc",
            "manager_pub",
            "heartbeat_endpoint",
            "process_data_endpoint",
        }
        bad_keys = sorted(set(init_kwargs) & forbidden)
        if bad_keys:
            raise ConfigError(
                "init_kwargs",
                f"contains reserved keys: {', '.join(bad_keys)}",
            )

        argv: list[str]
        if process_raw is not None:
            process_obj = require_dict(process_raw, path=["process"])
            process_file = process_obj.get("file")
            process_module = process_obj.get("module")
            if process_file and process_module:
                raise ConfigError("process", "file and module are mutually exclusive")
            if not process_file and not process_module:
                raise ConfigError("process", "file or module must be provided")
            if process_module:
                module_name = require_str(process_module, path=["process", "module"])
                spec = importlib.util.find_spec(module_name)
                if spec is None or spec.origin is None:
                    raise ConfigError(
                        "process.module", f"module not found: {module_name!r}"
                    )
                process_file = spec.origin
            process_file = require_str(process_file, path=["process", "file"])
            class_name = require_str(
                process_obj.get("class_name"), path=["process", "class_name"]
            )
            argv = [
                sys.executable,
                "-m",
                "experiment_control.cli.start_process",
                "--process-class-path",
                process_file,
                "--process-class-name",
                class_name,
                "--process-init-json",
                json.dumps(init_kwargs),
                "--manager-rpc",
                manager_rpc,
                "--manager-pub",
                manager_pub,
            ]
            if heartbeat_period_s is not None:
                argv += ["--heartbeat-period-s", str(heartbeat_period_s)]
        else:
            argv = normalize_list(argv_raw, path=["argv"])
            if not all(isinstance(a, str) for a in argv):
                raise ConfigError("argv", "must be a list[str]")

        restart_policy = raw_obj.get("restart_policy", RestartPolicy.NEVER)
        if isinstance(restart_policy, str):
            restart_policy = RestartPolicy(restart_policy)
        if not isinstance(restart_policy, RestartPolicy):
            raise ConfigError("restart_policy", "must be a RestartPolicy or string")
        cwd = optional_str(raw_obj.get("cwd"), path=["cwd"])
        env = optional_dict(raw_obj.get("env"), path=["env"])
        env_val = env or None
    except ConfigError as e:
        raise TypeError(str(e)) from None

    return ProcessSpec(
        process_id=process_id,
        argv=argv,
        cwd=cwd,
        env=env_val,
        heartbeat_period_s=(
            float(raw_obj.get("heartbeat_period_s", 1.0))
            if heartbeat_period_s is None
            else heartbeat_period_s
        ),
        heartbeat_timeout_s=float(raw_obj.get("heartbeat_timeout_s", 3.0)),
        shutdown_timeout_s=float(raw_obj.get("shutdown_timeout_s", 3.0)),
        restart_policy=restart_policy,
        restart_backoff_s=float(raw_obj.get("restart_backoff_s", 0.5)),
        max_restarts=raw_obj.get("max_restarts"),
        heartbeat_endpoint=raw_obj.get("heartbeat_endpoint"),
        process_data_endpoint=raw_obj.get("process_data_endpoint"),
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

        # Latest fast-data descriptor cache: (device_id -> stream_name -> descriptor json)
        self._latest_chunk_desc: dict[str, dict[str, Json]] = {}
        self._command_interceptor_routes: list[CommandInterceptorRoute] = []
        self._command_interceptor_order = 0
        self._command_interceptor_cache: dict[tuple[str, str], list[CommandInterceptorRoute]] = {}

        # Optional hooks for in-process consumers (handy for unit tests / local GUI)
        self._event_hooks: list[Callable[[str, Json], None]] = []
        self._rpc_seq = 0
        self._stop = False
        self._shutdown_requested = False

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
            heartbeat_timeout_s=3.0,
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
            if handle.popen is not None and handle.popen.poll() is not None:
                # Flush pending stdout/stderr so startup failures include the real cause.
                self._drain_supervisor_logs(max_items=5000)
                self._flush_stale_supervisor_blocks(force=True)
                raise RuntimeError(self._format_router_startup_failure(handle))
            if time.monotonic() > deadline:
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
        ]

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

    def _start_process_handle(self, handle: ProcessHandle) -> None:
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
        self._require_running_driver(device_id)
        resp = self._call_device_rpc(
            device_id=device_id, action="connect_device", params={}
        )
        if resp.get("ok"):
            try:
                meta_resp = self._call_device_rpc(
                    device_id=device_id, action="collect_run_metadata", params={}
                )
                if meta_resp.get("ok"):
                    self._publish_manager_event(
                        "manager.run_metadata",
                        {
                            "version": 1,
                            "device_id": device_id,
                            "run_metadata": meta_resp.get("result", {}),
                        },
                    )
            except Exception:
                pass
        return resp

    def disconnect_device(self, device_id: str) -> Json:
        self._require_running_driver(device_id)
        return self._call_device_rpc(
            device_id=device_id, action="disconnect_device", params={}
        )

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
        """
        Start a configured manager session in a predictable way.

        Steps:
        1) optionally start managed processes
        2) optionally wait for managed processes RUNNING (or timeout)
        3) optionally start all driver processes
        4) pump the event loop until all drivers register (or timeout)
        5) optionally connect all devices (if connect=True; if connect is None, obey policy)
        6) optionally pump until devices are ONLINE (or timeout)

        Notes:
        - If wait_processes_running is None, it defaults to start_processes.
        This avoids waiting for processes that were not started by this call.

        Raises:
        TimeoutError if registration/online wait exceeds timeout_s.
        """
        self._ensure_router_running(timeout_s=timeout_s, poll_ms=poll_ms)
        self._federation_hub.activate()
        if wait_processes_running is None:
            wait_processes_running = start_processes

        if start_processes:
            self.start_all_processes()

        deadline = time.monotonic() + timeout_s

        if wait_processes_running:
            while True:
                if time.monotonic() > deadline:
                    not_running = [
                        pid
                        for pid, h in self._processes.items()
                        if h.state != ManagedProcessState.RUNNING
                    ]
                    self._emit_log(
                        severity="error",
                        topic="manager.startup.process_timeout",
                        message="Timed out waiting for processes RUNNING",
                        source_kind="manager",
                        source_id="manager",
                        stream="event",
                        payload={"not_running": not_running},
                    )
                    raise TimeoutError(
                        f"Timed out waiting for processes RUNNING: {not_running}"
                    )

                self._pump_once(poll_ms=poll_ms)

                all_running = all(
                    h.state == ManagedProcessState.RUNNING
                    for h in self._processes.values()
                )
                if all_running:
                    break

        if start_drivers:
            self.start_all_drivers()

        def all_registered() -> bool:
            return all(h.rpc_endpoint is not None for h in self._devices.values())

        if wait_for_registered:
            while not all_registered():
                if time.monotonic() > deadline:
                    missing = [
                        k for k, h in self._devices.items() if h.rpc_endpoint is None
                    ]
                    self._emit_log(
                        severity="error",
                        topic="manager.startup.registration_timeout",
                        message="Timed out waiting for registration",
                        source_kind="manager",
                        source_id="manager",
                        stream="event",
                        payload={"missing": missing},
                    )
                    raise TimeoutError(f"Timed out waiting for registration: {missing}")
                self._pump_once(poll_ms=poll_ms)

        # Decide whether to connect: explicit arg wins; otherwise use policy flag.
        do_connect = self._auto_connect_on_register if connect is None else connect
        if do_connect:
            self.connect_all_devices()

        if wait_for_online:
            while True:
                if time.monotonic() > deadline:
                    not_online: list[str] = []
                    for device_id, h in self._devices.items():
                        if h.last_hb is None:
                            not_online.append(device_id)
                            continue
                        if (not h.last_hb.device_reachable) or (
                            h.last_hb.driver_state != DriverState.OK
                        ):
                            not_online.append(device_id)
                    self._emit_log(
                        severity="error",
                        topic="manager.startup.online_timeout",
                        message="Timed out waiting for devices ONLINE",
                        source_kind="manager",
                        source_id="manager",
                        stream="event",
                        payload={"not_online": not_online},
                    )

                    raise TimeoutError(
                        f"Timed out waiting for ONLINE devices: {not_online}"
                    )

                self._pump_once(poll_ms=poll_ms)

                all_online = True
                for h in self._devices.values():
                    if h.last_hb is None:
                        all_online = False
                        break
                    if (not h.last_hb.device_reachable) or (
                        h.last_hb.driver_state != DriverState.OK
                    ):
                        all_online = False
                        break

                if all_online:
                    break

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
        self._federation_hub.close()
        for handle in self._devices.values():
            try:
                self.stop_driver(handle.spec.device_id)
            except Exception:
                pass
        for handle in self._processes.values():
            try:
                self._stop_process_handle(handle)
            except Exception:
                pass
        self._drain_supervisor_logs(max_items=5000)
        self._flush_stale_supervisor_blocks(force=True)
        try:
            self._registry_rep.close(0)
        except Exception:
            pass
        try:
            self._sub.close(0)
        except Exception:
            pass
        try:
            self._process_hb_sub.close(0)
        except Exception:
            pass
        try:
            self._process_data_sub.close(0)
        except Exception:
            pass
        try:
            self._internal_rpc.close(0)
        except Exception:
            pass
        try:
            self._external_pub.close(0)
        except Exception:
            pass
        try:
            self._ctx.term()
        except Exception:
            pass

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
                self._publish_manager_event(
                    "manager.connect_device_sent",
                    {"device_id": reg.device_id, "response": resp},
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
        topic_b, payload_b = self._sub.recv_multipart()
        topic = self._normalize_topic(topic_b.decode("utf-8", errors="replace"))

        msg_any: Any
        try:
                msg_any = safe_json_loads(payload_b)
        except Exception:
            msg_any = None

        msg: Json | None
        if not isinstance(msg_any, dict):
            try:
                msg_any = json.loads(payload_b.decode("utf-8"))
            except Exception as e:
                self._publish_manager_event(
                    "manager.unknown_driver_pub",
                    {"topic": topic, "error": f"decode failed: {e}"},
                )
                return
            if not isinstance(msg_any, dict):
                self._publish_manager_event(
                    "manager.unknown_driver_pub",
                    {"topic": topic, "error": "payload not a dict"},
                )
                return

        msg = msg_any

        # Route by topic suffix; BaseDriver publishes:
        #   f"{device_id}/telemetry"
        #   f"{device_id}/heartbeat"
        if topic.endswith("/telemetry"):
            try:
                self._ingest_telemetry(msg)
            except Exception as e:
                self._publish_manager_event(
                    "manager.telemetry_error",
                    {"error": f"telemetry ingest failed: {e}", "raw": msg},
                )
            return

        if topic.endswith("/heartbeat"):
            # BaseDriver uses "driver_pid"; manager currently expects "pid".
            # Translate here to avoid touching _ingest_heartbeat.
            if "pid" not in msg and "driver_pid" in msg:
                msg["pid"] = msg["driver_pid"]
            if "pid" not in msg:
                self._publish_manager_event(
                    "manager.unknown_driver_pub",
                    {"topic": topic, "error": "heartbeat missing pid", "raw": msg},
                )
                return
            try:
                self._ingest_heartbeat(msg)
            except Exception as e:
                self._publish_manager_event(
                    "manager.heartbeat_error",
                    {"error": f"heartbeat ingest failed: {e}", "raw": msg},
                )
            return

        if topic.endswith("/chunk_ready"):
            try:
                self._ingest_chunk_ready(msg)
            except Exception as e:
                self._publish_manager_event(
                    "manager.chunk_error",
                    {"error": f"chunk ingest failed: {e}", "raw": msg},
                )
            return

        self._publish_manager_event(
            "manager.unknown_driver_pub", {"topic": topic, "raw": msg}
        )

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
        """
        Telemetry payload shape + semantics follow the newer telemetry spec:
        - one dict per update, merged signals
        - per-signal failure/quality
        - bundle ts present; per-signal ts is None unless device provides real timestamps
        """
        device_id_raw = msg.get("device_id")
        if device_id_raw is None:
            self._publish_manager_event(
                "manager.telemetry_error",
                {"error": "telemetry missing device_id", "raw": msg},
            )
            return
        device_id = str(device_id_raw)

        ts_raw = msg.get("ts")
        try:
            ts = self._parse_timestamp(ts_raw)
        except Exception as e:
            ts = Timestamp(t_wall=time.time(), t_mono=time.monotonic())
            self._publish_manager_event(
                "manager.telemetry_error",
                {
                    "device_id": device_id,
                    "error": f"telemetry bad ts: {e}",
                    "raw": msg,
                },
            )

        raw_signals = msg.get("signals")
        if not isinstance(raw_signals, dict):
            self._publish_manager_event(
                "manager.telemetry_error",
                {
                    "device_id": device_id,
                    "error": "telemetry signals must be a dict",
                    "raw": msg,
                },
            )
            return

        device_cache = self._telemetry_latest.setdefault(device_id, {})
        bad_signals: list[str] = []
        for name, s in raw_signals.items():
            if not isinstance(name, str) or not isinstance(s, dict):
                continue
            quality_raw = s.get("quality", TelemetryQuality.BAD)
            quality = self._coerce_enum(
                TelemetryQuality, quality_raw, TelemetryQuality.BAD
            )
            if "quality" in s:
                if quality is TelemetryQuality.BAD and quality_raw not in {
                    TelemetryQuality.BAD,
                    "BAD",
                }:
                    bad_signals.append(name)

            sig_ts = None
            if s.get("ts") is not None:
                try:
                    sig_ts = self._parse_timestamp(s["ts"])
                except Exception:
                    bad_signals.append(name)
                    sig_ts = None

            sig = TelemetrySignal(
                value=s.get("value"),
                units=s.get("units"),
                quality=quality,
                ts=sig_ts,
                quality_source="device",
            )
            device_cache[name] = (ts, sig)
        self._telemetry_last_bundle_ts[device_id] = ts

        if bad_signals:
            self._publish_manager_event(
                "manager.telemetry_error",
                {
                    "device_id": device_id,
                    "signals": sorted(set(bad_signals)),
                    "error": "telemetry had invalid quality or ts",
                },
            )

        # Publish a compact update for GUIs etc (do not spam large blobs unless needed)
        seq = int(msg.get("seq", -1))
        self._publish_manager_event(
            "manager.telemetry_update",
            {
                "version": 1,
                "device_id": device_id,
                "seq": seq,
                "ts": {"t_wall": ts.t_wall, "t_mono": ts.t_mono},
                "signals": raw_signals,
            },
        )

    def _ingest_heartbeat(self, msg: Json) -> None:
        device_id_raw = msg.get("device_id")
        if device_id_raw is None:
            self._publish_manager_event(
                "manager.heartbeat_error",
                {"error": "heartbeat missing device_id", "raw": msg},
            )
            return
        device_id = str(device_id_raw)

        pid_raw = msg.get("pid")
        try:
            pid = int(pid_raw)
        except Exception:
            self._publish_manager_event(
                "manager.heartbeat_error",
                {"device_id": device_id, "error": "heartbeat bad pid", "raw": msg},
            )
            return

        seq_raw = msg.get("seq", -1)
        try:
            seq = int(seq_raw)
        except Exception:
            seq = -1

        ts_raw = msg.get("ts")
        try:
            ts = self._parse_timestamp(ts_raw)
        except Exception as e:
            ts = Timestamp(t_wall=time.time(), t_mono=time.monotonic())
            self._publish_manager_event(
                "manager.heartbeat_error",
                {
                    "device_id": device_id,
                    "error": f"heartbeat bad ts: {e}",
                    "raw": msg,
                },
            )

        driver_state_raw = msg.get("driver_state", DriverState.INIT)
        driver_state = self._coerce_enum(
            DriverState, driver_state_raw, DriverState.INIT
        )
        device_state_raw = msg.get("device_state", DeviceState.UNKNOWN)
        device_state = self._coerce_enum(
            DeviceState, device_state_raw, DeviceState.UNKNOWN
        )

        bad_state = False
        driver_state_values = {s.value for s in DriverState}
        device_state_values = {s.value for s in DeviceState}
        if isinstance(driver_state_raw, str):
            if driver_state_raw not in driver_state_values:
                bad_state = True
        elif "driver_state" in msg and not isinstance(driver_state_raw, DriverState):
            bad_state = True
        if isinstance(device_state_raw, str):
            if device_state_raw not in device_state_values:
                bad_state = True
        elif "device_state" in msg and not isinstance(device_state_raw, DeviceState):
            bad_state = True
        if bad_state:
            self._publish_manager_event(
                "manager.heartbeat_error",
                {
                    "device_id": device_id,
                    "error": "heartbeat had invalid state values",
                    "raw": msg,
                },
            )

        hb = Heartbeat(
            pid=pid,
            seq=seq,
            driver_state=driver_state,
            device_reachable=bool(msg.get("device_reachable", False)),
            device_state=device_state,
            device_health=msg.get("device_health"),
            last_error=msg.get("last_error"),
            last_ok_wall=msg.get("last_ok_wall"),
            last_ok_mono=msg.get("last_ok_mono"),
            loop_lag_s=msg.get("loop_lag_s"),
            ts=ts,
        )
        handle = self._devices.get(device_id)
        if handle is not None:
            handle.last_hb = hb
            handle.last_hb_recv_mono = time.monotonic()
            if handle.driver_pid != hb.pid:
                handle.driver_pid = hb.pid

        self._publish_manager_event(
            "manager.heartbeat",
            {
                "version": 1,
                "device_id": device_id,
                "pid": hb.pid,
                "seq": hb.seq,
                "driver_state": hb.driver_state,
                "device_state": hb.device_state,
                "device_reachable": hb.device_reachable,
                "device_health": hb.device_health,
                "last_error": hb.last_error,
                "last_ok_wall": hb.last_ok_wall,
                "last_ok_mono": hb.last_ok_mono,
                "loop_lag_s": hb.loop_lag_s,
                "ts": {"t_wall": hb.ts.t_wall, "t_mono": hb.ts.t_mono},
            },
        )

    def _ingest_chunk_ready(self, msg: Json) -> None:
        """
        Fast data philosophy: never send raw arrays over IPC; publish a descriptor
        and keep payload out-of-band (shared memory or file).
        """
        desc_raw = (
            msg.get("descriptor") if isinstance(msg.get("descriptor"), dict) else msg
        )
        if not isinstance(desc_raw, dict):
            return

        desc = dict(desc_raw)
        device_id = str(desc.get("device_id") or msg.get("device_id") or "")
        stream = str(desc.get("stream") or msg.get("stream") or "")
        if not device_id or not stream:
            return
        shm_name = desc.get("shm_name")
        if not shm_name:
            return

        desc["version"] = int(desc.get("version", 1))
        desc["device_id"] = device_id
        desc["stream"] = stream
        desc["shm_name"] = shm_name

        if "layout_version" in desc:
            try:
                desc["layout_version"] = int(desc["layout_version"])
            except Exception:
                desc["layout_version"] = 1
        else:
            desc["layout_version"] = 1

        if "seq" in desc and desc["seq"] is not None:
            try:
                desc["seq"] = int(desc["seq"])
            except Exception:
                pass
        if "t0_mono_ns" in desc and desc["t0_mono_ns"] is not None:
            try:
                desc["t0_mono_ns"] = int(desc["t0_mono_ns"])
            except Exception:
                pass
        if "t0_wall_ns" in desc and desc["t0_wall_ns"] is not None:
            try:
                desc["t0_wall_ns"] = int(desc["t0_wall_ns"])
            except Exception:
                pass

        self._latest_chunk_desc.setdefault(device_id, {})[stream] = desc
        self._publish_manager_event("manager.chunk_ready", desc)

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
        return ordered

    def _command_interceptor_error(
        self,
        *,
        code: str,
        message: str,
        process_id: str,
        device_id: str,
        action: str,
        interceptor_id: str | None = None,
        rule: str | None = None,
        details: Json | None = None,
    ) -> Json:
        err: Json = {
            "kind": "command_interceptor",
            "code": code,
            "message": message,
            "process_id": process_id,
            "device_id": device_id,
            "action": action,
        }
        if interceptor_id is not None:
            err["interceptor_id"] = interceptor_id
        if rule is not None:
            err["rule"] = rule
        if details is not None:
            err["details"] = details
        return err

    def _apply_command_interceptors(
        self, cmd: Json, *, request_id: str | None, caller_process_id: str | None
    ) -> tuple[bool, Json | None, Json | None]:
        device_id = str(cmd.get("device_id", ""))
        action = str(cmd.get("action", ""))
        chain = self._command_interceptor_chain(device_id, action)
        if not chain:
            return True, cmd, None

        cur_cmd: Json = {
            "device_id": device_id,
            "action": action,
            "params": cmd.get("params", {}),
        }
        for route in chain:
            process_id = route.process_id
            handle = self._processes.get(process_id)
            if (
                handle is None
                or handle.state
                not in {
                    ManagedProcessState.STARTING,
                    ManagedProcessState.RUNNING,
                    ManagedProcessState.STOPPING,
                }
                or handle.rpc_endpoint is None
            ):
                err = self._command_interceptor_error(
                    code="INTERCEPTOR_UNAVAILABLE",
                    message=f"Interceptor {process_id!r} unavailable for {device_id}.{action}",
                    process_id=process_id,
                    device_id=device_id,
                    action=action,
                )
                self._publish_manager_event(
                    "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                )
                return False, None, err

            meta: Json = {"request_id": request_id, "t_mono": time.monotonic()}
            if caller_process_id:
                meta["caller_process_id"] = caller_process_id
            req = {"type": "command_interceptor.check", "command": cur_cmd, "meta": meta}

            try:
                resp = self._call_process_rpc(
                    process_id=process_id,
                    request=req,
                    timeout_ms=self._interceptor_rpc_timeout_ms,
                )
            except zmq.Again:
                err = self._command_interceptor_error(
                    code="INTERCEPTOR_TIMEOUT",
                    message=f"Interceptor {process_id!r} timed out for {device_id}.{action}",
                    process_id=process_id,
                    device_id=device_id,
                    action=action,
                )
                self._publish_manager_event(
                    "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                )
                return False, None, err
            except Exception as e:
                err = self._command_interceptor_error(
                    code="INTERCEPTOR_UNAVAILABLE",
                    message=f"Interceptor {process_id!r} failed for {device_id}.{action}: {e}",
                    process_id=process_id,
                    device_id=device_id,
                    action=action,
                )
                self._publish_manager_event(
                    "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                )
                return False, None, err

            if not isinstance(resp, dict):
                err = self._command_interceptor_error(
                    code="INTERCEPTOR_BAD_RESPONSE",
                    message=f"Interceptor {process_id!r} returned invalid response",
                    process_id=process_id,
                    device_id=device_id,
                    action=action,
                    details={"response": resp},
                )
                self._publish_manager_event(
                    "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                )
                return False, None, err

            if resp.get("ok") is False:
                err = self._command_interceptor_error(
                    code="INTERCEPTOR_BAD_RESPONSE",
                    message=f"Interceptor {process_id!r} returned error response",
                    process_id=process_id,
                    device_id=device_id,
                    action=action,
                    details={"response": resp},
                )
                self._publish_manager_event(
                    "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                )
                return False, None, err

            allow = resp.get("allow")
            if allow is True:
                if "command" in resp:
                    new_cmd_raw = resp.get("command")
                    if not isinstance(new_cmd_raw, dict):
                        err = self._command_interceptor_error(
                            code="INTERCEPTOR_BAD_RESPONSE",
                            message=f"Interceptor {process_id!r} returned invalid command",
                            process_id=process_id,
                            device_id=device_id,
                            action=action,
                        )
                        self._publish_manager_event(
                            "manager.command_interceptor.error",
                            {"error": err, "command": cur_cmd},
                        )
                        return False, None, err
                    new_device = str(new_cmd_raw.get("device_id", device_id))
                    new_action = str(new_cmd_raw.get("action", action))
                    if new_device != device_id or new_action != action:
                        err = self._command_interceptor_error(
                            code="INTERCEPTOR_BAD_RESPONSE",
                            message=(
                                f"Interceptor {process_id!r} attempted to change route"
                            ),
                            process_id=process_id,
                            device_id=device_id,
                            action=action,
                        )
                        self._publish_manager_event(
                            "manager.command_interceptor.error",
                            {"error": err, "command": cur_cmd},
                        )
                        return False, None, err
                    if "params" in new_cmd_raw:
                        new_params = new_cmd_raw.get("params")
                    else:
                        new_params = cur_cmd.get("params")
                    if not isinstance(new_params, dict):
                        err = self._command_interceptor_error(
                            code="INTERCEPTOR_BAD_RESPONSE",
                            message=f"Interceptor {process_id!r} returned invalid params",
                            process_id=process_id,
                            device_id=device_id,
                            action=action,
                        )
                        self._publish_manager_event(
                            "manager.command_interceptor.error",
                            {"error": err, "command": cur_cmd},
                        )
                        return False, None, err
                    new_cmd = {
                        "device_id": device_id,
                        "action": action,
                        "params": new_params,
                    }
                    if new_cmd != cur_cmd:
                        self._publish_manager_event(
                            "manager.command_interceptor.modified",
                            {
                                "process_id": process_id,
                                "interceptor_id": resp.get("interceptor_id"),
                                "rule": resp.get("rule"),
                                "note": resp.get("note"),
                                "before": cur_cmd,
                                "after": new_cmd,
                            },
                        )
                        cur_cmd = new_cmd
                continue

            if allow is False:
                inner = resp.get("error") or {}
                inner_code = str(inner.get("code", "CONDITION_FAILED"))
                inner_msg = str(inner.get("message", "Command rejected by interceptor"))
                err = self._command_interceptor_error(
                    code="INTERCEPTOR_REJECTED",
                    message=inner_msg,
                    process_id=process_id,
                    device_id=device_id,
                    action=action,
                    interceptor_id=resp.get("interceptor_id"),
                    rule=resp.get("rule"),
                    details={
                        "code": inner_code,
                        "message": inner_msg,
                        "details": inner.get("details", {}),
                    },
                )
                self._publish_manager_event(
                    "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                )
                return False, None, err

            err = self._command_interceptor_error(
                code="INTERCEPTOR_BAD_RESPONSE",
                message=f"Interceptor {process_id!r} returned invalid response",
                process_id=process_id,
                device_id=device_id,
                action=action,
                details={"response": resp},
            )
            self._publish_manager_event(
                "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
            )
            return False, None, err

        return True, cur_cmd, None

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
        if rtype == "get_telemetry":
            device_id = str(req["device_id"])
            return {
                "ok": True,
                "telemetry": self._get_device_telemetry_snapshot(device_id),
            }
        if rtype == "command":
            device_id = str(req["device_id"])
            action = str(req["action"])
            params = req.get("params", {})
            if not isinstance(params, dict):
                raise TypeError("params must be a dict")
            handle = self._devices.get(device_id)
            if handle is None:
                fed_resp = self._federation_hub.forward_device_request(req)
                if fed_resp is not None:
                    return fed_resp
                return {"ok": False, "error": f"Unknown device_id {device_id!r}"}
            if self._driver_is_stopped(handle) or handle.rpc_endpoint is None:
                return {"ok": False, "error": "driver not running"}
            cmd = {"device_id": device_id, "action": action, "params": params}
            ok, new_cmd, err = self._apply_command_interceptors(
                cmd,
                request_id=req.get("request_id"),
                caller_process_id=req.get("caller_process_id"),
            )
            if not ok:
                return {"ok": False, "error": err}
            if new_cmd is None:
                return {"ok": False, "error": "command blocked"}
            return self._call_device_rpc(
                device_id=str(new_cmd.get("device_id", device_id)),
                action=str(new_cmd.get("action", action)),
                params=new_cmd.get("params", params),
            )

        if rtype == "federation.capabilities.update":
            device_id = str(req.get("device_id", ""))
            capabilities = req.get("capabilities")
            if not device_id:
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_federation_update",
                        "message": "missing device_id",
                    },
                }
            if not isinstance(capabilities, dict):
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_federation_update",
                        "message": "capabilities must be a dict",
                    },
                }
            try:
                self._federation_hub.update_capabilities(device_id, capabilities)
            except KeyError:
                return {
                    "ok": False,
                    "error": {
                        "code": "unknown_device",
                        "message": f"Unknown mirrored device_id {device_id!r}",
                    },
                }
            return {"ok": True, "result": {"device_id": device_id}}

        if rtype == "device.get_status":
            device_id = str(req["device_id"])
            if self._federation_hub.is_mirrored_device(device_id):
                return {
                    "ok": True,
                    "result": self._federation_hub.device_status_snapshot(device_id),
                }
            return {"ok": True, "result": self._device_status_snapshot(device_id)}
        if rtype == "device.list_status":
            return {"ok": True, "result": self._list_devices_status_snapshot()}

        if rtype == "device.driver.start":
            device_id = str(req["device_id"])
            fed_resp = self._federation_hub.forward_device_request(req)
            if fed_resp is not None:
                return fed_resp
            handle = self._devices.get(device_id)
            if handle is None:
                return {"ok": False, "error": f"Unknown device_id {device_id!r}"}
            if self._driver_is_started(handle):
                return {"ok": False, "error": "driver already started"}
            self.start_driver(device_id)
            return {"ok": True, "result": {"device_id": device_id}}
        if rtype == "device.driver.stop":
            device_id = str(req["device_id"])
            force = bool(req.get("force", False))
            fed_resp = self._federation_hub.forward_device_request(req)
            if fed_resp is not None:
                return fed_resp
            handle = self._devices.get(device_id)
            if handle is None:
                return {"ok": False, "error": f"Unknown device_id {device_id!r}"}
            if self._driver_is_stopped(handle):
                return {"ok": False, "error": "driver already stopped"}
            self.stop_driver(device_id, force=force)
            return {"ok": True, "result": {"device_id": device_id}}
        if rtype == "device.driver.restart":
            device_id = str(req["device_id"])
            force = bool(req.get("force", False))
            fed_resp = self._federation_hub.forward_device_request(req)
            if fed_resp is not None:
                return fed_resp
            self.restart_driver(device_id, force=force)
            return {"ok": True, "result": {"device_id": device_id}}
        if rtype == "device.recover":
            device_id = str(req["device_id"])
            reconnect = bool(req.get("reconnect", True))
            force = bool(req.get("force", False))
            fed_resp = self._federation_hub.forward_device_request(req)
            if fed_resp is not None:
                return fed_resp
            self.recover_device(device_id, reconnect=reconnect, force=force)
            return {"ok": True, "result": {"device_id": device_id}}

        if rtype == "device.connect":
            device_id = str(req["device_id"])
            fed_resp = self._federation_hub.forward_device_request(req)
            if fed_resp is not None:
                return fed_resp
            resp = self.connect_device(device_id)
            self._publish_manager_event(
                "manager.device.connect_sent",
                {
                    "device_id": device_id,
                    "response": resp,
                    "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
                },
            )
            return {"ok": True, "result": resp}
        if rtype == "device.disconnect":
            device_id = str(req["device_id"])
            fed_resp = self._federation_hub.forward_device_request(req)
            if fed_resp is not None:
                return fed_resp
            resp = self.disconnect_device(device_id)
            self._publish_manager_event(
                "manager.device.disconnect_sent",
                {
                    "device_id": device_id,
                    "response": resp,
                    "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
                },
            )
            return {"ok": True, "result": resp}

        if rtype == "device.config.get":
            device_id = str(req["device_id"])
            fed_cfg = self._federation_hub.device_config_get(device_id)
            if fed_cfg is not None:
                return {"ok": True, "result": fed_cfg}
            handle = self._devices.get(device_id)
            if handle is None:
                return {"ok": False, "error": f"Unknown device_id {device_id!r}"}
            return {"ok": True, "result": self._device_config_payload(handle)}
        if rtype == "device.config.list":
            configs = [
                self._device_config_payload(handle)
                for handle in self._devices.values()
            ]
            configs.extend(self._federation_hub.device_config_list())
            configs.sort(key=lambda item: str(item.get("device_id", "")))
            return {"ok": True, "result": configs}

        if rtype == "process.list_status":
            return {"ok": True, "result": self.list_processes()}
        if rtype == "process.get":
            process_id = str(req["process_id"])
            return {"ok": True, "result": self.get_process(process_id)}
        if rtype == "process.start":
            process_id = str(req["process_id"])
            self.start_process(process_id)
            return {"ok": True, "result": {"process_id": process_id}}
        if rtype == "process.stop":
            process_id = str(req["process_id"])
            self.stop_process(process_id)
            return {"ok": True, "result": {"process_id": process_id}}
        if rtype == "process.restart":
            process_id = str(req["process_id"])
            self.restart_process(process_id)
            return {"ok": True, "result": {"process_id": process_id}}
        if rtype == "process.add":
            spec_raw = req.get("spec")
            if not isinstance(spec_raw, dict):
                raise TypeError("spec must be a dict")
            spec = self._parse_process_spec(spec_raw)
            self.add_process(spec)
            return {"ok": True, "result": {"process_id": spec.process_id}}
        if rtype == "process.remove":
            process_id = str(req["process_id"])
            self.remove_process(process_id)
            return {"ok": True, "result": {"process_id": process_id}}

        if rtype == "process.rpc.advertise":
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

        if rtype == "process.rpc":
            process_id = str(req.get("process_id", ""))
            request = req.get("request")
            if not process_id or not isinstance(request, dict):
                return {
                    "ok": False,
                    "error": {"code": "invalid_process_rpc", "message": "bad request"},
                }
            handle = self._processes.get(process_id)
            if handle is None:
                return {"ok": False, "error": {"code": "unknown_process"}}
            if handle.state not in {
                ManagedProcessState.STARTING,
                ManagedProcessState.RUNNING,
                ManagedProcessState.STOPPING,
            }:
                return {
                    "ok": False,
                    "error": {"code": "process_not_running"},
                }
            if handle.rpc_endpoint is None:
                return {
                    "ok": False,
                    "error": {"code": "process_rpc_not_ready"},
                }
            try:
                resp = self._call_process_rpc(
                    process_id=process_id,
                    request=request,
                )
            except Exception as e:
                return {
                    "ok": False,
                    "error": {"code": "process_rpc_failed", "message": str(e)},
                }
            return resp

        if rtype == "command_interceptor.register":
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

        if rtype == "command_interceptor.list":
            return {"ok": True, "result": {"routes": self._command_interceptor_routes_snapshot()}}

        if rtype == "manager.shutdown":
            self.shutdown()
            return {"ok": True, "result": {"status": "shutting_down"}}
        if rtype == "manager.identity":
            return {
                "ok": True,
                "result": {
                    "version": 1,
                    "instance_id": self._instance_id,
                    "started_ts": {
                        "t_wall": float(self._started_t_wall),
                        "t_mono": float(self._started_t_mono),
                    },
                },
            }

        if rtype == "manager.log.publish":
            payload = req.get("payload")
            if not isinstance(payload, dict):
                return {"ok": False, "error": {"code": "invalid_payload"}}
            entry = self._emit_log_from_payload(payload, default_topic="manager.log.publish")
            return {"ok": True, "result": {"status": "published", "entry": entry}}

        if rtype == "manager.log.tail":
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

        if rtype == "manager.event.publish":
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

        raise ValueError(f"Unknown external request type {rtype!r}")

    def _call_device_rpc(
        self,
        *,
        device_id: str,
        action: str,
        params: Json,
        timeout_ms: int | None = None,
    ) -> Json:
        """
        RPC semantics: strictly serial per device; blocking is allowed driver-side.
        """
        handle = self._devices.get(device_id)
        if handle is None or handle.rpc_endpoint is None:
            raise RuntimeError(f"Device {device_id!r} is not registered")

        sock = handle.rpc_sock
        if sock is None:
            sock = self._ctx.socket(zmq.REQ)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.REQ_RELAXED, 1)
            sock.setsockopt(zmq.REQ_CORRELATE, 1)
            sock.connect(handle.rpc_endpoint)
            handle.rpc_sock = sock

        effective_timeout = (
            self._device_rpc_timeout_ms if timeout_ms is None else timeout_ms
        )
        sock.setsockopt(zmq.RCVTIMEO, int(effective_timeout))
        sock.setsockopt(zmq.SNDTIMEO, int(effective_timeout))
        try:
            self._rpc_seq += 1
            envelope = {
                "id": self._rpc_seq,
                "action": action,
                "params": params,
            }
            self._send_json(sock, envelope)
            deadline = time.monotonic() + (effective_timeout / 1000.0)
            resp = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise zmq.Again()
                step_ms = int(min(50.0, remaining * 1000.0))
                if sock.poll(step_ms, zmq.POLLIN):
                    resp = self._recv_json(sock)
                    break
                while self._sub.poll(0, zmq.POLLIN):
                    self._handle_driver_pub()
                while self._process_hb_sub.poll(0, zmq.POLLIN):
                    self._handle_process_pub()
                while self._process_data_sub.poll(0, zmq.POLLIN):
                    self._handle_process_data_pub()
            if resp is None:
                raise zmq.Again()
            handle.rpc_fail_count = 0
            handle.rpc_last_fail_t_mono = None
            status = resp.get("status")
            ok = None
            if status in {"OK", "ERROR"}:
                ok = status == "OK"
            elif "ok" in resp:
                ok = bool(resp.get("ok"))
            cmd_payload: Json = {
                "version": 1,
                "device_id": device_id,
                "action": action,
                "params_json": self._safe_json(params),
                "ok": ok,
                "status": status,
                "error": resp.get("error"),
                "result_json": self._safe_json(resp.get("result")),
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            }
            self._publish_manager_event("manager.command", cmd_payload)
            return resp
        except Exception as e:
            handle.rpc_fail_count += 1
            handle.rpc_last_fail_t_mono = time.monotonic()
            if handle.rpc_fail_count >= 2:
                self._close_device_rpc(handle)
            cmd_payload = {
                "version": 1,
                "device_id": device_id,
                "action": action,
                "params_json": self._safe_json(params),
                "ok": False,
                "status": None,
                "error": str(e),
                "result_json": "",
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            }
            self._publish_manager_event("manager.command", cmd_payload)
            raise

    def _call_process_rpc(
        self,
        *,
        process_id: str,
        request: Json,
        timeout_ms: int | None = None,
    ) -> Json:
        handle = self._processes.get(process_id)
        if handle is None:
            raise RuntimeError(f"Process {process_id!r} is not configured")
        if handle.rpc_endpoint is None:
            raise RuntimeError("process rpc endpoint not ready")

        sock = handle.rpc_sock
        if sock is None:
            sock = self._ctx.socket(zmq.DEALER)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(handle.rpc_endpoint)
            handle.rpc_sock = sock

        effective_timeout = (
            self._device_rpc_timeout_ms if timeout_ms is None else timeout_ms
        )
        sock.setsockopt(zmq.RCVTIMEO, int(effective_timeout))
        sock.setsockopt(zmq.SNDTIMEO, int(effective_timeout))
        expected_request_id = request.get("request_id")
        try:
            # Drop late replies from previous timed-out requests on this socket.
            while True:
                try:
                    if not sock.poll(0, zmq.POLLIN):
                        break
                    _ = sock.recv(zmq.NOBLOCK)
                except zmq.Again:
                    break
                except Exception:
                    break
            sock.send(json_dumps(request))
            deadline = time.monotonic() + (int(effective_timeout) / 1000.0)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise zmq.Again()
                step_ms = int(min(50.0, max(1.0, remaining * 1000.0)))
                if not sock.poll(step_ms, zmq.POLLIN):
                    while self._sub.poll(0, zmq.POLLIN):
                        self._handle_driver_pub()
                    while self._process_hb_sub.poll(0, zmq.POLLIN):
                        self._handle_process_pub()
                    while self._process_data_sub.poll(0, zmq.POLLIN):
                        self._handle_process_data_pub()
                    continue
                resp = self._recv_json(sock)
                if not isinstance(resp, dict):
                    continue
                if (
                    expected_request_id is not None
                    and resp.get("request_id") != expected_request_id
                ):
                    # Late/stale reply from an older request; keep waiting.
                    continue
                break
            handle.rpc_fail_count = 0
            handle.rpc_last_fail_t_mono = None
            return resp
        except Exception:
            handle.rpc_fail_count += 1
            handle.rpc_last_fail_t_mono = time.monotonic()
            self._close_process_rpc(handle)
            raise

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

    def _check_timeouts(self) -> None:
        now_mono = time.monotonic()

        # Heartbeat-based liveness :contentReference[oaicite:16]{index=16}
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
        self._federation_hub.check_timeouts(now_mono)

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

        # Driver process supervision (per device)
        for device_id, handle in self._devices.items():
            proc = handle.process
            if proc is not None:
                rc = proc.poll()
                if rc is not None:
                    handle.driver_last_exit_code = int(rc)
                    handle.process = None
                    handle.driver_pid = None

                    if (
                        handle.driver_process_state == ManagedProcessState.STOPPING
                        and handle.driver_stop_requested_t_mono is not None
                    ):
                        handle.driver_process_state = ManagedProcessState.STOPPED
                        self._publish_driver_event("manager.driver.stopped", handle)
                    else:
                        if rc == 0:
                            handle.driver_process_state = ManagedProcessState.STOPPED
                            self._publish_driver_event("manager.driver.exited", handle)
                        else:
                            handle.driver_process_state = ManagedProcessState.FAILED
                            handle.driver_last_error = (
                                handle.driver_last_error or "driver exited"
                            )
                            self._publish_driver_event("manager.driver.failed", handle)

            if handle.driver_process_state == ManagedProcessState.STOPPING:
                if (
                    handle.driver_stop_requested_t_mono is not None
                    and handle.process is not None
                    and handle.process.poll() is None
                ):
                    if (
                        now_mono - handle.driver_stop_requested_t_mono
                        > handle.spec.driver_stop_timeout_s
                    ):
                        try:
                            handle.process.kill()
                            self._publish_driver_event("manager.driver.killing", handle)
                        except Exception as e:
                            handle.driver_last_error = str(e)

                    if (
                        now_mono - handle.driver_stop_requested_t_mono
                        > handle.spec.driver_stop_timeout_s
                        + handle.spec.driver_kill_timeout_s
                        and handle.process is not None
                        and handle.process.poll() is None
                    ):
                        handle.driver_process_state = ManagedProcessState.FAILED
                        handle.driver_last_error = "kill timeout"
                        self._publish_driver_event(
                            "manager.driver.kill_timeout", handle
                        )

            if (
                handle.driver_next_restart_t_mono is not None
                and now_mono >= handle.driver_next_restart_t_mono
            ):
                if (
                    handle.spec.driver_max_restarts is not None
                    and handle.driver_restart_count >= handle.spec.driver_max_restarts
                ):
                    handle.driver_process_state = ManagedProcessState.CRASHLOOP
                    handle.driver_next_restart_t_mono = None
                    self._publish_driver_event("manager.driver.crashloop", handle)
                else:
                    handle.driver_restart_count += 1
                    handle.driver_last_restart_t_mono = now_mono
                    handle.driver_next_restart_t_mono = None
                    self._publish_driver_event("manager.driver.restarting", handle)
                    self.start_driver(device_id)

        # Managed process supervision
        for process_id, handle in self._processes.items():
            popen = handle.popen
            if popen is not None:
                rc = popen.poll()
                if rc is not None:
                    handle.last_exit_code = int(rc)
                    handle.popen = None
                    handle.pid = None
                    handle.rpc_endpoint = None
                    self._close_process_rpc(handle)
                    if handle.state == ManagedProcessState.STOPPING:
                        handle.state = ManagedProcessState.EXITED
                        self._publish_process_event("manager.process.exited", handle)
                    else:
                        if rc == 0:
                            handle.state = ManagedProcessState.STOPPED
                            self._publish_process_event(
                                "manager.process.exited", handle
                            )
                        else:
                            handle.state = ManagedProcessState.FAILED
                            handle.last_error = handle.last_error or "process exited"
                            self._publish_process_event(
                                "manager.process.failed", handle
                            )

            # Heartbeat stale detection
            if handle.state in {
                ManagedProcessState.STARTING,
                ManagedProcessState.RUNNING,
            }:
                hb_age: float | None = None
                if handle.last_hb_t_mono is not None:
                    hb_age = now_mono - handle.last_hb_t_mono
                elif handle.last_start_t_mono is not None:
                    hb_age = now_mono - handle.last_start_t_mono

                if hb_age is not None and hb_age > handle.spec.heartbeat_timeout_s:
                    handle.state = ManagedProcessState.FAILED
                    handle.last_error = "heartbeat stale"
                    if handle.popen is not None and handle.popen.poll() is None:
                        try:
                            handle.popen.terminate()
                            handle.stop_requested_t_mono = now_mono
                        except Exception as e:
                            handle.last_error = (
                                f"heartbeat stale; terminate failed: {e}"
                            )
                    self._publish_process_event("manager.process.failed", handle)

            # Stop timeout -> kill
            if handle.state == ManagedProcessState.STOPPING:
                if (
                    handle.stop_requested_t_mono is not None
                    and handle.popen is not None
                    and handle.popen.poll() is None
                ):
                    if (
                        now_mono - handle.stop_requested_t_mono
                        > handle.spec.shutdown_timeout_s
                    ):
                        try:
                            handle.popen.kill()
                        except Exception as e:
                            handle.last_error = str(e)

            # Restart scheduling
            if handle.state in {ManagedProcessState.FAILED, ManagedProcessState.EXITED}:
                if handle.stop_requested_t_mono is None:
                    self._maybe_schedule_restart(handle, now_mono)

            # Execute scheduled restart
            if (
                handle.next_restart_t_mono is not None
                and now_mono >= handle.next_restart_t_mono
            ):
                self._try_restart_process(handle)

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
        for hook in self._event_hooks:
            hook(topic, payload)
        if topic != "manager.log":
            self._maybe_publish_log_event(topic, payload)

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
    def _normalize_log_severity(raw: Any) -> str:
        sev = str(raw or "info").strip().lower()
        if sev == "warn":
            return "warning"
        if sev not in {"debug", "info", "warning", "error", "critical"}:
            return "info"
        return sev

    @staticmethod
    def _severity_rank(raw: Any) -> int:
        sev = Manager._normalize_log_severity(raw)
        table = {
            "debug": 10,
            "info": 20,
            "warning": 30,
            "error": 40,
            "critical": 50,
        }
        return table.get(sev, 20)

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

    def _log_tail(self, params: Json) -> Json:
        limit_raw = params.get("limit", 200)
        try:
            limit = int(limit_raw)
        except Exception as e:
            raise TypeError(f"limit must be int: {e}") from e
        limit = max(1, min(limit, 5000))

        since_t_mono_raw = params.get("since_t_mono")
        since_t_mono: float | None = None
        if since_t_mono_raw is not None:
            try:
                since_t_mono = float(since_t_mono_raw)
            except Exception as e:
                raise TypeError(f"since_t_mono must be float: {e}") from e

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

        device_set = self._normalize_filter_set(params.get("device_ids"), field="device_ids")
        process_set = self._normalize_filter_set(params.get("process_ids"), field="process_ids")
        source_id_set = self._normalize_filter_set(params.get("source_ids"), field="source_ids")

        topic_contains = str(params.get("topic_contains", "") or "").strip().lower()
        text_contains = str(params.get("text_contains", "") or "").strip().lower()

        entries = list(self._log_history)
        filtered: list[Json] = []
        for entry in entries:
            if since_t_mono is not None:
                ts = entry.get("ts")
                if not isinstance(ts, dict):
                    continue
                try:
                    if float(ts.get("t_mono", -1.0)) < since_t_mono:
                        continue
                except Exception:
                    continue

            severity = self._normalize_log_severity(entry.get("severity"))
            if severity_min_rank is not None and self._severity_rank(severity) < severity_min_rank:
                continue
            if severity_set is not None and severity not in severity_set:
                continue

            source_kind = str(entry.get("source_kind", "") or "").lower()
            if source_kind_set is not None and source_kind not in source_kind_set:
                continue

            device_id = self._normalize_id(entry.get("device_id"))
            if device_set is not None and (device_id is None or device_id not in device_set):
                continue
            process_id = self._normalize_id(entry.get("process_id"))
            if process_set is not None and (process_id is None or process_id not in process_set):
                continue
            source_id = self._normalize_id(entry.get("source_id"))
            if source_id_set is not None and (source_id is None or source_id not in source_id_set):
                continue

            topic = str(entry.get("topic", "") or "").lower()
            if topic_contains and topic_contains not in topic:
                continue

            if text_contains:
                message = str(entry.get("message", "") or "").lower()
                payload_json = str(entry.get("payload_json", "") or "").lower()
                if text_contains not in message and text_contains not in payload_json:
                    continue

            filtered.append(entry)

        total = len(filtered)
        if total > limit:
            filtered = filtered[-limit:]

        latest_t_mono: float | None = None
        if filtered:
            ts = filtered[-1].get("ts")
            if isinstance(ts, dict):
                try:
                    latest_t_mono = float(ts.get("t_mono"))
                except Exception:
                    latest_t_mono = None

        return {
            "entries": filtered,
            "count": len(filtered),
            "total_matched": total,
            "limit": limit,
            "latest_t_mono": latest_t_mono,
        }

    def _maybe_publish_log_event(self, topic: str, payload: Json) -> None:
        severity = None
        if topic == "manager.command":
            ok = payload.get("ok")
            status = str(payload.get("status", "") or "").upper()
            if ok is False or status == "ERROR":
                severity = "error"
            else:
                return
        elif topic.endswith("telemetry_stale"):
            severity = "warning"
        elif (
            "error" in topic
            or topic.endswith("failed")
            or topic.endswith("crashloop")
            or "kill_timeout" in topic
        ):
            severity = "error"
        if severity is None:
            return

        process_id = payload.get("process_id")
        device_id = payload.get("device_id")
        source_kind = "manager"
        source_id = "manager"
        if process_id is not None:
            source_kind = "process"
            source_id = str(process_id)
        elif device_id is not None:
            source_kind = "driver"
            source_id = str(device_id)
        message = payload.get("error") or payload.get("message") or ""
        if topic == "manager.command":
            action = str(payload.get("action", "") or "")
            err_raw = payload.get("error")
            if isinstance(err_raw, dict):
                err_message = err_raw.get("message") or err_raw.get("code") or ""
                if err_message is None:
                    err_message = ""
            else:
                err_message = str(err_raw or "")
            target = (
                f"{device_id}.{action}"
                if device_id is not None and action
                else str(device_id or action or "unknown command")
            )
            message = (
                f"Command failed: {target} ({err_message})"
                if err_message
                else f"Command failed: {target}"
            )
        self._emit_log(
            severity=severity,
            topic=topic,
            message=str(message) if message is not None else "",
            source_kind=source_kind,
            source_id=source_id,
            device_id=device_id,
            process_id=process_id,
            stream="event",
            payload=payload,
        )

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

    def _publish_device_config(self, handle: DeviceHandle) -> None:
        payload: Json = self._device_config_payload(handle)
        self._publish_manager_event("manager.device_config", payload)

    def _device_config_payload(self, handle: DeviceHandle) -> Json:
        yaml_text = handle.spec.config_yaml_text
        if yaml_text is None:
            yaml_text = self._serialize_spec_yaml(handle.spec)
        return {
            "version": 1,
            "device_id": handle.spec.device_id,
            "yaml_text": yaml_text,
            "fixed_metadata": handle.spec.fixed_metadata or {},
            "telemetry_calls": telemetry_calls_to_json(handle.spec.telemetry_calls),
            "stream_calls": stream_calls_to_json(list(handle.spec.stream_calls or [])),
            "run_meta_calls": run_meta_calls_to_json(
                list(handle.spec.run_meta_calls or [])
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
            "fixed_metadata": spec.fixed_metadata or {},
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
