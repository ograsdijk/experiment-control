from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..utils.logging_levels import (
    is_valid_log_severity,
    normalize_log_severity,
    severity_rank,
)

if TYPE_CHECKING:
    from collections import deque
    from typing import TextIO

    from ..manager_protocol import ManagerProtocol

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

Json = dict[str, Any]


def parse_boolish(raw: Any, *, default: bool) -> bool:
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def resolve_manager_log_stderr_enabled(raw: Any) -> bool:
    # ``manager`` first arg was vestigial — never read. Dropped in
    # Phase 8.2.4 so the function reads cleanly as a pure utility.
    if raw is None:
        return parse_boolish(os.environ.get("MANAGER_LOG_STDERR"), default=True)
    return parse_boolish(raw, default=True)


def resolve_manager_log_file_path(raw: Any) -> Path | None:
    value = raw
    if value is None:
        value = os.environ.get("MANAGER_LOG_FILE")
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text).expanduser()


def resolve_manager_log_min_level(raw: Any) -> str:
    value = raw
    if value is None:
        value = os.environ.get("MANAGER_LOG_MIN_LEVEL")
    text = str(value or "").strip().lower()
    if not text:
        return "error"
    if not is_valid_log_severity(text):
        return "error"
    return normalize_log_severity(text, default="error")


def normalize_id(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text else None


def normalize_log_ts(raw: Any) -> Json:
    now_wall = time.time()
    now_mono = time.monotonic()
    if not isinstance(raw, dict):
        return {"t_wall": now_wall, "t_mono": now_mono}
    try:
        t_wall = float(raw.get("t_wall", now_wall))
    except Exception:
        t_wall = now_wall
    try:
        t_mono = float(raw.get("t_mono", now_mono))
    except Exception:
        t_mono = now_mono
    return {"t_wall": t_wall, "t_mono": t_mono}


def normalize_filter_set(raw: Any, *, field: str) -> set[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        return {text}
    if isinstance(raw, list):
        out: set[str] = set()
        for item in raw:
            text = str(item).strip()
            if text:
                out.add(text)
        return out if out else None
    raise TypeError(f"{field} must be a string or list[str]")


def parse_log_tail_limit(raw: Any) -> int:
    try:
        limit = int(raw)
    except Exception as exc:
        raise TypeError(f"limit must be int: {exc}") from exc
    return max(1, min(limit, 5000))


def parse_log_tail_since_t_mono(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except Exception as exc:
        raise TypeError(f"since_t_mono must be float: {exc}") from exc


def log_tail_filters(params: Json) -> dict[str, Any]:
    # ``manager`` first arg was vestigial — never read. Dropped in
    # Phase 8.2.4.
    severity_min_raw = params.get("severity_min")
    severity_min_rank: int | None = None
    if severity_min_raw is not None:
        severity_min_rank = severity_rank(severity_min_raw, default="info")

    severity_set = normalize_filter_set(params.get("severity"), field="severity")
    if severity_set is not None:
        severity_set = {
            normalize_log_severity(item, default="info") for item in severity_set
        }

    source_kind_set = normalize_filter_set(params.get("source_kind"), field="source_kind")
    if source_kind_set is not None:
        source_kind_set = {item.lower() for item in source_kind_set}

    return {
        "since_t_mono": parse_log_tail_since_t_mono(params.get("since_t_mono")),
        "severity_min_rank": severity_min_rank,
        "severity_set": severity_set,
        "source_kind_set": source_kind_set,
        "device_set": normalize_filter_set(params.get("device_ids"), field="device_ids"),
        "process_set": normalize_filter_set(params.get("process_ids"), field="process_ids"),
        "source_id_set": normalize_filter_set(params.get("source_ids"), field="source_ids"),
        "topic_contains": str(params.get("topic_contains", "") or "").strip().lower(),
        "text_contains": str(params.get("text_contains", "") or "").strip().lower(),
    }


def log_tail_entry_t_mono(entry: Json) -> float | None:
    ts = entry.get("ts")
    if not isinstance(ts, dict):
        return None
    raw = ts.get("t_mono")
    if raw is None:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def log_tail_matches_time(entry: Json, *, filters: dict[str, Any]) -> bool:
    since_t_mono = filters.get("since_t_mono")
    if since_t_mono is not None:
        t_mono = log_tail_entry_t_mono(entry)
        if t_mono is None or t_mono < float(since_t_mono):
            return False
    return True


def log_tail_matches_severity(entry: Json, *, filters: dict[str, Any]) -> bool:
    severity = normalize_log_severity(entry.get("severity"), default="info")
    severity_min_rank = filters.get("severity_min_rank")
    if severity_min_rank is not None and severity_rank(severity, default="info") < int(
        severity_min_rank
    ):
        return False
    severity_set = filters.get("severity_set")
    if isinstance(severity_set, set) and severity not in severity_set:
        return False
    return True


def log_tail_matches_source_kind(entry: Json, *, filters: dict[str, Any]) -> bool:
    source_kind = str(entry.get("source_kind", "") or "").lower()
    source_kind_set = filters.get("source_kind_set")
    if isinstance(source_kind_set, set) and source_kind not in source_kind_set:
        return False
    return True


def log_tail_matches_ids(entry: Json, *, filters: dict[str, Any]) -> bool:
    device_set = filters.get("device_set")
    device_id = normalize_id(entry.get("device_id"))
    if isinstance(device_set, set) and (device_id is None or device_id not in device_set):
        return False

    process_set = filters.get("process_set")
    process_id = normalize_id(entry.get("process_id"))
    if isinstance(process_set, set) and (process_id is None or process_id not in process_set):
        return False

    source_id_set = filters.get("source_id_set")
    source_id = normalize_id(entry.get("source_id"))
    if isinstance(source_id_set, set) and (source_id is None or source_id not in source_id_set):
        return False
    return True


def log_tail_matches_contains(entry: Json, *, filters: dict[str, Any]) -> bool:
    topic_contains = str(filters.get("topic_contains", "") or "")
    if topic_contains:
        topic = str(entry.get("topic", "") or "").lower()
        if topic_contains not in topic:
            return False

    text_contains = str(filters.get("text_contains", "") or "")
    if text_contains:
        message = str(entry.get("message", "") or "").lower()
        payload_json = str(entry.get("payload_json", "") or "").lower()
        if text_contains not in message and text_contains not in payload_json:
            return False
    return True


def log_tail_entry_matches(entry: Json, *, filters: dict[str, Any]) -> bool:
    if not log_tail_matches_time(entry, filters=filters):
        return False
    if not log_tail_matches_severity(entry, filters=filters):
        return False
    if not log_tail_matches_source_kind(entry, filters=filters):
        return False
    if not log_tail_matches_ids(entry, filters=filters):
        return False
    return log_tail_matches_contains(entry, filters=filters)


class LogsMixin(_MixinBase):
    """Mixin providing manager-log emit, sink, and tail helpers.

    Phase 8.2.4: migrated ``open_manager_log_sink_file``,
    ``close_manager_log_sink_file``, ``manager_log_sink_event``,
    ``manager_log_sink_is_duplicate``, ``emit_log``,
    ``emit_log_from_payload``, and ``log_tail`` from module-level
    helpers to mixin methods. Also dropped the unused trampoline
    methods on ``Manager`` (``_log_tail_filters``, ``_log_tail_matches_*``,
    ``_normalize_filter_set``, ``_parse_log_tail_*``) — none had
    external callers; the underlying pure helpers remain at module
    level for direct use.

    At runtime ``_MixinBase`` is ``object``; only mypy sees
    :class:`ManagerProtocol` as the base, supplying signatures for
    ``_safe_json``, ``_normalize_topic``, and ``_publish_manager_event``
    that this mixin invokes via ``self``.
    """

    # Owned-state attributes (concrete types declared on Manager).
    _manager_log_file_path: "Path | None"
    _manager_log_file: "TextIO | None"
    _manager_log_stderr_enabled: bool
    _manager_log_sink_recent: dict[str, float]
    _manager_log_sink_recent_window_s: float
    _manager_log_sink_recent_max: int
    # ``Manager.__init__`` allocates ``deque(maxlen=...)`` at line ~701
    # — NOT a plain list. Annotating as ``deque`` keeps mypy honest if
    # a future caller reaches for list-only ops (slicing, indexing).
    _log_history: "deque[Json]"

    def _open_manager_log_sink_file(self) -> None:
        path = self._manager_log_file_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._manager_log_file = path.open("a", encoding="utf-8", buffering=1)
        except Exception as exc:
            self._manager_log_file = None
            if self._manager_log_stderr_enabled:
                try:
                    sys.stderr.write(
                        f"[manager][warning] MANAGER_LOG_FILE open failed: {path} ({exc})\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass

    def _close_manager_log_sink_file(self) -> None:
        handle = self._manager_log_file
        self._manager_log_file = None
        if handle is None:
            return
        try:
            handle.close()
        except Exception:
            pass

    def _manager_log_sink_event(
        self, topic: str, payload: Json
    ) -> tuple[str, str, str, str | None, str]:
        if topic == "manager.log":
            severity = normalize_log_severity(payload.get("severity"), default="info")
            line_topic = self._normalize_topic(
                str(payload.get("topic") or "manager.log")
            )
        elif topic.startswith("manager.") and topic.endswith("_error"):
            severity = "error"
            line_topic = self._normalize_topic(topic)
        else:
            raise ValueError("not sink-eligible")

        source_kind = normalize_id(payload.get("source_kind")) or "manager"
        source_id = normalize_id(payload.get("source_id"))
        message = payload.get("message")
        if message is None:
            message = payload.get("error")
        text = str(message or "").strip()
        if not text:
            payload_json = payload.get("payload_json")
            if isinstance(payload_json, str) and payload_json.strip():
                text = payload_json.strip()
            else:
                text = self._safe_json(payload)
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        return severity, line_topic, source_kind, source_id, text

    def _manager_log_sink_is_duplicate(self, fingerprint: str) -> bool:
        now = time.monotonic()
        recent = self._manager_log_sink_recent
        window_s = self._manager_log_sink_recent_window_s
        max_items = self._manager_log_sink_recent_max
        prev = recent.get(fingerprint)
        if prev is not None and (now - prev) <= window_s:
            return True
        recent[fingerprint] = now
        if len(recent) > max_items:
            cutoff = now - window_s
            drop = [key for key, ts in recent.items() if ts < cutoff]
            for key in drop:
                recent.pop(key, None)
            if len(recent) > max_items:
                overflow = len(recent) - max_items
                for key in list(recent.keys())[:overflow]:
                    recent.pop(key, None)
        return False

    def _emit_log(
        self,
        *,
        severity: Any,
        topic: Any,
        message: Any,
        source_kind: Any = "manager",
        source_id: Any = None,
        device_id: Any = None,
        process_id: Any = None,
        stream: Any = "event",
        payload: Json | None = None,
        payload_json: Any = None,
        ts: Any = None,
    ) -> Json:
        sev = normalize_log_severity(severity, default="info")
        normalized_topic = self._normalize_topic(str(topic or "manager.log"))
        source_kind_text = normalize_id(source_kind) or "manager"
        source_id_text = normalize_id(source_id)
        device_id_text = normalize_id(device_id)
        process_id_text = normalize_id(process_id)
        stream_text = normalize_id(stream) or "event"
        msg_text = str(message or "")

        if payload_json is None:
            payload_json_text = (
                self._safe_json(payload) if payload is not None else ""
            )
        else:
            payload_json_text = str(payload_json)
            if len(payload_json_text) > 4000:
                payload_json_text = payload_json_text[:4000] + "...(truncated)"

        entry: Json = {
            "version": 1,
            "severity": sev,
            "topic": normalized_topic,
            "source_kind": source_kind_text,
            "source_id": source_id_text,
            "device_id": device_id_text,
            "process_id": process_id_text,
            "stream": stream_text,
            "message": msg_text,
            "payload_json": payload_json_text,
            "ts": normalize_log_ts(ts),
        }
        self._log_history.append(entry)
        self._publish_manager_event("manager.log", entry)
        return entry

    def _emit_log_from_payload(
        self, payload: Json, *, default_topic: str = "manager.log"
    ) -> Json:
        source_kind = payload.get("source_kind")
        source_id = payload.get("source_id")
        device_id = payload.get("device_id")
        process_id = payload.get("process_id")

        if source_kind is None:
            if process_id is not None:
                source_kind = "process"
                if source_id is None:
                    source_id = process_id
            elif device_id is not None:
                source_kind = "driver"
                if source_id is None:
                    source_id = device_id
            else:
                source_kind = "manager"

        message = payload.get("message")
        if message is None:
            message = payload.get("error", "")

        raw_payload: Json | None = None
        payload_value = payload.get("payload")
        if isinstance(payload_value, dict):
            raw_payload = payload_value

        return self._emit_log(
            severity=payload.get("severity", "info"),
            topic=payload.get("topic", default_topic),
            message=message,
            source_kind=source_kind,
            source_id=source_id,
            device_id=device_id,
            process_id=process_id,
            stream=payload.get("stream", "event"),
            payload=raw_payload,
            payload_json=payload.get("payload_json"),
            ts=payload.get("ts"),
        )

    def _log_tail(self, params: Json) -> Json:
        limit = parse_log_tail_limit(params.get("limit", 200))
        filters = log_tail_filters(params)

        filtered: list[Json] = []
        for entry in list(self._log_history):
            if log_tail_entry_matches(entry, filters=filters):
                filtered.append(entry)

        total = len(filtered)
        if total > limit:
            filtered = filtered[-limit:]

        latest_t_mono: float | None = None
        if filtered:
            latest_t_mono = log_tail_entry_t_mono(filtered[-1])

        return {
            "entries": filtered,
            "count": len(filtered),
            "total_matched": total,
            "limit": limit,
            "latest_t_mono": latest_t_mono,
        }
