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
from .manager_command_journal import (
    append_command_journal_entry as shared_append_command_journal_entry,
)
from .manager_command_journal import (
    command_journal_status_payload as shared_command_journal_status_payload,
)
from .manager_command_journal import (
    should_journal_command_action as shared_should_journal_command_action,
)
from .manager_device_routing import route_device_request
from .manager_driver_pub import handle_driver_pub as shared_handle_driver_pub
from .manager_driver_pub import ingest_chunk_ready as shared_ingest_chunk_ready
from .manager_driver_pub import ingest_heartbeat as shared_ingest_heartbeat
from .manager_driver_pub import ingest_telemetry as shared_ingest_telemetry
from .manager_route_handlers import (
    publish_process_command_response as shared_publish_process_command_response,
    route_command_interceptor_list as shared_route_command_interceptor_list,
    route_command_interceptor_register as shared_route_command_interceptor_register,
    route_manager_cleanup_orphans as shared_route_manager_cleanup_orphans,
    route_manager_command_journal_status as shared_route_manager_command_journal_status,
    route_manager_command_journal_tail as shared_route_manager_command_journal_tail,
    route_manager_event_publish as shared_route_manager_event_publish,
    route_manager_identity as shared_route_manager_identity,
    route_manager_log_publish as shared_route_manager_log_publish,
    route_manager_log_tail as shared_route_manager_log_tail,
    route_manager_request as shared_route_manager_request,
    route_manager_shutdown as shared_route_manager_shutdown,
    route_process_add as shared_route_process_add,
    route_process_control as shared_route_process_control,
    route_process_get as shared_route_process_get,
    route_process_list_status as shared_route_process_list_status,
    route_process_remove as shared_route_process_remove,
    route_process_request as shared_route_process_request,
    route_process_rpc as shared_route_process_rpc,
    route_process_rpc_advertise as shared_route_process_rpc_advertise,
)
from .manager_internal_rpc import (
    dispatch_registry_request as shared_dispatch_registry_request,
)
from .manager_internal_rpc import (
    ensure_route_registries as shared_ensure_route_registries,
)
from .manager_internal_rpc import handle_internal_rpc as shared_handle_internal_rpc
from .manager_internal_rpc import (
    route_internal_request as shared_route_internal_request,
)
from .manager_lifecycle import shutdown_cleanup as shared_shutdown_cleanup
from .manager_lifecycle import startup_sequence as shared_startup_sequence
from .manager_log_events import (
    maybe_emit_manager_log_sink as shared_maybe_emit_manager_log_sink,
)
from .manager_log_events import (
    maybe_publish_log_event as shared_maybe_publish_log_event,
)
from .manager_logs import (
    close_manager_log_sink_file as shared_close_manager_log_sink_file,
)
from .manager_logs import emit_log as shared_emit_log
from .manager_logs import emit_log_from_payload as shared_emit_log_from_payload
from .manager_logs import log_tail as shared_log_tail
from .manager_logs import log_tail_entry_matches as shared_log_tail_entry_matches
from .manager_logs import log_tail_entry_t_mono as shared_log_tail_entry_t_mono
from .manager_logs import log_tail_filters as shared_log_tail_filters
from .manager_logs import log_tail_matches_contains as shared_log_tail_matches_contains
from .manager_logs import log_tail_matches_ids as shared_log_tail_matches_ids
from .manager_logs import log_tail_matches_severity as shared_log_tail_matches_severity
from .manager_logs import (
    log_tail_matches_source_kind as shared_log_tail_matches_source_kind,
)
from .manager_logs import log_tail_matches_time as shared_log_tail_matches_time
from .manager_logs import manager_log_sink_event as shared_manager_log_sink_event
from .manager_logs import (
    manager_log_sink_is_duplicate as shared_manager_log_sink_is_duplicate,
)
from .manager_logs import normalize_filter_set as shared_normalize_filter_set
from .manager_logs import normalize_id as shared_normalize_id
from .manager_logs import normalize_log_ts as shared_normalize_log_ts
from .manager_logs import (
    open_manager_log_sink_file as shared_open_manager_log_sink_file,
)
from .manager_logs import parse_boolish as shared_parse_boolish
from .manager_logs import parse_log_tail_limit as shared_parse_log_tail_limit
from .manager_logs import (
    parse_log_tail_since_t_mono as shared_parse_log_tail_since_t_mono,
)
from .manager_logs import (
    resolve_manager_log_file_path as shared_resolve_manager_log_file_path,
)
from .manager_logs import (
    resolve_manager_log_min_level as shared_resolve_manager_log_min_level,
)
from .manager_logs import (
    resolve_manager_log_stderr_enabled as shared_resolve_manager_log_stderr_enabled,
)
from .manager_process_logs import drain_supervisor_logs as shared_drain_supervisor_logs
from .manager_process_logs import emit_supervisor_item as shared_emit_supervisor_item
from .manager_process_logs import (
    flush_stale_supervisor_blocks as shared_flush_stale_supervisor_blocks,
)
from .manager_process_logs import (
    prune_supervisor_log_threads as shared_prune_supervisor_log_threads,
)
from .manager_process_logs import queue_supervisor_log as shared_queue_supervisor_log
from .manager_process_logs import (
    start_child_log_readers as shared_start_child_log_readers,
)
from .manager_process_logs import (
    supervisor_block_continuation as shared_supervisor_block_continuation,
)
from .manager_process_logs import (
    supervisor_block_start as shared_supervisor_block_start,
)
from .manager_process_logs import (
    supervisor_infer_severity as shared_supervisor_infer_severity,
)
from .manager_process_logs import supervisor_key as shared_supervisor_key
from .manager_process_recovery import (
    cleanup_orphans_summary as shared_cleanup_orphans_summary,
)
from .manager_process_recovery import (
    format_router_startup_failure as shared_format_router_startup_failure,
)
from .manager_process_recovery import (
    is_endpoint_collision_process_start_failure as shared_is_endpoint_collision_process_start_failure,
)
from .manager_process_recovery import (
    maybe_recover_process_start_collision as shared_maybe_recover_process_start_collision,
)
from .manager_process_recovery import recent_process_logs as shared_recent_process_logs
from .manager_process_recovery import (
    recent_process_logs_structured as shared_recent_process_logs_structured,
)
from .manager_process_recovery import (
    recent_source_logs_structured as shared_recent_source_logs_structured,
)
from .manager_process_recovery import (
    record_orphan_cleanup as shared_record_orphan_cleanup,
)
from .manager_process_spec import process_spec_kwargs_from_yaml
from .manager_process_supervision import add_process as shared_add_process
from .manager_process_supervision import (
    adopt_with_process_guard as shared_adopt_with_process_guard,
)
from .manager_process_supervision import build_driver_cmd as shared_build_driver_cmd
from .manager_process_supervision import build_router_spec as shared_build_router_spec
from .manager_process_supervision import (
    FAILURE_DRIVER_TOPICS,
    FAILURE_PROCESS_TOPICS,
)
from .utils.exit_codes import describe_exit_code, exit_code_hex
from .manager_process_supervision import (
    connect_process_data as shared_connect_process_data,
)
from .manager_process_supervision import (
    connect_process_heartbeat as shared_connect_process_heartbeat,
)
from .manager_process_supervision import driver_is_started as shared_driver_is_started
from .manager_process_supervision import driver_is_stopped as shared_driver_is_stopped
from .manager_process_supervision import (
    enforce_device_driver_stop_timeout as shared_enforce_device_driver_stop_timeout,
)
from .manager_process_supervision import (
    enforce_managed_process_heartbeat_timeout as shared_enforce_managed_process_heartbeat_timeout,
)
from .manager_process_supervision import (
    enforce_managed_process_stop_timeout as shared_enforce_managed_process_stop_timeout,
)
from .manager_process_supervision import (
    ensure_router_handle as shared_ensure_router_handle,
)
from .manager_process_supervision import (
    ensure_router_running as shared_ensure_router_running,
)
from .manager_process_supervision import (
    expand_process_argv as shared_expand_process_argv,
)
from .manager_process_supervision import (
    mark_device_offline as shared_mark_device_offline,
)
from .manager_process_supervision import (
    maybe_restart_device_driver as shared_maybe_restart_device_driver,
)
from .manager_process_supervision import (
    maybe_restart_managed_process as shared_maybe_restart_managed_process,
)
from .manager_process_supervision import (
    maybe_schedule_restart as shared_maybe_schedule_restart,
)
from .manager_process_supervision import process_snapshot as shared_process_snapshot
from .manager_process_supervision import recover_device as shared_recover_device
from .manager_process_supervision import require_process as shared_require_process
from .manager_process_supervision import (
    resolve_process_data_endpoint as shared_resolve_process_data_endpoint,
)
from .manager_process_supervision import (
    resolve_process_heartbeat_endpoint as shared_resolve_process_heartbeat_endpoint,
)
from .manager_process_supervision import restart_driver as shared_restart_driver
from .manager_process_supervision import start_driver as shared_start_driver
from .manager_process_supervision import (
    start_process_handle as shared_start_process_handle,
)
from .manager_process_supervision import stop_driver as shared_stop_driver
from .manager_process_supervision import (
    stop_process_handle as shared_stop_process_handle,
)
from .manager_process_supervision import (
    supervise_device_drivers as shared_supervise_device_drivers,
)
from .manager_process_supervision import (
    supervise_managed_processes as shared_supervise_managed_processes,
)
from .manager_process_supervision import (
    try_restart_process as shared_try_restart_process,
)
from .manager_process_supervision import (
    update_device_driver_exit_state as shared_update_device_driver_exit_state,
)
from .manager_process_supervision import (
    update_managed_process_exit_state as shared_update_managed_process_exit_state,
)
from .manager_pubsub import publish_manager_event as shared_publish_manager_event
from .manager_request_routing import (
    build_internal_action_registry,
    build_internal_type_registry,
    build_manager_route_registry,
    build_process_route_registry,
)
from .manager_route_handlers import (
    apply_command_interceptors as shared_apply_command_interceptors,
)
from .manager_route_handlers import (
    command_interceptor_chain as shared_command_interceptor_chain,
)
from .manager_route_handlers import (
    command_interceptor_routes_snapshot as shared_command_interceptor_routes_snapshot,
)
from .manager_route_handlers import (
    drop_command_interceptor_routes as shared_drop_command_interceptor_routes,
)
from .manager_route_handlers import (
    invalidate_command_interceptor_cache as shared_invalidate_command_interceptor_cache,
)
from .manager_route_handlers import (
    match_command_interceptor_route as shared_match_command_interceptor_route,
)
from .manager_route_handlers import (
    publish_interceptor_routes_update as shared_publish_interceptor_routes_update,
)
from .manager_route_handlers import (
    register_command_interceptor_routes as shared_register_command_interceptor_routes,
)
from .manager_rpc_calls import call_device_rpc as shared_call_device_rpc
from .manager_rpc_calls import call_process_rpc as shared_call_process_rpc
from .manager_runtime_metadata import (
    device_config_payload as shared_device_config_payload,
)
from .manager_runtime_metadata import (
    effective_metadata_for_device as shared_effective_metadata_for_device,
)
from .manager_runtime_metadata import (
    merge_stream_metadata_dicts as shared_merge_stream_metadata_dicts,
)
from .manager_runtime_metadata import (
    normalize_runtime_metadata_dict as shared_normalize_runtime_metadata_dict,
)
from .manager_runtime_metadata import (
    normalize_runtime_stream_metadata_dict as shared_normalize_runtime_stream_metadata_dict,
)
from .manager_runtime_metadata import (
    publish_device_config as shared_publish_device_config,
)
from .manager_runtime_metadata import (
    runtime_metadata_state as shared_runtime_metadata_state,
)
from .manager_runtime_metadata import serialize_spec_yaml as shared_serialize_spec_yaml
from .manager_runtime_metadata import (
    touch_runtime_metadata_revision as shared_touch_runtime_metadata_revision,
)
from .schemas.run_meta import run_meta_calls_from_json
from .schemas.stream import stream_calls_from_json
from .schemas.telemetry import telemetry_calls_from_json
from .types import (
    DeviceState,
    DriverState,
    RunMetaCall,
    StreamCall,
    TelemetryCall,
    TelemetryQuality,
    Timestamp,
)
from .utils import instance_lock as _instance_lock
from .utils.command_journal import CommandJournal, CommandJournalSettings
from .utils.config_parsing import ConfigError, optional_dict, require_dict, require_str
from .utils.logging_levels import normalize_log_severity, severity_rank
from .utils.manager_network import derive_local_connect_endpoint
from .utils.process_lifecycle import ProcessGuardian
from .utils.rpc_dispatch import RpcDispatchRegistry
from .utils.yaml_helpers import load_yaml_file
from .utils.zmq_helpers import json_dumps, safe_json_loads

Json = dict[str, Any]

# Re-export for test patching and manager route-handler late binding.
read_instance_lock_status = _instance_lock.read_instance_lock_status
derive_lock_effective_status = _instance_lock.derive_lock_effective_status
lock_effective_status_help = _instance_lock.lock_effective_status_help
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


@dataclass(frozen=True)
class AutoReconnectSpec:
    enabled: bool = False
    on_telemetry_stale_s: float | None = None
    cooldown_s: float = 30.0
    max_attempts: int | None = 3
    reset_attempts_after_ok_s: float = 120.0
    disconnect_timeout_ms: int = 1000
    connect_timeout_ms: int | None = None


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


def _coerce_auto_reconnect(raw: object) -> AutoReconnectSpec:
    if raw is None:
        return AutoReconnectSpec()
    obj = require_dict(raw, path=["auto_reconnect"])
    enabled_raw = obj.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise ConfigError("auto_reconnect.enabled", "must be a bool")
    enabled = bool(enabled_raw)

    stale_raw = obj.get("on_telemetry_stale_s")
    stale_s = None if stale_raw is None else float(stale_raw)
    if enabled and (stale_s is None or stale_s <= 0):
        raise ConfigError(
            "auto_reconnect.on_telemetry_stale_s",
            "must be > 0 when enabled",
        )

    cooldown_s = float(obj.get("cooldown_s", 30.0))
    reset_s = float(obj.get("reset_attempts_after_ok_s", 120.0))
    if cooldown_s < 0:
        raise ConfigError("auto_reconnect.cooldown_s", "must be >= 0")
    if reset_s < 0:
        raise ConfigError("auto_reconnect.reset_attempts_after_ok_s", "must be >= 0")

    max_raw = obj.get("max_attempts", 3)
    max_attempts = None if max_raw is None else int(max_raw)
    if max_attempts is not None and max_attempts < 1:
        raise ConfigError("auto_reconnect.max_attempts", "must be >= 1 or null")

    disconnect_timeout_ms = int(obj.get("disconnect_timeout_ms", 1000))
    connect_timeout_raw = obj.get("connect_timeout_ms")
    connect_timeout_ms = None if connect_timeout_raw is None else int(connect_timeout_raw)
    if disconnect_timeout_ms <= 0:
        raise ConfigError("auto_reconnect.disconnect_timeout_ms", "must be > 0")
    if connect_timeout_ms is not None and connect_timeout_ms <= 0:
        raise ConfigError("auto_reconnect.connect_timeout_ms", "must be > 0")

    return AutoReconnectSpec(
        enabled=enabled,
        on_telemetry_stale_s=stale_s,
        cooldown_s=cooldown_s,
        max_attempts=max_attempts,
        reset_attempts_after_ok_s=reset_s,
        disconnect_timeout_ms=disconnect_timeout_ms,
        connect_timeout_ms=connect_timeout_ms,
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
    auto_reconnect: AutoReconnectSpec = field(default_factory=AutoReconnectSpec)
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
    rpc_sock: zmq.Socket | None = None
    rpc_fail_count: int = 0
    rpc_last_fail_t_mono: float | None = None
    last_start_t_wall: float | None = None
    last_start_t_mono: float | None = None
    last_hb_t_wall: float | None = None
    last_hb_t_mono: float | None = None
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
                raise ConfigError("driver.module", f"module not found: {module_name!r}")
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
        auto_reconnect = _coerce_auto_reconnect(raw_obj.get("auto_reconnect"))
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
        auto_reconnect=auto_reconnect,
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
        router_manager_worker_queue_max: int = 8192,
        router_process_worker_queue_max: int = 8192,
        router_device_worker_queue_max: int = 16384,
        router_mirrored_worker_queue_max: int = 8192,
        router_reply_queue_max: int = 32768,
        router_inflight_max: int = 32768,
        auto_connect_on_register: bool = True,
        log_history_size: int = 10000,
        command_journal_enabled: bool = True,
        command_journal_path: str | Path | None = None,
        command_journal_queue_max: int = 10_000,
        command_journal_batch_size: int = 200,
        command_journal_flush_interval_ms: int = 200,
        command_journal_retention_max_rows: int | None = 1_000_000,
        command_journal_retention_max_age_days: float | None = None,
        telemetry_cache_max_devices: int = 4096,
        telemetry_cache_max_signals_per_device: int = 4096,
        chunk_cache_max_devices: int = 4096,
        chunk_cache_max_streams_per_device: int = 2048,
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
        self._router_manager_worker_queue_max = max(
            1, int(router_manager_worker_queue_max)
        )
        self._router_process_worker_queue_max = max(
            1, int(router_process_worker_queue_max)
        )
        self._router_device_worker_queue_max = max(
            1, int(router_device_worker_queue_max)
        )
        self._router_mirrored_worker_queue_max = max(
            1, int(router_mirrored_worker_queue_max)
        )
        self._router_reply_queue_max = max(1, int(router_reply_queue_max))
        self._router_inflight_max = max(
            1,
            min(int(router_inflight_max), self._router_reply_queue_max),
        )
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
        self._telemetry_device_order: dict[str, None] = {}
        self._telemetry_cache_max_devices = max(1, int(telemetry_cache_max_devices))
        self._telemetry_cache_max_signals_per_device = max(
            1, int(telemetry_cache_max_signals_per_device)
        )
        self._telemetry_cache_evicted_devices = 0
        self._telemetry_cache_evicted_signals = 0
        self._last_liveness: dict[str, Liveness] = {}
        self._log_history_size = max(100, int(log_history_size))
        self._log_history: deque[Json] = deque(maxlen=self._log_history_size)
        self._supervisor_log_queue: queue.Queue[Json] = queue.Queue(maxsize=5000)
        self._supervisor_log_dropped = 0
        self._supervisor_log_threads: dict[
            tuple[str, str, int, str], threading.Thread
        ] = {}
        self._supervisor_pending_blocks: dict[tuple[str, str, int, str], Json] = {}
        self._supervisor_log_dir = Path(".state") / self._instance_id / "process-logs"
        self._supervisor_log_max_bytes = 10 * 1024 * 1024
        self._supervisor_log_backups = 3
        self._last_pump_start_mono: float | None = None
        self._last_pump_end_mono: float | None = None
        self._last_pump_duration_s: float | None = None
        self._last_pump_gap_s: float | None = None
        self._last_loop_stall_mono: float | None = None
        self._last_loop_stall_duration_s: float | None = None
        self._loop_stall_count = 0
        self._manager_loop_stall_warn_s = 1.0
        self._manager_loop_stall_recent_s = 10.0
        self._heartbeat_stale_strikes_to_fail = 2
        self._heartbeat_hard_timeout_multiplier = 3.0
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
        self._chunk_device_order: dict[str, None] = {}
        self._chunk_cache_max_devices = max(1, int(chunk_cache_max_devices))
        self._chunk_cache_max_streams_per_device = max(
            1, int(chunk_cache_max_streams_per_device)
        )
        self._chunk_cache_evicted_devices = 0
        self._chunk_cache_evicted_streams = 0
        self._command_interceptor_routes: list[CommandInterceptorRoute] = []
        self._command_interceptor_order = 0
        self._command_interceptor_cache_max = 2048
        self._command_interceptor_cache: dict[
            tuple[str, str], list[CommandInterceptorRoute]
        ] = {}
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
        self._internal_action_registry = self._build_internal_action_registry()
        self._internal_type_registry = self._build_internal_type_registry()
        self._process_route_registry = self._build_process_route_registry()
        self._manager_route_registry = self._build_manager_route_registry()

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
        shared_start_driver(self, device_id)

    def stop_driver(self, device_id: str, *, force: bool = False) -> None:
        shared_stop_driver(
            self,
            device_id,
            force=force,
            offline_state=Liveness.OFFLINE,
        )

    def _mark_device_offline(self, device_id: str, handle: DeviceHandle) -> None:
        shared_mark_device_offline(
            self,
            device_id,
            handle,
            offline_state=Liveness.OFFLINE,
        )

    def _driver_is_started(self, handle: DeviceHandle) -> bool:
        return shared_driver_is_started(handle)

    def _driver_is_stopped(self, handle: DeviceHandle) -> bool:
        return shared_driver_is_stopped(handle)

    def restart_driver(self, device_id: str, *, force: bool = False) -> None:
        shared_restart_driver(self, device_id, force=force)

    def recover_device(
        self, device_id: str, *, reconnect: bool = True, force: bool = False
    ) -> None:
        shared_recover_device(
            self,
            device_id,
            reconnect=reconnect,
            force=force,
        )

    # -----------------------------
    # Managed process public API
    # -----------------------------

    def add_process(self, spec: ProcessSpec) -> None:
        shared_add_process(self, spec, handle_cls=ProcessHandle)

    def _build_router_spec(self) -> ProcessSpec:
        return shared_build_router_spec(
            self,
            process_spec_cls=ProcessSpec,
            restart_policy_always=RestartPolicy.ALWAYS,
        )

    def _ensure_router_handle(self) -> ProcessHandle:
        return shared_ensure_router_handle(self)

    def _ensure_router_running(self, *, timeout_s: float, poll_ms: int) -> None:
        shared_ensure_router_running(self, timeout_s=timeout_s, poll_ms=poll_ms)

    def _recent_process_logs(self, *, process_id: str, limit: int = 6) -> list[str]:
        return shared_recent_process_logs(self, process_id=process_id, limit=limit)

    def _recent_process_logs_structured(
        self, *, process_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        return shared_recent_process_logs_structured(
            self, process_id=process_id, limit=limit
        )

    def _format_router_startup_failure(self, handle: ProcessHandle) -> str:
        return shared_format_router_startup_failure(self, handle)

    def _cleanup_orphans_summary(
        self,
        *,
        dry_run: bool,
        stale_only: bool = True,
        timeout_s: float = 2.0,
    ) -> Json:
        return shared_cleanup_orphans_summary(
            self,
            dry_run=dry_run,
            stale_only=stale_only,
            timeout_s=timeout_s,
        )

    def _record_orphan_cleanup(self, *, source: str, summary: Json) -> None:
        shared_record_orphan_cleanup(self, source=source, summary=summary)

    @staticmethod
    def _is_endpoint_collision_process_start_failure(handle: ProcessHandle) -> bool:
        return shared_is_endpoint_collision_process_start_failure(handle)

    def _maybe_recover_process_start_collision(self, handle: ProcessHandle) -> bool:
        return shared_maybe_recover_process_start_collision(self, handle)

    def remove_process(self, process_id: str) -> None:
        handle = self._require_process(process_id)
        if handle.popen is not None and handle.popen.poll() is None:
            raise RuntimeError(f"Process {process_id!r} is still running")
        hb_endpoint = str(handle.heartbeat_endpoint or "").strip()
        data_endpoint = str(handle.process_data_endpoint or "").strip()
        self._drop_command_interceptor_routes(process_id)
        self._processes.pop(process_id)
        if hb_endpoint and all(
            str(h.heartbeat_endpoint or "").strip() != hb_endpoint
            for h in self._processes.values()
        ):
            if hb_endpoint in self._process_hb_connected:
                try:
                    self._process_hb_sub.disconnect(hb_endpoint)
                except Exception:
                    pass
                self._process_hb_connected.discard(hb_endpoint)
        if data_endpoint and all(
            str(h.process_data_endpoint or "").strip() != data_endpoint
            for h in self._processes.values()
        ):
            if data_endpoint in self._process_data_connected:
                try:
                    self._process_data_sub.disconnect(data_endpoint)
                except Exception:
                    pass
                self._process_data_connected.discard(data_endpoint)
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
        return shared_build_driver_cmd(self, spec)

    def _adopt_with_process_guard(
        self,
        popen: subprocess.Popen[str] | None,
        *,
        target_kind: str,
        target_id: str,
    ) -> None:
        shared_adopt_with_process_guard(
            self,
            popen,
            target_kind=target_kind,
            target_id=target_id,
        )

    def _require_process(self, process_id: str) -> ProcessHandle:
        return shared_require_process(self, process_id)

    def _resolve_process_heartbeat_endpoint(self, spec: ProcessSpec) -> str:
        return shared_resolve_process_heartbeat_endpoint(self, spec)

    def _resolve_process_data_endpoint(self, spec: ProcessSpec) -> str:
        return shared_resolve_process_data_endpoint(self, spec)

    def _connect_process_heartbeat(self, endpoint: str) -> None:
        shared_connect_process_heartbeat(self, endpoint)

    def _connect_process_data(self, endpoint: str) -> None:
        shared_connect_process_data(self, endpoint)

    def _expand_process_argv(self, argv: list[str], handle: ProcessHandle) -> list[str]:
        return shared_expand_process_argv(self, argv, handle)

    def _start_process_handle(
        self,
        handle: ProcessHandle,
        *,
        reset_collision_retry: bool = True,
    ) -> None:
        shared_start_process_handle(
            self,
            handle,
            reset_collision_retry=reset_collision_retry,
        )

    def _stop_process_handle(self, handle: ProcessHandle) -> None:
        shared_stop_process_handle(self, handle)

    def _maybe_schedule_restart(self, handle: ProcessHandle, now_mono: float) -> None:
        shared_maybe_schedule_restart(self, handle, now_mono)

    def _try_restart_process(self, handle: ProcessHandle) -> None:
        shared_try_restart_process(self, handle)

    def _process_snapshot(self, handle: ProcessHandle) -> Json:
        return shared_process_snapshot(self, handle)

    def _start_child_log_readers(
        self,
        *,
        popen: subprocess.Popen[str],
        source_kind: str,
        source_id: str,
        device_id: str | None,
        process_id: str | None,
    ) -> None:
        shared_start_child_log_readers(
            self,
            popen=popen,
            source_kind=source_kind,
            source_id=source_id,
            device_id=device_id,
            process_id=process_id,
        )

    def _queue_supervisor_log(self, item: Json) -> None:
        shared_queue_supervisor_log(self, item)

    def _supervisor_handle_for(self, *, source_kind: str, source_id: str) -> Any:
        kind = str(source_kind or "").strip().lower()
        sid = str(source_id or "").strip()
        if kind == "process":
            return self._processes.get(sid)
        if kind == "driver":
            return self._devices.get(sid)
        return None

    @staticmethod
    def _supervisor_tail_entry(
        *,
        item: Json,
        message: str,
        stream: str,
        severity: str | None = None,
    ) -> Json:
        now_wall = time.time()
        now_mono = time.monotonic()
        entry: Json = {
            "message": message,
            "stream": stream,
            "pid": item.get("pid"),
            "t_wall": now_wall,
            "t_mono": now_mono,
        }
        if severity is not None:
            entry["severity"] = severity
        return entry

    def _record_supervisor_raw_log(self, item: Json) -> None:
        if not isinstance(item, dict):
            return
        source_kind = str(item.get("source_kind", "") or "")
        source_id = str(item.get("source_id", "") or "")
        stream = str(item.get("stream", "") or "")
        if stream not in {"stdout", "stderr"}:
            return
        message = str(item.get("message", "") or "")
        if not message:
            return
        handle = self._supervisor_handle_for(source_kind=source_kind, source_id=source_id)
        if handle is None:
            return
        entry = self._supervisor_tail_entry(item=item, message=message, stream=stream)
        if stream == "stdout":
            handle.supervisor_stdout_tail.append(entry)
        else:
            handle.supervisor_stderr_tail.append(entry)

    def _record_supervisor_emitted_log(self, item: Json, *, severity: str) -> None:
        if not isinstance(item, dict):
            return
        source_kind = str(item.get("source_kind", "") or "")
        source_id = str(item.get("source_id", "") or "")
        stream = str(item.get("stream", "") or "")
        message = str(item.get("message", "") or "")
        if not message:
            return
        handle = self._supervisor_handle_for(source_kind=source_kind, source_id=source_id)
        if handle is None:
            return
        handle.supervisor_log_tail.append(
            self._supervisor_tail_entry(
                item=item,
                message=message,
                stream=stream,
                severity=severity,
            )
        )

    @staticmethod
    def _supervisor_key(item: Json) -> tuple[str, str, int, str]:
        return shared_supervisor_key(item)

    @staticmethod
    def _supervisor_block_start(message: str) -> bool:
        return shared_supervisor_block_start(message)

    @staticmethod
    def _supervisor_block_continuation(message: str) -> bool:
        return shared_supervisor_block_continuation(message)

    def _supervisor_infer_severity(
        self, *, stream: str, message: str, reader_error: bool
    ) -> str:
        return shared_supervisor_infer_severity(
            self,
            stream=stream,
            message=message,
            reader_error=reader_error,
        )

    def _emit_supervisor_item(self, item: Json) -> None:
        shared_emit_supervisor_item(self, item)

    def _flush_stale_supervisor_blocks(
        self, *, max_age_s: float = 0.25, force: bool = False
    ) -> None:
        shared_flush_stale_supervisor_blocks(
            self,
            max_age_s=max_age_s,
            force=force,
        )

    def _prune_supervisor_log_threads(self) -> None:
        shared_prune_supervisor_log_threads(self)

    def _drain_supervisor_logs(self, *, max_items: int = 250) -> None:
        shared_drain_supervisor_logs(self, max_items=max_items)

    def _matching_supervisor_log_threads(
        self,
        *,
        source_kind: str,
        source_id: str,
        pid: int | None,
    ) -> list[threading.Thread]:
        if pid is None:
            return []
        try:
            pid_int = int(pid)
        except Exception:
            return []
        target_kind = str(source_kind or "").strip()
        target_id = str(source_id or "").strip()
        out: list[threading.Thread] = []
        for key, thread in list(self._supervisor_log_threads.items()):
            try:
                key_kind, key_id, key_pid, _stream = key
            except Exception:
                continue
            if (
                str(key_kind) == target_kind
                and str(key_id) == target_id
                and int(key_pid) == pid_int
                and thread.is_alive()
            ):
                out.append(thread)
        return out

    def _drain_failure_event_supervisor_logs(
        self,
        *,
        source_kind: str,
        source_id: str,
        pid: int | None,
        wait_timeout_s: float = 0.05,
    ) -> None:
        self._drain_supervisor_logs(max_items=5000)
        if pid is None:
            self._flush_stale_supervisor_blocks(force=True)
            return

        deadline = time.monotonic() + max(0.0, float(wait_timeout_s))
        while True:
            threads = self._matching_supervisor_log_threads(
                source_kind=source_kind,
                source_id=source_id,
                pid=pid,
            )
            if not threads:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            join_timeout = min(0.01, remaining)
            for thread in threads:
                thread.join(timeout=join_timeout)
            self._drain_supervisor_logs(max_items=5000)

        self._drain_supervisor_logs(max_items=5000)
        if not self._matching_supervisor_log_threads(
            source_kind=source_kind,
            source_id=source_id,
            pid=pid,
        ):
            self._flush_stale_supervisor_blocks(force=True)

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
            message = "connect_check failed: identity RPC must return an object/dict"
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

    def _record_pump_timing(self, start_mono: float, end_mono: float) -> None:
        prev_end = self._last_pump_end_mono
        self._last_pump_start_mono = start_mono
        self._last_pump_end_mono = end_mono
        self._last_pump_duration_s = end_mono - start_mono
        self._last_pump_gap_s = None if prev_end is None else start_mono - prev_end
        stall_s = max(self._last_pump_duration_s, self._last_pump_gap_s or 0.0)
        if stall_s <= self._manager_loop_stall_warn_s:
            return
        self._last_loop_stall_mono = end_mono
        self._last_loop_stall_duration_s = stall_s
        self._loop_stall_count += 1
        self._publish_manager_event(
            "manager.loop_stall",
            {
                "duration_s": stall_s,
                "pump_duration_s": self._last_pump_duration_s,
                "pump_gap_s": self._last_pump_gap_s,
                "count": self._loop_stall_count,
                "ts": {"t_wall": time.time(), "t_mono": end_mono},
            },
        )

    def _pump_once(self, poll_ms: int = 50) -> None:
        """Run one iteration of the manager poll loop."""
        start_mono = time.monotonic()
        try:
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
        finally:
            self._record_pump_timing(start_mono, time.monotonic())

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

        handle.heartbeat_pid = pid
        handle.pid = pid
        handle.last_hb_t_wall = float(ts["t_wall"])
        handle.last_hb_t_mono = float(ts["t_mono"])
        handle.last_heartbeat_payload = copy.deepcopy(msg)
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
        return shared_command_interceptor_routes_snapshot(self)

    def _publish_interceptor_routes_update(
        self, *, process_id: str, routes: list[Json], replace: bool
    ) -> None:
        shared_publish_interceptor_routes_update(
            self,
            process_id=process_id,
            routes=routes,
            replace=replace,
        )

    def _invalidate_command_interceptor_cache(self) -> None:
        shared_invalidate_command_interceptor_cache(self)

    def _drop_command_interceptor_routes(self, process_id: str) -> None:
        shared_drop_command_interceptor_routes(self, process_id)

    def _register_command_interceptor_routes(
        self, process_id: str, routes_raw: Any, *, replace: bool
    ) -> list[Json]:
        return shared_register_command_interceptor_routes(
            self,
            process_id,
            routes_raw,
            replace=replace,
            route_cls=CommandInterceptorRoute,
        )

    @staticmethod
    def _match_command_interceptor_route(
        route: CommandInterceptorRoute, device_id: str, action: str
    ) -> bool:
        return shared_match_command_interceptor_route(route, device_id, action)

    def _command_interceptor_chain(
        self, device_id: str, action: str
    ) -> list[CommandInterceptorRoute]:
        return shared_command_interceptor_chain(
            self,
            device_id,
            action,
            match_route=self._match_command_interceptor_route,
        )

    def _apply_command_interceptors(
        self, cmd: Json, *, request_id: str | None, caller_process_id: str | None
    ) -> tuple[bool, Json | None, Json | None]:
        return shared_apply_command_interceptors(
            self,
            cmd,
            request_id=request_id,
            caller_process_id=caller_process_id,
            running_states={
                ManagedProcessState.STARTING,
                ManagedProcessState.RUNNING,
                ManagedProcessState.STOPPING,
            },
        )

    def _handle_internal_rpc(self) -> None:
        shared_handle_internal_rpc(self)

    def _route_internal_request(self, req: Json) -> Json:
        return shared_route_internal_request(self, req)

    @staticmethod
    def _dispatch_registry_request(
        registry: RpcDispatchRegistry,
        *,
        route_key: Any,
        req: Json,
    ) -> Json | None:
        return shared_dispatch_registry_request(
            registry,
            route_key=route_key,
            req=req,
        )

    def _ensure_route_registries(self) -> None:
        shared_ensure_route_registries(self)

    def _build_internal_action_registry(self) -> RpcDispatchRegistry:
        return build_internal_action_registry(self)

    def _build_internal_type_registry(self) -> RpcDispatchRegistry:
        return build_internal_type_registry(self)

    def _build_process_route_registry(self) -> RpcDispatchRegistry:
        return build_process_route_registry(self)

    def _build_manager_route_registry(self) -> RpcDispatchRegistry:
        return build_manager_route_registry(self)

    def _route_action_telemetry_schema_list(self, req: Json) -> Json:
        del req
        return {"ok": True, "result": self._telemetry_schema_list()}

    def _route_type_list_devices(self, req: Json) -> Json:
        del req
        return {"ok": True, "devices": self._list_devices_snapshot()}

    def _route_type_telemetry_snapshot(self, req: Json) -> Json:
        del req
        return {"ok": True, "result": self._telemetry_snapshot()}

    def _route_type_get_telemetry(self, req: Json) -> Json:
        device_id = str(req["device_id"])
        return {
            "ok": True,
            "telemetry": self._get_device_telemetry_snapshot(device_id),
        }

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
        return shared_publish_process_command_response(
            self,
            process_id=process_id,
            action=action,
            params=params,
            response=response,
            request_id=request_id,
            caller_process_id=caller_process_id,
            source_kind=source_kind,
            source_id=source_id,
        )

    def _route_process_request(self, rtype: Any, req: Json) -> Json | None:
        return shared_route_process_request(self, rtype, req)

    def _route_process_list_status(self, req: Json) -> Json:
        return shared_route_process_list_status(self, req)

    def _route_process_get(self, req: Json) -> Json:
        return shared_route_process_get(self, req)

    def _route_process_control(
        self,
        req: Json,
        *,
        action: str,
        runner: Callable[[str], None],
    ) -> Json:
        return shared_route_process_control(
            self,
            req,
            action=action,
            runner=runner,
        )

    def _route_process_start(self, req: Json) -> Json:
        return self._route_process_control(
            req,
            action="manager.processes.start",
            runner=self.start_process,
        )

    def _route_process_stop(self, req: Json) -> Json:
        return self._route_process_control(
            req,
            action="manager.processes.stop",
            runner=self.stop_process,
        )

    def _route_process_restart(self, req: Json) -> Json:
        return self._route_process_control(
            req,
            action="manager.processes.restart",
            runner=self.restart_process,
        )

    def _route_process_add(self, req: Json) -> Json:
        return shared_route_process_add(self, req)

    def _route_process_remove(self, req: Json) -> Json:
        return shared_route_process_remove(self, req)

    def _route_process_rpc_advertise(self, req: Json) -> Json:
        return shared_route_process_rpc_advertise(self, req)

    def _route_process_rpc(self, req: Json) -> Json:
        return shared_route_process_rpc(
            self,
            req,
            running_states={
                ManagedProcessState.STARTING,
                ManagedProcessState.RUNNING,
                ManagedProcessState.STOPPING,
            },
            starting_state=ManagedProcessState.STARTING,
        )

    def _route_command_interceptor_register(self, req: Json) -> Json:
        return shared_route_command_interceptor_register(self, req)

    def _route_command_interceptor_list(self, req: Json) -> Json:
        return shared_route_command_interceptor_list(self, req)

    def _route_manager_request(self, rtype: Any, req: Json) -> Json | None:
        return shared_route_manager_request(self, rtype, req)

    def _route_manager_shutdown(self, req: Json) -> Json:
        return shared_route_manager_shutdown(self, req)

    def _route_manager_identity(self, req: Json) -> Json:
        return shared_route_manager_identity(self, req)

    def _route_manager_cleanup_orphans(self, req: Json) -> Json:
        return shared_route_manager_cleanup_orphans(self, req)

    def _route_manager_log_publish(self, req: Json) -> Json:
        return shared_route_manager_log_publish(self, req)

    def _route_manager_log_tail(self, req: Json) -> Json:
        return shared_route_manager_log_tail(self, req)

    def _route_manager_command_journal_status(self, req: Json) -> Json:
        return shared_route_manager_command_journal_status(self, req)

    def _route_manager_command_journal_tail(self, req: Json) -> Json:
        return shared_route_manager_command_journal_tail(self, req)

    def _route_manager_event_publish(self, req: Json) -> Json:
        return shared_route_manager_event_publish(self, req)

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
        shared_update_device_driver_exit_state(self, handle, rc)

    def _enforce_device_driver_stop_timeout(
        self, handle: DeviceHandle, now_mono: float
    ) -> None:
        shared_enforce_device_driver_stop_timeout(self, handle, now_mono)

    def _maybe_restart_device_driver(
        self, device_id: str, handle: DeviceHandle, now_mono: float
    ) -> None:
        shared_maybe_restart_device_driver(self, device_id, handle, now_mono)

    def _supervise_device_drivers(self, now_mono: float) -> None:
        shared_supervise_device_drivers(self, now_mono)

    def _update_managed_process_exit_state(
        self, handle: ProcessHandle, rc: int
    ) -> bool:
        return shared_update_managed_process_exit_state(self, handle, rc)

    def _enforce_managed_process_heartbeat_timeout(
        self, handle: ProcessHandle, now_mono: float
    ) -> None:
        shared_enforce_managed_process_heartbeat_timeout(self, handle, now_mono)

    def _enforce_managed_process_stop_timeout(
        self, handle: ProcessHandle, now_mono: float
    ) -> None:
        shared_enforce_managed_process_stop_timeout(self, handle, now_mono)

    def _maybe_restart_managed_process(
        self, handle: ProcessHandle, now_mono: float
    ) -> None:
        shared_maybe_restart_managed_process(self, handle, now_mono)

    def _supervise_managed_processes(self, now_mono: float) -> None:
        shared_supervise_managed_processes(self, now_mono)

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
                "popen_pid": handle.driver_popen_pid,
                "heartbeat_pid": handle.driver_heartbeat_pid,
                "restart_count": handle.driver_restart_count,
                "last_exit_code": handle.driver_last_exit_code,
                "last_error": handle.driver_last_error,
            },
            "connect_check": copy.deepcopy(handle.connect_check_last),
            "auto_reconnect": {
                "enabled": bool(handle.spec.auto_reconnect.enabled),
                "attempts": int(handle.auto_reconnect_attempts),
                "last_attempt_mono": handle.auto_reconnect_last_attempt_mono,
                "last_attempt_wall": handle.auto_reconnect_last_attempt_wall,
                "last_success_mono": handle.auto_reconnect_last_success_mono,
                "healthy_since_mono": handle.auto_reconnect_healthy_since_mono,
                "last_error": handle.auto_reconnect_last_error,
                "suppressed": bool(handle.auto_reconnect_suppressed),
                "on_telemetry_stale_s": handle.spec.auto_reconnect.on_telemetry_stale_s,
                "cooldown_s": handle.spec.auto_reconnect.cooldown_s,
                "max_attempts": handle.spec.auto_reconnect.max_attempts,
                "reset_attempts_after_ok_s": handle.spec.auto_reconnect.reset_attempts_after_ok_s,
            },
            "source_kind": "local",
            "is_remote": False,
            "owner_peer_id": None,
            "remote_device_id": None,
        }

    def _list_devices_status_snapshot(self) -> list[Json]:
        device_ids = sorted(
            set(self._devices) | set(self._federation_hub.mirrored_device_ids())
        )
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
        shared_publish_manager_event(self, topic, payload)

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
        return shared_should_journal_command_action(action)

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
        shared_append_command_journal_entry(self, payload)

    def _command_journal_status_payload(self) -> Json:
        return shared_command_journal_status_payload(self)

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
        if str(action or "").strip() == "process.capabilities" and error_code in {
            "process_rpc_not_ready",
            "process_starting",
        }:
            handle = self._processes.get(str(process_id))
            if handle is not None and handle.state == ManagedProcessState.STARTING:
                return
        self._publish_manager_event("manager.command", cmd_payload)

    @staticmethod
    def _normalize_log_severity(raw: Any) -> str:
        return normalize_log_severity(raw, default="info")

    @staticmethod
    def _parse_boolish(raw: Any, *, default: bool) -> bool:
        return shared_parse_boolish(raw, default=default)

    def _resolve_manager_log_stderr_enabled(self, raw: Any) -> bool:
        return shared_resolve_manager_log_stderr_enabled(self, raw)

    def _resolve_manager_log_file_path(self, raw: Any) -> Path | None:
        return shared_resolve_manager_log_file_path(raw)

    def _resolve_manager_log_min_level(self, raw: Any) -> str:
        return shared_resolve_manager_log_min_level(raw)

    @staticmethod
    def _severity_rank(raw: Any) -> int:
        return severity_rank(raw, default="info")

    def _open_manager_log_sink_file(self) -> None:
        shared_open_manager_log_sink_file(self)

    def _close_manager_log_sink_file(self) -> None:
        shared_close_manager_log_sink_file(self)

    def _manager_log_sink_event(
        self, topic: str, payload: Json
    ) -> tuple[str, str, str, str | None, str]:
        return shared_manager_log_sink_event(self, topic, payload)

    def _manager_log_sink_is_duplicate(self, fingerprint: str) -> bool:
        return shared_manager_log_sink_is_duplicate(self, fingerprint)

    def _maybe_emit_manager_log_sink(self, topic: str, payload: Json) -> None:
        shared_maybe_emit_manager_log_sink(self, topic, payload)

    @staticmethod
    def _normalize_id(raw: Any) -> str | None:
        return shared_normalize_id(raw)

    def _normalize_log_ts(self, raw: Any) -> Json:
        return shared_normalize_log_ts(raw)

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
        return shared_emit_log(
            self,
            severity=severity,
            topic=topic,
            message=message,
            source_kind=source_kind,
            source_id=source_id,
            device_id=device_id,
            process_id=process_id,
            stream=stream,
            payload=payload,
            payload_json=payload_json,
            ts=ts,
        )

    def _emit_log_from_payload(
        self, payload: Json, *, default_topic: str = "manager.log"
    ) -> Json:
        return shared_emit_log_from_payload(
            self,
            payload,
            default_topic=default_topic,
        )

    @staticmethod
    def _normalize_filter_set(raw: Any, *, field: str) -> set[str] | None:
        return shared_normalize_filter_set(raw, field=field)

    @staticmethod
    def _parse_log_tail_limit(raw: Any) -> int:
        return shared_parse_log_tail_limit(raw)

    @staticmethod
    def _parse_log_tail_since_t_mono(raw: Any) -> float | None:
        return shared_parse_log_tail_since_t_mono(raw)

    def _log_tail_filters(self, params: Json) -> dict[str, Any]:
        return shared_log_tail_filters(self, params)

    @staticmethod
    def _log_tail_entry_t_mono(entry: Json) -> float | None:
        return shared_log_tail_entry_t_mono(entry)

    def _log_tail_matches_time(self, entry: Json, *, filters: dict[str, Any]) -> bool:
        return shared_log_tail_matches_time(entry, filters=filters)

    def _log_tail_matches_severity(
        self, entry: Json, *, filters: dict[str, Any]
    ) -> bool:
        return shared_log_tail_matches_severity(entry, filters=filters)

    @staticmethod
    def _log_tail_matches_source_kind(entry: Json, *, filters: dict[str, Any]) -> bool:
        return shared_log_tail_matches_source_kind(entry, filters=filters)

    def _log_tail_matches_ids(self, entry: Json, *, filters: dict[str, Any]) -> bool:
        return shared_log_tail_matches_ids(entry, filters=filters)

    @staticmethod
    def _log_tail_matches_contains(entry: Json, *, filters: dict[str, Any]) -> bool:
        return shared_log_tail_matches_contains(entry, filters=filters)

    def _log_tail_entry_matches(self, entry: Json, *, filters: dict[str, Any]) -> bool:
        return shared_log_tail_entry_matches(entry, filters=filters)

    def _log_tail(self, params: Json) -> Json:
        return shared_log_tail(self, params)

    def _maybe_publish_log_event(self, topic: str, payload: Json) -> None:
        shared_maybe_publish_log_event(self, topic, payload)

    def _publish_process_event(self, topic: str, handle: ProcessHandle) -> None:
        payload: dict[str, Any] = {
            "version": 1,
            "process_id": handle.spec.process_id,
            "state": handle.state,
            "pid": handle.pid,
            "popen_pid": handle.popen_pid,
            "heartbeat_pid": handle.heartbeat_pid,
            "exit_code": handle.last_exit_code,
            "heartbeat_endpoint": handle.heartbeat_endpoint,
            "process_data_endpoint": handle.process_data_endpoint,
            "error": handle.last_error,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        if topic in FAILURE_PROCESS_TOPICS:
            failure_pid = handle.last_failure_pid
            payload["error_kind"] = handle.last_error_kind
            payload["signal"] = handle.last_signal_name
            payload["restart_count"] = handle.restart_count
            payload["heartbeat_age_s"] = handle.last_heartbeat_age_s
            payload["liveness_age_s"] = handle.last_liveness_age_s
            payload["heartbeat_received"] = handle.last_heartbeat_received
            payload["heartbeat_stale_strikes"] = handle.heartbeat_stale_strikes
            payload["last_stale_detected_mono"] = handle.last_stale_detected_mono
            payload["terminated_by_manager"] = handle.terminated_by_manager
            payload["termination_reason"] = handle.termination_reason
            payload["termination_method"] = handle.termination_method
            payload["termination_error"] = handle.termination_error
            payload["recent_manager_loop_stall"] = handle.recent_manager_loop_stall
            payload["last_manager_loop_stall_duration_s"] = handle.last_manager_loop_stall_duration_s
            payload["failure_pid"] = failure_pid
            payload["exit_code_hex"] = exit_code_hex(handle.last_exit_code)
            payload["exit_code_description"] = describe_exit_code(handle.last_exit_code)
            payload["last_heartbeat_payload"] = handle.last_heartbeat_payload
            payload.update(
                self._failure_event_log_context(
                    process_id=handle.spec.process_id,
                    pid=failure_pid,
                )
            )
        self._publish_manager_event(topic, payload)

    def _publish_driver_event(self, topic: str, handle: DeviceHandle) -> None:
        payload: dict[str, Any] = {
            "version": 1,
            "device_id": handle.spec.device_id,
            "state": handle.driver_process_state,
            "pid": handle.driver_pid,
            "popen_pid": handle.driver_popen_pid,
            "heartbeat_pid": handle.driver_heartbeat_pid,
            "exit_code": handle.driver_last_exit_code,
            "error": handle.driver_last_error,
            "restart_count": handle.driver_restart_count,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        if topic in FAILURE_DRIVER_TOPICS:
            failure_pid = handle.driver_last_failure_pid
            payload["error_kind"] = handle.driver_last_error_kind
            payload["signal"] = handle.driver_last_signal_name
            payload["failure_pid"] = failure_pid
            payload["exit_code_hex"] = exit_code_hex(handle.driver_last_exit_code)
            payload["exit_code_description"] = describe_exit_code(
                handle.driver_last_exit_code
            )
            payload.update(
                self._failure_event_log_context(
                    source_id=handle.spec.device_id,
                    source_kind="driver",
                    pid=failure_pid,
                )
            )
        self._publish_manager_event(topic, payload)

    def _failure_event_log_context(
        self,
        *,
        process_id: str | None = None,
        source_id: str | None = None,
        source_kind: str = "process",
        pid: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        resolved_source_id = source_id if source_id is not None else (process_id or "")
        try:
            self._drain_failure_event_supervisor_logs(
                source_kind=source_kind,
                source_id=resolved_source_id,
                pid=pid,
            )
            handle = self._supervisor_handle_for(
                source_kind=source_kind,
                source_id=resolved_source_id,
            )
            if handle is None:
                tail_stdout: list[Json] = []
                tail_stderr: list[Json] = []
                tail_supervisor_logs: list[Json] = []
                stdout_log_path = None
                stderr_log_path = None
            else:
                tail_stdout = list(handle.supervisor_stdout_tail)
                tail_stderr = list(handle.supervisor_stderr_tail)
                tail_supervisor_logs = list(handle.supervisor_log_tail)
                stdout_log_path = handle.stdout_log_path
                stderr_log_path = handle.stderr_log_path
            recent = self._log_tail(
                {
                    "limit": limit,
                    "since_t_mono": time.monotonic() - 300.0,
                    "source_kind": source_kind,
                    "source_ids": [resolved_source_id],
                }
            ).get("entries", [])
            return {
                "tail_logs": shared_recent_source_logs_structured(
                    self,
                    source_id=resolved_source_id,
                    source_kind=source_kind,
                    limit=limit,
                ),
                "tail_recent_logs": recent if isinstance(recent, list) else [],
                "tail_stdout": tail_stdout,
                "tail_stderr": tail_stderr,
                "tail_supervisor_logs": tail_supervisor_logs,
                "stdout_log_path": stdout_log_path,
                "stderr_log_path": stderr_log_path,
            }
        except Exception as exc:  # pragma: no cover - defensive
            try:
                self._emit_log(
                    severity="warning",
                    topic="manager.failure_event.tail_unavailable",
                    message=f"failed to gather tail logs: {exc}",
                    source_kind=source_kind,
                    source_id=source_id or process_id or "",
                    payload={"error": repr(exc)},
                )
            except Exception:
                pass
            return {
                "tail_logs": [],
                "tail_recent_logs": [],
                "tail_stdout": [],
                "tail_stderr": [],
                "tail_supervisor_logs": [],
                "stdout_log_path": None,
                "stderr_log_path": None,
            }

    def _failure_event_tail_logs(
        self,
        *,
        process_id: str | None = None,
        source_id: str | None = None,
        source_kind: str = "process",
        pid: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return list(
            self._failure_event_log_context(
                process_id=process_id,
                source_id=source_id,
                source_kind=source_kind,
                pid=pid,
                limit=limit,
            ).get("tail_logs", [])
        )

    @staticmethod
    def _normalize_runtime_metadata_dict(
        raw: object,
        *,
        label: str,
    ) -> dict[str, Any]:
        return shared_normalize_runtime_metadata_dict(raw, label=label)

    @classmethod
    def _normalize_runtime_stream_metadata_dict(
        cls,
        raw: object,
        *,
        label: str,
    ) -> dict[str, dict[str, Any]]:
        del cls
        return shared_normalize_runtime_stream_metadata_dict(raw, label=label)

    @staticmethod
    def _merge_stream_metadata_dicts(
        base: dict[str, dict[str, Any]],
        overlay: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        return shared_merge_stream_metadata_dicts(base, overlay)

    def _effective_metadata_for_device(
        self, device_id: str, spec: DeviceSpec
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        return shared_effective_metadata_for_device(self, device_id, spec)

    def _runtime_metadata_state(self, device_id: str, handle: DeviceHandle) -> Json:
        return shared_runtime_metadata_state(self, device_id, handle)

    def _touch_runtime_metadata_revision(self, device_id: str) -> int:
        return shared_touch_runtime_metadata_revision(self, device_id)

    def _publish_device_config(self, handle: DeviceHandle) -> None:
        shared_publish_device_config(self, handle)

    def _device_config_payload(self, handle: DeviceHandle) -> Json:
        return shared_device_config_payload(self, handle)

    def _serialize_spec_yaml(self, spec: DeviceSpec) -> str:
        return shared_serialize_spec_yaml(spec)

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

    @staticmethod
    def _merge_stream_metadata_dicts(
        base: dict[str, dict[str, Any]],
        overlay: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        return shared_merge_stream_metadata_dicts(base, overlay)

    def _effective_metadata_for_device(
        self, device_id: str, spec: DeviceSpec
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        return shared_effective_metadata_for_device(self, device_id, spec)

    def _runtime_metadata_state(self, device_id: str, handle: DeviceHandle) -> Json:
        return shared_runtime_metadata_state(self, device_id, handle)

    def _touch_runtime_metadata_revision(self, device_id: str) -> int:
        return shared_touch_runtime_metadata_revision(self, device_id)

    def _publish_device_config(self, handle: DeviceHandle) -> None:
        shared_publish_device_config(self, handle)

    def _device_config_payload(self, handle: DeviceHandle) -> Json:
        return shared_device_config_payload(self, handle)

    def _serialize_spec_yaml(self, spec: DeviceSpec) -> str:
        return shared_serialize_spec_yaml(spec)

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
