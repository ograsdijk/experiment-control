from __future__ import annotations

import copy
import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TextIO

import zmq

from .federation import FederationConfig
from .federation.hub import FederationHub
from ._manager.config import (
    device_spec_from_yaml,
    process_spec_from_yaml,
)
# Phase 8.2.14: route_device_request wrapped by DeviceRoutingMixin; the
# remaining call site at L1906 (_dispatch_lifecycle_task) uses the
# module-level callable directly because it runs off a background thread
# without ``self`` in scope.
from ._manager.device_routing import route_device_request
# Phase 8.2.13: ``handle_driver_pub`` and ``ingest_chunk_ready`` migrated
# onto ``DriverPubMixin``. ``ingest_heartbeat`` / ``ingest_telemetry`` stay
# imported because Manager's ``_ingest_heartbeat`` / ``_ingest_telemetry``
# forwarders still pass Manager-module-level enum classes through.
from ._manager.driver_pub import ingest_heartbeat as shared_ingest_heartbeat
from ._manager.driver_pub import ingest_telemetry as shared_ingest_telemetry
# Phase 8.2.15: 21 route_*/publish_process_command_response forwarders
# migrated onto ``RouteHandlersMixin``. Only ``route_process_rpc``
# (which needs the Manager-side ``ManagedProcessState`` running-state
# set) and ``apply_command_interceptors`` (same reason) stay imported
# below — Manager's ``_route_process_rpc`` and
# ``_apply_command_interceptors`` keep their thin wrappers because
# moving the enum sets onto the mixin would force a circular import.
from ._manager.route_handlers import route_process_rpc as shared_route_process_rpc
# Phase 8.2.6: ``handle_internal_rpc`` / ``route_internal_request`` /
# ``ensure_route_registries`` migrated onto ``InternalRpcMixin``. Only
# the pure ``dispatch_registry_request`` helper remains imported.
from ._manager.internal_rpc import (
    dispatch_registry_request as shared_dispatch_registry_request,
)
# Phase 8.2.11: startup_sequence / shutdown_cleanup migrated onto LifecycleMixin.
# Phase 8.2.4: most ``manager_logs`` helpers migrated onto
# ``LogsMixin``. Only the pure module-level utilities Manager still
# calls directly during ``__init__`` (and the two `_normalize_*`
# helpers exposed for cross-module use) remain imported.
from ._manager.logs import normalize_id as shared_normalize_id
from ._manager.logs import parse_boolish as shared_parse_boolish
from ._manager.logs import (
    resolve_manager_log_file_path as shared_resolve_manager_log_file_path,
)
from ._manager.logs import (
    resolve_manager_log_min_level as shared_resolve_manager_log_min_level,
)
from ._manager.logs import (
    resolve_manager_log_stderr_enabled as shared_resolve_manager_log_stderr_enabled,
)
# Phase 8.2.10: supervisor-log helpers migrated onto ``ProcessLogsMixin``.
# Only the pure parser utilities Manager still wraps as staticmethods
# remain imported.
from ._manager.process_logs import (
    supervisor_block_continuation as shared_supervisor_block_continuation,
)
from ._manager.process_logs import (
    supervisor_block_start as shared_supervisor_block_start,
)
from ._manager.process_logs import supervisor_key as shared_supervisor_key
# Phase 8.2.9: process-recovery helpers migrated onto
# ``ProcessRecoveryMixin``. Only the pure
# ``is_endpoint_collision_process_start_failure`` predicate remains
# imported (Manager exposes it as a static staticmethod for tests).
from ._manager.process_recovery import (
    is_endpoint_collision_process_start_failure as shared_is_endpoint_collision_process_start_failure,
)
from ._manager.process_supervision import add_process as shared_add_process
from ._manager.process_supervision import (
    adopt_with_process_guard as shared_adopt_with_process_guard,
)
from ._manager.process_supervision import build_router_spec as shared_build_router_spec
from ._manager.process_supervision import (
    FAILURE_DRIVER_TOPICS,
    FAILURE_PROCESS_TOPICS,
)
from .utils.exit_codes import describe_exit_code, exit_code_hex
from ._manager.process_supervision import (
    mark_device_offline as shared_mark_device_offline,
)
from ._manager.process_supervision import recover_device as shared_recover_device
from ._manager.process_supervision import stop_driver as shared_stop_driver
# Phase 8.2.7: build_*_registry helpers migrated onto RequestRoutingMixin.
from ._manager.models import (
    AutoReconnectSpec,
    CommandInterceptorRoute,
    ConnectCheckSpec,
    DeviceHandle,
    DeviceSpec,
    DriverRegistration,
    Heartbeat,
    Liveness,
    ManagedProcessState,
    ProcessHandle,
    ProcessSpec,
    RestartPolicy,
    TelemetryBundle,
    TelemetrySignal,
)
from ._manager.interceptor_routes import InterceptorRouteState
# Phase 8.2.15: command-interceptor helper wrappers also migrated to
# ``RouteHandlersMixin``. The two below stay as imports because they
# need Manager-side enum sets the mixin can't reference.
from ._manager.route_handlers import (
    apply_command_interceptors as shared_apply_command_interceptors,
)
from ._manager.route_handlers import (
    command_interceptor_chain as shared_command_interceptor_chain,
)
from ._manager.route_handlers import (
    match_command_interceptor_route as shared_match_command_interceptor_route,
)
from ._manager.route_handlers import (
    register_command_interceptor_routes as shared_register_command_interceptor_routes,
)
# Phase 8.2.8: call_device_rpc / call_process_rpc migrated onto RpcCallsMixin.
# Phase 8.2.5: manager-taking helpers migrated onto
# ``RuntimeMetadataMixin``. Only pure utilities still imported here.
from ._manager.runtime_metadata import (
    merge_stream_metadata_dicts as shared_merge_stream_metadata_dicts,
)
from ._manager.runtime_metadata import (
    normalize_runtime_metadata_dict as shared_normalize_runtime_metadata_dict,
)
from ._manager.runtime_metadata import (
    normalize_runtime_stream_metadata_dict as shared_normalize_runtime_stream_metadata_dict,
)
from ._manager.runtime_metadata import serialize_spec_yaml as shared_serialize_spec_yaml

# --- Phase 8.1 mixin scaffolding -------------------------------------
# Each ``manager_*.py`` helper module declares an empty mixin class at
# its bottom. ``Manager`` inherits from all of them so individual
# ``def shared_foo(manager, ...)`` helpers can migrate onto the mixin
# one at a time during the mixin migration without churning ``Manager``'s
# class header on every step. Until a method moves, the existing
# ``shared_*`` forwarder pattern continues to work unchanged.
from ._manager.command_journal import CommandJournalMixin
from ._manager.core_state import (
    LifecycleExecutor,
    ManagerCaches,
    ManagerJournal,
    ManagerSockets,
)
from ._manager.device_routing import DeviceRoutingMixin
from ._manager.driver_pub import DriverPubMixin
from ._manager.interceptor_routes import InterceptorRoutesMixin
from ._manager.internal_rpc import InternalRpcMixin
from ._manager.lifecycle import LifecycleMixin
from ._manager.log_events import LogEventsMixin
from ._manager.logs import LogsMixin
from ._manager.process_logs import ProcessLogsMixin
from ._manager.process_recovery import ProcessRecoveryMixin
from ._manager.process_supervision import ProcessSupervisionMixin
from ._manager.pubsub import PubSubMixin
from ._manager.request_routing import RequestRoutingMixin
from ._manager.route_handlers import RouteHandlersMixin
from ._manager.rpc_calls import RpcCallsMixin
from ._manager.runtime_metadata import RuntimeMetadataMixin

from .types import DeviceState, DriverState, TelemetryQuality, Timestamp
from .utils import instance_lock as _instance_lock
from .utils.command_journal import CommandJournal
from .utils.logging_levels import normalize_log_severity, severity_rank
from .utils.process_lifecycle import ProcessGuardian
from .utils.rpc_dispatch import RpcDispatchRegistry
from .utils.zmq_helpers import MAX_DRAIN_PER_TICK, json_dumps, safe_json_loads

Json = dict[str, Any]

__all__ = [
    "AutoReconnectSpec",
    "CommandInterceptorRoute",
    "ConnectCheckSpec",
    "DeviceHandle",
    "DeviceSpec",
    "DriverRegistration",
    "Heartbeat",
    "Liveness",
    "ManagedProcessState",
    "Manager",
    "ProcessHandle",
    "ProcessSpec",
    "RestartPolicy",
    "TelemetryBundle",
    "TelemetrySignal",
    "device_spec_from_yaml",
    "process_spec_from_yaml",
]

# Re-export for test patching and manager route-handler late binding.
read_instance_lock_status = _instance_lock.read_instance_lock_status
derive_lock_effective_status = _instance_lock.derive_lock_effective_status
lock_effective_status_help = _instance_lock.lock_effective_status_help


class Manager(
    # Mixin MRO follows the historical manager helper migration order.
    # (least-coupled first → most-coupled last) so a method-resolution
    # collision (if ever introduced) is won by the more foundational
    # mixin. All mixins are currently empty scaffolds — they will gain
    # methods one at a time as the migration progresses. Note: there is
    # no ``manager_process_spec`` / ``manager_config`` / ``manager_client``
    # mixin because those modules' helpers don't take ``manager`` as
    # first arg (they are pure pre-construction utilities).
    PubSubMixin,
    CommandJournalMixin,
    LogEventsMixin,
    LogsMixin,
    RuntimeMetadataMixin,
    DriverPubMixin,
    InternalRpcMixin,
    RequestRoutingMixin,
    RouteHandlersMixin,
    DeviceRoutingMixin,
    RpcCallsMixin,
    InterceptorRoutesMixin,
    ProcessRecoveryMixin,
    ProcessLogsMixin,
    LifecycleMixin,
    ProcessSupervisionMixin,
):
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

    _ctx: zmq.Context
    _registry_bind: str
    _registry_rep: zmq.Socket
    _sub: zmq.Socket
    _process_hb_sub: zmq.Socket
    _process_data_sub: zmq.Socket
    _internal_rpc_bind: str
    _internal_rpc: zmq.Socket
    _internal_rpc_endpoint: str
    _external_rpc_bind: str
    _external_pub_bind: str
    _external_pub_connect_local: str
    _external_pub: zmq.Socket
    _main_thread_id: int
    _lifecycle_executor: ThreadPoolExecutor
    _lifecycle_device_locks: dict[str, threading.Lock]
    _lifecycle_reply_queue: queue.Queue[tuple[bytes, Json]]
    _lifecycle_event_queue: queue.Queue[tuple[str, Json]]
    _lifecycle_event_dropped: int
    _lifecycle_event_dropped_lock: threading.Lock
    _telemetry_latest: dict[str, dict[str, tuple[Timestamp, TelemetrySignal]]]
    _telemetry_last_bundle_ts: dict[str, Timestamp]
    _telemetry_device_order: dict[str, None]
    _latest_chunk_desc: dict[str, dict[str, Json]]
    _chunk_device_order: dict[str, None]
    _last_liveness: dict[str, Liveness]
    _process_rss_cache: dict[int, tuple[float, int | None]]
    _runtime_device_metadata_overrides: dict[str, dict[str, Any]]
    _runtime_stream_metadata_overrides: dict[str, dict[str, dict[str, Any]]]
    _runtime_metadata_revision: dict[str, int]
    _command_journal_enabled: bool
    _command_journal_path: Path | None
    _command_journal: CommandJournal | None
    _command_journal_start_error: str | None
    _read_instance_lock_status: Callable[[str], Any]
    _derive_lock_effective_status: Callable[..., Any]
    _lock_effective_status_help: Callable[[str], str]

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
        self._read_instance_lock_status = read_instance_lock_status
        self._derive_lock_effective_status = derive_lock_effective_status
        self._lock_effective_status_help = lock_effective_status_help
        self._started_t_wall = time.time()
        self._started_t_mono = time.monotonic()
        sockets = ManagerSockets.create(
            ctx=zmq.Context.instance(),
            registry_bind=registry_bind,
            internal_rpc_bind=internal_rpc_bind,
            external_rpc_bind=external_rpc_bind,
            external_pub_bind=external_pub_bind,
            external_pub_connect_local=external_pub_connect_local,
        )
        sockets.bind_to_manager(self)

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
        caches = ManagerCaches()
        caches.bind_to_manager(self)
        self._process_rss_cache_ttl_s = 1.0
        self._process_hb_refresh_error_period_s = 10.0
        self._last_process_hb_refresh_error_mono: float | None = None
        self._process_hb_refresh_error_suppressed = 0

        self._auto_connect_on_register = auto_connect_on_register

        self._devices: dict[str, DeviceHandle] = {}
        self._processes: dict[str, ProcessHandle] = {}

        # Latest telemetry cache: (device_id -> signal_name -> TelemetrySignal + bundle ts)
        self._telemetry_cache_max_devices = max(1, int(telemetry_cache_max_devices))
        self._telemetry_cache_max_signals_per_device = max(
            1, int(telemetry_cache_max_signals_per_device)
        )
        self._telemetry_cache_evicted_devices = 0
        self._telemetry_cache_evicted_signals = 0
        self._log_history_size = max(100, int(log_history_size))
        self._log_history: deque[Json] = deque(maxlen=self._log_history_size)
        self._supervisor_log_queue: queue.Queue[Json] = queue.Queue(maxsize=5000)
        self._supervisor_log_dropped = 0
        # _supervisor_log_dropped is incremented from per-log-stream
        # reader threads (one per managed-process stdout/stderr) and
        # reset from the main thread's drain_supervisor_logs. CPython's
        # `+=` on an int attribute decomposes into get + add + set,
        # which is not atomic across threads — concurrent bumps lose
        # counts. Guard the increment and the snapshot-and-reset with
        # this lock so the drop count remains accurate under load.
        self._supervisor_log_dropped_lock = threading.Lock()
        self._supervisor_log_threads: dict[
            tuple[str, str, int, str], threading.Thread
        ] = {}
        self._supervisor_pending_blocks: dict[tuple[str, str, int, str], Json] = {}
        self._supervisor_log_dir = str(Path(".state") / self._instance_id / "process-logs")
        self._supervisor_log_max_bytes = 10 * 1024 * 1024
        self._supervisor_log_backups = 3
        self._last_pump_start_mono: float | None = None
        self._last_pump_end_mono: float | None = None
        self._last_pump_duration_s: float | None = None
        self._last_pump_gap_s: float | None = None
        self._last_loop_stall_mono: float | None = None
        self._last_loop_stall_duration_s: float | None = None
        # Set while startup_sequence runs (and for a short grace window after).
        # Process heartbeat staleness is deferred during this window so a
        # slow-importing process (e.g. stream_analysis pulling in scipy) isn't
        # failed before it can emit its first heartbeat. See
        # process_supervision._in_startup_grace.
        self._startup_sequence_active = False
        self._startup_sequence_complete_mono: float | None = None
        # Grace window after startup_sequence completes (distinct from the
        # loop-stall recency window) and an absolute ceiling on how long a
        # never-heartbeating process may be deferred during startup before it is
        # failed anyway (so a dead-at-boot process can't hide for a long startup).
        self._startup_grace_s = 10.0
        self._startup_grace_hard_timeout_s = 30.0
        self._loop_stall_count = 0
        self._manager_loop_stall_warn_s = 1.0
        self._manager_loop_stall_recent_s = 10.0
        self._heartbeat_stale_strikes_to_fail = 2
        self._heartbeat_hard_timeout_multiplier = 3.0
        self._last_orphan_cleanup: Json | None = None
        journal = ManagerJournal.start_or_disabled(
            enabled=bool(command_journal_enabled),
            instance_id=self._instance_id,
            path=command_journal_path,
            queue_max=command_journal_queue_max,
            batch_size=command_journal_batch_size,
            flush_interval_ms=command_journal_flush_interval_ms,
            retention_max_rows=command_journal_retention_max_rows,
            retention_max_age_days=command_journal_retention_max_age_days,
        )
        journal.bind_to_manager(self)

        # Lifecycle executor binds ``_main_thread_id`` and the lifecycle
        # event queue. Bound early so any ``_emit_log`` /
        # ``_publish_manager_event`` call during the rest of __init__
        # (notably ``process_guard.init_failed`` below) takes the
        # main-thread fast path instead of an ``AttributeError``.
        lifecycle = LifecycleExecutor.create()
        lifecycle.bind_to_manager(self)

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
        self._interceptor_route_state = InterceptorRouteState(
            routes=self._command_interceptor_routes,
            next_order=self._command_interceptor_order,
            max_cache=self._command_interceptor_cache_max,
        )

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

        # Per-socket monotonic timestamp of the last "drain cap hit"
        # event we published. Used to rate-limit drain-cap-hit notifications
        # (see _maybe_publish_drain_cap_hit) so a sustained backlog does
        # not flood the manager event bus.
        self._last_drain_cap_event_mono: dict[str, float] = {}

    # -----------------------------
    # Public API
    # -----------------------------

    def add_device(self, spec: DeviceSpec) -> None:
        if spec.device_id in self._devices:
            raise ValueError(f"Duplicate device_id {spec.device_id!r}")
        self._devices[spec.device_id] = DeviceHandle(spec=spec)

    def load_device_spec_from_disk(self, device_id: str) -> DeviceSpec:
        handle = self._devices.get(device_id)
        if handle is None:
            raise KeyError(f"Unknown device_id {device_id!r}")
        config_path = handle.spec.config_path
        if config_path is None:
            raise ValueError(f"Device {device_id!r} has no YAML config path")
        new_spec = device_spec_from_yaml(config_path)
        if new_spec.device_id != device_id:
            raise ValueError(
                f"Reloaded YAML device_id {new_spec.device_id!r} does not match {device_id!r}"
            )
        return new_spec

    def reload_device_spec(self, device_id: str) -> DeviceSpec:
        handle = self._devices.get(device_id)
        new_spec = self.load_device_spec_from_disk(device_id)
        assert handle is not None
        handle.spec = new_spec
        handle.config_published = False
        return new_spec

    # Phase 8.2.16: ``start_driver``, ``restart_driver``,
    # ``_driver_is_started``, ``_driver_is_stopped`` are now provided
    # by ``ProcessSupervisionMixin``. ``stop_driver`` and
    # ``_mark_device_offline`` stay here because they pass
    # ``Liveness.OFFLINE`` (Manager-module enum). Note that
    # ``start_driver`` / ``restart_driver`` shed the wrapper because
    # their bodies don't reference Manager-side enums.

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

    # Phase 8.2.16: ``_ensure_router_handle`` and
    # ``_ensure_router_running`` are now provided by
    # ``ProcessSupervisionMixin``. MRO resolves them.

    # Phase 8.2.9: ``_recent_process_logs``, ``_recent_process_logs_structured``,
    # ``_recent_source_logs_structured``, ``_format_router_startup_failure``,
    # ``_cleanup_orphans_summary``, ``_record_orphan_cleanup``, and
    # ``_maybe_recover_process_start_collision`` are now provided by
    # ``ProcessRecoveryMixin``. MRO resolves them.

    @staticmethod
    def _is_endpoint_collision_process_start_failure(handle: ProcessHandle) -> bool:
        return shared_is_endpoint_collision_process_start_failure(handle)

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
                except Exception as exc:
                    self._publish_manager_event(
                        "manager.log",
                        {
                            "severity": "warning",
                            "message": (
                                f"process hb sub disconnect failed for "
                                f"{hb_endpoint}: {exc!r}"
                            ),
                        },
                    )
                self._process_hb_connected.discard(hb_endpoint)
        if data_endpoint and all(
            str(h.process_data_endpoint or "").strip() != data_endpoint
            for h in self._processes.values()
        ):
            if data_endpoint in self._process_data_connected:
                try:
                    self._process_data_sub.disconnect(data_endpoint)
                except Exception as exc:
                    self._publish_manager_event(
                        "manager.log",
                        {
                            "severity": "warning",
                            "message": (
                                f"process data sub disconnect failed for "
                                f"{data_endpoint}: {exc!r}"
                            ),
                        },
                    )
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

    # Phase 8.2.16: ``_build_driver_cmd`` is now provided by
    # ``ProcessSupervisionMixin``. MRO resolves it.

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

    # Phase 8.2.16: ``_require_process``, ``_resolve_process_heartbeat_endpoint``,
    # ``_resolve_process_data_endpoint``, ``_connect_process_heartbeat``,
    # ``_connect_process_data``, ``_expand_process_argv``,
    # ``_start_process_handle``, ``_stop_process_handle``,
    # ``_maybe_schedule_restart``, ``_try_restart_process``,
    # ``_process_snapshot`` are now provided by
    # ``ProcessSupervisionMixin``. MRO resolves them.

    # Phase 8.2.10: ``_start_child_log_readers`` and
    # ``_queue_supervisor_log`` are now provided by ``ProcessLogsMixin``.
    # MRO resolves them.

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

    def _record_supervisor_tail_log(
        self,
        item: Json,
        *,
        severity: str | None = None,
        raw_stream_tail: bool = False,
    ) -> None:
        if not isinstance(item, dict):
            return
        source_kind = str(item.get("source_kind", "") or "")
        source_id = str(item.get("source_id", "") or "")
        stream = str(item.get("stream", "") or "")
        if raw_stream_tail and stream not in {"stdout", "stderr"}:
            return
        message = str(item.get("message", "") or "")
        if not message:
            return
        handle = self._supervisor_handle_for(source_kind=source_kind, source_id=source_id)
        if handle is None:
            return
        entry = self._supervisor_tail_entry(
            item=item,
            message=message,
            stream=stream,
            severity=severity,
        )
        if not raw_stream_tail:
            handle.supervisor_log_tail.append(entry)
        elif stream == "stdout":
            handle.supervisor_stdout_tail.append(entry)
        else:
            handle.supervisor_stderr_tail.append(entry)

    def _record_supervisor_raw_log(self, item: Json) -> None:
        self._record_supervisor_tail_log(item, raw_stream_tail=True)

    def _record_supervisor_emitted_log(self, item: Json, *, severity: str) -> None:
        self._record_supervisor_tail_log(item, severity=severity)

    # Phase 8.2.10: ``_supervisor_infer_severity``,
    # ``_emit_supervisor_item``, ``_flush_stale_supervisor_blocks``,
    # ``_prune_supervisor_log_threads``, ``_drain_supervisor_logs`` are
    # now provided by ``ProcessLogsMixin``. The three pure parser
    # staticmethods below remain on Manager so existing callers that
    # use ``Manager._supervisor_*`` keep working — they wrap the
    # module-level pure helpers that don't take ``manager``.

    @staticmethod
    def _supervisor_key(item: Json) -> tuple[str, str, int, str]:
        return shared_supervisor_key(item)

    @staticmethod
    def _supervisor_block_start(message: str) -> bool:
        return shared_supervisor_block_start(message)

    @staticmethod
    def _supervisor_block_continuation(message: str) -> bool:
        return shared_supervisor_block_continuation(message)

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
        already_connected = False
        if not self._device_rpc_status_ok(connect_resp):
            error_text = self._device_rpc_error_text(connect_resp)
            error_code = str(connect_resp.get("error_code") or "").strip().lower()
            # In-tree drivers emit error_code="already_connected" (see
            # DeviceRunner._rpc_route_connect_device). Substring branch is a
            # back-compat shim for out-of-tree drivers; remove on next major.
            if error_code == "already_connected" or "already connected" in error_text.lower():
                already_connected = True
            else:
                handle.connect_check_last = {
                    "ok": False,
                    "checked_at": {"t_wall": time.time(), "t_mono": time.monotonic()},
                    "message": f"connect RPC failed: {error_text}",
                }
                return connect_resp

        check = handle.spec.connect_check
        if not check.enabled:
            handle.connect_check_last = None
            if already_connected:
                return {"status": "OK", "result": None, "already_connected": True}
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
        if already_connected:
            return {"status": "OK", "result": None, "already_connected": True}
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
            # A single dead/exited driver must not abort the whole bulk
            # connect (and thus startup_sequence + the manager). The
            # single-device RPC path tolerates connect_device raising
            # (the command dispatcher catches it); this aggregator must
            # too. Record a per-device error result and publish the same
            # event the auto-connect-on-register path emits, then carry on.
            try:
                results[device_id] = self.connect_device(device_id)
            except Exception as exc:
                error_text = str(exc)
                results[device_id] = {
                    "status": "ERROR",
                    "error": error_text,
                    "error_code": "connect_failed",
                }
                self._publish_manager_event(
                    "manager.connect_device_failed",
                    {"device_id": device_id, "error": error_text},
                )
        return results

    # Phase 8.2.11: ``startup_sequence`` is now provided by
    # ``LifecycleMixin``. MRO resolves it. The mixin method has
    # defaults for ``managed_process_running`` and ``driver_state_ok``
    # that resolve to ``ManagedProcessState.RUNNING`` /
    # ``DriverState.OK`` on first use, matching the prior wrapper.

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
            # Drain replies + events produced by lifecycle worker
            # threads since the last tick. Both perform main-thread-only
            # ZMQ sends; the workers themselves never touch sockets.
            self._drain_lifecycle_replies()
            self._drain_lifecycle_events()
            # Check timeouts AFTER draining all SUB sockets. Doing it
            # at the top of the tick (the previous order) means freshly
            # buffered HBs sitting in the SUB queue haven't been
            # ingested yet, so the timeout check would fire against
            # stale `last_hb_recv_mono` even when the process is fine.
            self._check_timeouts()
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

    # Phase 8.2.11: ``_shutdown_cleanup`` is now provided by
    # ``LifecycleMixin``. MRO resolves it.

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

    # Phase 8.2.13: ``_handle_driver_pub`` is now provided by
    # ``DriverPubMixin``. MRO resolves it.

    def _handle_process_pub(self) -> None:
        # Drain all available HBs in one tick so a momentary stall
        # doesn't leave a backlog that drips out at 1-per-tick (which
        # in turn makes the timeout check see "stale" for processes
        # whose HBs are still queued). Cap bounds worst-case tick
        # duration on an avalanche.
        for _ in range(MAX_DRAIN_PER_TICK):
            try:
                topic_b, payload_b = self._process_hb_sub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                return
            topic = self._normalize_topic(topic_b.decode("utf-8", errors="replace"))
            try:
                msg = safe_json_loads(payload_b)
                if not isinstance(msg, dict):
                    self._publish_manager_event(
                        "manager.process.unknown_pub", {"topic": topic}
                    )
                    continue

                if not topic.startswith("process/") or not topic.endswith("/heartbeat"):
                    self._publish_manager_event(
                        "manager.process.unknown_pub", {"topic": topic, "raw": msg}
                    )
                    continue

                self._ingest_process_heartbeat(topic, msg)
            except Exception as e:
                self._publish_manager_event(
                    "manager.process.heartbeat_error",
                    {"topic": topic, "error": str(e)},
                )
        # Loop completed full MAX_DRAIN_PER_TICK iterations without zmq.Again:
        # queue still has data. Surface this (rate-limited) so operators see
        # the backlog instead of silent message lag.
        self._maybe_publish_drain_cap_hit("process_hb", MAX_DRAIN_PER_TICK)

    def _handle_process_data_pub(self) -> None:
        # Drain all available data events per tick. Same rationale as
        # _handle_process_pub.
        for _ in range(MAX_DRAIN_PER_TICK):
            try:
                topic_b, payload_b = self._process_data_sub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                return
            topic = self._normalize_topic(topic_b.decode("utf-8", errors="replace"))
            try:
                msg = safe_json_loads(payload_b)
                if not isinstance(msg, dict):
                    self._publish_manager_event(
                        "manager.process.unknown_pub", {"topic": topic}
                    )
                    continue

                if not topic.startswith("manager."):
                    self._publish_manager_event(
                        "manager.process.unknown_pub", {"topic": topic, "raw": msg}
                    )
                    continue

                if topic == "manager.log":
                    self._emit_log_from_payload(msg, default_topic=topic)
                    continue

                self._publish_manager_event(topic, msg)
            except Exception as e:
                self._publish_manager_event(
                    "manager.process.data_error",
                    {"topic": topic, "error": str(e)},
                )
        # Loop completed full MAX_DRAIN_PER_TICK iterations without zmq.Again:
        # queue still has data. Surface this (rate-limited) so operators see
        # the backlog instead of silent message lag.
        self._maybe_publish_drain_cap_hit("process_data", MAX_DRAIN_PER_TICK)

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

    # Phase 8.2.13: ``_ingest_chunk_ready`` is now provided by
    # ``DriverPubMixin``. MRO resolves it.

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
        # Manager-side timestamp of when we processed this HB; the
        # timeout check uses this (not the sender's t_mono) so a
        # manager-side drain delay doesn't get blamed on the process.
        handle.last_hb_recv_mono = time.monotonic()
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

    # Phase 8.2.15: ``_command_interceptor_routes_snapshot``,
    # ``_publish_interceptor_routes_update``,
    # ``_invalidate_command_interceptor_cache``,
    # ``_drop_command_interceptor_routes``,
    # ``_unregister_command_interceptor_routes`` are now provided by
    # ``RouteHandlersMixin``. ``_match_command_interceptor_route`` and
    # ``_register_command_interceptor_routes`` stay here because the
    # latter binds the Manager-side ``CommandInterceptorRoute`` class.

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

    # Phase 8.2.6: ``_handle_internal_rpc`` is now provided by
    # ``InternalRpcMixin`` via MRO.

    # -----------------------------
    # Lifecycle parallelism
    # -----------------------------
    #
    # Lifecycle ops (device.connect / disconnect / driver.start / stop /
    # restart / recover) used to block the manager's main poll loop
    # because route_device_request runs them synchronously and each does
    # one or two blocking device RPCs. A hypothetical "connect all 20
    # devices" UI button would take ~N × per-device latency.
    #
    # _dispatch_lifecycle_task hands the work off to the executor and
    # returns immediately. The worker thread takes a per-device lock
    # (so same-device ops serialise), runs the existing
    # route_device_request unchanged, then enqueues the reply. The main
    # poll loop drains the reply queue and sends each reply via
    # `_internal_rpc.send_multipart` — keeping all ZMQ socket writes on
    # the main thread.
    #
    # Events emitted from worker threads (via _publish_manager_event)
    # are redirected to `_lifecycle_event_queue` by the off-thread
    # check in manager_pubsub.publish_manager_event; the main loop
    # drains and publishes them.

    def _dispatch_lifecycle_task(
        self, identity: bytes, req: Json, rtype: str, device_id: str
    ) -> None:
        try:
            self._lifecycle_executor.submit(
                self._run_lifecycle, identity, req, rtype, device_id
            )
        except RuntimeError:
            # Executor was shut down (e.g. _shutdown_cleanup ran between
            # the RPC arriving and this dispatch). Send an immediate
            # `shutting_down` error reply so the caller sees a clean
            # failure rather than the manager loop crashing.
            rid = req.get("request_id")
            resp: Json = {
                "ok": False,
                "error": {
                    "code": "manager_shutting_down",
                    "message": (
                        "Lifecycle executor is shut down; manager is "
                        "tearing down."
                    ),
                },
            }
            if rid is not None:
                resp["request_id"] = rid
            try:
                self._internal_rpc.send_multipart([identity, json_dumps(resp)])
            except Exception:
                # Socket itself may already be closed in shutdown; drop.
                pass

    def _run_lifecycle(
        self, identity: bytes, req: Json, rtype: str, device_id: str
    ) -> None:
        lock = self._lifecycle_device_locks.setdefault(device_id, threading.Lock())
        with lock:
            try:
                resp = route_device_request(self, rtype, req)
                if resp is None:
                    resp = {
                        "ok": False,
                        "error": {
                            "code": "unknown_lifecycle_type",
                            "message": f"no handler for {rtype!r}",
                        },
                    }
            except Exception as exc:
                resp = {
                    "ok": False,
                    "error": {
                        "code": "lifecycle_error",
                        "message": str(exc),
                    },
                }
        rid = req.get("request_id")
        if isinstance(resp, dict) and rid is not None and "request_id" not in resp:
            # Mirror DeviceRouter's request_id echo so the pipelined
            # RouterRpcClient can correlate the reply.
            resp = dict(resp)
            resp["request_id"] = rid
        self._lifecycle_reply_queue.put((identity, resp))

    def _drain_lifecycle_replies(self) -> None:
        while True:
            try:
                identity, resp = self._lifecycle_reply_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._internal_rpc.send_multipart([identity, json_dumps(resp)])
            except Exception:
                # Socket may be torn down mid-shutdown; drop the reply.
                pass

    def _drain_lifecycle_events(self) -> None:
        while True:
            try:
                topic, payload = self._lifecycle_event_queue.get_nowait()
            except queue.Empty:
                break
            try:
                # Calls into manager_pubsub.publish_manager_event on the
                # main thread — the off-thread redirect check will see
                # the main thread id and run the full sync publish path
                # (socket send + journal + hooks + log forwarding).
                self._publish_manager_event(topic, payload)
            except Exception as exc:
                # Sibling _drain_lifecycle_replies swallows shutdown
                # races on the RPC socket; mirror that here, but log so
                # a real publish failure (e.g. encoder bug, hook
                # raising) isn't silently lost. The log call itself is
                # wrapped so a logging-stack failure can't recurse
                # through the drain loop and stall the manager.
                try:
                    import logging

                    logging.getLogger(__name__).warning(
                        "lifecycle event publish failed: topic=%s err=%s: %s",
                        topic,
                        type(exc).__name__,
                        exc,
                    )
                except Exception:
                    pass

        # After draining what we can, surface any events that publish
        # workers had to drop because the bounded queue was full. Snapshot
        # + reset under the lock so concurrent worker drops aren't lost.
        # Without this, _lifecycle_event_dropped would silently grow and
        # operators would have no signal that lifecycle events were being
        # lost.
        with self._lifecycle_event_dropped_lock:
            dropped = int(self._lifecycle_event_dropped)
            self._lifecycle_event_dropped = 0
        if dropped > 0:
            try:
                self._emit_log(
                    severity="warning",
                    topic="manager.lifecycle.events_dropped",
                    message=(
                        f"Lifecycle event queue full; dropped {dropped} events "
                        f"(non-audit topics; audit topics block briefly before "
                        f"falling back to the drop counter — see "
                        f"manager_pubsub._AUDIT_TOPICS)"
                    ),
                    source_kind="manager",
                    source_id="manager",
                    stream="event",
                    payload={"dropped": dropped},
                )
            except Exception:
                # Never let observability break the main loop.
                pass

    # Phase 8.2.6: ``_route_internal_request``, ``_ensure_route_registries``
    # are now provided by ``InternalRpcMixin`` via MRO.
    # ``_dispatch_registry_request`` stays here as the pure-helper
    # staticmethod wrapper because ``InternalRpcMixin`` invokes it via
    # ``self._dispatch_registry_request``; turning it into a staticmethod
    # forwarder on Manager keeps the wire shape simple.

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

    # Phase 8.2.7: ``_build_internal_action_registry``,
    # ``_build_internal_type_registry``, ``_build_process_route_registry``,
    # ``_build_manager_route_registry`` are now provided by
    # ``RequestRoutingMixin``. MRO resolves them.

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

    # Phase 8.2.14: ``_route_device_request`` is now provided by
    # ``DeviceRoutingMixin``. MRO resolves it.

    # Phase 8.2.15: ``_publish_process_command_response``,
    # ``_route_process_request``, ``_route_process_list_status``,
    # ``_route_process_get``, ``_route_process_control`` are now
    # provided by ``RouteHandlersMixin``. MRO resolves them.

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

    # Phase 8.2.15: ``_route_process_add``, ``_route_process_remove``,
    # ``_route_process_rpc_advertise`` are now provided by
    # ``RouteHandlersMixin``. MRO resolves them.

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

    # Phase 8.2.15: 12 ``_route_command_interceptor_*`` +
    # ``_route_manager_*`` forwarders are now provided by
    # ``RouteHandlersMixin``. MRO resolves them.

    # Phase 8.2.8: ``_call_device_rpc`` and ``_call_process_rpc`` are
    # now provided by ``RpcCallsMixin``. MRO resolves them.

    # Bounded wait when an external close-caller contends with a
    # worker's in-flight RPC. Picked at ~half the loop-stall warn
    # threshold (_manager_loop_stall_warn_s = 1.0s default) and well
    # under heartbeat_timeout_s = 3.0s so a single contended close
    # can't false-positive a heartbeat timeout. On timeout we leave
    # the socket reference intact — the worker's except branch is
    # responsible for closing it via re-entrant lock acquisition once
    # its call completes.
    _CLOSE_RPC_LOCK_WAIT_S = 0.5

    def _close_device_rpc(self, handle: DeviceHandle) -> None:
        # Take handle.rpc_lock (RLock) so a concurrent call_device_rpc
        # on another thread can't be mid-send/recv when we close the
        # socket out from under it. RLock allows re-entry from the
        # call-path's except branch (same thread, lock already held).
        #
        # Bounded wait: if a worker is holding the lock for an
        # in-flight call (worst case ~rpc_timeout_ms = 1.5s), block
        # main-thread callers for at most _CLOSE_RPC_LOCK_WAIT_S
        # before giving up. On timeout the worker's except branch
        # will close the socket when its call returns (it already
        # calls _close_*_rpc after rpc_fail_count >= 2).
        if not handle.rpc_lock.acquire(timeout=self._CLOSE_RPC_LOCK_WAIT_S):
            return
        try:
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
        finally:
            handle.rpc_lock.release()

    def _close_process_rpc(self, handle: ProcessHandle) -> None:
        # See _close_device_rpc for the locking rationale.
        if not handle.rpc_lock.acquire(timeout=self._CLOSE_RPC_LOCK_WAIT_S):
            return
        try:
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
        finally:
            handle.rpc_lock.release()

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

    # Phase 8.2.16: 9 ``_update_*`` / ``_enforce_*`` / ``_maybe_restart_*``
    # / ``_supervise_*`` forwarders are now provided by
    # ``ProcessSupervisionMixin``. MRO resolves them.

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
            t_mono_recv = self._telemetry_last_recv_mono.get(device_id)
            ts_payload = {"t_wall": ts.t_wall, "t_mono": ts.t_mono}
            if t_mono_recv is not None:
                ts_payload["t_mono_recv"] = t_mono_recv
            snap[name] = {
                "value": sig.value,
                "units": sig.units,
                "quality": sig.quality,
                "quality_source": sig.quality_source,
                "ts": ts_payload,
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
        latest_recv_mono = self._telemetry_last_recv_mono.get(device_id)
        if latest_recv_mono is not None:
            telemetry_age_s = now_mono - latest_recv_mono
        elif latest_ts is not None:
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

    def _process_telemetry_schema_list(self) -> Json:
        """Process-telemetry schema list (parallel to ``_telemetry_schema_list``).

        Entries are kept DISTINCT from device telemetry via
        ``source_kind: "process"`` and a ``process_id`` key. Includes locally
        advertised process schemas plus federation-warmed mirrored processes.
        """
        processes: list[Json] = []
        for process_id in sorted(self._processes.keys()):
            handle = self._processes[process_id]
            schema = handle.telemetry_schema or []
            processes.append(
                {
                    "process_id": process_id,
                    "signals": [str(e.get("name", "")) for e in schema],
                    "dtypes": [str(e.get("dtype", "f8")) for e in schema],
                    "units": [str(e.get("units", "")) for e in schema],
                    "source_kind": "process",
                    "is_remote": False,
                    "owner_peer_id": None,
                    "remote_process_id": None,
                }
            )
        processes.extend(self._federation_hub.process_telemetry_schema_processes())
        processes.sort(key=lambda item: str(item.get("process_id", "")))

        ts = {"t_wall": time.time(), "t_mono": time.monotonic()}
        return {"schema_version": 1, "generated_ts": ts, "processes": processes}

    # -----------------------------
    # Manager -> external PUB
    # -----------------------------
    # ``_publish_manager_event`` is now provided by ``PubSubMixin``
    # The prior one-line forwarder method has
    # been removed; ``self._publish_manager_event(...)`` continues to
    # work via MRO. Tests that imported the module-level
    # ``publish_manager_event`` directly still work via the trampoline
    # kept in ``manager_pubsub``.

    def _maybe_publish_drain_cap_hit(self, socket: str, cap: int) -> None:
        """Publish a `manager.drain_cap_hit` event for `socket`, rate-limited
        to at most once per second per socket name. Called when a SUB-drain
        loop completes its full iteration count without seeing `zmq.Again`,
        meaning unread messages remain queued at the end of the tick.
        """
        now_mono = time.monotonic()
        last = self._last_drain_cap_event_mono.get(socket, 0.0)
        if now_mono - last < 1.0:
            return
        self._last_drain_cap_event_mono[socket] = now_mono
        self._publish_manager_event(
            "manager.drain_cap_hit",
            {
                "socket": socket,
                "cap": cap,
                "ts": {"t_wall": time.time(), "t_mono": now_mono},
            },
        )

    @staticmethod
    def _safe_json(value: Any, *, max_len: int = 4000) -> str:
        try:
            text = json.dumps(value)
        except Exception:
            text = str(value)
        if len(text) > max_len:
            return text[:max_len] + "...(truncated)"
        return text

    # ``_should_journal_command_action`` was a one-line forwarder to
    # ``manager_command_journal.should_journal_command_action`` with
    # no internal callers; deleted in Phase 8.2.2. The module-level
    # function remains the canonical place to ask the question.

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

    # ``_append_command_journal_entry`` & ``_command_journal_status_payload``
    # are now provided by ``CommandJournalMixin`` (Phase 8.2.2). The
    # prior forwarders have been removed; ``self.<method>(...)`` and
    # ``Manager.<method>(mgr, ...)`` still resolve via MRO.

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

    # Phase 8.2.4: log emit/sink/tail methods provided by ``LogsMixin``.
    # The trampolines below are the ones Manager still owns: small
    # pure-helper wrappers used during ``__init__`` (severity / file /
    # min-level / boolish env parsing) plus ``_normalize_id`` which is
    # called from sibling mixins (manager_process_recovery,
    # manager_rpc_calls). ``_severity_rank`` stays here because
    # ``tui_manager`` and ``LogEventsMixin`` both reach for it via
    # Manager.

    @staticmethod
    def _parse_boolish(raw: Any, *, default: bool) -> bool:
        return shared_parse_boolish(raw, default=default)

    def _resolve_manager_log_stderr_enabled(self, raw: Any) -> bool:
        return shared_resolve_manager_log_stderr_enabled(raw)

    def _resolve_manager_log_file_path(self, raw: Any) -> Path | None:
        return shared_resolve_manager_log_file_path(raw)

    def _resolve_manager_log_min_level(self, raw: Any) -> str:
        return shared_resolve_manager_log_min_level(raw)

    @staticmethod
    def _severity_rank(raw: Any) -> int:
        return severity_rank(raw, default="info")

    @staticmethod
    def _normalize_id(raw: Any) -> str | None:
        return shared_normalize_id(raw)

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
                "tail_logs": self._recent_source_logs_structured(
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

    # Phase 8.2.5: ``_effective_metadata_for_device``,
    # ``_runtime_metadata_state``, ``_touch_runtime_metadata_revision``,
    # ``_publish_device_config``, ``_device_config_payload`` are now
    # provided by ``RuntimeMetadataMixin``. MRO resolves them.

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
