from __future__ import annotations

from typing import Any, Callable

import zmq
import zmq.utils.jsonapi

Json = dict[str, Any]


def json_dumps(payload: Any) -> bytes:
    return zmq.utils.jsonapi.dumps(payload)


def json_loads(data: bytes) -> Any:
    return zmq.utils.jsonapi.loads(data)


def safe_json_loads(data: bytes) -> Any | None:
    try:
        return json_loads(data)
    except (TypeError, ValueError):
        return None


def send_json(sock: zmq.Socket, payload: Any) -> None:
    sock.send(json_dumps(payload))


def recv_json(sock: zmq.Socket, *, flags: int = 0) -> Any | None:
    try:
        data = sock.recv(flags=flags)
    except zmq.ZMQError:
        return None
    return safe_json_loads(data)


def poll_and_drain(
    poller: zmq.Poller,
    timeout_ms: int,
    *,
    handlers: dict[zmq.Socket, Callable[[], None]] | None = None,
) -> dict[zmq.Socket, int]:
    events = dict(poller.poll(timeout_ms))
    if handlers:
        for sock, handler in handlers.items():
            if events.get(sock) == zmq.POLLIN:
                try:
                    handler()
                except (zmq.ZMQError, TypeError, ValueError):
                    pass
    return events
