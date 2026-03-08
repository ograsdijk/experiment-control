from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import zmq

from ..utils.zmq_helpers import json_dumps, safe_json_loads
from .errors import RpcTimeoutError, RpcTransportError
from .types import Json


def _default_source_id() -> str:
    return "python_client"


@dataclass(frozen=True, slots=True)
class TransportSettings:
    router_rpc: str
    timeout_ms: int = 2000
    retries: int = 0
    source_kind: str = "script"
    source_id: str = _default_source_id()


class RpcTransport:
    def __init__(
        self,
        *,
        router_rpc: str,
        timeout_ms: int = 2000,
        retries: int = 0,
        source_kind: str = "script",
        source_id: str | None = None,
        ctx: zmq.Context | None = None,
    ) -> None:
        self._settings = TransportSettings(
            router_rpc=str(router_rpc).strip(),
            timeout_ms=max(1, int(timeout_ms)),
            retries=max(0, int(retries)),
            source_kind=str(source_kind or "script").strip() or "script",
            source_id=str(source_id or _default_source_id()).strip() or _default_source_id(),
        )
        if not self._settings.router_rpc:
            raise ValueError("router_rpc is required")

        self._ctx = ctx or zmq.Context.instance()
        self._sock: zmq.Socket | None = None

    @property
    def router_rpc(self) -> str:
        return self._settings.router_rpc

    @property
    def timeout_ms(self) -> int:
        return int(self._settings.timeout_ms)

    def open(self) -> None:
        if self._sock is not None:
            return
        sock = self._ctx.socket(zmq.DEALER)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(self._settings.router_rpc)
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

    def __enter__(self) -> "RpcTransport":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def _reset_socket(self) -> None:
        self.close()
        self.open()

    def _ensure_socket(self) -> zmq.Socket:
        if self._sock is None:
            self.open()
        assert self._sock is not None
        return self._sock

    @staticmethod
    def _drain_stale(sock: zmq.Socket) -> None:
        while True:
            try:
                if not sock.poll(0, zmq.POLLIN):
                    break
                _ = sock.recv(zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break

    def _inject_request_envelope(self, payload: Json) -> Json:
        outbound = dict(payload)
        request_id = outbound.get("request_id")
        if request_id is None:
            request_id = uuid.uuid4().hex
            outbound["request_id"] = request_id

        req_type = str(outbound.get("type", "")).strip()
        if req_type in {
            "command",
            "process.rpc",
            "process.start",
            "process.stop",
            "process.restart",
            "device.connect",
            "device.disconnect",
            "device.driver.start",
            "device.driver.restart",
        }:
            if outbound.get("source_kind") is None:
                outbound["source_kind"] = self._settings.source_kind
            if outbound.get("source_id") is None:
                outbound["source_id"] = self._settings.source_id

        if req_type == "process.rpc":
            request_raw = outbound.get("request")
            if isinstance(request_raw, dict):
                request = dict(request_raw)
                if request.get("request_id") is None:
                    request["request_id"] = request_id
                params = request.get("params")
                if params is None:
                    request["params"] = {}
                outbound["request"] = request

        return outbound

    def request(
        self,
        payload: Json,
        *,
        timeout_ms: int | None = None,
        retries: int | None = None,
    ) -> Json:
        timeout = max(1, int(self._settings.timeout_ms if timeout_ms is None else timeout_ms))
        retry_count = (
            self._settings.retries if retries is None else max(0, int(retries))
        )
        max_attempts = 1 + retry_count
        outbound = self._inject_request_envelope(payload)
        expected_request_id = outbound.get("request_id")

        last_exception: Exception | None = None
        for attempt in range(max_attempts):
            sock = self._ensure_socket()
            sock.setsockopt(zmq.RCVTIMEO, timeout)
            sock.setsockopt(zmq.SNDTIMEO, timeout)
            deadline = time.monotonic() + (timeout / 1000.0)

            try:
                self._drain_stale(sock)
                sock.send(json_dumps(outbound))

                while True:
                    remaining_ms = int(max(1.0, (deadline - time.monotonic()) * 1000.0))
                    if remaining_ms <= 0:
                        raise RpcTimeoutError(
                            f"timed out waiting for RPC response after {timeout} ms"
                        )
                    if not sock.poll(remaining_ms, zmq.POLLIN):
                        raise RpcTimeoutError(
                            f"timed out waiting for RPC response after {timeout} ms"
                        )

                    raw = sock.recv()
                    response = safe_json_loads(raw)
                    if not isinstance(response, dict):
                        continue
                    got_request_id = response.get("request_id")
                    if (
                        expected_request_id is not None
                        and got_request_id is not None
                        and got_request_id != expected_request_id
                    ):
                        # Late/stale reply from older call.
                        continue
                    return response
            except RpcTimeoutError as exc:
                last_exception = exc
            except Exception as exc:  # pragma: no cover - transport edge cases
                last_exception = exc

            self._reset_socket()
            if attempt >= max_attempts - 1:
                break

        if isinstance(last_exception, RpcTimeoutError):
            raise last_exception
        message = (
            str(last_exception)
            if last_exception is not None
            else "unknown RPC transport error"
        )
        raise RpcTransportError(message)
