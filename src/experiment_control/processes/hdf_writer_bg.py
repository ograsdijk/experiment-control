from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import Any

Json = dict[str, Any]


@dataclass(frozen=True)
class _FlushBatch:
    """Snapshot of pending main-loop state ready to be written to HDF5.

    Shape of `stream_batches` matches `self._stream_buffers` — a dict
    keyed by `(device_id, stream)` whose values are dicts with `data`,
    `seq`, `t0_mono_ns`, `t0_wall_ns`, `context_id` lists. This keeps
    the existing `_write_stream_buffers_batch` signature stable.

    `_dropped_local` / `_dropped_events` are cumulative counters shared
    between threads and not snapshotted into the batch — main loop
    increments them in `_buffer_append` / `_buffer_event`, bg thread
    reads them in `_flush_active_file`. CPython GIL makes the int read
    atomic; the worst case is a momentarily stale attrs value that
    catches up on the next flush.
    """

    buffered_rows: list[Json] = field(default_factory=list)
    event_rows: list[tuple[str, Json]] = field(default_factory=list)
    stream_batches: dict[tuple[str, str], dict[str, list[Any]]] = field(
        default_factory=dict
    )
    pending_stream_metadata: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    force_flush: bool = False


@dataclass
class _BgRequest:
    """Base for synchronous RPC requests routed through the bg thread."""

    response: "queue.Queue[Any]" = field(default_factory=lambda: queue.Queue(maxsize=1))


@dataclass
class _RotateRequest(_BgRequest):
    filename: str | None = None
    disabled_devices: set[str] | None = None
    measurement_profile: str | None = None
    measurement_values: object = None


@dataclass
class _StartWritingRequest(_BgRequest):
    filename: str | None = None
    disabled_devices: set[str] | None = None
    measurement_profile: str | None = None
    measurement_values: object = None


@dataclass
class _StopWritingRequest(_BgRequest):
    pass


@dataclass
class _MeasurementNoteRequest(_BgRequest):
    author: str = ""
    kind: str = ""
    message: str = ""
    payload_json: str = ""


@dataclass
class _DevicesToggleRequest(_BgRequest):
    disabled: set[str] = field(default_factory=set)


class _BgSentinel:
    """Marker put on the bg queue to request a clean thread exit."""

    __slots__ = ()


_BG_SENTINEL = _BgSentinel()
