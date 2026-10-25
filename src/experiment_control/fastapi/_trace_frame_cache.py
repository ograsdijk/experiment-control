"""Encoded trace frames shared across WebSocket clients.

Every `/ws/raw_stream` and `/ws/stream/{workspace}` socket decimates and
encodes each trace frame it sends. Clients that load the same UI profile ask
for the same frames with the same parameters, so without sharing the gateway
repeats identical work once per client on its single event loop.

A cache entry is the finished wire form of one frame for one parameter set.
Keys must identify the input exactly (frame seq plus acquisition timestamps,
so a restarted producer cannot match an old entry), and callers only use the
cache when the socket's trace aggregator passes frames through unchanged
(`rolling_window <= 1`), because averaged traces are per socket. Entries hold
only encoded frames, never the source payloads, so the cache stays small even
for large digitizer records.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Hashable, NamedTuple


class EncodedTraceFrame(NamedTuple):
    """One frame as sent: a JSON text message, then an optional binary body."""

    text: str
    body: bytes | None


class SharedTraceFrameCache:
    """Small LRU of encoded frames."""

    def __init__(self, maxsize: int = 128) -> None:
        self.maxsize = int(maxsize)
        self._entries: OrderedDict[Hashable, EncodedTraceFrame] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable) -> EncodedTraceFrame | None:
        frame = self._entries.get(key)
        if frame is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return frame

    def put(self, key: Hashable, frame: EncodedTraceFrame) -> None:
        self._entries[key] = frame
        self._entries.move_to_end(key)
        while len(self._entries) > self.maxsize:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses}
