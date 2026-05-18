from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import orjson
import zmq
import zmq.utils.jsonapi

Json = dict[str, Any]


@dataclass(frozen=True)
class DrainResult:
    count: int
    limited: bool
    duration_s: float
    parse_errors: int = 0


# orjson is 13-25× faster than zmq.utils.jsonapi.dumps on the payload
# shapes the gateway and stream_analysis broadcast on every WS message
# (per `bench/run_microbench.py`). It also serialises numpy scalars
# natively via OPT_SERIALIZE_NUMPY, so callers don't need to convert
# numpy.int64 etc. before encoding.
#
# NaN/Inf handling: orjson silently encodes NaN/Inf floats as JSON
# `null` (it does NOT raise — verified empirically). This is a
# wire-format change vs the previous encoder, which emitted non-spec
# `NaN`/`Infinity` literals. The hot-path callers already sanitise
# via `_sanitize_json` before encoding, so null-emission only affects
# paths that bypass sanitisation — a tolerable degradation given
# those paths are not part of the analysis stream contract.
#
# The fallback below is therefore NOT reached for NaN/Inf. It exists
# for genuinely unsupported types: complex numbers, non-string dict
# keys, custom objects without __dict__ / dataclass support, etc.
# zmq.utils.jsonapi.dumps (stdlib json) handles a broader type set
# at the cost of speed.
_ORJSON_OPTIONS = orjson.OPT_SERIALIZE_NUMPY


def json_dumps(payload: Any) -> bytes:
    try:
        return orjson.dumps(payload, option=_ORJSON_OPTIONS)
    except (TypeError, ValueError):
        # Fallback for types orjson refuses (complex numbers,
        # non-string dict keys, custom objects, etc.). NaN/Inf
        # do NOT take this path — orjson encodes them as `null`.
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


def drain_multipart_nonblocking(
    sock: zmq.Socket,
    handler: Callable[[bytes, bytes], bool],
    *,
    max_messages: int | None = 1000,
    max_duration_s: float | None = 0.1,
) -> DrainResult:
    start = time.monotonic()
    count = 0
    parse_errors = 0
    limited = False
    max_count = None if max_messages is None else max(1, int(max_messages))
    max_duration = None if max_duration_s is None else max(0.0, float(max_duration_s))
    while True:
        if max_count is not None and count >= max_count:
            limited = True
            break
        if max_duration is not None and (time.monotonic() - start) >= max_duration:
            limited = True
            break
        try:
            topic_b, payload_b = sock.recv_multipart(flags=zmq.NOBLOCK)
        except zmq.Again:
            break
        except Exception:
            break
        count += 1
        try:
            ok = bool(handler(topic_b, payload_b))
        except Exception:
            ok = False
        if not ok:
            parse_errors += 1
    return DrainResult(
        count=count,
        limited=limited,
        duration_s=time.monotonic() - start,
        parse_errors=parse_errors,
    )


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
