from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Iterator

from ..utils.process_lifecycle import cleanup_orphan_children

if TYPE_CHECKING:
    from collections import deque

    from ..manager_protocol import ManagerProtocol

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

Json = dict[str, Any]


def _enum_member(current: Any, name: str) -> Any:
    enum_cls = current if isinstance(current, type) else type(current)
    return getattr(enum_cls, name, name)


_HIGH_SEVERITY_LEVELS = frozenset({"error", "critical", "warning"})


def _coerce_ts_field(ts_obj: Any, key: str) -> float | None:
    if not isinstance(ts_obj, dict):
        return None
    raw = ts_obj.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_endpoint_collision_process_start_failure(handle: Any) -> bool:
    err = str(handle.last_error or "").lower()
    return "already in use" in err or "bind failed" in err


class ProcessRecoveryMixin(_MixinBase):
    """Mixin providing process-recovery + log-tail + orphan-cleanup helpers.

    Phase 8.2.9: migrated ``recent_process_logs``,
    ``recent_source_logs_structured``, ``recent_process_logs_structured``,
    ``format_router_startup_failure``, ``cleanup_orphans_summary``,
    ``record_orphan_cleanup``, ``maybe_recover_process_start_collision``,
    plus internal log-iterator helpers from module-level helpers to
    mixin methods. Pure helpers ``_enum_member``, ``_coerce_ts_field``,
    ``is_endpoint_collision_process_start_failure`` stay at module level.
    """

    # Owned-state attributes (concrete types declared on Manager).
    _last_orphan_cleanup: Json | None
    _log_history: "deque[Json]"
    _instance_id: str

    def _iter_matching_source_log_entries(
        self, source_id: str, source_kind: str
    ) -> Iterator[dict[str, Any]]:
        """Yield raw log-history entries for ``source_id`` in reverse-chronological order."""
        target_kind = source_kind.strip().lower()
        for entry in reversed(self._log_history):
            if not isinstance(entry, dict):
                continue
            entry_kind = str(entry.get("source_kind", "") or "").strip().lower()
            if entry_kind != target_kind:
                continue
            entry_source_id = self._normalize_id(entry.get("source_id"))
            if entry_source_id == source_id:
                yield entry
                continue
            # Process logs may be tagged with a separate process_id;
            # drivers use device_id. Honor both indirections.
            if target_kind == "process":
                entry_process_id = self._normalize_id(entry.get("process_id"))
                if entry_process_id == source_id:
                    yield entry
            elif target_kind == "driver":
                entry_device_id = self._normalize_id(entry.get("device_id"))
                if entry_device_id == source_id:
                    yield entry

    def _iter_matching_process_log_entries(
        self, process_id: str
    ) -> Iterator[dict[str, Any]]:
        """Backwards-compatible wrapper for process-kind entries."""
        return self._iter_matching_source_log_entries(process_id, "process")

    def _recent_process_logs(self, *, process_id: str, limit: int = 6) -> list[str]:
        pid = process_id.strip()
        if not pid or limit <= 0:
            return []
        out: list[str] = []
        for entry in self._iter_matching_process_log_entries(pid):
            message = str(entry.get("message", "") or "").strip()
            if not message:
                continue
            if len(message) > 220:
                message = message[:217] + "..."
            severity = self._normalize_log_severity(entry.get("severity"))
            stream = str(entry.get("stream", "event") or "event").strip()
            out.append(f"{severity}/{stream}: {message}")
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def _recent_source_logs_structured(
        self,
        *,
        source_id: str,
        source_kind: str = "process",
        limit: int = 20,
        max_message_len: int = 400,
        prefer_high_severity: bool = True,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` recent log entries for a process or driver source.

        When ``prefer_high_severity`` is true the result is biased toward
        stderr/error lines: those entries are collected first up to the
        limit, then chronological info-level entries fill any remaining
        slots so plain crash output is still visible. Final list is in
        chronological order.
        """
        sid = source_id.strip()
        if not sid or limit <= 0:
            return []

        def _build_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
            message = str(entry.get("message", "") or "")
            if not message.strip():
                return None
            if len(message) > max_message_len:
                message = message[: max_message_len - 3] + "..."
            severity = self._normalize_log_severity(entry.get("severity"))
            stream = str(entry.get("stream", "event") or "event").strip()
            ts_obj = entry.get("ts")
            return {
                "severity": severity,
                "stream": stream,
                "message": message,
                "t_wall": _coerce_ts_field(ts_obj, "t_wall"),
                "t_mono": _coerce_ts_field(ts_obj, "t_mono"),
            }

        if not prefer_high_severity:
            out: list[dict[str, Any]] = []
            for entry in self._iter_matching_source_log_entries(sid, source_kind):
                built = _build_entry(entry)
                if built is None:
                    continue
                out.append(built)
                if len(out) >= limit:
                    break
            out.reverse()
            return out

        raw_cap = max(limit * 4, limit + 8)
        high: list[dict[str, Any]] = []
        low: list[dict[str, Any]] = []
        for entry in self._iter_matching_source_log_entries(sid, source_kind):
            built = _build_entry(entry)
            if built is None:
                continue
            is_high = (
                built["severity"] in _HIGH_SEVERITY_LEVELS
                or built["stream"] == "stderr"
            )
            target = high if is_high else low
            if len(high) + len(low) >= raw_cap:
                # Cap raw collection so we don't scan the whole deque
                # on huge limits.
                break
            target.append(built)
            if len(high) >= limit:
                break

        selected = high[:limit]
        if len(selected) < limit:
            remaining = limit - len(selected)
            selected = selected + low[:remaining]
        # Restore chronological order.
        selected.reverse()
        return selected

    def _recent_process_logs_structured(
        self,
        *,
        process_id: str,
        limit: int = 20,
        max_message_len: int = 400,
        prefer_high_severity: bool = True,
    ) -> list[dict[str, Any]]:
        """Backwards-compatible wrapper for process-kind sources."""
        return self._recent_source_logs_structured(
            source_id=process_id,
            source_kind="process",
            limit=limit,
            max_message_len=max_message_len,
            prefer_high_severity=prefer_high_severity,
        )

    def _format_router_startup_failure(self, handle: Any) -> str:
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

        logs = self._recent_process_logs(process_id=process_id, limit=6)
        if logs:
            details.append("recent_logs=" + " | ".join(logs))

        if not details:
            return f"{process_id} exited during startup"
        return f"{process_id} exited during startup ({'; '.join(details)})"

    def _cleanup_orphans_summary(
        self,
        *,
        dry_run: bool,
        stale_only: bool = True,
        timeout_s: float = 2.0,
    ) -> Json:
        summary = cleanup_orphan_children(
            instance_id=self._instance_id,
            exclude_pids={os.getpid()},
            current_parent_pid=os.getpid(),
            timeout_s=float(timeout_s),
            stale_only=bool(stale_only),
            dry_run=bool(dry_run),
        )
        return {
            "instance_id": self._instance_id,
            "dry_run": bool(summary.get("dry_run", dry_run)),
            "stale_only": bool(summary.get("stale_only", stale_only)),
            "matched": int(summary.get("matched", 0) or 0),
            "terminated": list(summary.get("terminated", [])),
            "failed": list(summary.get("failed", [])),
            "skipped_live_parent": list(summary.get("skipped_live_parent", [])),
            "candidates": list(summary.get("candidates", [])),
        }

    def _record_orphan_cleanup(self, *, source: str, summary: Json) -> None:
        self._last_orphan_cleanup = {
            "source": str(source),
            "ts": {
                "t_wall": float(time.time()),
                "t_mono": float(time.monotonic()),
            },
            "result": summary,
        }

    def _maybe_recover_process_start_collision(self, handle: Any) -> bool:
        if str(handle.state) != "STARTING":
            return False
        if handle.startup_collision_retry_done:
            return False
        if not is_endpoint_collision_process_start_failure(handle):
            recent = " ".join(
                self._recent_process_logs(
                    process_id=handle.spec.process_id, limit=8
                )
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
        summary = self._cleanup_orphans_summary(dry_run=False, stale_only=True)
        self._record_orphan_cleanup(
            source="startup_collision_recovery",
            summary=summary,
        )
        self._emit_log(
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
        self._publish_manager_event(
            "manager.process.collision_recover",
            {
                "process_id": handle.spec.process_id,
                "summary": summary,
                "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
            },
        )
        try:
            self._start_process_handle(handle, reset_collision_retry=False)
            return True
        except Exception as exc:
            handle.state = _enum_member(handle.state, "FAILED")
            handle.last_error = f"collision cleanup retry failed: {exc}"
            handle.last_error_kind = "collision_recover_failed"
            # Caller (``update_managed_process_exit_state``) is
            # responsible for the FAILED publish so we don't emit two
            # events back-to-back.
            return False
