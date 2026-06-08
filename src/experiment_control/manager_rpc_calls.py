from __future__ import annotations

import time
from typing import Any

import zmq

Json = dict[str, Any]


def _pump_manager_subscriptions(manager: Any) -> None:
    while manager._sub.poll(0, zmq.POLLIN):
        manager._handle_driver_pub()
    while manager._process_hb_sub.poll(0, zmq.POLLIN):
        manager._handle_process_pub()
    while manager._process_data_sub.poll(0, zmq.POLLIN):
        manager._handle_process_data_pub()


def _ensure_device_req_socket(manager: Any, handle: Any) -> zmq.Socket:
    sock = handle.rpc_sock
    if sock is not None:
        return sock
    sock = manager._ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.REQ_RELAXED, 1)
    sock.setsockopt(zmq.REQ_CORRELATE, 1)
    sock.connect(handle.rpc_endpoint)
    handle.rpc_sock = sock
    return sock


def _build_manager_command_payload(
    manager: Any,
    *,
    device_id: str,
    action: str,
    params: Json,
    ok: bool | None,
    status: Any,
    error: Any,
    result: Any,
    source_kind: str,
    source_id: str,
    is_remote_target: bool,
    request_id: Any,
    caller_process_id: str | None,
) -> Json:
    payload: Json = {
        "version": 1,
        "device_id": device_id,
        "action": action,
        "params_json": manager._safe_json(params),
        "ok": ok,
        "status": status,
        "error": error,
        "result_json": manager._safe_json(result),
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


def _effective_status_ok(resp: Json) -> bool | None:
    status = resp.get("status")
    if status in {"OK", "ERROR"}:
        return status == "OK"
    if "ok" in resp:
        return bool(resp.get("ok"))
    return None


def call_device_rpc(
    manager: Any,
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
    handle = manager._devices.get(device_id)
    if handle is None or handle.rpc_endpoint is None:
        raise RuntimeError(f"Device {device_id!r} is not registered")

    # Serialise the full REQ-socket cycle (ensure-socket + setsockopt +
    # send + recv-loop + state-bump + close-on-failure) per handle.
    # ZMQ REQ sockets are not thread-safe; without this guard,
    # concurrent lifecycle workers dispatching commands to the same
    # device interleave send/recv and break the REQ state machine.
    # Held across the whole call so the rpc_fail_count and rpc_sock
    # mutations on the failure path stay consistent with the in-flight
    # send. rpc_lock is an RLock so the except branch's
    # _close_device_rpc re-entry on the same thread does not deadlock.
    with handle.rpc_lock:
        sock = _ensure_device_req_socket(manager, handle)
        effective_timeout = manager._device_rpc_timeout_ms if timeout_ms is None else timeout_ms
        caller_process_id_text = manager._normalize_id(caller_process_id)
        source_kind_text, source_id_text = manager._normalize_command_source(
            source_kind=source_kind,
            source_id=source_id,
            caller_process_id=caller_process_id_text,
        )
        sock.setsockopt(zmq.RCVTIMEO, int(effective_timeout))
        sock.setsockopt(zmq.SNDTIMEO, int(effective_timeout))
        try:
            manager._rpc_seq += 1
            envelope = {
                "id": manager._rpc_seq,
                "action": action,
                "params": params,
            }
            manager._send_json(sock, envelope)
            deadline = time.monotonic() + (effective_timeout / 1000.0)
            resp: Json | None = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise zmq.Again()
                step_ms = int(min(50.0, remaining * 1000.0))
                if sock.poll(step_ms, zmq.POLLIN):
                    resp = manager._recv_json(sock)
                    break
                _pump_manager_subscriptions(manager)
            if resp is None:
                raise zmq.Again()
            handle.rpc_fail_count = 0
            handle.rpc_last_fail_t_mono = None
            payload = _build_manager_command_payload(
                manager,
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
            manager._publish_manager_event("manager.command", payload)
            return resp
        except Exception as e:
            handle.rpc_fail_count += 1
            handle.rpc_last_fail_t_mono = time.monotonic()
            if handle.rpc_fail_count >= 2:
                manager._close_device_rpc(handle)
            payload = _build_manager_command_payload(
                manager,
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
            manager._publish_manager_event("manager.command", payload)
            raise


def _ensure_process_dealer_socket(manager: Any, handle: Any) -> zmq.Socket:
    sock = handle.rpc_sock
    if sock is not None:
        return sock
    sock = manager._ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(handle.rpc_endpoint)
    handle.rpc_sock = sock
    return sock


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


def call_process_rpc(
    manager: Any,
    *,
    process_id: str,
    request: Json,
    timeout_ms: int | None = None,
) -> Json:
    handle = manager._processes.get(process_id)
    if handle is None:
        raise RuntimeError(f"Process {process_id!r} is not configured")
    if handle.rpc_endpoint is None:
        raise RuntimeError("process rpc endpoint not ready")

    # Serialise the full DEALER-socket cycle per handle. ZMQ DEALER
    # sockets are not thread-safe; without this guard, concurrent
    # lifecycle workers (interceptor-invoke + process-command + the
    # supervisor's stop path) interleave send/recv on the same DEALER
    # and either tangle request_id correlation (different in-flight
    # requests see each other's replies) or trigger ZMQ EFSM. Held
    # across the whole call so close-on-failure stays consistent.
    # rpc_lock is an RLock so the except branch's _close_process_rpc
    # re-entry on the same thread does not deadlock.
    with handle.rpc_lock:
        sock = _ensure_process_dealer_socket(manager, handle)
        effective_timeout = manager._device_rpc_timeout_ms if timeout_ms is None else timeout_ms
        sock.setsockopt(zmq.RCVTIMEO, int(effective_timeout))
        sock.setsockopt(zmq.SNDTIMEO, int(effective_timeout))
        expected_request_id = request.get("request_id")
        try:
            _drain_stale_replies(sock)
            manager._send_json(sock, request)
            deadline = time.monotonic() + (int(effective_timeout) / 1000.0)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise zmq.Again()
                step_ms = int(min(50.0, max(1.0, remaining * 1000.0)))
                if not sock.poll(step_ms, zmq.POLLIN):
                    _pump_manager_subscriptions(manager)
                    continue
                resp = manager._recv_json(sock)
                if not isinstance(resp, dict):
                    continue
                if expected_request_id is not None and resp.get("request_id") != expected_request_id:
                    continue
                break
            handle.rpc_fail_count = 0
            handle.rpc_last_fail_t_mono = None
            return resp
        except Exception:
            handle.rpc_fail_count += 1
            handle.rpc_last_fail_t_mono = time.monotonic()
            manager._close_process_rpc(handle)
            raise
