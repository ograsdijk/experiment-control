from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import zmq

from ..utils.zmq_helpers import safe_json_loads
from .types import EventMessage


class EventSubscriber:
    def __init__(
        self,
        *,
        manager_pub: str,
        topics: list[str] | tuple[str, ...],
        rcvtimeo_ms: int = 200,
        ctx: zmq.Context | None = None,
    ) -> None:
        endpoint = str(manager_pub).strip()
        if not endpoint:
            raise ValueError("manager_pub is required")

        topic_list = [str(topic).strip() for topic in topics if str(topic).strip()]
        if not topic_list:
            raise ValueError("at least one topic is required")

        self._ctx = ctx or zmq.Context.instance()
        self._endpoint = endpoint
        self._topics = tuple(topic_list)
        self._rcvtimeo_ms = max(1, int(rcvtimeo_ms))
        self._sock: zmq.Socket | None = None

    @property
    def topics(self) -> tuple[str, ...]:
        return self._topics

    def open(self) -> None:
        if self._sock is not None:
            return
        sock = self._ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.RCVTIMEO, self._rcvtimeo_ms)
        for topic in self._topics:
            sock.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
        sock.connect(self._endpoint)
        self._sock = sock

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is None:
            return
        try:
            sock.close(0)
        except Exception:
            pass

    def __enter__(self) -> "EventSubscriber":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def _ensure_socket(self) -> zmq.Socket:
        if self._sock is None:
            self.open()
        assert self._sock is not None
        return self._sock

    def recv(self, *, timeout_ms: int | None = None) -> EventMessage | None:
        sock = self._ensure_socket()
        timeout = self._rcvtimeo_ms if timeout_ms is None else max(1, int(timeout_ms))
        if timeout != self._rcvtimeo_ms:
            sock.setsockopt(zmq.RCVTIMEO, timeout)
        try:
            topic_b, payload_b = sock.recv_multipart()
        except zmq.Again:
            return None
        except Exception:
            return None
        finally:
            if timeout != self._rcvtimeo_ms:
                sock.setsockopt(zmq.RCVTIMEO, self._rcvtimeo_ms)
        topic = topic_b.decode("utf-8", errors="replace")
        payload: Any = safe_json_loads(payload_b)
        return EventMessage.create(topic=topic, payload=payload)

    def drain(self, *, max_items: int | None = None) -> list[EventMessage]:
        out: list[EventMessage] = []
        limit = max_items if max_items is None else max(0, int(max_items))
        while True:
            if limit is not None and len(out) >= limit:
                break
            msg = self.recv(timeout_ms=1)
            if msg is None:
                break
            out.append(msg)
        return out

    def iter(self, *, timeout_ms: int | None = None) -> Iterator[EventMessage]:
        while True:
            msg = self.recv(timeout_ms=timeout_ms)
            if msg is None:
                break
            yield msg

