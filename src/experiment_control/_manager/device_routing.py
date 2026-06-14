from __future__ import annotations

import copy
import time
from typing import Any, Callable

from ..utils.responses import RpcResponse

Json = dict[str, Any]


def _rpc_failure(code: str, message: str | None = None) -> Json:
    return RpcResponse.failure(code, message=message).to_dict()


def route_device_request(manager: Any, rtype: Any, req: Json) -> Json | None:
    handler = _DEVICE_ROUTE_HANDLERS.get(str(rtype))
    if handler is None:
        return None
    return handler(manager, req)


def _unknown_device_response(device_id: str) -> Json:
    # Structured envelope (matches sibling routes that use
    # ``_rpc_failure`` for the same condition). Client code can read
    # ``resp["error"]["code"] == "unknown_device"`` without having to
    # handle a string-vs-dict polymorphism for the same failure mode.
    return _rpc_failure("unknown_device", f"Unknown device_id {device_id!r}")


def _forward_device_request(manager: Any, req: Json) -> Json | None:
    return manager._federation_hub.forward_device_request(req)


def _resolve_local_device(manager: Any, req: Json) -> tuple[str | None, Any, Json | None]:
    """Resolve ``req["device_id"]`` to a local handle, or forward to federation.

    Precedence matches the pre-refactor behaviour: the local ``_devices``
    table wins. Only when the device is not registered locally do we ask
    the federation hub to forward the request. This preserves the
    long-standing invariant that a device_id present in the local
    manager is *the* canonical owner; federation mirrors are only
    consulted as a fallback. Changing this ordering would silently
    re-route locally-owned devices through the federation path if a
    duplicate id is ever introduced.

    Returns ``(device_id, handle, None)`` on a local match, or
    ``(None, None, response)`` when federation handled the request or
    the device is unknown.
    """
    device_id = str(req["device_id"])
    handle = manager._devices.get(device_id)
    if handle is not None:
        return device_id, handle, None
    fed_resp = _forward_device_request(manager, req)
    if fed_resp is not None:
        return None, None, fed_resp
    return device_id, None, _unknown_device_response(device_id)


def _route_command(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    action = str(req["action"])
    params = req.get("params", {})
    request_id = req.get("request_id")
    caller_process_id = req.get("caller_process_id")
    source_kind, source_id = manager._normalize_command_source(
        source_kind=req.get("source_kind"),
        source_id=req.get("source_id"),
        caller_process_id=caller_process_id,
    )
    if not isinstance(params, dict):
        raise TypeError("params must be a dict")
    _, handle, error_resp = _resolve_local_device(manager, req)
    if error_resp is not None:
        return error_resp
    if manager._driver_is_stopped(handle) or handle.rpc_endpoint is None:
        return _rpc_failure("driver_not_running", "driver not running")
    cmd = {"device_id": device_id, "action": action, "params": params}
    ok, new_cmd, err = manager._apply_command_interceptors(
        cmd,
        request_id=request_id,
        caller_process_id=caller_process_id,
    )
    if not ok:
        return _rpc_failure(
            "command_interceptor_rejected",
            None if err is None else str(err),
        )
    if new_cmd is None:
        return _rpc_failure("command_blocked", "command blocked")
    return manager._call_device_rpc(
        device_id=str(new_cmd.get("device_id", device_id)),
        action=str(new_cmd.get("action", action)),
        params=new_cmd.get("params", params),
        request_id=request_id,
        caller_process_id=caller_process_id,
        source_kind=source_kind,
        source_id=source_id,
        is_remote_target=False,
    )


def _route_federation_capabilities_update(manager: Any, req: Json) -> Json:
    device_id = str(req.get("device_id", ""))
    capabilities = req.get("capabilities")
    if not device_id:
        return _rpc_failure("invalid_federation_update", "missing device_id")
    if not isinstance(capabilities, dict):
        return _rpc_failure(
            "invalid_federation_update", "capabilities must be a dict"
        )
    try:
        manager._federation_hub.update_capabilities(device_id, capabilities)
    except KeyError:
        return _rpc_failure(
            "unknown_device", f"Unknown mirrored device_id {device_id!r}"
        )
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_get_status(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    if manager._federation_hub.is_mirrored_device(device_id):
        return {
            "ok": True,
            "result": manager._federation_hub.device_status_snapshot(device_id),
        }
    return {"ok": True, "result": manager._device_status_snapshot(device_id)}


def _route_device_list_status(manager: Any, req: Json) -> Json:
    del req
    return {"ok": True, "result": manager._list_devices_status_snapshot()}


def _route_device_driver_start(manager: Any, req: Json) -> Json:
    device_id, handle, error_resp = _resolve_local_device(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    if manager._driver_is_started(handle):
        return _rpc_failure("driver_already_started", "driver already started")
    manager.start_driver(device_id)
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_driver_stop(manager: Any, req: Json) -> Json:
    device_id, handle, error_resp = _resolve_local_device(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    force = bool(req.get("force", False))
    if manager._driver_is_stopped(handle):
        return _rpc_failure("driver_already_stopped", "driver already stopped")
    manager.stop_driver(device_id, force=force)
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_driver_restart(manager: Any, req: Json) -> Json:
    device_id, _, error_resp = _resolve_local_device(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    force = bool(req.get("force", False))
    reload_config = bool(req.get("reload_config", False))
    try:
        manager.restart_driver(device_id, force=force, reload_config=reload_config)
    except Exception as exc:
        return _rpc_failure("driver_restart_failed", str(exc))
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_recover(manager: Any, req: Json) -> Json:
    device_id, _, error_resp = _resolve_local_device(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    reconnect = bool(req.get("reconnect", True))
    force = bool(req.get("force", False))
    manager.recover_device(device_id, reconnect=reconnect, force=force)
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_connect(manager: Any, req: Json) -> Json:
    device_id, _, error_resp = _resolve_local_device(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    resp = manager.connect_device(device_id)
    manager._publish_manager_event(
        "manager.device.connect_sent",
        {
            "device_id": device_id,
            "response": resp,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )
    if manager._device_rpc_status_ok(resp):
        return {"ok": True, "result": resp}
    # Route the ok-but-with-result-payload failure through RpcResponse
    # so the wire envelope is built by the same factory as every other
    # failure in this module. Keeps shape changes (e.g., future
    # trace_id / request_id additions) in a single source of truth.
    details_raw = resp.get("error_details")
    return RpcResponse.failure(
        str(resp.get("error_code") or "device_error"),
        message=manager._device_rpc_error_text(resp),
        details=details_raw if isinstance(details_raw, dict) else None,
        result=resp,
        include_result=True,
    ).to_dict()


def _route_device_disconnect(manager: Any, req: Json) -> Json:
    device_id, _, error_resp = _resolve_local_device(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    resp = manager.disconnect_device(device_id)
    manager._publish_manager_event(
        "manager.device.disconnect_sent",
        {
            "device_id": device_id,
            "response": resp,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )
    return {"ok": True, "result": resp}


def _route_device_config_get(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    fed_cfg = manager._federation_hub.device_config_get(device_id)
    if fed_cfg is not None:
        return {"ok": True, "result": fed_cfg}
    handle = manager._devices.get(device_id)
    if handle is None:
        return _unknown_device_response(device_id)
    return {"ok": True, "result": manager._device_config_payload(handle)}


def _route_device_config_list(manager: Any, req: Json) -> Json:
    del req
    configs = [
        manager._device_config_payload(handle)
        for handle in manager._devices.values()
    ]
    configs.extend(manager._federation_hub.device_config_list())
    configs.sort(key=lambda item: str(item.get("device_id", "")))
    return {"ok": True, "result": configs}


def _resolve_runtime_metadata_target(manager: Any, req: Json) -> tuple[str | None, Any, Json | None]:
    device_id = str(req.get("device_id", "")).strip()
    if not device_id:
        return None, None, _rpc_failure("invalid_device_id", "device_id is required")
    if manager._federation_hub.is_mirrored_device(device_id):
        return (
            None,
            None,
            _rpc_failure(
                "remote_device_unsupported",
                "runtime metadata overrides only apply to local devices",
            ),
        )
    handle = manager._devices.get(device_id)
    if handle is None:
        return None, None, _rpc_failure("unknown_device")
    return device_id, handle, None


def _route_device_metadata_get(manager: Any, req: Json) -> Json:
    device_id, handle, error_resp = _resolve_runtime_metadata_target(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    return {"ok": True, "result": manager._runtime_metadata_state(device_id, handle)}


def _parse_metadata_set_config(params: Any) -> tuple[dict[str, Any] | None, str | None, Json | None]:
    normalized: dict[str, Any]
    if params is None:
        normalized = {}
    elif isinstance(params, dict):
        normalized = params
    else:
        return (
            None,
            None,
            _rpc_failure("invalid_params", "params must be a dict"),
        )
    mode = str(normalized.get("mode", "merge")).strip().lower()
    if mode not in {"merge", "replace"}:
        return (
            None,
            None,
            _rpc_failure("invalid_params", "mode must be 'merge' or 'replace'"),
        )
    has_device = "device_metadata" in normalized
    has_stream = "stream_metadata" in normalized
    if not has_device and not has_stream:
        return (
            None,
            None,
            _rpc_failure(
                "invalid_params", "device_metadata and/or stream_metadata required"
            ),
        )
    return normalized, mode, None


def _parse_metadata_payloads(
    manager: Any,
    *,
    params: dict[str, Any],
) -> tuple[
    dict[str, Any] | None,
    dict[str, dict[str, Any]] | None,
    bool,
    bool,
    Json | None,
]:
    parsed_device: dict[str, Any] | None = None
    parsed_stream: dict[str, dict[str, Any]] | None = None
    clear_device = False
    clear_stream = False
    try:
        if "device_metadata" in params:
            raw_device = params.get("device_metadata")
            if raw_device is None:
                clear_device = True
            else:
                parsed_device = manager._normalize_runtime_metadata_dict(
                    raw_device, label="device_metadata"
                )
        if "stream_metadata" in params:
            raw_stream = params.get("stream_metadata")
            if raw_stream is None:
                clear_stream = True
            else:
                parsed_stream = manager._normalize_runtime_stream_metadata_dict(
                    raw_stream, label="stream_metadata"
                )
    except Exception as e:
        return (
            None,
            None,
            False,
            False,
            _rpc_failure("invalid_params", str(e)),
        )
    return parsed_device, parsed_stream, clear_device, clear_stream, None


def _metadata_override_state(
    manager: Any,
    *,
    device_id: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    cur_device = copy.deepcopy(manager._runtime_device_metadata_overrides.get(device_id, {}))
    cur_stream = copy.deepcopy(manager._runtime_stream_metadata_overrides.get(device_id, {}))
    next_device = copy.deepcopy(cur_device)
    next_stream = copy.deepcopy(cur_stream)
    return cur_device, cur_stream, next_device, next_stream


def _next_metadata_overrides(
    manager: Any,
    *,
    params: dict[str, Any],
    mode: str,
    parsed_device: dict[str, Any] | None,
    parsed_stream: dict[str, dict[str, Any]] | None,
    clear_device: bool,
    clear_stream: bool,
    next_device: dict[str, Any],
    next_stream: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if "device_metadata" in params:
        if clear_device:
            next_device = {}
        elif parsed_device is not None:
            if mode == "replace":
                next_device = dict(parsed_device)
            else:
                next_device.update(parsed_device)

    if "stream_metadata" in params:
        if clear_stream:
            next_stream = {}
        elif parsed_stream is not None:
            if mode == "replace":
                next_stream = dict(parsed_stream)
            else:
                next_stream = manager._merge_stream_metadata_dicts(
                    next_stream, parsed_stream
                )
    return next_device, next_stream


def _apply_metadata_override_changes(
    manager: Any,
    *,
    device_id: str,
    cur_device: dict[str, Any],
    cur_stream: dict[str, dict[str, Any]],
    next_device: dict[str, Any],
    next_stream: dict[str, dict[str, Any]],
) -> bool:
    changed = False
    if next_device != cur_device:
        changed = True
        if next_device:
            manager._runtime_device_metadata_overrides[device_id] = next_device
        else:
            manager._runtime_device_metadata_overrides.pop(device_id, None)
    if next_stream != cur_stream:
        changed = True
        if next_stream:
            manager._runtime_stream_metadata_overrides[device_id] = next_stream
        else:
            manager._runtime_stream_metadata_overrides.pop(device_id, None)
    return changed


def _prepare_metadata_set(
    manager: Any, req: Json
) -> tuple[
    str | None,
    Any,
    dict[str, Any] | None,
    str | None,
    dict[str, Any] | None,
    dict[str, dict[str, Any]] | None,
    bool,
    bool,
    Json | None,
]:
    device_id, handle, error_resp = _resolve_runtime_metadata_target(manager, req)
    if error_resp is not None:
        return None, None, None, None, None, None, False, False, error_resp
    params, mode, error_resp = _parse_metadata_set_config(req.get("params", {}))
    if error_resp is not None:
        return None, None, None, None, None, None, False, False, error_resp
    assert params is not None
    parsed_device, parsed_stream, clear_device, clear_stream, error_resp = (
        _parse_metadata_payloads(manager, params=params)
    )
    if error_resp is not None:
        return None, None, None, None, None, None, False, False, error_resp
    return (
        device_id,
        handle,
        params,
        mode,
        parsed_device,
        parsed_stream,
        clear_device,
        clear_stream,
        None,
    )


def _route_device_metadata_set(manager: Any, req: Json) -> Json:
    (
        device_id,
        handle,
        params,
        mode,
        parsed_device,
        parsed_stream,
        clear_device,
        clear_stream,
        error_resp,
    ) = _prepare_metadata_set(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None
    assert params is not None
    assert mode is not None

    cur_device, cur_stream, next_device, next_stream = _metadata_override_state(
        manager, device_id=device_id
    )

    next_device, next_stream = _next_metadata_overrides(
        manager,
        params=params,
        mode=mode,
        parsed_device=parsed_device,
        parsed_stream=parsed_stream,
        clear_device=clear_device,
        clear_stream=clear_stream,
        next_device=next_device,
        next_stream=next_stream,
    )

    changed = _apply_metadata_override_changes(
        manager,
        device_id=device_id,
        cur_device=cur_device,
        cur_stream=cur_stream,
        next_device=next_device,
        next_stream=next_stream,
    )

    if changed:
        manager._touch_runtime_metadata_revision(device_id)
        manager._publish_device_config(handle)

    result = manager._runtime_metadata_state(device_id, handle)
    result["changed"] = changed
    result["mode"] = mode
    return {"ok": True, "result": result}


def _route_device_metadata_clear(manager: Any, req: Json) -> Json:
    device_id, handle, error_resp = _resolve_runtime_metadata_target(manager, req)
    if error_resp is not None:
        return error_resp
    assert device_id is not None

    params = req.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_failure("invalid_params", "params must be a dict")
    scope = str(params.get("scope", "all")).strip().lower()
    if scope not in {"all", "device", "stream"}:
        return _rpc_failure(
            "invalid_params", "scope must be 'all', 'device', or 'stream'"
        )

    changed = False
    if scope in {"all", "device"} and device_id in manager._runtime_device_metadata_overrides:
        manager._runtime_device_metadata_overrides.pop(device_id, None)
        changed = True
    if scope in {"all", "stream"} and device_id in manager._runtime_stream_metadata_overrides:
        manager._runtime_stream_metadata_overrides.pop(device_id, None)
        changed = True
    if changed:
        manager._touch_runtime_metadata_revision(device_id)
        manager._publish_device_config(handle)

    result = manager._runtime_metadata_state(device_id, handle)
    result["changed"] = changed
    result["scope"] = scope
    return {"ok": True, "result": result}


_DEVICE_ROUTE_HANDLERS: dict[str, Callable[[Any, Json], Json]] = {
    "command": _route_command,
    "federation.capabilities.update": _route_federation_capabilities_update,
    "device.get_status": _route_device_get_status,
    "device.list_status": _route_device_list_status,
    "device.driver.start": _route_device_driver_start,
    "device.driver.stop": _route_device_driver_stop,
    "device.driver.restart": _route_device_driver_restart,
    "device.recover": _route_device_recover,
    "device.connect": _route_device_connect,
    "device.disconnect": _route_device_disconnect,
    "device.config.get": _route_device_config_get,
    "device.config.list": _route_device_config_list,
    "device.metadata.get": _route_device_metadata_get,
    "device.metadata.set": _route_device_metadata_set,
    "device.metadata.clear": _route_device_metadata_clear,
}


class DeviceRoutingMixin:
    """Thin mixin exposing the single public device-routing entry point.

    Phase 8.2.14: ``manager_device_routing.py`` exposes 19+ private
    ``_route_device_*`` handlers and a top-level ``route_device_request``
    dispatcher. ``Manager`` only ever forwarded to ``route_device_request``;
    the per-handler functions are dispatch-internal. Wrapping
    ``route_device_request`` on this mixin lets the Manager forwarder
    method (``_route_device_request``) be deleted and MRO take over.

    The module-level ``route_device_request`` callable is preserved
    because ``InternalRpcMixin`` uses it via ``self._route_device_request``
    -> MRO -> mixin method (no direct module-level call needed). Other
    helpers in this file remain module-level — they form a tight
    dispatch table that doesn't benefit from per-method migration.
    """

    def _route_device_request(self, rtype: Any, req: Json) -> Json | None:
        return route_device_request(self, rtype, req)
