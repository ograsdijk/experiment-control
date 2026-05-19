
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Callable

import zmq

from .manager_client_helper import ManagerClientHelper
from .process_base import ManagedProcessBase
from ..capabilities import capabilities_payload, method
from ..manager_client import ManagerClient
from ..utils.command_interceptors import apply_command_interceptor_chain
from ..utils.zmq_helpers import json_dumps, poll_and_drain, safe_json_loads

Json = dict[str, Any]

_ALLOWED_PROCESS_STATES = {"STARTING", "RUNNING", "STOPPING"}


def _safe_json(value: Any, *, max_len: int = 4000) -> str:
    try:
        text = json.dumps(value)
    except Exception:
        text = str(value)
    if len(text) > max_len:
        return text[:max_len] + "...(truncated)"
    return text


def _is_zmq_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, zmq.Again):
        return True
    if isinstance(exc, zmq.ZMQError):
        try:
            return int(getattr(exc, "errno", -1)) == int(zmq.EAGAIN)
        except Exception:
            return False
    return False


def _device_rpc_exception_error(
    exc: BaseException,
    *,
    timeout_ms: int,
    device_id: str,
    action: str,
) -> Json:
    if _is_zmq_timeout_error(exc):
        return {
            "code": "device_rpc_timeout",
            "message": f"device RPC timed out after {int(timeout_ms)} ms",
            "device_id": device_id,
            "action": action,
            "timeout_ms": int(timeout_ms),
            "transient": True,
            "retryable": True,
        }
    message = str(exc).strip() or exc.__class__.__name__
    return {
        "code": "device_rpc_failed",
        "message": message,
        "device_id": device_id,
        "action": action,
    }


@dataclass(frozen=True)
class CommandInterceptorRoute:
    process_id: str
    device_id: str
    action: str
    order: int


@dataclass(frozen=True)
class _DeviceTask:
    identity: bytes
    request: Json
    device_id: str
    action: str
    params: Json
    request_id: Any | None
    caller_process_id: str | None
    source_kind: str | None
    source_id: str | None
    device_endpoint: str | None
    device_state: str | None
    chain: list[CommandInterceptorRoute]
    interceptor_endpoints: dict[str, str | None]
    interceptor_states: dict[str, str | None]
    inflight_reserved: bool = False


@dataclass(frozen=True)
class _ProcessTask:
    identity: bytes
    process_id: str
    request: Json
    endpoint: str | None
    action: str
    params: Json
    request_id: Any | None
    caller_process_id: str | None
    source_kind: str | None
    source_id: str | None
    process_state: str | None
    inflight_reserved: bool = False


@dataclass(frozen=True)
class _ManagerTask:
    identity: bytes
    request: Json
    inflight_reserved: bool = False


@dataclass(frozen=True)
class MirroredRoute:
    local_id: str
    peer_id: str
    remote_device_id: str
    peer_router_rpc: str
    rpc_timeout_ms: int
    allow_device_actions: tuple[str, ...]
    deny_device_actions: tuple[str, ...]
    allow_lifecycle_ops: bool
    allow_admin_ops: bool
    origin_instance_id: str

    def allows_device_action(self, action: str) -> bool:
        text = str(action or "").strip()
        if not text:
            return False
        for pattern in self.deny_device_actions:
            if fnmatchcase(text, pattern):
                return False
        for pattern in self.allow_device_actions:
            if fnmatchcase(text, pattern):
                return True
        return False


@dataclass(frozen=True)
class _MirroredTask:
    identity: bytes
    request: Json
    route: MirroredRoute
    inflight_reserved: bool = False


@dataclass(frozen=True)
class _ReplyItem:
    identity: bytes
    response: Json
    inflight_reserved: bool = False
    request_id: Any = None


def _inject_request_id(resp: Json, request_id: Any) -> Json:
    # Echo the caller's request_id back onto the reply so a pipelined
    # client (gateway's RouterRpcClient) can correlate response to
    # request. Only injects when the response doesn't already carry a
    # non-None id, so routes that supply their own keep control while
    # an explicit ``{"request_id": None, ...}`` from a handler still
    # gets overwritten (otherwise the gateway would silently drop the
    # reply via `pending.pop(None, None)`).
    if (
        not isinstance(resp, dict)
        or request_id is None
        or resp.get("request_id") is not None
    ):
        return resp
    out = dict(resp)
    out["request_id"] = request_id
    return out


class _BaseWorker(threading.Thread):
    def __init__(
        self,
        *,
        name: str,
        ctx: zmq.Context,
        reply_queue: queue.Queue,
        queue_max: int,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self._ctx = ctx
        self._reply_queue = reply_queue
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, int(queue_max)))
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            # Queue is saturated but stop flag is set; worker exits after current task.
            pass

    def submit(self, task: object) -> bool:
        try:
            self._queue.put_nowait(task)
        except queue.Full:
            return False
        return True

    def queue_depth(self) -> int | None:
        try:
            return int(self._queue.qsize())
        except Exception:
            return None

    def queue_max(self) -> int:
        try:
            return int(self._queue.maxsize)
        except Exception:
            return 0

    def _enqueue_reply(
        self,
        *,
        identity: bytes,
        response: Json,
        inflight_reserved: bool,
        request_id: Any = None,
    ) -> None:
        self._reply_queue.put(
            _ReplyItem(
                identity=identity,
                response=response,
                inflight_reserved=bool(inflight_reserved),
                request_id=request_id,
            )
        )


class _ProcessWorker(_BaseWorker):
    def __init__(
        self,
        *,
        process_id: str,
        ctx: zmq.Context,
        reply_queue: queue.Queue,
        manager_rpc: str,
        manager_pub: str,
        timeout_ms: int,
        queue_max: int,
    ) -> None:
        super().__init__(
            name=f"process-rpc-{process_id}",
            ctx=ctx,
            reply_queue=reply_queue,
            queue_max=queue_max,
        )
        self._process_id = process_id
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._timeout_ms = int(timeout_ms)
        self._manager_helper = ManagerClientHelper(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=self._timeout_ms,
        )
        self._manager: ManagerClient | None = None
        self._sock: zmq.Socket | None = None
        self._endpoint: str | None = None

    def _close_sock(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close(0)
        except Exception:
            pass
        self._sock = None
        self._endpoint = None

    def _drain_stale_replies(self) -> None:
        if self._sock is None:
            return
        while True:
            try:
                if not self._sock.poll(0, zmq.POLLIN):
                    break
                _ = self._sock.recv(zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break

    def _call(self, endpoint: str, request: Json) -> Json | None:
        if self._sock is None or endpoint != self._endpoint:
            self._close_sock()
            sock = self._ctx.socket(zmq.DEALER)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(endpoint)
            self._sock = sock
            self._endpoint = endpoint
        assert self._sock is not None
        self._sock.setsockopt(zmq.RCVTIMEO, self._timeout_ms)
        self._sock.setsockopt(zmq.SNDTIMEO, self._timeout_ms)
        expected_request_id = request.get("request_id")
        deadline = time.monotonic() + (self._timeout_ms / 1000.0)
        try:
            # Clear any late replies from previous timed-out calls on this socket.
            self._drain_stale_replies()
            self._sock.send(json_dumps(request))
            while True:
                remaining_ms = int(max(1.0, (deadline - time.monotonic()) * 1000.0))
                if remaining_ms <= 0:
                    raise zmq.Again()
                if not self._sock.poll(remaining_ms, zmq.POLLIN):
                    raise zmq.Again()
                raw = self._sock.recv()
                resp = safe_json_loads(raw)
                if not isinstance(resp, dict):
                    continue
                if expected_request_id is not None and resp.get("request_id") != expected_request_id:
                    # Late/stale reply from an older request; keep waiting.
                    continue
                return resp
        except Exception:
            # Reset the socket to avoid request/response desync after timeout/errors.
            self._close_sock()
            return None

    def _publish_event(self, topic: str, payload: Json) -> None:
        if self._manager is None:
            return
        self._manager_helper.publish_event(
            self._manager,
            topic=topic,
            payload=payload,
            include_process_id=False,
            include_ts=True,
        )

    def _publish_process_command(self, task: _ProcessTask, response: Json) -> None:
        error_obj = response.get("error")
        error_code = (
            str(error_obj.get("code", "")).strip()
            if isinstance(error_obj, dict)
            else ""
        )
        if (
            task.action == "process.capabilities"
            and str(task.process_state or "").strip().upper() == "STARTING"
            and error_code in {"process_rpc_not_ready", "process_starting"}
        ):
            return
        status = response.get("status")
        ok = response.get("ok")
        if status in {"OK", "ERROR"}:
            ok = status == "OK"
        payload: Json = {
            "version": 1,
            "device_id": f"process:{task.process_id}",
            "process_id": task.process_id,
            "action": task.action,
            "params_json": _safe_json(task.params),
            "ok": ok,
            "status": status,
            "error": response.get("error"),
            "result_json": _safe_json(response.get("result")),
            "request_id": task.request_id,
            "caller_process_id": task.caller_process_id,
            "source_kind": task.source_kind,
            "source_id": task.source_id,
            "is_remote_target": False,
            "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }
        self._publish_event("manager.command", payload)

    def run(self) -> None:
        self._manager = self._manager_helper.init_client(
            ctx=self._ctx,
            process_id=self._process_id,
            subscribe_telemetry=False,
        )
        while not self._stop_evt.is_set():
            task = self._queue.get()
            if task is None:
                break
            if not isinstance(task, _ProcessTask):
                continue
            if not task.endpoint:
                if (
                    task.action == "process.capabilities"
                    and str(task.process_state or "").strip().upper() == "STARTING"
                ):
                    resp = {
                        "ok": False,
                        "error": {
                            "code": "process_starting",
                            "message": "process is starting; RPC endpoint not advertised yet",
                            "retry_after_ms": 500,
                        },
                    }
                else:
                    resp = {"ok": False, "error": {"code": "process_rpc_not_ready"}}
                self._publish_process_command(task, resp)
                self._enqueue_reply(
                    identity=task.identity,
                    response=resp,
                    inflight_reserved=task.inflight_reserved,
                    request_id=task.request_id,
                )
                continue
            resp = self._call(task.endpoint, task.request)
            if resp is None:
                resp = {"ok": False, "error": "timeout"}
            self._publish_process_command(task, resp)
            self._enqueue_reply(
                identity=task.identity,
                response=resp,
                inflight_reserved=task.inflight_reserved,
                request_id=task.request_id,
            )
        if self._manager is not None:
            self._manager.close()
        self._close_sock()

class _ManagerWorker(_BaseWorker):
    def __init__(
        self,
        *,
        ctx: zmq.Context,
        reply_queue: queue.Queue,
        manager_rpc: str,
        manager_pub: str,
        timeout_ms: int,
        queue_max: int,
    ) -> None:
        super().__init__(
            name="manager-rpc",
            ctx=ctx,
            reply_queue=reply_queue,
            queue_max=queue_max,
        )
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._timeout_ms = int(timeout_ms)
        self._manager: ManagerClient | None = None

    def run(self) -> None:
        self._manager = ManagerClient(
            ctx=self._ctx,
            manager_rpc=self._manager_rpc,
            manager_pub=self._manager_pub,
            rpc_timeout_ms=self._timeout_ms,
            subscribe_telemetry=False,
        )
        while not self._stop_evt.is_set():
            task = self._queue.get()
            if task is None:
                break
            if not isinstance(task, _ManagerTask):
                continue
            resp = self._manager.call(task.request, timeout_ms=self._timeout_ms)
            if resp is None:
                resp = {"ok": False, "error": "timeout"}
            self._enqueue_reply(
                identity=task.identity,
                response=resp,
                inflight_reserved=task.inflight_reserved,
                request_id=task.request.get("request_id") if isinstance(task.request, dict) else None,
            )
        if self._manager is not None:
            self._manager.close()


class _DeviceWorker(_BaseWorker):
    def __init__(
        self,
        *,
        device_id: str,
        ctx: zmq.Context,
        reply_queue: queue.Queue,
        manager_rpc: str,
        manager_pub: str,
        device_rpc_timeout_ms: int,
        interceptor_timeout_ms: int,
        queue_max: int,
    ) -> None:
        super().__init__(
            name=f"device-rpc-{device_id}",
            ctx=ctx,
            reply_queue=reply_queue,
            queue_max=queue_max,
        )
        self._device_id = device_id
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._device_rpc_timeout_ms = int(device_rpc_timeout_ms)
        self._interceptor_timeout_ms = int(interceptor_timeout_ms)
        self._manager_helper = ManagerClientHelper(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=self._device_rpc_timeout_ms,
        )
        self._manager: ManagerClient | None = None
        self._device_sock: zmq.Socket | None = None
        self._device_endpoint: str | None = None
        self._process_socks: dict[str, tuple[str, zmq.Socket]] = {}
        self._seq = 0

    def _close_device_sock(self) -> None:
        if self._device_sock is None:
            return
        try:
            self._device_sock.close(0)
        except Exception:
            pass
        self._device_sock = None
        self._device_endpoint = None

    def _get_device_sock(self, endpoint: str) -> zmq.Socket:
        if self._device_sock is None or endpoint != self._device_endpoint:
            self._close_device_sock()
            sock = self._ctx.socket(zmq.REQ)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.REQ_RELAXED, 1)
            sock.setsockopt(zmq.REQ_CORRELATE, 1)
            sock.connect(endpoint)
            self._device_sock = sock
            self._device_endpoint = endpoint
        assert self._device_sock is not None
        return self._device_sock

    def _get_process_sock(self, process_id: str, endpoint: str) -> zmq.Socket:
        entry = self._process_socks.get(process_id)
        if entry is None or entry[0] != endpoint:
            if entry is not None:
                try:
                    entry[1].close(0)
                except Exception:
                    pass
            sock = self._ctx.socket(zmq.DEALER)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(endpoint)
            self._process_socks[process_id] = (endpoint, sock)
        return self._process_socks[process_id][1]

    def _publish_event(self, topic: str, payload: Json) -> None:
        if self._manager is None:
            return
        self._manager_helper.publish_event(
            self._manager,
            topic=topic,
            payload=payload,
            include_process_id=False,
            include_ts=True,
        )

    def _call_interceptor(
        self, *, process_id: str, endpoint: str, request: Json
    ) -> Json | None:
        sock = self._get_process_sock(process_id, endpoint)
        sock.setsockopt(zmq.RCVTIMEO, self._interceptor_timeout_ms)
        sock.setsockopt(zmq.SNDTIMEO, self._interceptor_timeout_ms)
        try:
            sock.send(json_dumps(request))
            raw = sock.recv()
        except Exception:
            return None
        resp = safe_json_loads(raw)
        return resp if isinstance(resp, dict) else None

    def _apply_command_interceptors(
        self, task: _DeviceTask
    ) -> tuple[bool, Json | None, Json | None]:
        def _is_route_available(process_id: str) -> bool:
            endpoint = task.interceptor_endpoints.get(process_id)
            state = task.interceptor_states.get(process_id)
            if not endpoint:
                return False
            return not state or state in _ALLOWED_PROCESS_STATES

        def _call(process_id: str, request: Json) -> tuple[str, Json | None, str | None]:
            endpoint = task.interceptor_endpoints.get(process_id)
            if not endpoint:
                return "unavailable", None, None
            resp = self._call_interceptor(
                process_id=process_id,
                endpoint=endpoint,
                request=request,
            )
            if resp is None:
                return "timeout", None, None
            return "ok", resp, None

        return apply_command_interceptor_chain(
            initial_command={
                "device_id": task.device_id,
                "action": task.action,
                "params": task.params,
            },
            chain=task.chain,
            request_id=task.request_id,
            caller_process_id=task.caller_process_id,
            is_route_available=_is_route_available,
            call_interceptor=_call,
            publish_event=self._publish_event,
            distinct_ok_false_message=False,
        )

    def _call_device(self, endpoint: str, action: str, params: Json) -> Json:
        sock = self._get_device_sock(endpoint)
        sock.setsockopt(zmq.RCVTIMEO, self._device_rpc_timeout_ms)
        sock.setsockopt(zmq.SNDTIMEO, self._device_rpc_timeout_ms)
        self._seq += 1
        envelope = {"id": self._seq, "action": action, "params": params}
        sock.send(json_dumps(envelope))
        raw = sock.recv()
        resp = safe_json_loads(raw)
        if not isinstance(resp, dict):
            raise RuntimeError("invalid device response")
        return resp

    @staticmethod
    def _device_endpoint_unavailable_error(task: _DeviceTask) -> Json:
        state = str(task.device_state or "").strip().upper()
        if state == "STARTING":
            return {
                "code": "device_starting",
                "message": "driver is starting; RPC endpoint is not ready",
                "state": state,
                "retry_after_ms": 250,
                "transient": True,
                "retryable": True,
            }
        if state == "STOPPING":
            return {
                "code": "device_stopping",
                "message": "driver is stopping; RPC endpoint is not available",
                "state": state,
                "retry_after_ms": 250,
                "transient": True,
                "retryable": True,
            }
        err: Json = {
            "code": "driver_not_running",
            "message": "driver is not running",
        }
        if state:
            err["state"] = state
        return err

    def run(self) -> None:
        self._manager = ManagerClient(
            ctx=self._ctx,
            manager_rpc=self._manager_rpc,
            manager_pub=self._manager_pub,
            rpc_timeout_ms=self._device_rpc_timeout_ms,
            subscribe_telemetry=False,
        )
        while not self._stop_evt.is_set():
            task = self._queue.get()
            if task is None:
                break
            if not isinstance(task, _DeviceTask):
                continue
            if not task.device_endpoint:
                resp = {
                    "ok": False,
                    "error": self._device_endpoint_unavailable_error(task),
                }
                self._enqueue_reply(
                    identity=task.identity,
                    response=resp,
                    inflight_reserved=task.inflight_reserved,
                    request_id=task.request_id,
                )
                continue
            ok, new_cmd, err = self._apply_command_interceptors(task)
            if not ok:
                resp = {"ok": False, "error": err}
                self._enqueue_reply(
                    identity=task.identity,
                    response=resp,
                    inflight_reserved=task.inflight_reserved,
                    request_id=task.request_id,
                )
                continue
            assert new_cmd is not None
            try:
                resp = self._call_device(
                    task.device_endpoint, task.action, new_cmd.get("params", task.params)
                )
                cmd_payload: Json = {
                    "version": 1,
                    "device_id": task.device_id,
                    "action": task.action,
                    "params_json": _safe_json(new_cmd.get("params", task.params)),
                    "ok": resp.get("status") == "OK" if "status" in resp else resp.get("ok"),
                    "status": resp.get("status"),
                    "error": resp.get("error"),
                    "result_json": _safe_json(resp.get("result")),
                    "request_id": task.request_id,
                    "caller_process_id": task.caller_process_id,
                    "source_kind": task.source_kind,
                    "source_id": task.source_id,
                    "is_remote_target": False,
                    "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
                }
                self._publish_event("manager.command", cmd_payload)
            except Exception as e:
                error_obj = _device_rpc_exception_error(
                    e,
                    timeout_ms=self._device_rpc_timeout_ms,
                    device_id=task.device_id,
                    action=task.action,
                )
                cmd_payload = {
                    "version": 1,
                    "device_id": task.device_id,
                    "action": task.action,
                    "params_json": _safe_json(new_cmd.get("params", task.params)),
                    "ok": False,
                    "status": None,
                    "error": error_obj,
                    "result_json": "",
                    "request_id": task.request_id,
                    "caller_process_id": task.caller_process_id,
                    "source_kind": task.source_kind,
                    "source_id": task.source_id,
                    "is_remote_target": False,
                    "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
                }
                self._publish_event("manager.command", cmd_payload)
                resp = {"ok": False, "error": error_obj}
            self._enqueue_reply(
                identity=task.identity,
                response=resp,
                inflight_reserved=task.inflight_reserved,
                request_id=task.request_id,
            )

        if self._manager is not None:
            self._manager.close()
        self._close_device_sock()
        for _endpoint, sock in self._process_socks.values():
            try:
                sock.close(0)
            except Exception:
                pass
        self._process_socks.clear()


class _MirroredDeviceWorker(_BaseWorker):
    def __init__(
        self,
        *,
        route: MirroredRoute,
        ctx: zmq.Context,
        reply_queue: queue.Queue,
        manager_rpc: str,
        manager_pub: str,
        manager_timeout_ms: int,
        queue_max: int,
    ) -> None:
        super().__init__(
            name=f"mirrored-rpc-{route.local_id}",
            ctx=ctx,
            reply_queue=reply_queue,
            queue_max=queue_max,
        )
        self._route = route
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._manager_timeout_ms = int(manager_timeout_ms)
        self._manager: ManagerClient | None = None
        self._sock: zmq.Socket | None = None

    def _ensure_sock(self) -> zmq.Socket:
        if self._sock is None:
            sock = self._ctx.socket(zmq.DEALER)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVTIMEO, int(self._route.rpc_timeout_ms))
            sock.setsockopt(zmq.SNDTIMEO, int(self._route.rpc_timeout_ms))
            sock.connect(self._route.peer_router_rpc)
            self._sock = sock
        return self._sock

    def _close_sock(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close(0)
        except Exception:
            pass
        self._sock = None

    def _next_federation_meta(self, raw: object) -> Json:
        hop_count = 0
        origin_instance_id = self._route.origin_instance_id
        if isinstance(raw, dict):
            try:
                hop_count = int(raw.get("hop_count", 0))
            except Exception:
                hop_count = 0
            if isinstance(raw.get("origin_instance_id"), str) and str(
                raw.get("origin_instance_id")
            ).strip():
                origin_instance_id = str(raw.get("origin_instance_id")).strip()
        return {
            "origin_instance_id": origin_instance_id,
            "hop_count": hop_count + 1,
        }

    def _forward(self, task: _MirroredTask) -> Json:
        sock = self._ensure_sock()
        outbound = dict(task.request)
        outbound["device_id"] = task.route.remote_device_id
        outbound["federation"] = self._next_federation_meta(
            task.request.get("federation")
        )
        sock.send(json_dumps(outbound))
        raw = sock.recv()
        resp = safe_json_loads(raw)
        if not isinstance(resp, dict):
            raise RuntimeError("invalid mirrored device response")
        return resp

    def _maybe_cache_capabilities(self, task: _MirroredTask, response: Json) -> None:
        action = str(task.request.get("action", ""))
        if action != "capabilities":
            return
        if not bool(response.get("ok")):
            return
        result = response.get("result")
        if not isinstance(result, dict):
            return
        if self._manager is None:
            return
        try:
            self._manager.call(
                {
                    "type": "federation.capabilities.update",
                    "device_id": task.route.local_id,
                    "capabilities": result,
                },
                timeout_ms=self._manager_timeout_ms,
            )
        except Exception:
            pass

    def run(self) -> None:
        self._manager = ManagerClient(
            ctx=self._ctx,
            manager_rpc=self._manager_rpc,
            manager_pub=self._manager_pub,
            rpc_timeout_ms=self._manager_timeout_ms,
            subscribe_telemetry=False,
        )
        while not self._stop_evt.is_set():
            task = self._queue.get()
            if task is None:
                break
            if not isinstance(task, _MirroredTask):
                continue
            try:
                resp = self._forward(task)
                self._maybe_cache_capabilities(task, resp)
            except Exception as e:
                self._close_sock()
                resp = {"ok": False, "error": str(e)}
            self._enqueue_reply(
                identity=task.identity,
                response=resp,
                inflight_reserved=task.inflight_reserved,
                request_id=task.request.get("request_id") if isinstance(task.request, dict) else None,
            )
        if self._manager is not None:
            self._manager.close()
        self._close_sock()

class DeviceRouter(ManagedProcessBase):
    def __init__(
        self,
        *,
        external_rpc_bind: str,
        manager_rpc: str | None = None,
        manager_pub: str | None = None,
        device_rpc_timeout_ms: int = 1500,
        interceptor_rpc_timeout_ms: int = 500,
        manager_worker_queue_max: int = 8192,
        process_worker_queue_max: int = 8192,
        device_worker_queue_max: int = 16384,
        mirrored_worker_queue_max: int = 8192,
        reply_queue_max: int = 32768,
        inflight_max: int = 32768,
        federation_mirrors: list[Json] | None = None,
        origin_instance_id: str | None = None,
        process_id: str | None = None,
        heartbeat_endpoint: str | None = None,
        heartbeat_period_s: float = 1.0,
        ctx: zmq.Context | None = None,
    ) -> None:
        super().__init__(
            process_id=process_id,
            heartbeat_endpoint=heartbeat_endpoint,
            heartbeat_period_s=heartbeat_period_s,
            ctx=ctx,
        )
        self._external_rpc_bind = external_rpc_bind
        self._manager_rpc = manager_rpc or "tcp://127.0.0.1:6000"
        self._manager_pub = manager_pub or "tcp://127.0.0.1:6001"
        self._device_rpc_timeout_ms = int(device_rpc_timeout_ms)
        self._interceptor_rpc_timeout_ms = int(interceptor_rpc_timeout_ms)
        self._manager_worker_queue_max = max(1, int(manager_worker_queue_max))
        self._process_worker_queue_max = max(1, int(process_worker_queue_max))
        self._device_worker_queue_max = max(1, int(device_worker_queue_max))
        self._mirrored_worker_queue_max = max(1, int(mirrored_worker_queue_max))
        self._reply_queue_max = max(1, int(reply_queue_max))
        self._inflight_max = max(1, min(int(inflight_max), self._reply_queue_max))
        self._inflight_count = 0
        self._inflight_rejected = 0
        self._overload_rejected: dict[str, int] = {
            "inflight": 0,
            "manager_worker": 0,
            "process_worker": 0,
            "device_worker": 0,
            "mirrored_worker": 0,
        }
        self._origin_instance_id = (
            str(origin_instance_id or "").strip() or str(process_id or "unknown")
        )
        self._manager_helper = ManagerClientHelper(
            manager_rpc=self._manager_rpc,
            manager_pub=self._manager_pub,
            rpc_timeout_ms=self._device_rpc_timeout_ms,
        )

        self._external_rpc: zmq.Socket | None = None
        self._manager_sub: zmq.Socket | None = None
        self._manager: ManagerClient | None = None

        self._device_endpoints: dict[str, str] = {}
        self._device_states: dict[str, str] = {}
        self._process_endpoints: dict[str, str] = {}
        self._process_states: dict[str, str] = {}

        self._route_lock = threading.Lock()
        self._routes: list[CommandInterceptorRoute] = []
        self._route_cache_max = 2048
        self._route_cache: dict[tuple[str, str], list[CommandInterceptorRoute]] = {}
        self._route_order = 0

        self._device_workers: dict[str, _DeviceWorker] = {}
        self._mirrored_routes: dict[str, MirroredRoute] = {}
        self._mirrored_workers: dict[str, _MirroredDeviceWorker] = {}
        self._process_workers: dict[str, _ProcessWorker] = {}
        self._manager_worker: _ManagerWorker | None = None
        self._reply_queue: queue.Queue[_ReplyItem] = queue.Queue(
            maxsize=self._reply_queue_max
        )
        self._load_federation_mirrors(federation_mirrors)

    def _close_external(self) -> None:
        if self._external_rpc is None:
            return
        try:
            self._external_rpc.close(0)
        except Exception:
            pass
        self._external_rpc = None

    def _close_manager_sub(self) -> None:
        if self._manager_sub is None:
            return
        try:
            self._manager_sub.close(0)
        except Exception:
            pass
        self._manager_sub = None

    def close(self) -> None:
        for worker in self._device_workers.values():
            worker.stop()
        for worker in self._mirrored_workers.values():
            worker.stop()
        for worker in self._process_workers.values():
            worker.stop()
        if self._manager_worker is not None:
            self._manager_worker.stop()
        super().close()
        self._close_external()
        self._close_manager_sub()

    def _load_federation_mirrors(self, items: list[Json] | None) -> None:
        if items is None:
            return
        if not isinstance(items, list):
            raise TypeError("federation_mirrors must be a list")
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise TypeError(f"federation_mirrors[{idx}] must be an object")
            local_id = str(item.get("local_id", "")).strip()
            peer_id = str(item.get("peer_id", "")).strip()
            remote_device_id = str(item.get("remote_device_id", "")).strip()
            peer_router_rpc = str(item.get("peer_router_rpc", "")).strip()
            if not local_id or not peer_id or not remote_device_id or not peer_router_rpc:
                raise ValueError(
                    "federation_mirrors entries require local_id, peer_id, "
                    "remote_device_id, and peer_router_rpc"
                )
            if local_id in self._mirrored_routes:
                raise ValueError(f"duplicate mirrored route for {local_id!r}")
            allow_raw = item.get("allow_device_actions", ["*"])
            deny_raw = item.get("deny_device_actions", [])
            if not isinstance(allow_raw, list) or not all(
                isinstance(v, str) and str(v).strip() for v in allow_raw
            ):
                raise TypeError("allow_device_actions must be a list[str]")
            if not isinstance(deny_raw, list) or not all(
                isinstance(v, str) and str(v).strip() for v in deny_raw
            ):
                raise TypeError("deny_device_actions must be a list[str]")
            self._mirrored_routes[local_id] = MirroredRoute(
                local_id=local_id,
                peer_id=peer_id,
                remote_device_id=remote_device_id,
                peer_router_rpc=peer_router_rpc,
                rpc_timeout_ms=int(item.get("rpc_timeout_ms", self._device_rpc_timeout_ms)),
                allow_device_actions=tuple(str(v).strip() for v in allow_raw),
                deny_device_actions=tuple(str(v).strip() for v in deny_raw),
                allow_lifecycle_ops=bool(item.get("allow_lifecycle_ops", False)),
                allow_admin_ops=bool(item.get("allow_admin_ops", False)),
                origin_instance_id=(
                    str(item.get("origin_instance_id", "")).strip()
                    or self._origin_instance_id
                ),
            )

    def _ensure_manager_worker(self) -> _ManagerWorker:
        if self._manager_worker is None or not self._manager_worker.is_alive():
            worker = _ManagerWorker(
                ctx=self._ctx,
                reply_queue=self._reply_queue,
                manager_rpc=self._manager_rpc,
                manager_pub=self._manager_pub,
                timeout_ms=self._device_rpc_timeout_ms,
                queue_max=self._manager_worker_queue_max,
            )
            worker.start()
            self._manager_worker = worker
        return self._manager_worker

    def _ensure_device_worker(self, device_id: str) -> _DeviceWorker:
        worker = self._device_workers.get(device_id)
        if worker is None or not worker.is_alive():
            worker = _DeviceWorker(
                device_id=device_id,
                ctx=self._ctx,
                reply_queue=self._reply_queue,
                manager_rpc=self._manager_rpc,
                manager_pub=self._manager_pub,
                device_rpc_timeout_ms=self._device_rpc_timeout_ms,
                interceptor_timeout_ms=self._interceptor_rpc_timeout_ms,
                queue_max=self._device_worker_queue_max,
            )
            worker.start()
            self._device_workers[device_id] = worker
        return worker

    def _ensure_mirrored_worker(self, device_id: str) -> _MirroredDeviceWorker:
        route = self._mirrored_routes[device_id]
        worker = self._mirrored_workers.get(device_id)
        if worker is None or not worker.is_alive():
            worker = _MirroredDeviceWorker(
                route=route,
                ctx=self._ctx,
                reply_queue=self._reply_queue,
                manager_rpc=self._manager_rpc,
                manager_pub=self._manager_pub,
                manager_timeout_ms=self._device_rpc_timeout_ms,
                queue_max=self._mirrored_worker_queue_max,
            )
            worker.start()
            self._mirrored_workers[device_id] = worker
        return worker

    def _ensure_process_worker(self, process_id: str) -> _ProcessWorker:
        worker = self._process_workers.get(process_id)
        if worker is None or not worker.is_alive():
            worker = _ProcessWorker(
                process_id=process_id,
                ctx=self._ctx,
                reply_queue=self._reply_queue,
                manager_rpc=self._manager_rpc,
                manager_pub=self._manager_pub,
                timeout_ms=self._device_rpc_timeout_ms,
                queue_max=self._process_worker_queue_max,
            )
            worker.start()
            self._process_workers[process_id] = worker
        return worker

    def _publish_manager_event(self, topic: str, payload: Json) -> None:
        if self._manager is None:
            return
        self._manager_helper.publish_event(
            self._manager,
            topic=topic,
            payload=payload,
            include_process_id=False,
            include_ts=True,
        )

    @staticmethod
    def _safe_queue_depth(q: queue.Queue | None) -> int | None:
        if q is None:
            return None
        try:
            return int(q.qsize())
        except Exception:
            return None

    def _reserve_inflight(self) -> bool:
        if self._inflight_count >= self._inflight_max:
            self._inflight_rejected += 1
            return False
        self._inflight_count += 1
        return True

    def _release_inflight(self) -> None:
        if self._inflight_count > 0:
            self._inflight_count -= 1

    @staticmethod
    def _worker_depth(worker: _BaseWorker | None) -> int | None:
        if worker is None:
            return None
        try:
            return worker.queue_depth()
        except Exception:
            return None

    @staticmethod
    def _worker_max(worker: _BaseWorker | None) -> int | None:
        if worker is None:
            return None
        try:
            return worker.queue_max()
        except Exception:
            return None

    def _overload_response(
        self,
        *,
        code: str,
        message: str,
        queue_depth: int | None = None,
        queue_max: int | None = None,
    ) -> Json:
        details: Json = {
            "inflight": int(self._inflight_count),
            "inflight_max": int(self._inflight_max),
            "retry_after_ms": 100,
        }
        if queue_depth is not None:
            details["queue_depth"] = int(queue_depth)
        if queue_max is not None:
            details["queue_max"] = int(queue_max)
        return {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details,
            },
        }

    def _reject_overload(
        self,
        identity: bytes,
        *,
        bucket: str,
        message: str,
        worker: _BaseWorker | None = None,
        request_id: Any = None,
    ) -> None:
        self._overload_rejected[bucket] = int(self._overload_rejected.get(bucket, 0)) + 1
        self._send_external_response(
            identity,
            self._overload_response(
                code="router_busy",
                message=message,
                queue_depth=self._worker_depth(worker),
                queue_max=self._worker_max(worker),
            ),
            request_id=request_id,
        )

    def _worker_queue_depth_snapshot(self) -> Json:
        device_workers: list[Json] = []
        process_workers: list[Json] = []
        mirrored_workers: list[Json] = []

        total_device_queue_depth = 0
        total_process_queue_depth = 0
        total_mirrored_queue_depth = 0

        for device_id in sorted(self._device_workers):
            worker = self._device_workers[device_id]
            depth = worker.queue_depth()
            if depth is not None:
                total_device_queue_depth += depth
            device_workers.append(
                {
                    "device_id": device_id,
                    "queue_depth": depth,
                    "queue_max": worker.queue_max(),
                    "alive": bool(worker.is_alive()),
                }
            )

        for process_id in sorted(self._process_workers):
            worker = self._process_workers[process_id]
            depth = worker.queue_depth()
            if depth is not None:
                total_process_queue_depth += depth
            process_workers.append(
                {
                    "process_id": process_id,
                    "queue_depth": depth,
                    "queue_max": worker.queue_max(),
                    "alive": bool(worker.is_alive()),
                }
            )

        for local_id in sorted(self._mirrored_workers):
            worker = self._mirrored_workers[local_id]
            depth = worker.queue_depth()
            if depth is not None:
                total_mirrored_queue_depth += depth
            mirrored_workers.append(
                {
                    "device_id": local_id,
                    "queue_depth": depth,
                    "queue_max": worker.queue_max(),
                    "alive": bool(worker.is_alive()),
                }
            )

        manager_worker_depth: int | None = None
        manager_worker_alive = False
        if self._manager_worker is not None:
            manager_worker_depth = self._manager_worker.queue_depth()
            manager_worker_alive = bool(self._manager_worker.is_alive())

        return {
            "reply_queue_depth": self._safe_queue_depth(self._reply_queue),
            "reply_queue_max": int(self._reply_queue_max),
            "manager_worker": {
                "queue_depth": manager_worker_depth,
                "queue_max": (
                    self._manager_worker.queue_max()
                    if self._manager_worker is not None
                    else int(self._manager_worker_queue_max)
                ),
                "alive": manager_worker_alive,
            },
            "device_workers": device_workers,
            "process_workers": process_workers,
            "mirrored_workers": mirrored_workers,
            "overload_rejected": dict(self._overload_rejected),
            "totals": {
                "device_worker_count": int(len(device_workers)),
                "process_worker_count": int(len(process_workers)),
                "mirrored_worker_count": int(len(mirrored_workers)),
                "device_worker_queue_depth": int(total_device_queue_depth),
                "process_worker_queue_depth": int(total_process_queue_depth),
                "mirrored_worker_queue_depth": int(total_mirrored_queue_depth),
                "inflight": int(self._inflight_count),
                "inflight_max": int(self._inflight_max),
                "inflight_rejected": int(self._inflight_rejected),
            },
        }

    def _invalidate_route_cache(self) -> None:
        self._route_cache.clear()

    def _register_command_interceptor_routes(
        self, process_id: str, routes_raw: Any, *, replace: bool
    ) -> list[Json]:
        if not isinstance(routes_raw, list):
            raise TypeError("routes must be a list")
        with self._route_lock:
            if replace:
                self._routes = [r for r in self._routes if r.process_id != process_id]
                self._invalidate_route_cache()
            seen: set[tuple[str, str, str]] = set()
            added: list[Json] = []
            for route in routes_raw:
                if not isinstance(route, dict):
                    raise TypeError("route must be an object")
                device_id = str(route.get("device_id", "")).strip()
                action = str(route.get("action", "")).strip()
                if not device_id or not action:
                    raise ValueError("route.device_id and route.action are required")
                key = (process_id, device_id, action)
                if key in seen:
                    continue
                seen.add(key)
                self._route_order += 1
                entry = CommandInterceptorRoute(
                    process_id=process_id,
                    device_id=device_id,
                    action=action,
                    order=self._route_order,
                )
                self._routes.append(entry)
                added.append(
                    {
                        "process_id": process_id,
                        "device_id": device_id,
                        "action": action,
                        "order": entry.order,
                    }
                )
            self._invalidate_route_cache()
        self._publish_manager_event(
            "manager.command_interceptor.routes_updated",
            {"process_id": process_id, "routes": added, "replace": replace},
        )
        return added

    def _unregister_command_interceptor_routes(self, process_id: str) -> bool:
        with self._route_lock:
            before = len(self._routes)
            self._routes = [r for r in self._routes if r.process_id != process_id]
            removed = len(self._routes) != before
            if removed:
                self._invalidate_route_cache()
            # Snapshot remaining routes inside the lock so the published event
            # matches the manager-side shape (process_id, removed, routes).
            routes_snapshot = [
                {
                    "process_id": r.process_id,
                    "device_id": r.device_id,
                    "action": r.action,
                    "order": r.order,
                }
                for r in self._routes
            ]
        self._publish_manager_event(
            "manager.command_interceptor.routes_unregistered",
            {
                "process_id": process_id,
                "removed": removed,
                "routes": routes_snapshot,
            },
        )
        return removed

    @staticmethod
    def _match_route(
        route: CommandInterceptorRoute, device_id: str, action: str
    ) -> bool:
        if route.device_id != "*" and route.device_id != device_id:
            return False
        if route.action != "*" and route.action != action:
            return False
        return True

    def _command_interceptor_chain(
        self, device_id: str, action: str
    ) -> list[CommandInterceptorRoute]:
        key = (device_id, action)
        cached = self._route_cache.get(key)
        if cached is not None:
            # Touch entry so eviction behaves as LRU.
            self._route_cache.pop(key, None)
            self._route_cache[key] = cached
            return list(cached)
        matches = [r for r in self._routes if self._match_route(r, device_id, action)]
        matches.sort(key=lambda r: r.order)
        ordered: list[CommandInterceptorRoute] = []
        seen: set[str] = set()
        for r in matches:
            if r.process_id in seen:
                continue
            seen.add(r.process_id)
            ordered.append(r)
        self._route_cache[key] = list(ordered)
        max_items = max(32, int(getattr(self, "_route_cache_max", 2048)))
        while len(self._route_cache) > max_items:
            oldest = next(iter(self._route_cache))
            self._route_cache.pop(oldest, None)
        return ordered

    def _handle_command_interceptor(self, req: Json) -> Json:
        rtype = str(req.get("type", ""))
        if rtype == "manager.interceptors.list":
            with self._route_lock:
                routes = [
                    {
                        "process_id": r.process_id,
                        "device_id": r.device_id,
                        "action": r.action,
                        "order": r.order,
                    }
                    for r in sorted(self._routes, key=lambda r: r.order)
                ]
            return {"ok": True, "result": {"routes": routes}}

        if rtype == "manager.interceptors.register":
            process_id = str(req.get("process_id", ""))
            routes_raw = req.get("routes", [])
            replace = bool(req.get("replace", False))
            if not process_id:
                return {
                    "ok": False,
                    "error": {"code": "invalid_register", "message": "missing process_id"},
                }
            try:
                routes = self._register_command_interceptor_routes(
                    process_id, routes_raw, replace=replace
                )
            except Exception as e:
                return {
                    "ok": False,
                    "error": {"code": "register_failed", "message": str(e)},
                }
            return {"ok": True, "result": {"routes": routes}}

        if rtype == "manager.interceptors.unregister":
            process_id = str(req.get("process_id", "")).strip()
            if not process_id:
                return {
                    "ok": False,
                    "error": {"code": "invalid_unregister", "message": "missing process_id"},
                }
            try:
                removed = self._unregister_command_interceptor_routes(process_id)
            except Exception as e:
                return {
                    "ok": False,
                    "error": {"code": "unregister_failed", "message": str(e)},
                }
            return {"ok": True, "result": {"process_id": process_id, "removed": removed}}

        return {"ok": False, "error": {"code": "unknown_request"}}

    def _handle_manager_pub(self) -> None:
        if self._manager_sub is None:
            return
        while True:
            try:
                topic_b, payload_b = self._manager_sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break
            topic = topic_b.decode("utf-8", errors="ignore")
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                continue
            if topic == "manager.driver_registered":
                device_id = str(payload.get("device_id", ""))
                rpc_endpoint = payload.get("rpc_endpoint")
                if device_id and isinstance(rpc_endpoint, str):
                    self._device_endpoints[device_id] = rpc_endpoint
            elif topic.startswith("manager.driver."):
                device_id = str(payload.get("device_id", ""))
                state = payload.get("state")
                if device_id and isinstance(state, str):
                    self._device_states[device_id] = state
                if topic in {"manager.driver.stopped", "manager.driver.failed"} and device_id:
                    self._device_endpoints.pop(device_id, None)
            elif topic == "manager.process.rpc_update":
                process_id = str(payload.get("process_id", ""))
                rpc_endpoint = payload.get("rpc_endpoint")
                if process_id and isinstance(rpc_endpoint, str):
                    self._process_endpoints[process_id] = rpc_endpoint
            elif topic.startswith("manager.process."):
                process_id = str(payload.get("process_id", ""))
                state = payload.get("state")
                if process_id and isinstance(state, str):
                    self._process_states[process_id] = state
                if topic in {"manager.process.exited", "manager.process.failed", "manager.process.removed"}:
                    if process_id:
                        self._process_endpoints.pop(process_id, None)

    def _drain_replies(self) -> None:
        while True:
            try:
                item = self._reply_queue.get_nowait()
            except queue.Empty:
                break
            request_id: Any = None
            if isinstance(item, _ReplyItem):
                identity = item.identity
                resp = item.response
                inflight_reserved = bool(item.inflight_reserved)
                request_id = item.request_id
            elif (
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], (bytes, bytearray))
                and isinstance(item[1], dict)
            ):
                # Backward compatibility for any queued legacy tuple entries.
                identity = bytes(item[0])
                resp = item[1]
                inflight_reserved = False
            else:
                continue
            if self._external_rpc is None:
                if inflight_reserved:
                    self._release_inflight()
                continue
            try:
                outbound = _inject_request_id(resp, request_id)
                if request_id is not None and isinstance(item, _ReplyItem):
                    outbound = dict(outbound)
                    outbound["request_id"] = request_id
                self._external_rpc.send_multipart([identity, json_dumps(outbound)])
            except Exception:
                pass
            finally:
                if inflight_reserved:
                    self._release_inflight()

    def _send_external_response(
        self, identity: bytes, resp: Json, *, request_id: Any = None
    ) -> None:
        if self._external_rpc is None:
            return
        self._external_rpc.send_multipart(
            [identity, json_dumps(_inject_request_id(resp, request_id))]
        )

    @staticmethod
    def _federation_hop_count(raw: object) -> int:
        if not isinstance(raw, dict):
            return 0
        try:
            return max(0, int(raw.get("hop_count", 0)))
        except Exception:
            return 0

    def _dispatch_mirrored_command(
        self,
        identity: bytes,
        req: Json,
        *,
        route: MirroredRoute,
        action: str,
    ) -> None:
        req_id = req.get("request_id")
        hop_count = self._federation_hop_count(req.get("federation"))
        if hop_count > 0:
            self._send_external_response(
                identity,
                {
                    "ok": False,
                    "error": {
                        "code": "federation_reexport_blocked",
                        "message": (
                            "mirrored devices cannot be re-exported to another peer"
                        ),
                    },
                },
                request_id=req_id,
            )
            return
        if not route.allows_device_action(action):
            self._send_external_response(
                identity,
                {
                    "ok": False,
                    "error": {
                        "code": "federation_acl_denied",
                        "message": (
                            f"federation policy denied mirrored command "
                            f"{route.local_id!r}.{action}"
                        ),
                    },
                },
                request_id=req_id,
            )
            return
        if not self._reserve_inflight():
            self._reject_overload(
                identity,
                bucket="inflight",
                message="router inflight request limit reached",
                request_id=req_id,
            )
            return
        task = _MirroredTask(
            identity=identity,
            request=req,
            route=route,
            inflight_reserved=True,
        )
        worker = self._ensure_mirrored_worker(route.local_id)
        if not worker.submit(task):
            self._release_inflight()
            self._reject_overload(
                identity,
                bucket="mirrored_worker",
                message="mirrored worker queue is full",
                worker=worker,
                request_id=req_id,
            )

    def _dispatch_device_command(self, identity: bytes, req: Json) -> None:
        device_id = str(req.get("device_id", ""))
        action = str(req.get("action", ""))
        params = req.get("params", {})
        req_id = req.get("request_id")
        if not device_id or not action or not isinstance(params, dict):
            resp = {"ok": False, "error": "invalid command"}
            self._send_external_response(identity, resp, request_id=req_id)
            return
        mirrored_route = self._mirrored_routes.get(device_id)
        if mirrored_route is not None:
            self._dispatch_mirrored_command(
                identity, req, route=mirrored_route, action=action
            )
            return

        chain: list[CommandInterceptorRoute]
        with self._route_lock:
            chain = self._command_interceptor_chain(device_id, action)

        interceptor_endpoints: dict[str, str | None] = {}
        interceptor_states: dict[str, str | None] = {}
        for route in chain:
            pid = route.process_id
            interceptor_endpoints[pid] = self._process_endpoints.get(pid)
            interceptor_states[pid] = self._process_states.get(pid)

        if not self._reserve_inflight():
            self._reject_overload(
                identity,
                bucket="inflight",
                message="router inflight request limit reached",
                request_id=req_id,
            )
            return
        task = _DeviceTask(
            identity=identity,
            request=req,
            device_id=device_id,
            action=action,
            params=params,
            request_id=req.get("request_id"),
            caller_process_id=req.get("caller_process_id"),
            source_kind=(
                str(req.get("source_kind")).strip()
                if req.get("source_kind") is not None
                and str(req.get("source_kind")).strip()
                else None
            ),
            source_id=(
                str(req.get("source_id")).strip()
                if req.get("source_id") is not None
                and str(req.get("source_id")).strip()
                else None
            ),
            device_endpoint=self._device_endpoints.get(device_id),
            device_state=self._device_states.get(device_id),
            chain=chain,
            interceptor_endpoints=interceptor_endpoints,
            interceptor_states=interceptor_states,
            inflight_reserved=True,
        )
        worker = self._ensure_device_worker(device_id)
        if not worker.submit(task):
            self._release_inflight()
            self._reject_overload(
                identity,
                bucket="device_worker",
                message="device worker queue is full",
                worker=worker,
                request_id=req_id,
            )

    def _dispatch_process_rpc(self, identity: bytes, req: Json) -> None:
        process_id = str(req.get("process_id", ""))
        request = req.get("request")
        request_id = req.get("request_id")
        if not process_id or not isinstance(request, dict):
            resp = {"ok": False, "error": {"code": "invalid_process_rpc"}}
            self._send_external_response(identity, resp, request_id=request_id)
            return
        action = str(request.get("type", "process.rpc") or "process.rpc")
        params_raw = request.get("params", {})
        params = params_raw if isinstance(params_raw, dict) else {}
        if request_id is None:
            request_id = request.get("request_id")
        caller_process_id = (
            str(req.get("caller_process_id", "")).strip() or None
        )
        source_kind = str(req.get("source_kind", "")).strip().lower() or None
        source_id = str(req.get("source_id", "")).strip() or None
        if source_kind is None:
            if caller_process_id is not None:
                source_kind = "process"
                if source_id is None:
                    source_id = caller_process_id
            else:
                source_kind = "manager"
                if source_id is None:
                    source_id = "rpc"
        elif source_kind == "process" and source_id is None and caller_process_id is not None:
            source_id = caller_process_id
        endpoint = self._process_endpoints.get(process_id)
        if endpoint is None and process_id == self._process_id and self._rpc_endpoint:
            endpoint = self._rpc_endpoint
            self._process_endpoints[process_id] = endpoint
        if not self._reserve_inflight():
            self._reject_overload(
                identity,
                bucket="inflight",
                message="router inflight request limit reached",
                request_id=request_id,
            )
            return
        task = _ProcessTask(
            identity=identity,
            process_id=process_id,
            request=request,
            endpoint=endpoint,
            action=action,
            params=params,
            request_id=request_id,
            caller_process_id=caller_process_id,
            source_kind=source_kind,
            source_id=source_id,
            process_state=self._process_states.get(process_id),
            inflight_reserved=True,
        )
        worker = self._ensure_process_worker(process_id)
        if not worker.submit(task):
            self._release_inflight()
            self._reject_overload(
                identity,
                bucket="process_worker",
                message="process worker queue is full",
                worker=worker,
                request_id=request_id,
            )

    def _dispatch_manager_rpc(self, identity: bytes, req: Json) -> None:
        req_id = req.get("request_id")
        if not self._reserve_inflight():
            self._reject_overload(
                identity,
                bucket="inflight",
                message="router inflight request limit reached",
                request_id=req_id,
            )
            return
        task = _ManagerTask(identity=identity, request=req, inflight_reserved=True)
        worker = self._ensure_manager_worker()
        if not worker.submit(task):
            self._release_inflight()
            self._reject_overload(
                identity,
                bucket="manager_worker",
                message="manager worker queue is full",
                worker=worker,
                request_id=req_id,
            )

    def _handle_external_rpc(self) -> None:
        if self._external_rpc is None:
            return
        try:
            identity, payload_b = self._external_rpc.recv_multipart()
        except Exception:
            return
        req = safe_json_loads(payload_b)
        if not isinstance(req, dict):
            resp = {"ok": False, "error": "invalid request"}
            self._send_external_response(identity, resp)
            return

        req_id = req.get("request_id")
        rtype = req.get("type")
        if rtype == "command":
            self._dispatch_device_command(identity, req)
            return
        if rtype == "manager.processes.rpc":
            self._dispatch_process_rpc(identity, req)
            return
        if rtype in {
            "manager.interceptors.register",
            "manager.interceptors.unregister",
            "manager.interceptors.list",
        }:
            resp = self._handle_command_interceptor(req)
            self._send_external_response(identity, resp, request_id=req_id)
            return
        if rtype == "manager.processes.rpc.advertise":
            process_id = str(req.get("process_id", ""))
            rpc_endpoint = req.get("rpc_endpoint")
            if process_id and isinstance(rpc_endpoint, str):
                self._process_endpoints[process_id] = rpc_endpoint
        self._dispatch_manager_rpc(identity, req)

    def _handle_rpc(self, req: Json) -> Json:
        rtype = str(req.get("type", ""))
        common = self._handle_common_rpc(req)
        if common is not None:
            return common
        if rtype == "process.capabilities":
            members = self._with_common_capabilities(
                [method("router.stats", params=None, doc="Return basic router stats.")]
            )
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": capabilities_payload(members),
            }
        if rtype == "router.stats":
            return {
                "request_id": req.get("request_id"),
                "ok": True,
                "result": {
                    "devices": len(self._device_endpoints),
                    "mirrored_devices": len(self._mirrored_routes),
                    "processes": len(self._process_endpoints),
                    "routes": len(self._routes),
                    "queue_depths": self._worker_queue_depth_snapshot(),
                },
            }
        return {"request_id": req.get("request_id"), "ok": False, "error": {"code": "unknown_request"}}

    def run(self) -> None:
        self._init_rpc_router()
        if self._process_id and self._rpc_endpoint:
            self._process_endpoints[self._process_id] = self._rpc_endpoint
        self._manager = self._init_manager_client(
            manager_rpc=self._manager_rpc,
            manager_pub=self._manager_pub,
            rpc_timeout_ms=self._device_rpc_timeout_ms,
            process_id=self._process_id,
            subscribe_telemetry=False,
        )
        self._advertise_process_rpc()

        self._external_rpc = self._ctx.socket(zmq.ROUTER)
        self._external_rpc.setsockopt(zmq.LINGER, 0)
        try:
            self._external_rpc.bind(self._external_rpc_bind)
        except Exception as e:
            self._external_rpc.close(0)
            raise RuntimeError(f"Router bind failed for {self._external_rpc_bind}: {e}") from e

        sub = self._ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt(zmq.SUBSCRIBE, b"manager.driver_")
        sub.setsockopt(zmq.SUBSCRIBE, b"manager.process.")
        sub.connect(self._manager_pub)
        self._manager_sub = sub

        self._start_heartbeat_thread(state_provider=lambda: "RUNNING")

        poller = zmq.Poller()
        poller.register(self._external_rpc, zmq.POLLIN)
        poller.register(self._manager_sub, zmq.POLLIN)
        if self._rpc_router is not None:
            poller.register(self._rpc_router, zmq.POLLIN)

        try:
            while not self._stop_evt.is_set():
                handlers: dict[zmq.Socket, Callable[[], None]] = {}
                if self._external_rpc is not None:
                    handlers[self._external_rpc] = self._handle_external_rpc
                if self._manager_sub is not None:
                    handlers[self._manager_sub] = self._handle_manager_pub
                if self._rpc_router is not None:
                    handlers[self._rpc_router] = self._drain_rpc
                poll_and_drain(poller, 50, handlers=handlers)
                self._drain_replies()
        finally:
            self.close()
