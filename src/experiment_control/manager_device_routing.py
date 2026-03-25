from __future__ import annotations

import copy
import time
from typing import Any, Callable

Json = dict[str, Any]


def route_device_request(manager: Any, rtype: Any, req: Json) -> Json | None:
    handler = _DEVICE_ROUTE_HANDLERS.get(str(rtype))
    if handler is None:
        return None
    return handler(manager, req)


def _unknown_device_response(device_id: str) -> Json:
    return {"ok": False, "error": f"Unknown device_id {device_id!r}"}


def _forward_device_request(manager: Any, req: Json) -> Json | None:
    return manager._federation_hub.forward_device_request(req)


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
    handle = manager._devices.get(device_id)
    if handle is None:
        fed_resp = _forward_device_request(manager, req)
        if fed_resp is not None:
            return fed_resp
        return _unknown_device_response(device_id)
    if manager._driver_is_stopped(handle) or handle.rpc_endpoint is None:
        return {"ok": False, "error": "driver not running"}
    cmd = {"device_id": device_id, "action": action, "params": params}
    ok, new_cmd, err = manager._apply_command_interceptors(
        cmd,
        request_id=request_id,
        caller_process_id=caller_process_id,
    )
    if not ok:
        return {"ok": False, "error": err}
    if new_cmd is None:
        return {"ok": False, "error": "command blocked"}
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
        manager._federation_hub.update_capabilities(device_id, capabilities)
    except KeyError:
        return {
            "ok": False,
            "error": {
                "code": "unknown_device",
                "message": f"Unknown mirrored device_id {device_id!r}",
            },
        }
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
    device_id = str(req["device_id"])
    fed_resp = _forward_device_request(manager, req)
    if fed_resp is not None:
        return fed_resp
    handle = manager._devices.get(device_id)
    if handle is None:
        return _unknown_device_response(device_id)
    if manager._driver_is_started(handle):
        return {"ok": False, "error": "driver already started"}
    manager.start_driver(device_id)
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_driver_stop(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    force = bool(req.get("force", False))
    fed_resp = _forward_device_request(manager, req)
    if fed_resp is not None:
        return fed_resp
    handle = manager._devices.get(device_id)
    if handle is None:
        return _unknown_device_response(device_id)
    if manager._driver_is_stopped(handle):
        return {"ok": False, "error": "driver already stopped"}
    manager.stop_driver(device_id, force=force)
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_driver_restart(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    force = bool(req.get("force", False))
    fed_resp = _forward_device_request(manager, req)
    if fed_resp is not None:
        return fed_resp
    manager.restart_driver(device_id, force=force)
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_recover(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    reconnect = bool(req.get("reconnect", True))
    force = bool(req.get("force", False))
    fed_resp = _forward_device_request(manager, req)
    if fed_resp is not None:
        return fed_resp
    manager.recover_device(device_id, reconnect=reconnect, force=force)
    return {"ok": True, "result": {"device_id": device_id}}


def _route_device_connect(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    fed_resp = _forward_device_request(manager, req)
    if fed_resp is not None:
        return fed_resp
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
    error: Json = {
        "code": str(resp.get("error_code") or "device_error"),
        "message": manager._device_rpc_error_text(resp),
    }
    details = resp.get("error_details")
    if isinstance(details, dict):
        error["details"] = details
    return {"ok": False, "error": error, "result": resp}


def _route_device_disconnect(manager: Any, req: Json) -> Json:
    device_id = str(req["device_id"])
    fed_resp = _forward_device_request(manager, req)
    if fed_resp is not None:
        return fed_resp
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
        return (
            None,
            None,
            {
                "ok": False,
                "error": {
                    "code": "invalid_device_id",
                    "message": "device_id is required",
                },
            },
        )
    if manager._federation_hub.is_mirrored_device(device_id):
        return (
            None,
            None,
            {
                "ok": False,
                "error": {
                    "code": "remote_device_unsupported",
                    "message": "runtime metadata overrides only apply to local devices",
                },
            },
        )
    handle = manager._devices.get(device_id)
    if handle is None:
        return None, None, {"ok": False, "error": {"code": "unknown_device"}}
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
            {
                "ok": False,
                "error": {"code": "invalid_params", "message": "params must be a dict"},
            },
        )
    mode = str(normalized.get("mode", "merge")).strip().lower()
    if mode not in {"merge", "replace"}:
        return (
            None,
            None,
            {
                "ok": False,
                "error": {
                    "code": "invalid_params",
                    "message": "mode must be 'merge' or 'replace'",
                },
            },
        )
    has_device = "device_metadata" in normalized
    has_stream = "stream_metadata" in normalized
    if not has_device and not has_stream:
        return (
            None,
            None,
            {
                "ok": False,
                "error": {
                    "code": "invalid_params",
                    "message": "device_metadata and/or stream_metadata required",
                },
            },
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
            {"ok": False, "error": {"code": "invalid_params", "message": str(e)}},
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
        return {
            "ok": False,
            "error": {"code": "invalid_params", "message": "params must be a dict"},
        }
    scope = str(params.get("scope", "all")).strip().lower()
    if scope not in {"all", "device", "stream"}:
        return {
            "ok": False,
            "error": {
                "code": "invalid_params",
                "message": "scope must be 'all', 'device', or 'stream'",
            },
        }

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
