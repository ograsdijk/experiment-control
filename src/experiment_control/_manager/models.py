from __future__ import annotations

import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import zmq

from ..types import (
    DeviceState,
    DriverState,
    RunMetaCall,
    StreamCall,
    TelemetryCall,
    TelemetryQuality,
    Timestamp,
)

Json = dict[str, Any]


@dataclass(frozen=True)
class ConnectCheckSpec:
    enabled: bool = False
    identity: dict[str, Any] = field(default_factory=dict)
    on_fail: str = "disconnect"


@dataclass(frozen=True)
class AutoReconnectSpec:
    enabled: bool = False
    on_telemetry_stale_s: float | None = None
    cooldown_s: float = 30.0
    max_attempts: int | None = 3
    reset_attempts_after_ok_s: float = 120.0
    disconnect_timeout_ms: int = 1000
    connect_timeout_ms: int | None = None


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
    auto_reconnect: AutoReconnectSpec = field(default_factory=AutoReconnectSpec)
    config_yaml_text: str | None = None
    telemetry_period_s: float = 1.0
    heartbeat_period_s: float = 1.0
    command_poll_period_s: float = 0.01
    driver_stop_timeout_s: float = 3.0
    driver_kill_timeout_s: float = 3.0
    driver_restart_backoff_s: float = 0.5
    driver_max_restarts: int | None = None
    config_path: Path | None = None


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
    # Serialises access to `rpc_sock` (a ZMQ REQ socket, NOT thread-safe).
    # Lifecycle workers can dispatch concurrent device RPCs (e.g. two
    # operators triggering commands on the same device, or the
    # supervisor's stop-path racing a worker's command) -- without this
    # lock, two threads can interleave send/recv on the same socket and
    # break ZMQ's REQ state machine. Cheap to take in the no-contention
    # case; only contended when the same device sees concurrent RPCs.
    #
    # RLock so the call-path's `except` branch can re-enter via
    # _close_device_rpc (same thread, lock already held) without
    # deadlocking; external close-callers from a different thread
    # block until the in-flight call returns.
    rpc_lock: threading.RLock = field(default_factory=threading.RLock)
    rpc_fail_count: int = 0
    rpc_last_fail_t_mono: float | None = None
    pub_endpoint: str | None = None
    capabilities: Json | None = None
    last_hb_recv_mono: float | None = None
    last_hb: Heartbeat | None = None
    driver_process_state: ManagedProcessState = ManagedProcessState.STOPPED
    driver_pid: int | None = None
    driver_popen_pid: int | None = None
    driver_heartbeat_pid: int | None = None
    driver_last_exit_code: int | None = None
    driver_restart_count: int = 0
    driver_last_restart_t_mono: float | None = None
    driver_last_error: str | None = None
    driver_last_error_kind: str | None = None
    driver_last_signal_name: str | None = None
    driver_last_failure_pid: int | None = None
    driver_stop_requested_t_mono: float | None = None
    driver_next_restart_t_mono: float | None = None
    connect_check_last: dict[str, Any] | None = None
    config_published: bool = False
    supervisor_stdout_tail: deque[Json] = field(default_factory=lambda: deque(maxlen=300))
    supervisor_stderr_tail: deque[Json] = field(default_factory=lambda: deque(maxlen=300))
    supervisor_log_tail: deque[Json] = field(default_factory=lambda: deque(maxlen=100))
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    auto_reconnect_attempts: int = 0
    auto_reconnect_last_attempt_mono: float | None = None
    auto_reconnect_last_attempt_wall: float | None = None
    auto_reconnect_healthy_since_mono: float | None = None
    auto_reconnect_last_success_mono: float | None = None
    auto_reconnect_last_error: str | None = None
    auto_reconnect_suppressed: bool = False


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
    popen_pid: int | None = None
    heartbeat_pid: int | None = None
    rpc_endpoint: str | None = None
    # Flat process-telemetry schema advertised by the process
    # (manager.process_telemetry.schema.advertise): a list of
    # {"name", "dtype", "units"} entries. Served via
    # manager.process_telemetry.schema.list so the HDF writer can create
    # /process_telemetry/<id> datasets and federation peers can warm it.
    telemetry_schema: list[Json] | None = None
    rpc_sock: zmq.Socket | None = None
    # Serialises access to `rpc_sock` (a ZMQ DEALER socket, NOT
    # thread-safe). Lifecycle workers can dispatch concurrent process
    # RPCs (e.g. interceptor-invoke calls overlapping with process-
    # command calls, or the supervisor's stop-path racing a worker's
    # command). Without this lock, two threads can interleave send/recv
    # on the same DEALER and either tangle correlation or trigger ZMQ
    # EFSM. Cheap to take in the no-contention case.
    #
    # RLock so the call-path's `except` branch can re-enter via
    # _close_process_rpc (same thread, lock already held) without
    # deadlocking; external close-callers from a different thread
    # block until the in-flight call returns.
    rpc_lock: threading.RLock = field(default_factory=threading.RLock)
    rpc_fail_count: int = 0
    rpc_last_fail_t_mono: float | None = None
    last_start_t_wall: float | None = None
    last_start_t_mono: float | None = None
    last_hb_t_wall: float | None = None
    last_hb_t_mono: float | None = None
    # Manager-side timestamp of when we DRAINED the HB from the SUB
    # buffer, used for the timeout check. Distinct from last_hb_t_mono
    # (the sender's clock when the HB was generated): including buffer
    # + scheduling delay in the timeout check produces false positives
    # when the manager is briefly slow.
    last_hb_recv_mono: float | None = None
    last_heartbeat_payload: Json | None = None
    last_exit_code: int | None = None
    restart_count: int = 0
    last_restart_t_mono: float | None = None
    last_error: str | None = None
    last_error_kind: str | None = None
    last_signal_name: str | None = None
    last_failure_pid: int | None = None
    last_heartbeat_age_s: float | None = None
    last_liveness_age_s: float | None = None
    last_heartbeat_received: bool | None = None
    heartbeat_stale_strikes: int = 0
    last_stale_detected_mono: float | None = None
    terminated_by_manager: bool = False
    termination_reason: str | None = None
    termination_method: str | None = None
    termination_error: str | None = None
    recent_manager_loop_stall: bool = False
    last_manager_loop_stall_duration_s: float | None = None
    heartbeat_endpoint: str = ""
    process_data_endpoint: str = ""
    stop_requested_t_mono: float | None = None
    next_restart_t_mono: float | None = None
    startup_collision_retry_done: bool = False
    supervisor_stdout_tail: deque[Json] = field(default_factory=lambda: deque(maxlen=300))
    supervisor_stderr_tail: deque[Json] = field(default_factory=lambda: deque(maxlen=300))
    supervisor_log_tail: deque[Json] = field(default_factory=lambda: deque(maxlen=100))
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None


@dataclass(frozen=True)
class CommandInterceptorRoute:
    process_id: str
    device_id: str
    action: str
    order: int
