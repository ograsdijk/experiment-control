from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

import zmq

from .driver_pub import normalized_chunk_descriptor_or_raise
from ..utils.zmq_helpers import json_dumps, safe_json_loads

if TYPE_CHECKING:
    from .models import DeviceHandle, ProcessHandle
    from ..manager_protocol import ManagerProtocol

    _MixinBase = ManagerProtocol
else:
    _MixinBase = object

Json = dict[str, Any]


def _effective_status_ok(resp: Json) -> bool | None:
    # Matches utils.responses strictness: identity-check on "ok" and
    # exact-case on "status". Refuses to silently coerce a driver that
    # returns ``ok=1`` or ``status="ok"`` so the bug surfaces instead
    # of mislabelling a failure as success.
    status = resp.get("status")
    if status in {"OK", "ERROR"}:
        return status == "OK"
    if "ok" in resp:
        return resp.get("ok") is True
    return None


def _send_json(sock: zmq.Socket, msg: Json) -> None:
    sock.send(json_dumps(msg))


def _recv_json(sock: zmq.Socket) -> Json:
    data = sock.recv()
    msg = safe_json_loads(data)
    if not isinstance(msg, dict):
        raise TypeError("JSON message must be an object")
    return msg


def _blocking_call_with_pump(
    sock: zmq.Socket,
    request_b: bytes,
    *,
    timeout_ms: int,
    response_filter: Callable[[Json], bool],
    pump_fn: Callable[[], None],
) -> Json:
    """Send ``request_b`` and block until ``response_filter`` accepts a reply.

    ``response_filter`` returning ``False`` (e.g. a stale reply whose
    request_id doesn't match) silently drops the frame and keeps
    polling until the deadline. If every poll up to ``timeout_ms``
    returns only filter-rejected frames, the function raises
    ``zmq.Again`` — the rejected payloads are not surfaced. Callers
    that need to surface stale replies must capture them inside their
    ``response_filter`` callback.

    ``pump_fn`` is called on each poll-timeout so the caller can keep
    other manager sockets drained while waiting for the reply.
    """
    sock.send(request_b)
    deadline = time.monotonic() + (int(timeout_ms) / 1000.0)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise zmq.Again()
        step_ms = int(min(50.0, max(1.0, remaining * 1000.0)))
        if not sock.poll(step_ms, zmq.POLLIN):
            pump_fn()
            continue
        resp = _recv_json(sock)
        if response_filter(resp):
            return resp


def _drain_stale_replies(sock: zmq.Socket) -> None:
    while True:
        try:
            if not sock.poll(0, zmq.POLLIN):
                break
            _ = sock.recv(zmq.NOBLOCK)
        except zmq.Again:
            break
        except Exception:
            break


class RpcCallsMixin(_MixinBase):
    """Mixin providing blocking device/process RPC calls.

    Phase 8.2.8: migrated ``call_device_rpc`` + ``call_process_rpc``
    (and the per-socket-ensure / payload-build / subscription-pump
    helpers they call) from module-level helpers to mixin methods.
    Pure socket utilities (``_send_json``, ``_recv_json``,
    ``_blocking_call_with_pump``, ``_effective_status_ok``,
    ``_drain_stale_replies``) stay at module level — they don't need
    Manager state.
    """

    # Owned-state attributes (concrete types declared on Manager).
    _ctx: zmq.Context
    _sub: zmq.Socket
    _process_hb_sub: zmq.Socket
    _process_data_sub: zmq.Socket
    _device_rpc_timeout_ms: int
    _rpc_seq: int
    _devices: dict[str, "DeviceHandle"]
    _processes: dict[str, "ProcessHandle"]

    def _pump_manager_subscriptions(self) -> None:
        while self._sub.poll(0, zmq.POLLIN):
            self._handle_driver_pub()
        while self._process_hb_sub.poll(0, zmq.POLLIN):
            self._handle_process_pub()
        while self._process_data_sub.poll(0, zmq.POLLIN):
            self._handle_process_data_pub()

    def _ensure_device_req_socket(self, handle: "DeviceHandle") -> zmq.Socket:
        sock = handle.rpc_sock
        if sock is not None:
            return sock
        sock = self._ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.REQ_RELAXED, 1)
        sock.setsockopt(zmq.REQ_CORRELATE, 1)
        sock.connect(handle.rpc_endpoint)
        handle.rpc_sock = sock
        return sock

    def _ensure_process_dealer_socket(self, handle: "ProcessHandle") -> zmq.Socket:
        sock = handle.rpc_sock
        if sock is not None:
            return sock
        sock = self._ctx.socket(zmq.DEALER)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(handle.rpc_endpoint)
        handle.rpc_sock = sock
        return sock

    def _build_manager_command_payload(
        self,
        *,
        device_id: str,
        action: str,
        params: Json,
        ok: bool | None,
        status: Any,
        error: Any,
        result: Any,
        source_kind: str,
        source_id: str | None,
        is_remote_target: bool,
        request_id: Any,
        caller_process_id: str | None,
    ) -> Json:
        payload: Json = {
            "version": 1,
            "device_id": device_id,
            "action": action,
            "params_json": self._safe_json(params),
            "ok": ok,
            "status": status,
            "error": error,
            "result_json": self._safe_json(result),
            "source_kind": source_kind,
            "source_id": source_id,
            "is_remote_target": bool(is_remote_target),
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        if request_id is not None:
            payload["request_id"] = request_id
        if caller_process_id is not None:
            payload["caller_process_id"] = caller_process_id
        return payload

    def _call_device_rpc(
        self,
        *,
        device_id: str,
        action: str,
        params: Json,
        timeout_ms: int | None = None,
        request_id: Any = None,
        caller_process_id: Any = None,
        source_kind: Any = None,
        source_id: Any = None,
        is_remote_target: bool = False,
    ) -> Json:
        handle = self._devices.get(device_id)
        if handle is None or handle.rpc_endpoint is None:
            raise RuntimeError(f"Device {device_id!r} is not registered")

        # Serialise the full REQ-socket cycle (ensure-socket + setsockopt
        # + send + recv-loop + state-bump + close-on-failure) per
        # handle. ZMQ REQ sockets are not thread-safe; without this
        # guard, concurrent lifecycle workers dispatching commands to
        # the same device interleave send/recv and break the REQ state
        # machine. Held across the whole call so the rpc_fail_count
        # and rpc_sock mutations on the failure path stay consistent
        # with the in-flight send. rpc_lock is an RLock so the except
        # branch's _close_device_rpc re-entry on the same thread does
        # not deadlock.
        with handle.rpc_lock:
            sock = self._ensure_device_req_socket(handle)
            effective_timeout = (
                self._device_rpc_timeout_ms if timeout_ms is None else timeout_ms
            )
            caller_process_id_text = self._normalize_id(caller_process_id)
            source_kind_text, source_id_text = self._normalize_command_source(
                source_kind=source_kind,
                source_id=source_id,
                caller_process_id=caller_process_id_text,
            )
            sock.setsockopt(zmq.RCVTIMEO, int(effective_timeout))
            sock.setsockopt(zmq.SNDTIMEO, int(effective_timeout))
            try:
                self._rpc_seq += 1
                envelope = {
                    "id": self._rpc_seq,
                    "action": action,
                    "params": params,
                }
                resp = _blocking_call_with_pump(
                    sock,
                    json_dumps(envelope),
                    timeout_ms=int(effective_timeout),
                    response_filter=lambda _: True,
                    pump_fn=self._pump_manager_subscriptions,
                )
                handle.rpc_fail_count = 0
                handle.rpc_last_fail_t_mono = None
            except Exception as e:
                handle.rpc_fail_count += 1
                handle.rpc_last_fail_t_mono = time.monotonic()
                if handle.rpc_fail_count >= 2:
                    self._close_device_rpc(handle)
                payload = self._build_manager_command_payload(
                    device_id=device_id,
                    action=action,
                    params=params,
                    ok=False,
                    status=None,
                    error=str(e),
                    result="",
                    source_kind=source_kind_text,
                    source_id=source_id_text,
                    is_remote_target=bool(is_remote_target),
                    request_id=request_id,
                    caller_process_id=caller_process_id_text,
                )
                self._publish_manager_event("manager.command", payload)
                raise
            payload = self._build_manager_command_payload(
                device_id=device_id,
                action=action,
                params=params,
                ok=_effective_status_ok(resp),
                status=resp.get("status"),
                error=resp.get("error"),
                result=resp.get("result"),
                source_kind=source_kind_text,
                source_id=source_id_text,
                is_remote_target=bool(is_remote_target),
                request_id=request_id,
                caller_process_id=caller_process_id_text,
            )
            self._publish_manager_event("manager.command", payload)
            if _effective_status_ok(resp) is True and action.startswith("stream__"):
                result = resp.get("result")
                try:
                    if not isinstance(result, dict):
                        raise TypeError("stream RPC result must be a descriptor dict")
                    desc = normalized_chunk_descriptor_or_raise({"descriptor": result})
                    self._ingest_chunk_ready({"descriptor": desc})
                except Exception as exc:
                    self._publish_manager_event(
                        "manager.chunk_error",
                        {
                            "device_id": device_id,
                            "action": action,
                            "error": (
                                "stream RPC result chunk ingest failed: "
                                f"{exc}"
                            ),
                            "raw": result,
                        },
                    )
                    raise
            return resp

    def _call_process_rpc(
        self,
        *,
        process_id: str,
        request: Json,
        timeout_ms: int | None = None,
    ) -> Json:
        handle = self._processes.get(process_id)
        if handle is None:
            raise RuntimeError(f"Process {process_id!r} is not configured")
        if handle.rpc_endpoint is None:
            raise RuntimeError("process rpc endpoint not ready")

        # Serialise the full DEALER-socket cycle per handle. ZMQ DEALER
        # sockets are not thread-safe; without this guard, concurrent
        # lifecycle workers (interceptor-invoke + process-command + the
        # supervisor's stop path) interleave send/recv on the same
        # DEALER and either tangle request_id correlation (different
        # in-flight requests see each other's replies) or trigger ZMQ
        # EFSM. Held across the whole call so close-on-failure stays
        # consistent. rpc_lock is an RLock so the except branch's
        # _close_process_rpc re-entry on the same thread does not
        # deadlock.
        with handle.rpc_lock:
            sock = self._ensure_process_dealer_socket(handle)
            effective_timeout = (
                self._device_rpc_timeout_ms if timeout_ms is None else timeout_ms
            )
            sock.setsockopt(zmq.RCVTIMEO, int(effective_timeout))
            sock.setsockopt(zmq.SNDTIMEO, int(effective_timeout))
            expected_request_id = request.get("request_id")
            try:
                _drain_stale_replies(sock)
                resp = _blocking_call_with_pump(
                    sock,
                    json_dumps(request),
                    timeout_ms=int(effective_timeout),
                    response_filter=lambda item: expected_request_id is None
                    or item.get("request_id") == expected_request_id,
                    pump_fn=self._pump_manager_subscriptions,
                )
                handle.rpc_fail_count = 0
                handle.rpc_last_fail_t_mono = None
                return resp
            except Exception:
                handle.rpc_fail_count += 1
                handle.rpc_last_fail_t_mono = time.monotonic()
                self._close_process_rpc(handle)
                raise
