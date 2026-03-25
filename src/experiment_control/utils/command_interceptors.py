from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

Json = dict[str, Any]
CallResult = tuple[str, Json | None, str | None]


def build_interceptor_error(
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


def _route_process_id(route: Any) -> str:
    if isinstance(route, dict):
        return str(route.get("process_id") or "").strip()
    return str(getattr(route, "process_id", "") or "").strip()


def _emit_interceptor_error(
    *,
    publish_event: Callable[[str, Json], None],
    err: Json,
    command: Json,
) -> None:
    publish_event("manager.command_interceptor.error", {"error": err, "command": command})


def _validate_route_availability(
    *,
    process_id: str,
    is_route_available: Callable[[str], bool],
    device_id: str,
    action: str,
) -> Json | None:
    if not process_id:
        return build_interceptor_error(
            code="INTERCEPTOR_BAD_ROUTE",
            message=f"Interceptor route missing process_id for {device_id}.{action}",
            process_id=process_id,
            device_id=device_id,
            action=action,
        )
    if is_route_available(process_id):
        return None
    return build_interceptor_error(
        code="INTERCEPTOR_UNAVAILABLE",
        message=f"Interceptor {process_id!r} unavailable for {device_id}.{action}",
        process_id=process_id,
        device_id=device_id,
        action=action,
    )


def _build_call_error(
    *,
    process_id: str,
    status_text: str,
    call_error: str | None,
    device_id: str,
    action: str,
) -> Json | None:
    if status_text == "ok":
        return None
    if status_text == "timeout":
        return build_interceptor_error(
            code="INTERCEPTOR_TIMEOUT",
            message=f"Interceptor {process_id!r} timed out for {device_id}.{action}",
            process_id=process_id,
            device_id=device_id,
            action=action,
        )
    if call_error:
        message = f"Interceptor {process_id!r} failed for {device_id}.{action}: {call_error}"
    else:
        message = f"Interceptor {process_id!r} unavailable for {device_id}.{action}"
    return build_interceptor_error(
        code="INTERCEPTOR_UNAVAILABLE",
        message=message,
        process_id=process_id,
        device_id=device_id,
        action=action,
    )


def _validate_interceptor_response(
    *,
    process_id: str,
    response: Any,
    distinct_ok_false_message: bool,
    device_id: str,
    action: str,
) -> tuple[Json | None, Json | None]:
    if not isinstance(response, dict):
        return None, build_interceptor_error(
            code="INTERCEPTOR_BAD_RESPONSE",
            message=f"Interceptor {process_id!r} returned invalid response",
            process_id=process_id,
            device_id=device_id,
            action=action,
            details={"response": response},
        )
    if response.get("ok") is False:
        message = (
            f"Interceptor {process_id!r} returned error response"
            if distinct_ok_false_message
            else f"Interceptor {process_id!r} returned invalid response"
        )
        return None, build_interceptor_error(
            code="INTERCEPTOR_BAD_RESPONSE",
            message=message,
            process_id=process_id,
            device_id=device_id,
            action=action,
            details={"response": response},
        )
    return response, None


def _apply_allow_response(
    *,
    process_id: str,
    response: Json,
    current_command: Json,
    device_id: str,
    action: str,
) -> tuple[Json | None, Json | None, Json | None]:
    if "command" not in response:
        return current_command, None, None
    new_cmd_raw = response.get("command")
    if not isinstance(new_cmd_raw, dict):
        return None, None, build_interceptor_error(
            code="INTERCEPTOR_BAD_RESPONSE",
            message=f"Interceptor {process_id!r} returned invalid command",
            process_id=process_id,
            device_id=device_id,
            action=action,
        )
    new_device = str(new_cmd_raw.get("device_id", device_id))
    new_action = str(new_cmd_raw.get("action", action))
    if new_device != device_id or new_action != action:
        return None, None, build_interceptor_error(
            code="INTERCEPTOR_BAD_RESPONSE",
            message=f"Interceptor {process_id!r} attempted to change route",
            process_id=process_id,
            device_id=device_id,
            action=action,
        )
    new_params = (
        new_cmd_raw.get("params")
        if "params" in new_cmd_raw
        else current_command.get("params")
    )
    if not isinstance(new_params, dict):
        return None, None, build_interceptor_error(
            code="INTERCEPTOR_BAD_RESPONSE",
            message=f"Interceptor {process_id!r} returned invalid params",
            process_id=process_id,
            device_id=device_id,
            action=action,
        )
    new_cmd = {
        "device_id": device_id,
        "action": action,
        "params": new_params,
    }
    if new_cmd == current_command:
        return new_cmd, None, None
    modified_event: Json = {
        "process_id": process_id,
        "interceptor_id": response.get("interceptor_id"),
        "rule": response.get("rule"),
        "note": response.get("note"),
        "before": current_command,
        "after": new_cmd,
    }
    return new_cmd, modified_event, None


def _build_reject_error(
    *,
    process_id: str,
    response: Json,
    device_id: str,
    action: str,
) -> Json:
    inner = response.get("error") or {}
    inner_code = str(inner.get("code", "CONDITION_FAILED"))
    inner_msg = str(inner.get("message", "Command rejected by interceptor"))
    return build_interceptor_error(
        code="INTERCEPTOR_REJECTED",
        message=inner_msg,
        process_id=process_id,
        device_id=device_id,
        action=action,
        interceptor_id=response.get("interceptor_id"),
        rule=response.get("rule"),
        details={
            "code": inner_code,
            "message": inner_msg,
            "details": inner.get("details", {}),
        },
    )


def _build_interceptor_request(
    *,
    current_command: Json,
    request_id: Any | None,
    caller_process_id: Any | None,
) -> Json:
    meta: Json = {"request_id": request_id, "t_mono": time.monotonic()}
    if caller_process_id:
        meta["caller_process_id"] = caller_process_id
    return {"type": "command_interceptor.check", "command": current_command, "meta": meta}


def _call_interceptor_checked(
    *,
    process_id: str,
    req: Json,
    call_interceptor: Callable[[str, Json], CallResult],
    distinct_ok_false_message: bool,
    device_id: str,
    action: str,
) -> tuple[Json | None, Json | None]:
    status, resp_raw, call_error = call_interceptor(process_id, req)
    status_text = str(status or "").strip().lower()
    call_err = _build_call_error(
        process_id=process_id,
        status_text=status_text,
        call_error=call_error,
        device_id=device_id,
        action=action,
    )
    if call_err is not None:
        return None, call_err
    return _validate_interceptor_response(
        process_id=process_id,
        response=resp_raw,
        distinct_ok_false_message=distinct_ok_false_message,
        device_id=device_id,
        action=action,
    )


def _apply_interceptor_decision(
    *,
    process_id: str,
    response: Json,
    current_command: Json,
    publish_event: Callable[[str, Json], None],
    device_id: str,
    action: str,
) -> tuple[bool, Json, Json | None]:
    allow = response.get("allow")
    if allow is True:
        new_cmd, modified_event, allow_err = _apply_allow_response(
            process_id=process_id,
            response=response,
            current_command=current_command,
            device_id=device_id,
            action=action,
        )
        if allow_err is not None:
            return False, current_command, allow_err
        assert new_cmd is not None
        if modified_event is not None:
            publish_event("manager.command_interceptor.modified", modified_event)
        return True, new_cmd, None

    if allow is False:
        reject_err = _build_reject_error(
            process_id=process_id,
            response=response,
            device_id=device_id,
            action=action,
        )
        return False, current_command, reject_err

    bad_response_err = build_interceptor_error(
        code="INTERCEPTOR_BAD_RESPONSE",
        message=f"Interceptor {process_id!r} returned invalid response",
        process_id=process_id,
        device_id=device_id,
        action=action,
        details={"response": response},
    )
    return False, current_command, bad_response_err


def _apply_interceptor_step(
    *,
    route: Any,
    current_command: Json,
    request_id: Any | None,
    caller_process_id: Any | None,
    is_route_available: Callable[[str], bool],
    call_interceptor: Callable[[str, Json], CallResult],
    publish_event: Callable[[str, Json], None],
    distinct_ok_false_message: bool,
    device_id: str,
    action: str,
) -> tuple[bool, Json, Json | None]:
    process_id = _route_process_id(route)
    route_err = _validate_route_availability(
        process_id=process_id,
        is_route_available=is_route_available,
        device_id=device_id,
        action=action,
    )
    if route_err is not None:
        _emit_interceptor_error(
            publish_event=publish_event,
            err=route_err,
            command=current_command,
        )
        return False, current_command, route_err

    req = _build_interceptor_request(
        current_command=current_command,
        request_id=request_id,
        caller_process_id=caller_process_id,
    )
    resp, checked_err = _call_interceptor_checked(
        process_id=process_id,
        req=req,
        call_interceptor=call_interceptor,
        distinct_ok_false_message=distinct_ok_false_message,
        device_id=device_id,
        action=action,
    )
    if checked_err is not None:
        _emit_interceptor_error(
            publish_event=publish_event,
            err=checked_err,
            command=current_command,
        )
        return False, current_command, checked_err
    assert resp is not None

    ok, next_cmd, decision_err = _apply_interceptor_decision(
        process_id=process_id,
        response=resp,
        current_command=current_command,
        publish_event=publish_event,
        device_id=device_id,
        action=action,
    )
    if decision_err is not None:
        _emit_interceptor_error(
            publish_event=publish_event,
            err=decision_err,
            command=current_command,
        )
    return ok, next_cmd, decision_err


def apply_command_interceptor_chain(
    *,
    initial_command: Json,
    chain: Sequence[Any],
    request_id: Any | None,
    caller_process_id: Any | None,
    is_route_available: Callable[[str], bool],
    call_interceptor: Callable[[str, Json], CallResult],
    publish_event: Callable[[str, Json], None],
    distinct_ok_false_message: bool = False,
) -> tuple[bool, Json | None, Json | None]:
    device_id = str(initial_command.get("device_id", ""))
    action = str(initial_command.get("action", ""))
    cur_cmd: Json = {
        "device_id": device_id,
        "action": action,
        "params": initial_command.get("params", {}),
    }
    if not chain:
        return True, cur_cmd, None

    for route in chain:
        ok, next_cmd, err = _apply_interceptor_step(
            route=route,
            current_command=cur_cmd,
            request_id=request_id,
            caller_process_id=caller_process_id,
            is_route_available=is_route_available,
            call_interceptor=call_interceptor,
            publish_event=publish_event,
            distinct_ok_false_message=distinct_ok_false_message,
            device_id=device_id,
            action=action,
        )
        if not ok:
            return False, None, err
        cur_cmd = next_cmd

    return True, cur_cmd, None
