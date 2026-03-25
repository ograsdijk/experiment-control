from __future__ import annotations

import os
import time
from typing import Any

from .utils.process_lifecycle import cleanup_orphan_children

Json = dict[str, Any]


def _enum_member(current: Any, name: str) -> Any:
    enum_cls = current if isinstance(current, type) else type(current)
    return getattr(enum_cls, name, name)


def recent_process_logs(manager: Any, *, process_id: str, limit: int = 6) -> list[str]:
    pid = process_id.strip()
    if not pid or limit <= 0:
        return []
    out: list[str] = []
    for entry in reversed(manager._log_history):
        if not isinstance(entry, dict):
            continue
        source_kind = str(entry.get("source_kind", "") or "").strip().lower()
        if source_kind != "process":
            continue
        source_id = manager._normalize_id(entry.get("source_id"))
        entry_process_id = manager._normalize_id(entry.get("process_id"))
        if source_id != pid and entry_process_id != pid:
            continue
        message = str(entry.get("message", "") or "").strip()
        if not message:
            continue
        if len(message) > 220:
            message = message[:217] + "..."
        severity = manager._normalize_log_severity(entry.get("severity"))
        stream = str(entry.get("stream", "event") or "event").strip()
        out.append(f"{severity}/{stream}: {message}")
        if len(out) >= limit:
            break
    out.reverse()
    return out


def format_router_startup_failure(manager: Any, handle: Any) -> str:
    process_id = handle.spec.process_id
    exit_code = handle.last_exit_code
    if exit_code is None and handle.popen is not None:
        try:
            polled = handle.popen.poll()
            if polled is not None:
                exit_code = int(polled)
        except Exception:
            exit_code = None

    details: list[str] = []
    if exit_code is not None:
        details.append(f"exit_code={exit_code}")
    if handle.last_error:
        details.append(f"last_error={handle.last_error}")

    logs = manager._recent_process_logs(process_id=process_id, limit=6)
    if logs:
        details.append("recent_logs=" + " | ".join(logs))

    if not details:
        return f"{process_id} exited during startup"
    return f"{process_id} exited during startup ({'; '.join(details)})"


def cleanup_orphans_summary(
    manager: Any,
    *,
    dry_run: bool,
    stale_only: bool = True,
    timeout_s: float = 2.0,
) -> Json:
    summary = cleanup_orphan_children(
        instance_id=manager._instance_id,
        exclude_pids={os.getpid()},
        current_parent_pid=os.getpid(),
        timeout_s=float(timeout_s),
        stale_only=bool(stale_only),
        dry_run=bool(dry_run),
    )
    return {
        "instance_id": manager._instance_id,
        "dry_run": bool(summary.get("dry_run", dry_run)),
        "stale_only": bool(summary.get("stale_only", stale_only)),
        "matched": int(summary.get("matched", 0) or 0),
        "terminated": list(summary.get("terminated", [])),
        "failed": list(summary.get("failed", [])),
        "skipped_live_parent": list(summary.get("skipped_live_parent", [])),
        "candidates": list(summary.get("candidates", [])),
    }


def record_orphan_cleanup(manager: Any, *, source: str, summary: Json) -> None:
    manager._last_orphan_cleanup = {
        "source": str(source),
        "ts": {
            "t_wall": float(time.time()),
            "t_mono": float(time.monotonic()),
        },
        "result": summary,
    }


def is_endpoint_collision_process_start_failure(handle: Any) -> bool:
    err = str(handle.last_error or "").lower()
    return "already in use" in err or "bind failed" in err


def maybe_recover_process_start_collision(manager: Any, handle: Any) -> bool:
    if str(handle.state) != "STARTING":
        return False
    if handle.startup_collision_retry_done:
        return False
    if not is_endpoint_collision_process_start_failure(handle):
        recent = " ".join(
            manager._recent_process_logs(process_id=handle.spec.process_id, limit=8)
        ).lower()
        markers = (
            "already in use",
            "bind failed",
            "endpoint is likely already in use",
            "address already in use",
        )
        if not any(marker in recent for marker in markers):
            return False
    handle.startup_collision_retry_done = True
    summary = manager._cleanup_orphans_summary(dry_run=False, stale_only=True)
    manager._record_orphan_cleanup(
        source="startup_collision_recovery",
        summary=summary,
    )
    manager._emit_log(
        severity="warning",
        topic="manager.process.collision_recover",
        message=(
            f"startup collision cleanup for {handle.spec.process_id}: "
            f"matched={summary.get('matched', 0)} "
            f"terminated={len(summary.get('terminated', []))} "
            f"failed={len(summary.get('failed', []))}"
        ),
        source_kind="process",
        source_id=handle.spec.process_id,
        process_id=handle.spec.process_id,
        stream="event",
        payload=summary,
    )
    manager._publish_manager_event(
        "manager.process.collision_recover",
        {
            "process_id": handle.spec.process_id,
            "summary": summary,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        },
    )
    try:
        manager._start_process_handle(handle, reset_collision_retry=False)
        return True
    except Exception as exc:
        handle.state = _enum_member(handle.state, "FAILED")
        handle.last_error = f"collision cleanup retry failed: {exc}"
        manager._publish_process_event("manager.process.failed", handle)
        return False
