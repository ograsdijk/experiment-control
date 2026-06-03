from __future__ import annotations

import functools
import os
import time
from typing import Any, Callable

import zmq

from .manager_interceptor_routes import (
    chain as interceptor_chain,
)
from .manager_interceptor_routes import (
    drop_process as interceptor_drop_process,
)
from .manager_interceptor_routes import (
    invalidate as interceptor_invalidate,
)
from .manager_interceptor_routes import (
    register as interceptor_register,
)
from .manager_interceptor_routes import (
    snapshot as interceptor_snapshot,
)
from .manager_interceptor_routes import (
    unregister as interceptor_unregister,
)
from .utils.command_interceptors import apply_command_interceptor_chain
from .utils.responses import RpcResponse

Json = dict[str, Any]


def _reply(code: str, **kw: Any) -> Json:
    message = kw.pop("message", None)
    details = kw.pop("details", None)
    resp = RpcResponse.failure(code, message=message, details=details).to_dict()
    if kw:
        error = resp["error"]
        if isinstance(error, dict):
            error.update(kw)
    return resp


def _params_from_req(req: Json) -> Json:
    params = req.get("params", {})
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise TypeError("params must be a dict")
    return params


def _simple_params_call(callable_: Callable[[Json], Any], params: Any) -> Json:
    try:
        result = callable_(_params_from_req({"params": params}))
    except Exception as exc:
        return _reply("invalid_params", message=str(exc))
    return {"ok": True, "result": result}


def _payload_from_req(req: Json) -> tuple[Json | None, Json | None]:
    payload = req.get("payload")
    if not isinstance(payload, dict):
        return None, _reply("invalid_payload")
    return payload, None


def _publish_topic_from_req(manager: Any, req: Json) -> tuple[str | None, Json | None, Json | None]:
    topic = req.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        return None, None, _reply("invalid_topic")
    payload, error_resp = _payload_from_req(req)
    if error_resp is not None:
        return None, None, error_resp
    return manager._normalize_topic(topic), payload, None


# Cap on the user-supplied timeout_s parameter to
# route_manager_cleanup_orphans. The handler runs synchronously inside
# the main RPC handler, blocking the manager loop for the full
# duration; an unbounded timeout would let a misconfigured client
# stall the manager for minutes. 30s is well above the realistic
# cleanup time (typically sub-second).
_CLEANUP_ORPHANS_TIMEOUT_CAP_S = 30.0


def command_interceptor_routes_snapshot(manager: Any) -> list[Json]:
    return interceptor_snapshot(manager)


def publish_interceptor_routes_update(
    manager: Any, *, process_id: str, routes: list[Json], replace: bool
) -> None:
    manager._publish_manager_event(
        "manager.command_interceptor.routes_updated",
        {
            "process_id": process_id,
            "routes": routes,
            "replace": replace,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )


def invalidate_command_interceptor_cache(manager: Any) -> None:
    interceptor_invalidate(manager)


def drop_command_interceptor_routes(manager: Any, process_id: str) -> None:
    changed = interceptor_drop_process(manager, process_id)
    if changed:
        publish_interceptor_routes_update(
            manager,
            process_id=process_id,
            routes=[],
            replace=True,
        )


def register_command_interceptor_routes(
    manager: Any,
    process_id: str,
    routes_raw: Any,
    *,
    replace: bool,
    route_cls: Any,
) -> list[Json]:
    if process_id not in manager._processes:
        raise KeyError(f"Unknown process_id {process_id!r}")
    added = interceptor_register(
        manager,
        process_id=process_id,
        routes_raw=routes_raw,
        replace=replace,
        route_cls=route_cls,
    )

    publish_interceptor_routes_update(
        manager,
        process_id=process_id,
        routes=added,
        replace=replace,
    )
    return added


def unregister_command_interceptor_routes(manager: Any, process_id: str) -> bool:
    # Intentionally idempotent: unknown process_ids return removed=False
    # rather than raising, so callers cleaning up after a process that
    # already exited do not have to special-case the race. See
    # tests/test_manager_interceptor_unregister.py for the contract.
    removed = interceptor_unregister(manager, process_id)
    manager._publish_manager_event(
        "manager.command_interceptor.routes_unregistered",
        {
            "process_id": process_id,
            "removed": removed,
            "routes": manager._command_interceptor_routes_snapshot(),
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )
    return removed


def match_command_interceptor_route(route: Any, device_id: str, action: str) -> bool:
    if route.device_id != "*" and route.device_id != device_id:
        return False
    if route.action != "*" and route.action != action:
        return False
    return True


def command_interceptor_chain(
    manager: Any,
    device_id: str,
    action: str,
    *,
    match_route: Callable[[Any, str, str], bool],
) -> list[Any]:
    return interceptor_chain(
        manager,
        device_id=device_id,
        action=action,
        match_route=match_route,
    )


def apply_command_interceptors(
    manager: Any,
    cmd: Json,
    *,
    request_id: str | None,
    caller_process_id: str | None,
    running_states: set[Any],
) -> tuple[bool, Json | None, Json | None]:
    device_id = str(cmd.get("device_id", ""))
    action = str(cmd.get("action", ""))
    chain = manager._command_interceptor_chain(device_id, action)

    def _is_route_available(process_id: str) -> bool:
        handle = manager._processes.get(process_id)
        if handle is None:
            return False
        if handle.state not in running_states:
            return False
        return handle.rpc_endpoint is not None

    def _call(process_id: str, request: Json) -> tuple[str, Json | None, str | None]:
        try:
            resp = manager._call_process_rpc(
                process_id=process_id,
                request=request,
                timeout_ms=manager._interceptor_rpc_timeout_ms,
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
        publish_event=manager._publish_manager_event,
        distinct_ok_false_message=True,
    )


def publish_process_command_response(
    manager: Any,
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
    manager._publish_process_command_event(
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


def route_process_request(manager: Any, rtype: Any, req: Json) -> Json | None:
    manager._ensure_route_registries()
    return manager._dispatch_registry_request(
        manager._process_route_registry,
        route_key=rtype,
        req=req,
    )


def route_process_list_status(manager: Any, req: Json) -> Json:
    del req
    return {"ok": True, "result": manager.list_processes()}


def route_process_get(manager: Any, req: Json) -> Json:
    process_id = str(req["process_id"])
    return {"ok": True, "result": manager.get_process(process_id)}


def route_process_control(
    manager: Any,
    req: Json,
    *,
    action: str,
    runner: Callable[[str], None],
) -> Json:
    process_id = str(req["process_id"])
    request_id = req.get("request_id")
    caller_process_id = req.get("caller_process_id")
    source_kind, source_id = manager._normalize_command_source(
        source_kind=req.get("source_kind"),
        source_id=req.get("source_id"),
        caller_process_id=caller_process_id,
    )
    runner(process_id)
    resp = {"ok": True, "result": {"process_id": process_id}}
    return manager._publish_process_command_response(
        process_id=process_id,
        action=action,
        params={"process_id": process_id},
        response=resp,
        request_id=request_id,
        caller_process_id=caller_process_id,
        source_kind=source_kind,
        source_id=source_id,
    )


def route_process_add(manager: Any, req: Json) -> Json:
    spec_raw = req.get("spec")
    if not isinstance(spec_raw, dict):
        raise TypeError("spec must be a dict")
    spec = manager._parse_process_spec(spec_raw)
    manager.add_process(spec)
    return {"ok": True, "result": {"process_id": spec.process_id}}


def route_process_remove(manager: Any, req: Json) -> Json:
    process_id = str(req["process_id"])
    manager.remove_process(process_id)
    return {"ok": True, "result": {"process_id": process_id}}


def route_process_rpc_advertise(manager: Any, req: Json) -> Json:
    process_id = str(req.get("process_id", ""))
    rpc_endpoint = str(req.get("rpc_endpoint", ""))
    if not process_id or not rpc_endpoint:
        return _reply("invalid_advertise", message="missing fields")
    handle = manager._processes.get(process_id)
    if handle is None:
        return _reply("unknown_process")
    if handle.rpc_endpoint != rpc_endpoint:
        manager._close_process_rpc(handle)
    handle.rpc_endpoint = rpc_endpoint
    manager._publish_manager_event(
        "manager.process.rpc_update",
        {
            "process_id": process_id,
            "rpc_endpoint": rpc_endpoint,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )
    return {"ok": True, "result": {"process_id": process_id}}


def route_process_rpc(
    manager: Any,
    req: Json,
    *,
    running_states: set[Any],
    starting_state: Any,
) -> Json:
    process_id = str(req.get("process_id", ""))
    request = req.get("request")
    request_id = req.get("request_id")
    caller_process_id = req.get("caller_process_id")
    source_kind, source_id = manager._normalize_command_source(
        source_kind=req.get("source_kind"),
        source_id=req.get("source_id"),
        caller_process_id=caller_process_id,
    )
    process_action = "manager.processes.rpc"
    process_params: Json = {}
    if isinstance(request, dict):
        process_action = str(
            request.get("type", "manager.processes.rpc") or "manager.processes.rpc"
        )
        raw_params = request.get("params", {})
        if isinstance(raw_params, dict):
            process_params = raw_params
    _publish = functools.partial(
        manager._publish_process_command_response,
        action=process_action,
        params=process_params,
        request_id=request_id,
        caller_process_id=caller_process_id,
        source_kind=source_kind,
        source_id=source_id,
    )
    publish_process_id = process_id or "unknown"
    if not process_id or not isinstance(request, dict):
        resp = _reply("invalid_process_rpc", message="bad request")
    else:
        handle = manager._processes.get(process_id)
        if handle is None:
            resp = _reply("unknown_process")
        elif handle.state not in running_states:
            resp = _reply("process_not_running")
        elif handle.rpc_endpoint is None:
            if process_action == "process.capabilities" and handle.state == starting_state:
                resp = _reply(
                    "process_starting",
                    message="process is starting; RPC endpoint not advertised yet",
                    retry_after_ms=500,
                )
            else:
                resp = _reply("process_rpc_not_ready")
        else:
            try:
                resp = manager._call_process_rpc(
                    process_id=process_id,
                    request=request,
                )
            except Exception as exc:
                resp = _reply("process_rpc_failed", message=str(exc))
    return _publish(process_id=publish_process_id, response=resp)


def route_command_interceptor_register(manager: Any, req: Json) -> Json:
    process_id = str(req.get("process_id", ""))
    routes_raw = req.get("routes", [])
    replace = bool(req.get("replace", False))
    if not process_id:
        return _reply("invalid_register", message="missing process_id")
    try:
        routes = manager._register_command_interceptor_routes(
            process_id,
            routes_raw,
            replace=replace,
        )
    except Exception as exc:
        return _reply("register_failed", message=str(exc))
    return {"ok": True, "result": {"routes": routes}}


def route_command_interceptor_unregister(manager: Any, req: Json) -> Json:
    process_id = str(req.get("process_id", "")).strip()
    if not process_id:
        return _reply("invalid_unregister", message="missing process_id")
    try:
        removed = manager._unregister_command_interceptor_routes(process_id)
    except Exception as exc:
        return _reply("unregister_failed", message=str(exc))
    return {"ok": True, "result": {"process_id": process_id, "removed": removed}}


def route_command_interceptor_list(manager: Any, req: Json) -> Json:
    del req
    return {"ok": True, "result": {"routes": manager._command_interceptor_routes_snapshot()}}


def route_manager_request(manager: Any, rtype: Any, req: Json) -> Json | None:
    manager._ensure_route_registries()
    return manager._dispatch_registry_request(
        manager._manager_route_registry,
        route_key=rtype,
        req=req,
    )


def route_manager_shutdown(manager: Any, req: Json) -> Json:
    del req
    manager.shutdown()
    return {"ok": True, "result": {"status": "shutting_down"}}


def _int_or_zero(value: Any) -> int:
    # Defensive coercion for late-bound counter fields that test stubs
    # may omit. Maps ``None`` / ``0`` / non-integer junk to 0 so a
    # routine identity RPC can never crash with ``TypeError`` /
    # ``ValueError`` mid-response.
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _identity_default(manager: Any, attr: str, default: Any) -> Any:
    # Production ``Manager`` initialises every attribute read by
    # ``route_manager_identity`` in ``__init__``. ``getattr`` with a
    # default keeps tests that build a bare ``object.__new__(Manager)``
    # and patch ``manager_module.read_instance_lock_status`` working —
    # those tests rely on the module-level lookup as the fallback when
    # the instance attr was never assigned.
    return getattr(manager, attr, default)


def route_manager_identity(manager: Any, req: Json) -> Json:
    del req
    # Late module import so the ``_read_instance_lock_status`` /
    # ``_derive_lock_effective_status`` / ``_lock_effective_status_help``
    # fallbacks honor monkeypatches applied at the module level after
    # this module was imported. See ``_identity_default`` docstring.
    from . import manager as manager_module

    manager_pid = int(os.getpid())
    read_instance_lock_status = _identity_default(
        manager,
        "_read_instance_lock_status",
        manager_module.read_instance_lock_status,
    )
    derive_lock_effective_status = _identity_default(
        manager,
        "_derive_lock_effective_status",
        manager_module.derive_lock_effective_status,
    )
    lock_effective_status_help = _identity_default(
        manager,
        "_lock_effective_status_help",
        manager_module.lock_effective_status_help,
    )
    lock_status = read_instance_lock_status(manager._instance_id)
    lock_effective_status = derive_lock_effective_status(
        lock_status=lock_status,
        manager_pid=manager_pid,
        manager_reachable=True,
        reported_effective_status=None,
    )
    process_guard = _identity_default(manager, "_process_guard", None)
    process_guard_init_error = _identity_default(
        manager, "_process_guard_init_error", None
    )
    if process_guard_init_error is None and process_guard is not None:
        process_guard_init_error = process_guard.init_error
    return {
        "ok": True,
        "result": {
            "version": 1,
            "instance_id": manager._instance_id,
            "manager_pid": manager_pid,
            "started_ts": {
                "t_wall": float(manager._started_t_wall),
                "t_mono": float(manager._started_t_mono),
            },
            "lock_status": lock_status,
            "lock_effective_status": lock_effective_status,
            "lock_effective_help": lock_effective_status_help(lock_effective_status),
            "last_orphan_cleanup": manager._last_orphan_cleanup,
            "process_guard": {
                "enabled": bool(process_guard.available) if process_guard is not None else False,
                "init_error": process_guard_init_error,
                "attach_failures": _int_or_zero(
                    _identity_default(manager, "_process_guard_attach_failures", 0)
                ),
                "last_attach_error": _identity_default(
                    manager, "_process_guard_last_error", None
                ),
            },
            "cache_bounds": {
                "telemetry_max_devices": _int_or_zero(
                    _identity_default(manager, "_telemetry_cache_max_devices", 4096)
                ),
                "telemetry_max_signals_per_device": _int_or_zero(
                    _identity_default(
                        manager, "_telemetry_cache_max_signals_per_device", 4096
                    )
                ),
                "chunk_max_devices": _int_or_zero(
                    _identity_default(manager, "_chunk_cache_max_devices", 4096)
                ),
                "chunk_max_streams_per_device": _int_or_zero(
                    _identity_default(
                        manager, "_chunk_cache_max_streams_per_device", 2048
                    )
                ),
            },
            "manager_loop": {
                "last_pump_start_mono": _identity_default(
                    manager, "_last_pump_start_mono", None
                ),
                "last_pump_end_mono": _identity_default(
                    manager, "_last_pump_end_mono", None
                ),
                "last_pump_duration_s": _identity_default(
                    manager, "_last_pump_duration_s", None
                ),
                "last_pump_gap_s": _identity_default(manager, "_last_pump_gap_s", None),
                "last_loop_stall_mono": _identity_default(
                    manager, "_last_loop_stall_mono", None
                ),
                "last_loop_stall_duration_s": _identity_default(
                    manager, "_last_loop_stall_duration_s", None
                ),
                "loop_stall_count": _int_or_zero(
                    _identity_default(manager, "_loop_stall_count", 0)
                ),
            },
            "cache_stats": {
                "telemetry_devices": int(
                    len(_identity_default(manager, "_telemetry_latest", {}))
                ),
                "chunk_devices": int(
                    len(_identity_default(manager, "_latest_chunk_desc", {}))
                ),
                "telemetry_evicted_devices": _int_or_zero(
                    _identity_default(manager, "_telemetry_cache_evicted_devices", 0)
                ),
                "telemetry_evicted_signals": _int_or_zero(
                    _identity_default(manager, "_telemetry_cache_evicted_signals", 0)
                ),
                "chunk_evicted_devices": _int_or_zero(
                    _identity_default(manager, "_chunk_cache_evicted_devices", 0)
                ),
                "chunk_evicted_streams": _int_or_zero(
                    _identity_default(manager, "_chunk_cache_evicted_streams", 0)
                ),
            },
        },
    }


def route_manager_cleanup_orphans(manager: Any, req: Json) -> Json:
    params = req.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _reply("invalid_params", message="params must be a dict")
    try:
        dry_run = bool(params.get("dry_run", False))
        stale_only = bool(params.get("stale_only", True))
        timeout_s_requested = float(params.get("timeout_s", 2.0))
        if timeout_s_requested <= 0:
            raise ValueError("timeout_s must be > 0")
        # Cap operator-supplied timeout so a misconfigured client can't
        # block the manager loop for minutes. The cleanup-orphans path
        # walks `psutil.process_iter` synchronously inside the main
        # RPC handler; a 30s ceiling is well above the realistic
        # cleanup time (typically <1s for a handful of orphan PIDs)
        # while bounding worst-case manager unresponsiveness.
        if timeout_s_requested > _CLEANUP_ORPHANS_TIMEOUT_CAP_S:
            timeout_s = _CLEANUP_ORPHANS_TIMEOUT_CAP_S
        else:
            timeout_s = timeout_s_requested
    except Exception as exc:
        return _reply("invalid_params", message=str(exc))
    result = manager._cleanup_orphans_summary(
        dry_run=dry_run,
        stale_only=stale_only,
        timeout_s=timeout_s,
    )
    # Echo the effective timeout (and the requested one when clamped) so
    # a caller asking for 60s can tell their budget was reduced. Without
    # this, a partial-scan-due-to-clamping result is indistinguishable
    # from a clean "no orphans found" outcome.
    if isinstance(result, dict):
        result.setdefault("timeout_s_effective", float(timeout_s))
        if timeout_s_requested != timeout_s:
            result.setdefault("timeout_s_requested", float(timeout_s_requested))
    manager._record_orphan_cleanup(source="rpc", summary=result)
    manager._publish_manager_event(
        "manager.orphan_cleanup",
        {
            "result": result,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )
    return {"ok": True, "result": result}


def route_manager_log_publish(manager: Any, req: Json) -> Json:
    payload, error_resp = _payload_from_req(req)
    if error_resp is not None:
        return error_resp
    assert payload is not None
    entry = manager._emit_log_from_payload(
        payload,
        default_topic="manager.logs.publish",
    )
    return {"ok": True, "result": {"status": "published", "entry": entry}}


def route_manager_log_tail(manager: Any, req: Json) -> Json:
    return _simple_params_call(manager._log_tail, req.get("params", {}))


def route_manager_command_journal_status(manager: Any, req: Json) -> Json:
    del req
    return {"ok": True, "result": manager._command_journal_status_payload()}


def route_manager_command_journal_tail(manager: Any, req: Json) -> Json:
    journal = manager._command_journal
    if journal is None:
        return _reply("journal_disabled", message="command journal is disabled")
    return _simple_params_call(journal.tail, req.get("params", {}))


def route_manager_event_publish(manager: Any, req: Json) -> Json:
    normalized_topic, payload, error_resp = _publish_topic_from_req(manager, req)
    if error_resp is not None:
        return error_resp
    assert normalized_topic is not None
    assert payload is not None
    if normalized_topic == "manager.log":
        manager._emit_log_from_payload(payload, default_topic=normalized_topic)
    else:
        manager._publish_manager_event(normalized_topic, payload)
    return {"ok": True, "result": {"status": "published"}}


class RouteHandlersMixin:
    """Thin mixin wrapping module-level route handlers + interceptor helpers.

    Phase 8.2.15: ``manager_route_handlers.py`` is a 700-line dispatch
    module with 30+ ``manager``-taking functions tightly coupled by
    shared internal helpers (``_reply``, ``_params_from_req``,
    ``_simple_params_call``, etc.). The full mechanical migration
    would force ~700 LOC of churn for ~80 LOC of Manager-side
    forwarder removal.

    Instead the mixin wraps every public function Manager called as
    a forwarder. Each wrapper is one line. Manager's forwarder
    methods can be deleted; MRO does the dispatch. The module-level
    callables stay — they're still imported by ``device_router.py``
    and ``manager.py`` for non-method-bound code paths
    (``_dispatch_lifecycle_task``, etc.).

    Pre-bound enum sets (``running_states`` / ``starting_state`` for
    ``route_process_rpc`` / ``apply_command_interceptors``) live on
    ``Manager`` itself because they reference Manager-module-level
    ``ManagedProcessState`` imports.
    """

    # -- process routes -------------------------------------------------

    def _route_process_request(self, rtype: Any, req: Json) -> Json | None:
        return route_process_request(self, rtype, req)

    def _route_process_list_status(self, req: Json) -> Json:
        return route_process_list_status(self, req)

    def _route_process_get(self, req: Json) -> Json:
        return route_process_get(self, req)

    def _route_process_control(
        self,
        req: Json,
        *,
        action: str,
        runner: Any,  # Callable[[str], None]
    ) -> Json:
        return route_process_control(self, req, action=action, runner=runner)

    def _route_process_add(self, req: Json) -> Json:
        return route_process_add(self, req)

    def _route_process_remove(self, req: Json) -> Json:
        return route_process_remove(self, req)

    def _route_process_rpc_advertise(self, req: Json) -> Json:
        return route_process_rpc_advertise(self, req)

    # -- interceptor route handlers (request-layer wrappers) -----------

    def _route_command_interceptor_register(self, req: Json) -> Json:
        return route_command_interceptor_register(self, req)

    def _route_command_interceptor_unregister(self, req: Json) -> Json:
        return route_command_interceptor_unregister(self, req)

    def _route_command_interceptor_list(self, req: Json) -> Json:
        return route_command_interceptor_list(self, req)

    # -- interceptor state helpers (call-site wrappers) ----------------

    def _command_interceptor_routes_snapshot(self) -> list[Json]:
        return command_interceptor_routes_snapshot(self)

    def _publish_interceptor_routes_update(
        self, *, process_id: str, routes: list[Json], replace: bool
    ) -> None:
        publish_interceptor_routes_update(
            self, process_id=process_id, routes=routes, replace=replace
        )

    def _invalidate_command_interceptor_cache(self) -> None:
        invalidate_command_interceptor_cache(self)

    def _drop_command_interceptor_routes(self, process_id: str) -> None:
        drop_command_interceptor_routes(self, process_id)

    def _unregister_command_interceptor_routes(self, process_id: str) -> bool:
        return unregister_command_interceptor_routes(self, process_id)

    # -- manager routes -------------------------------------------------

    def _route_manager_request(self, rtype: Any, req: Json) -> Json | None:
        return route_manager_request(self, rtype, req)

    def _route_manager_shutdown(self, req: Json) -> Json:
        return route_manager_shutdown(self, req)

    def _route_manager_identity(self, req: Json) -> Json:
        return route_manager_identity(self, req)

    def _route_manager_cleanup_orphans(self, req: Json) -> Json:
        return route_manager_cleanup_orphans(self, req)

    def _route_manager_log_publish(self, req: Json) -> Json:
        return route_manager_log_publish(self, req)

    def _route_manager_log_tail(self, req: Json) -> Json:
        return route_manager_log_tail(self, req)

    def _route_manager_command_journal_status(self, req: Json) -> Json:
        return route_manager_command_journal_status(self, req)

    def _route_manager_command_journal_tail(self, req: Json) -> Json:
        return route_manager_command_journal_tail(self, req)

    def _route_manager_event_publish(self, req: Json) -> Json:
        return route_manager_event_publish(self, req)

    # -- response publish ----------------------------------------------

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
        return publish_process_command_response(
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
