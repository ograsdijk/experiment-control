from __future__ import annotations

import datetime
import sys
import time
from typing import TYPE_CHECKING, Any

from ..utils.errors import TRANSIENT_CAPABILITIES_ERROR_CODES
from ..utils.logging_levels import normalize_log_severity

if TYPE_CHECKING:
    from ..manager_protocol import ManagerProtocol
    from ..utils.rotating_jsonl import RotatingJsonlSink

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

Json = dict[str, Any]


def _is_transient_capabilities_failure(payload: Json) -> bool:
    action = str(payload.get("action", "") or "").strip().lower()
    if action != "capabilities":
        return False
    err = payload.get("error")
    if isinstance(err, dict):
        code = str(err.get("code", "") or "").strip().lower()
        if code in TRANSIENT_CAPABILITIES_ERROR_CODES:
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


def _write_sink_line_impl(
    *,
    stderr_enabled: bool,
    line: str,
) -> None:
    """Write one human-readable manager event to stderr when enabled.

    Kept as a pure function (no ``manager`` first arg) so the mixin
    method can pass already-narrowed attributes — gives mypy enough
    information without sprinkling ``Any``-typed access through the body.
    """
    if stderr_enabled:
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except Exception:
            pass


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
    if topic in {
        "manager.watchdog.action_started",
        "manager.watchdog.action_succeeded",
        "manager.watchdog.recovered",
        "manager.watchdog.latch_cleared",
    }:
        return "info"
    if topic == "manager.watchdog.action_chain_completed":
        return "info" if payload.get("success") is True else "error"
    if topic in {"manager.watchdog.latched", "manager.watchdog.action_retry"}:
        return "warning"
    if topic in {
        "manager.watchdog.action_failed",
        "manager.watchdog.action_chain_error",
        "manager.watchdog.rule_error",
    }:
        return "error"
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


def _event_log_source(topic: str, payload: Json) -> tuple[str, str, Any, Any]:
    process_id = payload.get("process_id")
    device_id = payload.get("device_id")
    explicit_source_kind = payload.get("source_kind")
    explicit_source_id = payload.get("source_id")
    if (
        topic in {"manager.command", "manager.logs.publish"}
        or topic.startswith("manager.instance_ui.")
    ) and explicit_source_kind is not None and explicit_source_id is not None:
        return str(explicit_source_kind), str(explicit_source_id), device_id, process_id
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


def _watchdog_action_text(payload: Json) -> str:
    command = payload.get("command")
    if not isinstance(command, dict):
        return "unknown action"
    target = command.get("device_id") or command.get("process_id") or "unknown"
    action = command.get("action") or "unknown"
    return f"{target}.{action}"


def _watchdog_log_message(topic: str, payload: Json) -> str:
    watchdog_id = str(payload.get("watchdog_id") or "unknown")
    rule = str(payload.get("rule") or "unknown")
    prefix = f"Watchdog {watchdog_id}:{rule}"
    if topic == "manager.watchdog.triggered":
        return str(payload.get("message") or f"{prefix} triggered")
    if topic == "manager.watchdog.latched":
        return f"{prefix} latched"
    if topic == "manager.watchdog.recovered":
        suffix = "; latch remains set" if payload.get("latched") is True else ""
        return f"{prefix} condition recovered{suffix}"
    if topic == "manager.watchdog.latch_cleared":
        return f"{prefix} latch cleared"
    if topic.startswith("manager.watchdog.action_"):
        action_text = _watchdog_action_text(payload)
        attempt = payload.get("attempt")
        max_attempts = payload.get("max_attempts")
        attempt_text = (
            f" attempt {attempt}/{max_attempts}"
            if attempt is not None and max_attempts is not None
            else ""
        )
        if topic == "manager.watchdog.action_started":
            return f"{prefix} started {action_text}{attempt_text}"
        if topic == "manager.watchdog.action_succeeded":
            return f"{prefix} succeeded {action_text}{attempt_text}"
        if topic == "manager.watchdog.action_retry":
            return (
                f"{prefix} will retry {action_text} after{attempt_text}: "
                f"{payload.get('error')}"
            )
        if topic == "manager.watchdog.action_failed":
            return f"{prefix} failed {action_text}{attempt_text}: {payload.get('error')}"
        if topic == "manager.watchdog.action_chain_completed":
            succeeded = payload.get("succeeded_actions", 0)
            count = payload.get("action_count", 0)
            failed = payload.get("failed_actions", 0)
            return (
                f"{prefix} action chain completed: {succeeded}/{count} succeeded, "
                f"{failed} failed"
            )
        if topic == "manager.watchdog.action_chain_error":
            return f"{prefix} action chain error: {payload.get('error')}"
    if topic == "manager.watchdog.rule_error":
        return f"{prefix} condition evaluation error: {payload.get('error')}"
    return str(payload.get("message") or prefix)


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


class LogEventsMixin(_MixinBase):
    """Mixin providing manager-log event sinks.

    Phase 8.2.3: migrated ``_maybe_emit_manager_log_sink``,
    ``_maybe_publish_log_event``, and the private ``_write_sink_line``
    helper from module-level helpers to mixin methods. A
    ``maybe_publish_log_event`` module-level forwarder is kept below
    for ``tests.test_manager_log_events`` (which calls it directly).

    At runtime ``_MixinBase`` is ``object``; only mypy sees
    :class:`ManagerProtocol` as the base, which supplies signatures
    for ``_manager_log_sink_event`` / ``_severity_rank`` /
    ``_manager_log_sink_is_duplicate`` / ``_close_manager_jsonl_sink``
    / ``_emit_log`` (all still on ``Manager`` itself, scheduled to move
    onto ``LogsMixin`` in §8.2.4).
    """

    # Owned-state attributes (concrete types declared on Manager).
    _manager_log_stderr_enabled: bool
    _manager_log_jsonl_sink: "RotatingJsonlSink | None"
    _manager_log_min_level_rank: int

    def _write_sink_line(self, line: str) -> None:
        _write_sink_line_impl(
            stderr_enabled=self._manager_log_stderr_enabled,
            line=line,
        )

    def _write_jsonl_sink_record(
        self,
        *,
        topic: str,
        payload: Json,
        severity: str,
        line_topic: str,
        source_kind: str,
        source_id: str | None,
        message: str,
    ) -> None:
        sink = self._manager_log_jsonl_sink
        if sink is None:
            return
        if topic == "manager.log":
            record = dict(payload)
            record["timestamp"] = _sink_timestamp_text(payload)
            record["instance_id"] = getattr(self, "_instance_id", "unknown")
        else:
            record = {
                "version": 1,
                "timestamp": _sink_timestamp_text(payload),
                "instance_id": getattr(self, "_instance_id", "unknown"),
                "severity": severity,
                "topic": line_topic,
                "source_kind": source_kind,
                "source_id": source_id,
                "message": message,
                "payload": payload,
            }
        try:
            sink.write(record)
        except Exception as exc:
            self._close_manager_jsonl_sink()
            if self._manager_log_stderr_enabled:
                try:
                    sys.stderr.write(
                        f"[manager][warning] rotating JSONL log write failed: {exc}\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass

    def _maybe_emit_manager_log_sink(self, topic: str, payload: Json) -> None:
        try:
            severity, line_topic, source_kind, source_id, message = (
                self._manager_log_sink_event(topic, payload)
            )
        except Exception:
            return
        min_rank = self._manager_log_min_level_rank
        if self._severity_rank(severity) < min_rank:
            return
        fingerprint = f"{severity}|{line_topic}|{source_kind}|{source_id}|{message}"
        is_duplicate = self._manager_log_sink_is_duplicate(fingerprint)
        ts_text = _sink_timestamp_text(payload)
        line = _sink_line_text(
            severity=severity,
            line_topic=line_topic,
            source_kind=source_kind,
            source_id=source_id,
            message=message,
            ts_text=ts_text,
        )
        self._write_jsonl_sink_record(
            topic=topic,
            payload=payload,
            severity=severity,
            line_topic=line_topic,
            source_kind=source_kind,
            source_id=source_id,
            message=message,
        )
        if not is_duplicate:
            self._write_sink_line(line)

    def _maybe_publish_log_event(self, topic: str, payload: Json) -> None:
        severity = _event_log_severity(topic, payload)
        if severity is None:
            return
        source_kind, source_id, device_id, process_id = _event_log_source(topic, payload)
        message = payload.get("error") or payload.get("message") or ""
        if topic == "manager.command":
            message = _command_failure_message(payload)
        elif topic.startswith("manager.watchdog."):
            message = _watchdog_log_message(topic, payload)
        elif topic.startswith("manager.device.auto_reconnect."):
            message = _auto_reconnect_message(topic, payload)
        elif (
            topic.endswith("failed")
            or topic.endswith("crashloop")
            or "kill_timeout" in topic
        ):
            message = _failure_message(topic, payload)
        self._emit_log(
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


# --- Backward-compat module-level forwarder --------------------------
# ``tests/test_manager_log_events.py`` imports ``maybe_publish_log_event``
# directly and calls it against a ``SimpleNamespace`` stub. The body
# lives on :class:`LogEventsMixin`; this trampoline delegates. (The
# ``maybe_emit_manager_log_sink`` trampoline was removed as dead code
# — no external importer exists; tests call the mixin form via
# ``Manager._maybe_emit_manager_log_sink``.)

def maybe_publish_log_event(manager: Any, topic: str, payload: Json) -> None:
    LogEventsMixin._maybe_publish_log_event(manager, topic, payload)
