from __future__ import annotations

import datetime
import sys
import time
from typing import Any

from .utils.logging_levels import normalize_log_severity

Json = dict[str, Any]


_TRANSIENT_CAPABILITIES_CODES = {
    "device_rpc_timeout",
    "device_starting",
    "device_stopping",
    "device_rpc_not_ready",
    "driver_not_running",
    "gateway_busy",
    "gateway_timeout",
}


def _is_transient_capabilities_failure(payload: Json) -> bool:
    action = str(payload.get("action", "") or "").strip().lower()
    if action != "capabilities":
        return False
    err = payload.get("error")
    if isinstance(err, dict):
        code = str(err.get("code", "") or "").strip().lower()
        if code in _TRANSIENT_CAPABILITIES_CODES:
            return True
        if bool(err.get("transient")):
            return True
        message = str(err.get("message", "") or "").strip().lower()
        if "resource temporarily unavailable" in message:
            return True
    elif isinstance(err, str):
        if "resource temporarily unavailable" in err.lower():
            return True
    return False


def _sink_timestamp_text(payload: Json) -> str:
    ts = payload.get("ts")
    t_wall = time.time()
    if isinstance(ts, dict):
        try:
            t_wall = float(ts.get("t_wall", t_wall))
        except Exception:
            pass
    try:
        dt = datetime.datetime.fromtimestamp(t_wall, tz=datetime.timezone.utc)
        return dt.isoformat(timespec="milliseconds")
    except Exception:
        return str(t_wall)


def _sink_line_text(
    *,
    severity: str,
    line_topic: str,
    source_kind: str,
    source_id: str | None,
    message: str,
    ts_text: str,
) -> str:
    source_text = f"{source_kind}:{source_id}" if source_id else source_kind
    return f"{ts_text} [{severity.upper()}] {line_topic} {source_text} {message}"


def _write_sink_line(manager: Any, line: str) -> None:
    if bool(getattr(manager, "_manager_log_stderr_enabled", False)):
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except Exception:
            pass
    log_file = getattr(manager, "_manager_log_file", None)
    if log_file is not None:
        try:
            log_file.write(line + "\n")
        except Exception:
            manager._close_manager_log_sink_file()


def maybe_emit_manager_log_sink(manager: Any, topic: str, payload: Json) -> None:
    try:
        severity, line_topic, source_kind, source_id, message = manager._manager_log_sink_event(
            topic, payload
        )
    except Exception:
        return
    min_rank = int(
        getattr(manager, "_manager_log_min_level_rank", manager._severity_rank("error"))
    )
    if manager._severity_rank(severity) < min_rank:
        return
    fingerprint = f"{severity}|{line_topic}|{source_kind}|{source_id}|{message}"
    if manager._manager_log_sink_is_duplicate(fingerprint):
        return
    ts_text = _sink_timestamp_text(payload)
    line = _sink_line_text(
        severity=severity,
        line_topic=line_topic,
        source_kind=source_kind,
        source_id=source_id,
        message=message,
        ts_text=ts_text,
    )
    _write_sink_line(manager, line)


def _event_log_severity(topic: str, payload: Json) -> str | None:
    if topic == "manager.command":
        ok = payload.get("ok")
        status = str(payload.get("status", "") or "").upper()
        if ok is False and _is_transient_capabilities_failure(payload):
            return "warning"
        if ok is False or status == "ERROR":
            return "error"
        return None
    if topic == "manager.watchdog.triggered":
        return normalize_log_severity(payload.get("severity"), default="warning")
    if topic == "manager.loop_stall":
        return "warning"
    if topic == "manager.process.heartbeat_stale_deferred":
        return "warning"
    if topic.startswith("manager.device.auto_reconnect."):
        if topic.endswith("success") or topic.endswith("reset"):
            return "info"
        if topic.endswith("attempt") or topic.endswith("suppressed"):
            return "warning"
        return "error"
    if topic.endswith("telemetry_stale"):
        return "warning"
    if (
        "error" in topic
        or topic.endswith("failed")
        or topic.endswith("crashloop")
        or "kill_timeout" in topic
    ):
        return "error"
    return None


def _event_log_source(payload: Json) -> tuple[str, str, Any, Any]:
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
    return source_kind, source_id, device_id, process_id


def _command_failure_message(payload: Json) -> str:
    device_id = payload.get("device_id")
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
    if err_message:
        return f"Command failed: {target} ({err_message})"
    return f"Command failed: {target}"


def _last_tail_message(payload: Json, key: str) -> str:
    tail = payload.get(key)
    if not isinstance(tail, list) or not tail:
        return ""
    last = tail[-1]
    if not isinstance(last, dict):
        return ""
    return str(last.get("message", "") or "").strip()


def _heartbeat_detail(payload: Json) -> str:
    hb = payload.get("last_heartbeat_payload")
    if not isinstance(hb, dict):
        return ""
    phase = str(hb.get("phase", "") or "").strip()
    detail = str(hb.get("detail", "") or "").strip()
    if phase and detail:
        return f"last heartbeat phase={phase}: {detail}"
    if phase:
        return f"last heartbeat phase={phase}"
    if detail:
        return f"last heartbeat detail={detail}"
    return ""


def _auto_reconnect_message(topic: str, payload: Json) -> str:
    device_id = str(payload.get("device_id") or "unknown")
    reconnect = payload.get("auto_reconnect")
    max_attempts = None
    if isinstance(reconnect, dict):
        max_attempts = reconnect.get("max_attempts")
    attempt = payload.get("attempt")
    age = payload.get("telemetry_age_s")
    suffix = ""
    if attempt is not None:
        suffix += f" attempt {attempt}"
        if max_attempts is not None:
            suffix += f"/{max_attempts}"
    if age is not None:
        try:
            suffix += f" telemetry_age={float(age):.2f}s"
        except Exception:
            pass
    if topic.endswith("attempt"):
        return f"Auto-reconnect {device_id}: attempting reconnect{suffix}"
    if topic.endswith("success"):
        return f"Auto-reconnect {device_id}: reconnect succeeded{suffix}"
    if topic.endswith("suppressed"):
        return f"Auto-reconnect {device_id}: suppressed ({payload.get('reason')}){suffix}"
    if topic.endswith("reset"):
        return f"Auto-reconnect {device_id}: attempts reset after healthy telemetry"
    return f"Auto-reconnect {device_id}: failed ({payload.get('error')}){suffix}"


def _failure_message(topic: str, payload: Json) -> str:
    process_id = payload.get("process_id")
    device_id = payload.get("device_id")
    target_kind = "Process" if process_id is not None else "Driver"
    target_id = str(process_id if process_id is not None else device_id or "unknown")
    error_text = str(payload.get("error") or payload.get("message") or topic)
    parts = [f"{target_kind} {target_id} failed: {error_text}"]
    if payload.get("terminated_by_manager"):
        method = str(payload.get("termination_method") or "terminate")
        parts.append(f"manager sent {method} due to {payload.get('termination_reason')}")
    strikes = payload.get("heartbeat_stale_strikes")
    if strikes is not None:
        parts.append(f"stale strikes={strikes}")
    if payload.get("recent_manager_loop_stall"):
        duration = payload.get("last_manager_loop_stall_duration_s")
        parts.append(f"recent manager loop stall={duration}s")
    stderr = _last_tail_message(payload, "tail_stderr")
    if stderr:
        parts.append(f"last stderr: {stderr}")
    elif recent := _last_tail_message(payload, "tail_recent_logs"):
        parts.append(f"recent log: {recent}")
    elif supervisor := _last_tail_message(payload, "tail_supervisor_logs"):
        parts.append(f"last log: {supervisor}")
    heartbeat = _heartbeat_detail(payload)
    if heartbeat:
        parts.append(heartbeat)
    return "; ".join(parts)


def maybe_publish_log_event(manager: Any, topic: str, payload: Json) -> None:
    severity = _event_log_severity(topic, payload)
    if severity is None:
        return
    source_kind, source_id, device_id, process_id = _event_log_source(payload)
    message = payload.get("error") or payload.get("message") or ""
    if topic == "manager.command":
        message = _command_failure_message(payload)
    elif topic.startswith("manager.device.auto_reconnect."):
        message = _auto_reconnect_message(topic, payload)
    elif topic.endswith("failed") or topic.endswith("crashloop") or "kill_timeout" in topic:
        message = _failure_message(topic, payload)
    manager._emit_log(
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
