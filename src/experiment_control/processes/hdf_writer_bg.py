from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
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
    # Per-stream write metadata captured at snapshot time so the bg write is
    # fully self-contained: it never reads the main-owned `_stream_schema` /
    # `_stream_active_session` maps during a (slow) write. Keyed by
    # `(device_id, stream)` -> {"dtype": np.dtype, "shape": tuple, "session": int}.
    stream_meta: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    # Context-table rows observed on the main drain thread and deferred to the
    # bg thread for the actual h5py write. Each entry:
    # {"context_id": int, "fields": dict, "ts_wall_ns": int, "ts_mono_ns": int}.
    context_rows: list[dict[str, Any]] = field(default_factory=list)
    force_flush: bool = False


@dataclass
class _BgRequest:
    """Base for synchronous RPC requests routed through the bg thread."""

    response: "queue.Queue[Any]" = field(default_factory=lambda: queue.Queue(maxsize=1))


@dataclass
class _FileOpRequest:
    """A file close/rotate executed on the bg thread instead of the RPC thread.

    Closing a large file can take tens of seconds, far longer than any RPC
    timeout. The RPC handler validates, queues one of these behind every
    already-queued `_FlushBatch` (so FIFO order lands them in the old file),
    and replies immediately. While it is in flight the main loop runs in hold
    mode: it keeps the SUB socket drained but parks raw messages instead of
    dispatching them, so the op body has the same exclusive access to
    per-file state it had when it ran synchronously on the main thread.

    The bg thread fills `outcome` and sets `done`; the main loop then
    publishes the outcome and replays the held messages.
    """

    op: str = ""
    file: str | None = None
    started_wall: float = 0.0
    started_mono: float = 0.0
    submitted: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    outcome: Json | None = None


@dataclass
class _StopWritingRequest(_FileOpRequest):
    op: str = "stop"


@dataclass
class _RotateRequest(_FileOpRequest):
    op: str = "rotate"
    new_file: str | None = None
    path: Path | None = None
    disabled_devices: set[str] | None = None
    measurement_meta: Json | None = None


@dataclass
class _StartWritingRequest(_BgRequest):
    filename: str | None = None
    disabled_devices: set[str] | None = None
    measurement_profile: str | None = None
    measurement_values: object = None


@dataclass
class _CaptureSequencerYamlRequest(_BgRequest):
    """Fire-and-forget request to snapshot the loaded sequencer YAML off the
    main drain loop. ``measurement_id`` is a staleness token: if the active
    file changes before the bg worker writes the snapshot, the result is
    discarded rather than associated with the wrong run. ``run_id`` is the
    sequencer run id from the triggering ``start`` event (None if absent);
    the snapshot is linked to it via a ``yaml_snapshot`` sequencer event.
    """

    process_id: str = "sequencer"
    measurement_id: str = ""
    run_id: int | None = None


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
