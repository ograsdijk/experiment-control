from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zmq

from ..types import Timestamp
from ..utils.command_journal import CommandJournal, CommandJournalSettings
from ..utils.manager_network import derive_local_connect_endpoint
from .models import Liveness, TelemetrySignal

Json = dict[str, Any]


@dataclass
class ManagerSockets:
    ctx: zmq.Context
    registry_bind: str
    internal_rpc_bind: str
    external_rpc_bind: str
    external_pub_bind: str
    external_pub_connect_local: str
    registry_rep: zmq.Socket
    sub: zmq.Socket
    process_hb_sub: zmq.Socket
    process_data_sub: zmq.Socket
    internal_rpc: zmq.Socket
    internal_rpc_endpoint: str
    external_pub: zmq.Socket

    @classmethod
    def create(
        cls,
        *,
        ctx: zmq.Context,
        registry_bind: str,
        internal_rpc_bind: str,
        external_rpc_bind: str,
        external_pub_bind: str,
        external_pub_connect_local: str | None,
    ) -> "ManagerSockets":
        registry_rep = ctx.socket(zmq.REP)
        registry_rep.bind(registry_bind)
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        process_hb_sub = ctx.socket(zmq.SUB)
        process_hb_sub.setsockopt(zmq.SUBSCRIBE, b"")
        process_data_sub = ctx.socket(zmq.SUB)
        process_data_sub.setsockopt(zmq.SUBSCRIBE, b"")
        internal_rpc = ctx.socket(zmq.ROUTER)
        internal_rpc.bind(internal_rpc_bind)
        external_pub = ctx.socket(zmq.PUB)
        external_pub.bind(external_pub_bind)
        external_pub_connect = (
            str(external_pub_connect_local).strip()
            if isinstance(external_pub_connect_local, str)
            and str(external_pub_connect_local).strip()
            else derive_local_connect_endpoint(external_pub_bind, 6001)
        )
        return cls(
            ctx=ctx,
            registry_bind=registry_bind,
            internal_rpc_bind=internal_rpc_bind,
            external_rpc_bind=external_rpc_bind,
            external_pub_bind=external_pub_bind,
            external_pub_connect_local=external_pub_connect,
            registry_rep=registry_rep,
            sub=sub,
            process_hb_sub=process_hb_sub,
            process_data_sub=process_data_sub,
            internal_rpc=internal_rpc,
            internal_rpc_endpoint=internal_rpc.getsockopt_string(zmq.LAST_ENDPOINT),
            external_pub=external_pub,
        )

    def bind_to_manager(self, manager: Any) -> None:
        manager._ctx = self.ctx
        manager._registry_bind = self.registry_bind
        manager._registry_rep = self.registry_rep
        manager._sub = self.sub
        manager._process_hb_sub = self.process_hb_sub
        manager._process_data_sub = self.process_data_sub
        manager._internal_rpc_bind = self.internal_rpc_bind
        manager._internal_rpc = self.internal_rpc
        manager._internal_rpc_endpoint = self.internal_rpc_endpoint
        manager._external_rpc_bind = self.external_rpc_bind
        manager._external_pub_bind = self.external_pub_bind
        manager._external_pub_connect_local = self.external_pub_connect_local
        manager._external_pub = self.external_pub


@dataclass
class ManagerCaches:
    telemetry_latest: dict[str, dict[str, tuple[Timestamp, TelemetrySignal]]] = field(
        default_factory=dict
    )
    telemetry_last_bundle_ts: dict[str, Timestamp] = field(default_factory=dict)
    telemetry_device_order: dict[str, None] = field(default_factory=dict)
    latest_chunk_desc: dict[str, dict[str, Json]] = field(default_factory=dict)
    chunk_device_order: dict[str, None] = field(default_factory=dict)
    last_liveness: dict[str, Liveness] = field(default_factory=dict)
    process_rss_cache: dict[int, tuple[float, int | None]] = field(default_factory=dict)
    runtime_device_metadata_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_stream_metadata_overrides: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    runtime_metadata_revision: dict[str, int] = field(default_factory=dict)

    def bind_to_manager(self, manager: Any) -> None:
        manager._telemetry_latest = self.telemetry_latest
        manager._telemetry_last_bundle_ts = self.telemetry_last_bundle_ts
        manager._telemetry_device_order = self.telemetry_device_order
        manager._latest_chunk_desc = self.latest_chunk_desc
        manager._chunk_device_order = self.chunk_device_order
        manager._last_liveness = self.last_liveness
        manager._process_rss_cache = self.process_rss_cache
        manager._runtime_device_metadata_overrides = self.runtime_device_metadata_overrides
        manager._runtime_stream_metadata_overrides = self.runtime_stream_metadata_overrides
        manager._runtime_metadata_revision = self.runtime_metadata_revision


@dataclass
class ManagerJournal:
    enabled: bool
    path: Path | None
    journal: CommandJournal | None = None
    start_error: str | None = None

    @classmethod
    def start_or_disabled(
        cls,
        *,
        enabled: bool,
        instance_id: str,
        path: str | Path | None,
        queue_max: int,
        batch_size: int,
        flush_interval_ms: int,
        retention_max_rows: int | None,
        retention_max_age_days: float | None,
    ) -> "ManagerJournal":
        path_raw = str(path).strip() if path is not None else ""
        journal_path = (
            Path(path_raw).expanduser()
            if path_raw
            else Path(".state") / instance_id / "command_journal.sqlite3"
        )
        if not enabled:
            return cls(enabled=False, path=journal_path)
        try:
            settings = CommandJournalSettings(
                path=journal_path,
                queue_max=int(queue_max),
                batch_size=int(batch_size),
                flush_interval_ms=int(flush_interval_ms),
                retention_max_rows=(
                    None if retention_max_rows is None else int(retention_max_rows)
                ),
                retention_max_age_days=(
                    None if retention_max_age_days is None else float(retention_max_age_days)
                ),
            )
            journal = CommandJournal(settings=settings, instance_id=instance_id)
            journal.start()
            return cls(enabled=True, path=journal_path, journal=journal)
        except Exception as exc:
            return cls(enabled=False, path=journal_path, start_error=str(exc))

    def bind_to_manager(self, manager: Any) -> None:
        manager._command_journal_enabled = self.enabled
        manager._command_journal_path = self.path
        manager._command_journal = self.journal
        manager._command_journal_start_error = self.start_error


@dataclass
class LifecycleExecutor:
    main_thread_id: int
    executor: ThreadPoolExecutor
    device_locks: dict[str, threading.Lock]
    reply_queue: queue.Queue[tuple[bytes, Json]]
    event_queue: queue.Queue[tuple[str, Json]]
    event_dropped: int
    event_dropped_lock: threading.Lock

    @classmethod
    def create(cls) -> "LifecycleExecutor":
        return cls(
            main_thread_id=threading.get_ident(),
            executor=ThreadPoolExecutor(max_workers=32, thread_name_prefix="mgr-lifecycle"),
            device_locks={},
            reply_queue=queue.Queue(),
            event_queue=queue.Queue(maxsize=10_000),
            event_dropped=0,
            event_dropped_lock=threading.Lock(),
        )

    def bind_to_manager(self, manager: Any) -> None:
        manager._main_thread_id = self.main_thread_id
        manager._lifecycle_executor = self.executor
        manager._lifecycle_device_locks = self.device_locks
        manager._lifecycle_reply_queue = self.reply_queue
        manager._lifecycle_event_queue = self.event_queue
        manager._lifecycle_event_dropped = self.event_dropped
        manager._lifecycle_event_dropped_lock = self.event_dropped_lock
