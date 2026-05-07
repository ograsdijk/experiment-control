from __future__ import annotations

import queue
import re
import threading
import time
from typing import Any

Json = dict[str, Any]

_LOG_LEVEL_PREFIX_RE = re.compile(
    r"^\s*(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b",
    re.IGNORECASE,
)
_LOG_LEVEL_BRACKET_PREFIX_RE = re.compile(
    r"^\s*\[(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\]\b",
    re.IGNORECASE,
)
_LOG_LEVEL_INLINE_RE = re.compile(
    r"\blevel\s*=\s*(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b",
    re.IGNORECASE,
)
_LOG_LEVEL_TABLE_RE = re.compile(
    r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b",
    re.IGNORECASE,
)
_EXCEPTION_LINE_RE = re.compile(
    r"^(?:[A-Za-z_][\w\.]*Error|Exception|Traceback)\b",
)


def start_child_log_readers(
    manager: Any,
    *,
    popen: Any,
    source_kind: str,
    source_id: str,
    device_id: str | None,
    process_id: str | None,
) -> None:
    pid = int(getattr(popen, "pid", -1) or -1)
    if pid <= 0:
        return
    for stream in ("stdout", "stderr"):
        pipe = getattr(popen, stream, None)
        if pipe is None:
            continue
        key = (source_kind, source_id, pid, stream)
        existing = manager._supervisor_log_threads.get(key)
        if existing is not None and existing.is_alive():
            continue

        def _reader(
            *,
            pipe_obj: Any,
            stream_name: str,
            source_kind_name: str,
            source_id_name: str,
            pid_value: int,
            device_id_value: str | None,
            process_id_value: str | None,
        ) -> None:
            try:
                for line in iter(pipe_obj.readline, ""):
                    text = str(line).rstrip("\r\n")
                    if not text:
                        continue
                    queue_supervisor_log(
                        manager,
                        {
                            "source_kind": source_kind_name,
                            "source_id": source_id_name,
                            "stream": stream_name,
                            "pid": pid_value,
                            "device_id": device_id_value,
                            "process_id": process_id_value,
                            "message": text,
                        },
                    )
            except Exception as exc:
                queue_supervisor_log(
                    manager,
                    {
                        "source_kind": source_kind_name,
                        "source_id": source_id_name,
                        "stream": stream_name,
                        "pid": pid_value,
                        "device_id": device_id_value,
                        "process_id": process_id_value,
                        "message": f"log stream read failed: {exc}",
                        "reader_error": True,
                    },
                )
            finally:
                try:
                    pipe_obj.close()
                except Exception:
                    pass

        thread = threading.Thread(
            target=_reader,
            kwargs={
                "pipe_obj": pipe,
                "stream_name": stream,
                "source_kind_name": source_kind,
                "source_id_name": source_id,
                "pid_value": pid,
                "device_id_value": device_id,
                "process_id_value": process_id,
            },
            daemon=True,
            name=f"ec-log-{source_kind}-{source_id}-{pid}-{stream}",
        )
        manager._supervisor_log_threads[key] = thread
        thread.start()


def queue_supervisor_log(manager: Any, item: Json) -> None:
    try:
        manager._record_supervisor_raw_log(item)
    except Exception:
        pass
    try:
        manager._supervisor_log_queue.put_nowait(item)
    except queue.Full:
        manager._supervisor_log_dropped += 1


def supervisor_key(item: Json) -> tuple[str, str, int, str]:
    source_kind = str(item.get("source_kind", "manager") or "manager")
    source_id = str(item.get("source_id", "") or "")
    stream = str(item.get("stream", "stdout") or "stdout")
    pid = -1
    try:
        pid = int(item.get("pid", -1))
    except Exception:
        pid = -1
    return (source_kind, source_id, pid, stream)


def supervisor_block_start(message: str) -> bool:
    lower = message.strip().lower()
    return (
        lower.startswith("traceback (most recent call last):")
        or lower.startswith("call stack:")
        or lower.startswith("--- logging error ---")
    )


def supervisor_block_continuation(message: str) -> bool:
    if not message.strip():
        return False
    if message.startswith((" ", "\t")):
        return True
    lower = message.strip().lower()
    if lower.startswith(("traceback (most recent call last):", "call stack:")):
        return True
    if lower.startswith("--- logging error ---"):
        return True
    if lower.startswith(("message:", "arguments:")):
        return True
    if lower.startswith(
        (
            "during handling of the above exception",
            "the above exception was the direct cause of the following exception",
        )
    ):
        return True
    if _EXCEPTION_LINE_RE.match(message) is not None:
        return True
    return False


def supervisor_infer_severity(
    manager: Any,
    *,
    stream: str,
    message: str,
    reader_error: bool,
) -> str:
    if reader_error:
        return "error"

    match = _LOG_LEVEL_PREFIX_RE.match(message)
    if match is None:
        match = _LOG_LEVEL_BRACKET_PREFIX_RE.match(message)
    if match is None:
        match = _LOG_LEVEL_INLINE_RE.search(message)
    if match is None:
        match = _LOG_LEVEL_TABLE_RE.search(message)
    if match is not None:
        return manager._normalize_log_severity(match.group(1))

    lower = message.lower()
    if "traceback (most recent call last):" in lower:
        return "error"
    if _EXCEPTION_LINE_RE.match(message.strip()) is not None:
        return "error"
    if "fatal" in lower and "error" in lower:
        return "critical"
    if stream == "stderr":
        return "warning"
    return "info"


def emit_supervisor_item(manager: Any, item: Json) -> None:
    if not isinstance(item, dict):
        return
    stream = str(item.get("stream", "") or "stdout")
    reader_error = bool(item.get("reader_error", False))
    source_kind = str(item.get("source_kind", "manager") or "manager")
    source_id = str(item.get("source_id", "") or "")
    message = str(item.get("message", "") or "")
    if not message:
        return
    device_id_raw = item.get("device_id")
    process_id_raw = item.get("process_id")
    pid_raw = item.get("pid")
    severity = supervisor_infer_severity(
        manager,
        stream=stream,
        message=message,
        reader_error=reader_error,
    )
    try:
        manager._record_supervisor_emitted_log(item, severity=severity)
    except Exception:
        pass
    payload: Json = {}
    try:
        payload["pid"] = int(pid_raw)
    except Exception:
        pass
    manager._emit_log(
        severity=severity,
        topic=f"manager.supervisor.{source_kind}.{stream}",
        message=message,
        source_kind=source_kind,
        source_id=source_id or None,
        device_id=str(device_id_raw) if device_id_raw is not None else None,
        process_id=str(process_id_raw) if process_id_raw is not None else None,
        stream=stream,
        payload=payload if payload else None,
    )


def flush_stale_supervisor_blocks(
    manager: Any,
    *,
    max_age_s: float = 0.25,
    force: bool = False,
) -> None:
    now = time.monotonic()
    stale_keys: list[tuple[str, str, int, str]] = []
    for key, item in manager._supervisor_pending_blocks.items():
        last_update_raw = item.get("last_update_mono", now)
        try:
            last_update = float(last_update_raw)
        except Exception:
            last_update = now
        if force or (now - last_update) >= max_age_s:
            stale_keys.append(key)
    for key in stale_keys:
        item = manager._supervisor_pending_blocks.pop(key, None)
        if isinstance(item, dict):
            item.pop("last_update_mono", None)
            emit_supervisor_item(manager, item)


def prune_supervisor_log_threads(manager: Any) -> None:
    stale = [
        key
        for key, thread in manager._supervisor_log_threads.items()
        if not thread.is_alive()
    ]
    for key in stale:
        manager._supervisor_log_threads.pop(key, None)


def drain_supervisor_logs(manager: Any, *, max_items: int = 250) -> None:
    if manager._supervisor_log_dropped > 0:
        dropped = int(manager._supervisor_log_dropped)
        manager._supervisor_log_dropped = 0
        manager._emit_log(
            severity="warning",
            topic="manager.supervisor.drop",
            message=f"Dropped {dropped} supervisor log lines",
            source_kind="manager",
            source_id="manager",
            stream="event",
            payload={"dropped": dropped},
        )
    flush_stale_supervisor_blocks(manager)
    for _ in range(max_items):
        try:
            item = manager._supervisor_log_queue.get_nowait()
        except queue.Empty:
            break
        if not isinstance(item, dict):
            continue
        message = str(item.get("message", "") or "")
        if not message:
            continue
        key = supervisor_key(item)
        pending = manager._supervisor_pending_blocks.get(key)
        if pending is not None:
            if supervisor_block_continuation(message):
                pending_message = str(pending.get("message", "") or "")
                pending["message"] = (
                    f"{pending_message}\n{message}" if pending_message else message
                )
                pending["last_update_mono"] = time.monotonic()
                continue
            pending.pop("last_update_mono", None)
            emit_supervisor_item(manager, pending)
            manager._supervisor_pending_blocks.pop(key, None)

        if supervisor_block_start(message):
            pending_item = dict(item)
            pending_item["message"] = message
            pending_item["last_update_mono"] = time.monotonic()
            manager._supervisor_pending_blocks[key] = pending_item
            continue

        emit_supervisor_item(manager, item)
    flush_stale_supervisor_blocks(manager)
    prune_supervisor_log_threads(manager)
