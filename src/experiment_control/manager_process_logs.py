from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager_protocol import ManagerProtocol

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

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
_SAFE_LOG_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_log_id(raw: str) -> str:
    text = _SAFE_LOG_ID_RE.sub("_", str(raw or "").strip()).strip("._-")
    return text or "unknown"


def _rotate_log_file(path: Path, *, max_bytes: int, backups: int) -> None:
    if max_bytes <= 0 or backups <= 0 or not path.exists():
        return
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    for idx in range(backups - 1, 0, -1):
        src = path.with_name(f"{path.name}.{idx}")
        dst = path.with_name(f"{path.name}.{idx + 1}")
        if src.exists():
            try:
                if dst.exists():
                    dst.unlink()
                src.replace(dst)
            except OSError:
                pass
    first = path.with_name(f"{path.name}.1")
    try:
        if first.exists():
            first.unlink()
        path.replace(first)
    except OSError:
        pass


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


class ProcessLogsMixin(_MixinBase):
    """Mixin providing supervisor-log capture, queue, and emit.

    Phase 8.2.10: migrated ``supervisor_log_path``,
    ``append_supervisor_jsonl``, ``append_supervisor_marker``,
    ``start_child_log_readers``, ``queue_supervisor_log``,
    ``supervisor_infer_severity``, ``emit_supervisor_item``,
    ``flush_stale_supervisor_blocks``, ``prune_supervisor_log_threads``,
    and ``drain_supervisor_logs`` from module-level helpers to mixin
    methods. Pure utilities (``_safe_log_id``, ``_rotate_log_file``,
    ``supervisor_key``, ``supervisor_block_start``,
    ``supervisor_block_continuation``) stay at module level.

    Five module-level forwarders are kept below — direct-import tests
    (``tests.test_group_f_hardening``, ``tests.test_process_diagnostics``)
    use the bare module-attribute names.
    """

    # Owned-state attributes (concrete types declared on Manager).
    _supervisor_log_dir: str
    _supervisor_log_max_bytes: int
    _supervisor_log_backups: int
    _supervisor_log_threads: dict[tuple[str, str, int, str], threading.Thread]
    _supervisor_log_queue: "queue.Queue[Json]"
    _supervisor_log_dropped: int
    _supervisor_log_dropped_lock: threading.Lock
    _supervisor_pending_blocks: dict[tuple[str, str, int, str], Json]

    def _supervisor_log_path(
        self,
        *,
        source_kind: str,
        source_id: str,
        pid: int,
        stream: str,
    ) -> Path:
        directory = Path(self._supervisor_log_dir)
        filename = (
            f"{_safe_log_id(source_kind)}-"
            f"{_safe_log_id(source_id)}-"
            f"{int(pid)}.{_safe_log_id(stream)}.jsonl"
        )
        return directory / filename

    def _append_supervisor_jsonl(self, item: Json) -> None:
        path_raw = item.get("log_path")
        if not path_raw:
            return
        path = Path(str(path_raw))
        max_bytes = int(self._supervisor_log_max_bytes)
        backups = int(self._supervisor_log_backups)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log_file(path, max_bytes=max_bytes, backups=backups)
            entry = {
                "t_wall": time.time(),
                "t_mono": time.monotonic(),
                "source_kind": item.get("source_kind"),
                "source_id": item.get("source_id"),
                "device_id": item.get("device_id"),
                "process_id": item.get("process_id"),
                "pid": item.get("pid"),
                "stream": item.get("stream"),
                "message": item.get("message"),
            }
            if item.get("event") is not None:
                entry["event"] = item.get("event")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _append_supervisor_marker(
        self,
        *,
        log_path: Path,
        source_kind: str,
        source_id: str,
        stream: str,
        pid: int,
        event: str,
        device_id: str | None,
        process_id: str | None,
        message: str | None = None,
    ) -> None:
        self._append_supervisor_jsonl(
            {
                "source_kind": source_kind,
                "source_id": source_id,
                "stream": stream,
                "pid": pid,
                "device_id": device_id,
                "process_id": process_id,
                "message": message or event,
                "event": event,
                "log_path": str(log_path),
            },
        )

    def _start_child_log_readers(
        self,
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
            existing = self._supervisor_log_threads.get(key)
            if existing is not None and existing.is_alive():
                continue
            log_path = self._supervisor_log_path(
                source_kind=source_kind,
                source_id=source_id,
                pid=pid,
                stream=stream,
            )
            try:
                handle = self._supervisor_handle_for(
                    source_kind=source_kind,
                    source_id=source_id,
                )
                if handle is not None:
                    if stream == "stdout":
                        handle.stdout_log_path = str(log_path)
                    elif stream == "stderr":
                        handle.stderr_log_path = str(log_path)
            except Exception:
                pass

            self._append_supervisor_marker(
                log_path=log_path,
                source_kind=source_kind,
                source_id=source_id,
                stream=stream,
                pid=pid,
                event="stream_opened",
                device_id=device_id,
                process_id=process_id,
            )

            # Bind ``self`` into the reader closure so it can call back
            # into ``_queue_supervisor_log`` / ``_append_supervisor_marker``
            # without re-resolving the manager on every line. Captured by
            # the inner def below.
            mixin = self

            def _reader(
                *,
                pipe_obj: Any,
                stream_name: str,
                source_kind_name: str,
                source_id_name: str,
                pid_value: int,
                device_id_value: str | None,
                process_id_value: str | None,
                log_path_value: Path,
            ) -> None:
                try:
                    for line in iter(pipe_obj.readline, ""):
                        text = str(line).rstrip("\r\n")
                        if not text:
                            continue
                        mixin._queue_supervisor_log(
                            {
                                "source_kind": source_kind_name,
                                "source_id": source_id_name,
                                "stream": stream_name,
                                "pid": pid_value,
                                "device_id": device_id_value,
                                "process_id": process_id_value,
                                "message": text,
                                "log_path": str(log_path_value),
                            },
                        )
                except Exception as exc:
                    mixin._queue_supervisor_log(
                        {
                            "source_kind": source_kind_name,
                            "source_id": source_id_name,
                            "stream": stream_name,
                            "pid": pid_value,
                            "device_id": device_id_value,
                            "process_id": process_id_value,
                            "message": f"log stream read failed: {exc}",
                            "reader_error": True,
                            "event": "stream_reader_error",
                            "log_path": str(log_path_value),
                        },
                    )
                finally:
                    mixin._append_supervisor_marker(
                        log_path=log_path_value,
                        source_kind=source_kind_name,
                        source_id=source_id_name,
                        stream=stream_name,
                        pid=pid_value,
                        event="stream_closed",
                        device_id=device_id_value,
                        process_id=process_id_value,
                    )
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
                    "log_path_value": log_path,
                },
                daemon=True,
                name=f"ec-log-{source_kind}-{source_id}-{pid}-{stream}",
            )
            self._supervisor_log_threads[key] = thread
            thread.start()

    def _queue_supervisor_log(self, item: Json) -> None:
        try:
            self._append_supervisor_jsonl(item)
        except Exception:
            pass
        try:
            self._record_supervisor_raw_log(item)
        except Exception:
            pass
        try:
            self._supervisor_log_queue.put_nowait(item)
        except queue.Full:
            # Increment under the manager-owned lock; this method runs
            # on per-log-stream reader threads while
            # ``_drain_supervisor_logs`` snapshot+resets on the main
            # thread.
            with self._supervisor_log_dropped_lock:
                self._supervisor_log_dropped += 1

    def _supervisor_infer_severity(
        self,
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
            return self._normalize_log_severity(match.group(1))

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

    def _emit_supervisor_item(self, item: Json) -> None:
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
        severity = self._supervisor_infer_severity(
            stream=stream,
            message=message,
            reader_error=reader_error,
        )
        try:
            self._record_supervisor_emitted_log(item, severity=severity)
        except Exception:
            pass
        payload: Json = {}
        if pid_raw is not None:
            try:
                payload["pid"] = int(pid_raw)
            except Exception:
                pass
        self._emit_log(
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

    def _flush_stale_supervisor_blocks(
        self,
        *,
        max_age_s: float = 0.25,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        stale_keys: list[tuple[str, str, int, str]] = []
        for key, item in self._supervisor_pending_blocks.items():
            last_update_raw = item.get("last_update_mono", now)
            try:
                last_update = float(last_update_raw)
            except Exception:
                last_update = now
            if force or (now - last_update) >= max_age_s:
                stale_keys.append(key)
        for key in stale_keys:
            popped = self._supervisor_pending_blocks.pop(key, None)
            if isinstance(popped, dict):
                popped.pop("last_update_mono", None)
                self._emit_supervisor_item(popped)

    def _prune_supervisor_log_threads(self) -> None:
        stale = [
            key
            for key, thread in self._supervisor_log_threads.items()
            if not thread.is_alive()
        ]
        for key in stale:
            self._supervisor_log_threads.pop(key, None)

    def _drain_supervisor_logs(self, *, max_items: int = 250) -> None:
        # Snapshot + reset atomically so concurrent reader-thread bumps
        # during the drain aren't lost.
        with self._supervisor_log_dropped_lock:
            dropped = int(self._supervisor_log_dropped)
            self._supervisor_log_dropped = 0
        if dropped > 0:
            self._emit_log(
                severity="warning",
                topic="manager.supervisor.drop",
                message=f"Dropped {dropped} supervisor log lines",
                source_kind="manager",
                source_id="manager",
                stream="event",
                payload={"dropped": dropped},
            )
        self._flush_stale_supervisor_blocks()
        for _ in range(max_items):
            try:
                item = self._supervisor_log_queue.get_nowait()
            except queue.Empty:
                break
            if not isinstance(item, dict):
                continue
            message = str(item.get("message", "") or "")
            if not message:
                continue
            key = supervisor_key(item)
            pending = self._supervisor_pending_blocks.get(key)
            if pending is not None:
                if supervisor_block_continuation(message):
                    pending_message = str(pending.get("message", "") or "")
                    pending["message"] = (
                        f"{pending_message}\n{message}"
                        if pending_message
                        else message
                    )
                    pending["last_update_mono"] = time.monotonic()
                    continue
                pending.pop("last_update_mono", None)
                self._emit_supervisor_item(pending)
                self._supervisor_pending_blocks.pop(key, None)

            if supervisor_block_start(message):
                pending_item = dict(item)
                pending_item["message"] = message
                pending_item["last_update_mono"] = time.monotonic()
                self._supervisor_pending_blocks[key] = pending_item
                continue

            self._emit_supervisor_item(item)
        self._flush_stale_supervisor_blocks()
        self._prune_supervisor_log_threads()


# --- Backward-compat module-level forwarders -------------------------
# ``tests.test_group_f_hardening`` and ``tests.test_process_diagnostics``
# import these names directly and call them against real or stubbed
# managers. The bodies live on :class:`ProcessLogsMixin`; these
# trampolines delegate.

def supervisor_log_path(
    manager: Any,
    *,
    source_kind: str,
    source_id: str,
    pid: int,
    stream: str,
) -> Path:
    return ProcessLogsMixin._supervisor_log_path(
        manager,
        source_kind=source_kind,
        source_id=source_id,
        pid=pid,
        stream=stream,
    )


def append_supervisor_jsonl(manager: Any, item: Json) -> None:
    ProcessLogsMixin._append_supervisor_jsonl(manager, item)


def append_supervisor_marker(
    manager: Any,
    *,
    log_path: Path,
    source_kind: str,
    source_id: str,
    stream: str,
    pid: int,
    event: str,
    device_id: str | None,
    process_id: str | None,
    message: str | None = None,
) -> None:
    ProcessLogsMixin._append_supervisor_marker(
        manager,
        log_path=log_path,
        source_kind=source_kind,
        source_id=source_id,
        stream=stream,
        pid=pid,
        event=event,
        device_id=device_id,
        process_id=process_id,
        message=message,
    )


def queue_supervisor_log(manager: Any, item: Json) -> None:
    ProcessLogsMixin._queue_supervisor_log(manager, item)


def drain_supervisor_logs(manager: Any, *, max_items: int = 250) -> None:
    ProcessLogsMixin._drain_supervisor_logs(manager, max_items=max_items)
