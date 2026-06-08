"""Single source of truth for cross-mixin method signatures.

The Phase 8 mixin migration (REFACTOR_PLAN §8) splits ``Manager`` into
~16 mixins, each living in its own ``manager_*.py`` file. Mixin
methods routinely call methods provided by *sibling* mixins, but mypy
checks each mixin module in isolation — without help, ``self._foo()``
where ``_foo`` lives on a sibling reads as an undeclared attribute.

The naive fix is to redeclare each sibling method on each consumer
mixin as ``_foo: Callable[..., ...]``. That creates a quadratic drift
problem: every sibling-method signature change has to be mirrored at
every consumer site, with no runtime enforcement (class-level
annotations are inert) and no mypy enforcement on the composed
``Manager`` class (where MRO supplies the real method).

Instead, every cross-mixin method signature lives **once** on
:class:`ManagerProtocol` below. Each mixin method body that calls a
sibling method types ``self`` (informally via a TYPE_CHECKING guard
or directly) as ``ManagerProtocol`` so mypy resolves the call against
the canonical signature. When a sibling signature changes, it changes
**here**, and mypy errors fire at every divergent call site.

Owned **state** (zmq sockets, locks, ints, queues) stays on each
mixin as local class-level annotations — those have concrete types
declared on ``Manager`` and don't drift across modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    Json = dict[str, Any]


class ManagerProtocol(Protocol):
    """Cross-mixin method contracts.

    Add a method here when one mixin needs to call a method on a sibling
    mixin (or on ``Manager`` itself). Do NOT add owned-state attributes
    (zmq sockets, locks, counters) — those stay on the mixin that needs
    them, declared as class-level annotations.

    Methods are listed in alphabetical order to keep the diff stable
    as new mixins migrate.
    """

    # --- CommandJournalMixin -----------------------------------------
    def _append_command_journal_entry(self, payload: "Json") -> None: ...

    # --- Manager (not yet migrated; used by CommandJournalMixin / LogsMixin) ----
    def _safe_json(self, value: "Any", *, max_len: int = 4000) -> str: ...

    def _normalize_topic(self, topic: str) -> str: ...

    # --- PubSubMixin --------------------------------------------------
    def _publish_manager_event(self, topic: str, payload: "Json") -> None: ...

    # --- Manager (registry builders + sub-route dispatchers) ---------
    # Used by InternalRpcMixin. Will eventually move onto
    # RequestRoutingMixin (8.2.7).
    def _build_internal_action_registry(self) -> "Any": ...

    def _build_internal_type_registry(self) -> "Any": ...

    def _build_process_route_registry(self) -> "Any": ...

    def _build_manager_route_registry(self) -> "Any": ...

    def _dispatch_registry_request(
        self, registry: "Any", *, route_key: "Any", req: "Json"
    ) -> "Json | None": ...

    def _route_device_request(self, rtype: "Any", req: "Json") -> "Json | None": ...

    def _route_process_request(self, rtype: "Any", req: "Json") -> "Json | None": ...

    def _route_manager_request(self, rtype: "Any", req: "Json") -> "Json | None": ...

    def _dispatch_lifecycle_task(
        self, identity: bytes, req: "Json", rtype: "Any", device_id: str
    ) -> None: ...

    # --- Manager (route handlers used by RequestRoutingMixin) --------
    # All ``_route_*`` handlers share the ``(req: Json) -> Json``
    # contract that ``RpcDispatchRegistry`` expects. Listed
    # alphabetically. Will migrate onto ``RouteHandlersMixin`` /
    # ``DeviceRoutingMixin`` etc. in later phases.
    def _route_action_telemetry_schema_list(self, req: "Json") -> "Json": ...

    def _route_command_interceptor_list(self, req: "Json") -> "Json": ...

    def _route_command_interceptor_register(self, req: "Json") -> "Json": ...

    def _route_command_interceptor_unregister(self, req: "Json") -> "Json": ...

    def _route_manager_cleanup_orphans(self, req: "Json") -> "Json": ...

    def _route_manager_command_journal_status(self, req: "Json") -> "Json": ...

    def _route_manager_command_journal_tail(self, req: "Json") -> "Json": ...

    def _route_manager_event_publish(self, req: "Json") -> "Json": ...

    def _route_manager_identity(self, req: "Json") -> "Json": ...

    def _route_manager_log_publish(self, req: "Json") -> "Json": ...

    def _route_manager_log_tail(self, req: "Json") -> "Json": ...

    def _route_manager_shutdown(self, req: "Json") -> "Json": ...

    def _route_process_add(self, req: "Json") -> "Json": ...

    def _route_process_get(self, req: "Json") -> "Json": ...

    def _route_process_list_status(self, req: "Json") -> "Json": ...

    def _route_process_remove(self, req: "Json") -> "Json": ...

    def _route_process_restart(self, req: "Json") -> "Json": ...

    def _route_process_rpc(self, req: "Json") -> "Json": ...

    def _route_process_rpc_advertise(self, req: "Json") -> "Json": ...

    def _route_process_start(self, req: "Json") -> "Json": ...

    def _route_process_stop(self, req: "Json") -> "Json": ...

    def _route_type_get_telemetry(self, req: "Json") -> "Json": ...

    def _route_type_list_devices(self, req: "Json") -> "Json": ...

    def _route_type_telemetry_snapshot(self, req: "Json") -> "Json": ...

    # --- Manager / DriverPubMixin / ProcessSupervisionMixin ----------
    # Used by RpcCallsMixin._pump_manager_subscriptions and the
    # device/process RPC close paths. Will eventually move onto the
    # appropriate mixin (some are already on InternalRpcMixin's
    # neighbours, but they're declared on the Protocol now to keep
    # the cross-mixin signature contract in one place).
    def _handle_driver_pub(self) -> None: ...

    def _handle_process_pub(self) -> None: ...

    def _handle_process_data_pub(self) -> None: ...

    def _close_device_rpc(self, handle: "Any") -> None: ...

    def _close_process_rpc(self, handle: "Any") -> None: ...

    def _normalize_command_source(
        self,
        *,
        source_kind: "Any",
        source_id: "Any",
        caller_process_id: "Any",
    ) -> tuple[str, str | None]: ...

    def _normalize_id(self, raw: "Any") -> str | None: ...

    # --- Manager methods used by ProcessRecoveryMixin ----------------
    # ``_normalize_log_severity`` + ``_start_process_handle`` will
    # eventually move onto sibling mixins (LogsMixin / ProcessSupervisionMixin).
    def _normalize_log_severity(self, raw: "Any") -> str: ...

    def _start_process_handle(
        self, handle: "Any", *, reset_collision_retry: bool = True
    ) -> None: ...

    # --- Manager methods used by ProcessLogsMixin --------------------
    def _supervisor_handle_for(
        self, *, source_kind: str, source_id: str
    ) -> "Any": ...

    def _record_supervisor_raw_log(self, item: "Json") -> None: ...

    def _record_supervisor_emitted_log(
        self, item: "Json", *, severity: str
    ) -> None: ...

    # --- Manager methods used by LifecycleMixin ----------------------
    def _ensure_router_running(self, *, timeout_s: float, poll_ms: int) -> None: ...

    def _pump_once(self, poll_ms: int = 50) -> None: ...

    def _drain_lifecycle_replies(self) -> None: ...

    def _drain_lifecycle_events(self) -> None: ...

    def _stop_process_handle(self, handle: "Any") -> None: ...

    # ---- ProcessLogsMixin supervisor-log surface ---------------------
    # ``_drain_supervisor_logs`` was the only one of these originally
    # declared on the Protocol (because LifecycleMixin._shutdown_cleanup
    # calls ``self._drain_supervisor_logs``). The remaining four are
    # declared here so the drift test catches signature changes for
    # the module-level trampolines (``supervisor_log_path``,
    # ``append_supervisor_jsonl``, ``append_supervisor_marker``,
    # ``queue_supervisor_log``) that re-state the same shape for
    # ``tests.test_process_diagnostics`` / ``tests.test_group_f_hardening``.

    def _supervisor_log_path(
        self,
        *,
        source_kind: str,
        source_id: str,
        pid: int,
        stream: str,
    ) -> "Path": ...

    def _append_supervisor_jsonl(self, item: "Json") -> None: ...

    def _append_supervisor_marker(
        self,
        *,
        log_path: "Path",
        source_kind: str,
        source_id: str,
        stream: str,
        pid: int,
        event: str,
        device_id: str | None,
        process_id: str | None,
        message: str | None = None,
    ) -> None: ...

    def _queue_supervisor_log(self, item: "Json") -> None: ...

    def _drain_supervisor_logs(self, *, max_items: int = 250) -> None: ...

    def _flush_stale_supervisor_blocks(
        self, *, max_age_s: float = 0.25, force: bool = False
    ) -> None: ...

    def start_all_processes(self) -> None: ...

    def start_all_drivers(self) -> None: ...

    # ---- public driver lifecycle (split between Manager + mixin) ----
    # ``start_driver`` / ``restart_driver`` live on ``ProcessSupervisionMixin``
    # because their bodies don't reference Manager-module enums.
    # ``stop_driver`` stays on ``Manager`` itself because it must pass
    # ``Liveness.OFFLINE`` (Manager-module enum the mixin can't reach
    # without a circular import). All three signatures are declared on
    # the Protocol so the drift test guards against future asymmetry —
    # e.g. someone adding ``force`` to ``start_driver`` but forgetting
    # the sibling on ``stop_driver``.
    def start_driver(self, device_id: str) -> None: ...

    def restart_driver(self, device_id: str, *, force: bool = False) -> None: ...

    def stop_driver(self, device_id: str, *, force: bool = False) -> None: ...

    def connect_all_devices(self) -> "dict[str, Json]": ...

    # --- LogEventsMixin ----------------------------------------------
    def _maybe_emit_manager_log_sink(self, topic: str, payload: "Json") -> None: ...

    def _maybe_publish_log_event(self, topic: str, payload: "Json") -> None: ...

    # --- Manager (not yet migrated) ----------------------------------
    # Called by LogEventsMixin._maybe_emit_manager_log_sink. Will
    # eventually move onto LogsMixin in 8.2.4.
    def _manager_log_sink_event(
        self, topic: str, payload: "Json"
    ) -> tuple[str, str, str, str | None, str]: ...

    def _manager_log_sink_is_duplicate(self, fingerprint: str) -> bool: ...

    def _close_manager_log_sink_file(self) -> None: ...

    def _severity_rank(self, raw: "Any") -> int: ...

    # Called by LogEventsMixin._maybe_publish_log_event. Will move onto
    # LogsMixin in 8.2.4. All args are keyword-only on the real impl.
    def _emit_log(
        self,
        *,
        severity: "Any",
        topic: "Any",
        message: "Any",
        source_kind: "Any" = "manager",
        source_id: "Any" = None,
        device_id: "Any" = None,
        process_id: "Any" = None,
        stream: "Any" = "event",
        payload: "Json | None" = None,
        payload_json: "Any" = None,
        ts: "Any" = None,
    ) -> "Json": ...
