from __future__ import annotations

import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Json = dict[str, Any]


@dataclass(frozen=True)
class CommandJournalSettings:
    path: Path
    queue_max: int = 10_000
    batch_size: int = 200
    flush_interval_ms: int = 200
    retention_max_rows: int | None = 1_000_000
    retention_max_age_days: float | None = None
    prune_interval_s: float = 60.0
    prune_chunk_rows: int = 1_000


class CommandJournal:
    _INSERT_SQL = """
        INSERT INTO command_journal (
            t_wall,
            t_mono,
            instance_id,
            device_id,
            action,
            params_json,
            ok,
            status,
            error_json,
            result_json,
            request_id,
            caller_process_id,
            source_kind,
            source_id,
            is_remote_target
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, *, settings: CommandJournalSettings, instance_id: str) -> None:
        self._settings = settings
        self._instance_id = str(instance_id or "").strip() or "unknown"
        self._queue: queue.Queue[Json] = queue.Queue(maxsize=int(settings.queue_max))
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._started = False
        self._lock = threading.Lock()

        self._written = 0
        self._dropped = 0
        self._write_errors = 0
        self._pruned_rows = 0
        self._close_incomplete_count = 0
        self._last_error: str | None = None

    @property
    def path(self) -> Path:
        return self._settings.path

    def start(self) -> None:
        if self._started:
            return
        self._settings.path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"ec-command-journal-{self._instance_id}",
            daemon=True,
        )
        self._thread.start()
        self._started = True

    def close(self, *, timeout_s: float = 2.0) -> None:
        if not self._started:
            return
        self._stop_evt.set()
        thread = self._thread
        timed_out = False
        if thread is not None:
            deadline = time.monotonic() + max(0.1, float(timeout_s))
            while thread.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                thread.join(timeout=min(0.1, remaining))
        if timed_out:
            queue_depth = int(self._queue.qsize())
            with self._lock:
                self._close_incomplete_count += 1
                self._last_error = (
                    "command journal close timed out "
                    f"(queue_depth={queue_depth}, timeout_s={float(timeout_s):.2f})"
                )
        elif thread is not None and not thread.is_alive():
            self._thread = None
        self._started = False

    def append(self, item: Json) -> None:
        if not self._started:
            return
        try:
            self._queue.put_nowait(dict(item))
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def status(self) -> Json:
        with self._lock:
            written = int(self._written)
            dropped = int(self._dropped)
            write_errors = int(self._write_errors)
            pruned_rows = int(self._pruned_rows)
            close_incomplete_count = int(self._close_incomplete_count)
            last_error = self._last_error
        thread = self._thread
        return {
            "enabled": True,
            "path": str(self._settings.path),
            "queue_depth": int(self._queue.qsize()),
            "queue_max": int(self._settings.queue_max),
            "batch_size": int(self._settings.batch_size),
            "flush_interval_ms": int(self._settings.flush_interval_ms),
            "retention": {
                "max_rows": self._settings.retention_max_rows,
                "max_age_days": self._settings.retention_max_age_days,
            },
            "written": written,
            "dropped": dropped,
            "write_errors": write_errors,
            "pruned_rows": pruned_rows,
            "close_incomplete_count": close_incomplete_count,
            "last_error": last_error,
            "thread_alive": bool(thread is not None and thread.is_alive()),
        }

    @staticmethod
    def _normalize_filter_set(raw: Any, *, field: str) -> set[str] | None:
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

    def tail(self, params: Json | None = None) -> Json:
        query_params = params or {}
        if not isinstance(query_params, dict):
            raise TypeError("params must be a dict")

        limit_raw = query_params.get("limit", 200)
        try:
            limit = int(limit_raw)
        except Exception as e:
            raise TypeError(f"limit must be int: {e}") from e
        limit = max(1, min(limit, 5_000))

        since_t_wall_raw = query_params.get("since_t_wall")
        since_t_wall: float | None = None
        if since_t_wall_raw is not None:
            try:
                since_t_wall = float(since_t_wall_raw)
            except Exception as e:
                raise TypeError(f"since_t_wall must be float: {e}") from e

        ok_filter: bool | None = None
        ok_raw = query_params.get("ok")
        if ok_raw is not None:
            if isinstance(ok_raw, bool):
                ok_filter = ok_raw
            elif isinstance(ok_raw, int) and ok_raw in {0, 1}:
                ok_filter = bool(ok_raw)
            else:
                raise TypeError("ok must be a bool")

        device_ids = self._normalize_filter_set(query_params.get("device_ids"), field="device_ids")
        actions = self._normalize_filter_set(query_params.get("actions"), field="actions")
        source_kind_set = self._normalize_filter_set(
            query_params.get("source_kind"), field="source_kind"
        )
        if source_kind_set is not None:
            source_kind_set = {item.lower() for item in source_kind_set}
        source_ids = self._normalize_filter_set(query_params.get("source_ids"), field="source_ids")

        where: list[str] = []
        sql_args: list[Any] = []

        if since_t_wall is not None:
            where.append("t_wall >= ?")
            sql_args.append(since_t_wall)
        if ok_filter is not None:
            where.append("ok = ?")
            sql_args.append(1 if ok_filter else 0)
        if device_ids:
            placeholders = ",".join("?" for _ in device_ids)
            where.append(f"device_id IN ({placeholders})")
            sql_args.extend(sorted(device_ids))
        if actions:
            placeholders = ",".join("?" for _ in actions)
            where.append(f"action IN ({placeholders})")
            sql_args.extend(sorted(actions))
        if source_kind_set:
            placeholders = ",".join("?" for _ in source_kind_set)
            where.append(f"LOWER(source_kind) IN ({placeholders})")
            sql_args.extend(sorted(source_kind_set))
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            where.append(f"source_id IN ({placeholders})")
            sql_args.extend(sorted(source_ids))

        where_sql = ""
        if where:
            where_sql = " WHERE " + " AND ".join(where)

        count_sql = "SELECT COUNT(*) FROM command_journal" + where_sql
        select_sql = (
            "SELECT id, t_wall, t_mono, instance_id, device_id, action, params_json, ok, status, "
            "error_json, result_json, request_id, caller_process_id, source_kind, source_id, is_remote_target "
            "FROM command_journal"
            + where_sql
            + " ORDER BY id DESC LIMIT ?"
        )
        select_args = list(sql_args)
        select_args.append(limit)

        conn = sqlite3.connect(str(self._settings.path), timeout=1.0)
        try:
            total_matched = int(conn.execute(count_sql, sql_args).fetchone()[0])  # type: ignore[index]
            rows = conn.execute(select_sql, select_args).fetchall()
        finally:
            conn.close()

        rows.reverse()
        entries: list[Json] = []
        for row in rows:
            (
                row_id,
                t_wall,
                t_mono,
                instance_id,
                device_id,
                action,
                params_json,
                ok_value,
                status,
                error_json,
                result_json,
                request_id,
                caller_process_id,
                source_kind,
                source_id,
                is_remote_target,
            ) = row
            entries.append(
                {
                    "id": int(row_id),
                    "t_wall": float(t_wall),
                    "t_mono": float(t_mono),
                    "instance_id": instance_id,
                    "device_id": device_id,
                    "action": action,
                    "params_json": params_json,
                    "ok": bool(ok_value),
                    "status": status,
                    "error_json": error_json,
                    "result_json": result_json,
                    "request_id": request_id,
                    "caller_process_id": caller_process_id,
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "is_remote_target": bool(is_remote_target),
                }
            )

        latest_id: int | None = None
        if entries:
            latest_id = int(entries[-1]["id"])

        return {
            "entries": entries,
            "count": len(entries),
            "total_matched": total_matched,
            "limit": limit,
            "latest_id": latest_id,
        }

    def _run(self) -> None:
        try:
            conn = sqlite3.connect(str(self._settings.path), timeout=1.0)
        except Exception as e:
            with self._lock:
                self._write_errors += 1
                self._last_error = str(e)
            return
        try:
            self._init_db(conn)
            flush_timeout_s = max(0.01, float(self._settings.flush_interval_ms) / 1000.0)
            next_prune = time.monotonic() + max(0.5, float(self._settings.prune_interval_s))
            while not self._stop_evt.is_set() or not self._queue.empty():
                timeout_s = 0.01 if self._stop_evt.is_set() else flush_timeout_s
                batch = self._dequeue_batch(timeout_s=timeout_s)
                if batch:
                    self._write_batch(conn, batch)

                now = time.monotonic()
                if now >= next_prune:
                    self._run_prune(conn)
                    next_prune = now + max(0.5, float(self._settings.prune_interval_s))
        finally:
            conn.close()

    def _dequeue_batch(self, *, timeout_s: float) -> list[Json]:
        batch: list[Json] = []
        max_items = max(1, int(self._settings.batch_size))
        try:
            first = self._queue.get(timeout=max(0.01, timeout_s))
        except queue.Empty:
            return batch
        batch.append(first)
        while len(batch) < max_items:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write_batch(self, conn: sqlite3.Connection, batch: list[Json]) -> None:
        try:
            conn.execute("BEGIN")
            conn.executemany(
                self._INSERT_SQL,
                [self._row_to_tuple(item) for item in batch],
            )
            conn.execute("COMMIT")
            with self._lock:
                self._written += len(batch)
                self._last_error = None
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            with self._lock:
                self._write_errors += 1
                self._last_error = str(e)

    def _run_prune(self, conn: sqlite3.Connection) -> None:
        pruned = 0
        chunk = max(100, int(self._settings.prune_chunk_rows))

        max_age_days = self._settings.retention_max_age_days
        if max_age_days is not None:
            cutoff = time.time() - (float(max_age_days) * 86_400.0)
            while True:
                cur = conn.execute(
                    "DELETE FROM command_journal WHERE id IN ("
                    "SELECT id FROM command_journal WHERE t_wall < ? ORDER BY id LIMIT ?"
                    ")",
                    (cutoff, chunk),
                )
                deleted = int(cur.rowcount or 0)
                pruned += max(0, deleted)
                if deleted < chunk:
                    break

        max_rows = self._settings.retention_max_rows
        if max_rows is not None:
            cur = conn.execute("SELECT COUNT(*) FROM command_journal")
            total = int(cur.fetchone()[0])  # type: ignore[index]
            overflow = total - int(max_rows)
            while overflow > 0:
                step = min(chunk, overflow)
                cur = conn.execute(
                    "DELETE FROM command_journal WHERE id IN ("
                    "SELECT id FROM command_journal ORDER BY id LIMIT ?"
                    ")",
                    (step,),
                )
                deleted = int(cur.rowcount or 0)
                pruned += max(0, deleted)
                if deleted <= 0:
                    break
                overflow -= deleted

        if pruned > 0:
            try:
                conn.commit()
            except Exception:
                pass
            with self._lock:
                self._pruned_rows += pruned

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS command_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                t_wall REAL NOT NULL,
                t_mono REAL NOT NULL,
                instance_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                action TEXT NOT NULL,
                params_json TEXT NOT NULL,
                ok INTEGER NOT NULL,
                status TEXT,
                error_json TEXT,
                result_json TEXT,
                request_id TEXT,
                caller_process_id TEXT,
                source_kind TEXT,
                source_id TEXT,
                is_remote_target INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_command_journal_t_wall ON command_journal(t_wall)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_command_journal_device_action "
            "ON command_journal(device_id, action)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_command_journal_source "
            "ON command_journal(source_kind, source_id)"
        )

    def _row_to_tuple(self, row: Json) -> tuple[Any, ...]:
        t_wall_raw = row.get("t_wall", time.time())
        t_mono_raw = row.get("t_mono", time.monotonic())
        try:
            t_wall = float(t_wall_raw)
        except Exception:
            t_wall = time.time()
        try:
            t_mono = float(t_mono_raw)
        except Exception:
            t_mono = time.monotonic()

        return (
            t_wall,
            t_mono,
            str(row.get("instance_id", self._instance_id) or self._instance_id),
            str(row.get("device_id", "") or ""),
            str(row.get("action", "") or ""),
            str(row.get("params_json", "") or ""),
            1 if bool(row.get("ok")) else 0,
            row.get("status"),
            row.get("error_json"),
            row.get("result_json"),
            row.get("request_id"),
            row.get("caller_process_id"),
            row.get("source_kind"),
            row.get("source_id"),
            1 if bool(row.get("is_remote_target")) else 0,
        )
