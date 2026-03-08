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
        process_id = _route_process_id(route)
        if not process_id or not is_route_available(process_id):
            err = build_interceptor_error(
                code="INTERCEPTOR_UNAVAILABLE",
                message=f"Interceptor {process_id!r} unavailable for {device_id}.{action}",
                process_id=process_id,
                device_id=device_id,
                action=action,
            )
            publish_event("manager.command_interceptor.error", {"error": err, "command": cur_cmd})
            return False, None, err

        meta: Json = {"request_id": request_id, "t_mono": time.monotonic()}
        if caller_process_id:
            meta["caller_process_id"] = caller_process_id
        req = {"type": "command_interceptor.check", "command": cur_cmd, "meta": meta}
        status, resp, call_error = call_interceptor(process_id, req)
        status_text = str(status or "").strip().lower()

        if status_text == "timeout":
            err = build_interceptor_error(
                code="INTERCEPTOR_TIMEOUT",
                message=f"Interceptor {process_id!r} timed out for {device_id}.{action}",
                process_id=process_id,
                device_id=device_id,
                action=action,
            )
            publish_event("manager.command_interceptor.error", {"error": err, "command": cur_cmd})
            return False, None, err
        if status_text != "ok":
            if call_error:
                message = (
                    f"Interceptor {process_id!r} failed for {device_id}.{action}: {call_error}"
                )
            else:
                message = f"Interceptor {process_id!r} unavailable for {device_id}.{action}"
            err = build_interceptor_error(
                code="INTERCEPTOR_UNAVAILABLE",
                message=message,
                process_id=process_id,
                device_id=device_id,
                action=action,
            )
            publish_event("manager.command_interceptor.error", {"error": err, "command": cur_cmd})
            return False, None, err

        if not isinstance(resp, dict):
            err = build_interceptor_error(
                code="INTERCEPTOR_BAD_RESPONSE",
                message=f"Interceptor {process_id!r} returned invalid response",
                process_id=process_id,
                device_id=device_id,
                action=action,
                details={"response": resp},
            )
            publish_event("manager.command_interceptor.error", {"error": err, "command": cur_cmd})
            return False, None, err

        if resp.get("ok") is False:
            message = (
                f"Interceptor {process_id!r} returned error response"
                if distinct_ok_false_message
                else f"Interceptor {process_id!r} returned invalid response"
            )
            err = build_interceptor_error(
                code="INTERCEPTOR_BAD_RESPONSE",
                message=message,
                process_id=process_id,
                device_id=device_id,
                action=action,
                details={"response": resp},
            )
            publish_event("manager.command_interceptor.error", {"error": err, "command": cur_cmd})
            return False, None, err

        allow = resp.get("allow")
        if allow is True:
            if "command" in resp:
                new_cmd_raw = resp.get("command")
                if not isinstance(new_cmd_raw, dict):
                    err = build_interceptor_error(
                        code="INTERCEPTOR_BAD_RESPONSE",
                        message=f"Interceptor {process_id!r} returned invalid command",
                        process_id=process_id,
                        device_id=device_id,
                        action=action,
                    )
                    publish_event(
                        "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                    )
                    return False, None, err
                new_device = str(new_cmd_raw.get("device_id", device_id))
                new_action = str(new_cmd_raw.get("action", action))
                if new_device != device_id or new_action != action:
                    err = build_interceptor_error(
                        code="INTERCEPTOR_BAD_RESPONSE",
                        message=f"Interceptor {process_id!r} attempted to change route",
                        process_id=process_id,
                        device_id=device_id,
                        action=action,
                    )
                    publish_event(
                        "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                    )
                    return False, None, err
                new_params = (
                    new_cmd_raw.get("params")
                    if "params" in new_cmd_raw
                    else cur_cmd.get("params")
                )
                if not isinstance(new_params, dict):
                    err = build_interceptor_error(
                        code="INTERCEPTOR_BAD_RESPONSE",
                        message=f"Interceptor {process_id!r} returned invalid params",
                        process_id=process_id,
                        device_id=device_id,
                        action=action,
                    )
                    publish_event(
                        "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
                    )
                    return False, None, err
                new_cmd = {
                    "device_id": device_id,
                    "action": action,
                    "params": new_params,
                }
                if new_cmd != cur_cmd:
                    publish_event(
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
            err = build_interceptor_error(
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
            publish_event("manager.command_interceptor.error", {"error": err, "command": cur_cmd})
            return False, None, err

        err = build_interceptor_error(
            code="INTERCEPTOR_BAD_RESPONSE",
            message=f"Interceptor {process_id!r} returned invalid response",
            process_id=process_id,
            device_id=device_id,
            action=action,
            details={"response": resp},
        )
        publish_event("manager.command_interceptor.error", {"error": err, "command": cur_cmd})
        return False, None, err

    return True, cur_cmd, None
