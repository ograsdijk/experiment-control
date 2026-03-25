from __future__ import annotations

import datetime
import sys
import time
from typing import Any

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


def maybe_publish_log_event(manager: Any, topic: str, payload: Json) -> None:
    severity = _event_log_severity(topic, payload)
    if severity is None:
        return
    source_kind, source_id, device_id, process_id = _event_log_source(payload)
    message = payload.get("error") or payload.get("message") or ""
    if topic == "manager.command":
        message = _command_failure_message(payload)
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
