
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
    chain: list[CommandInterceptorRoute]
    interceptor_endpoints: dict[str, str | None]
    interceptor_states: dict[str, str | None]


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


@dataclass(frozen=True)
class _ManagerTask:
    identity: bytes
    request: Json


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


class _BaseWorker(threading.Thread):
    def __init__(self, *, name: str, ctx: zmq.Context, reply_queue: queue.Queue) -> None:
        super().__init__(name=name, daemon=True)
        self._ctx = ctx
        self._reply_queue = reply_queue
        self._queue: queue.Queue = queue.Queue()
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()
        self._queue.put(None)

    def submit(self, task: object) -> None:
        self._queue.put(task)


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
    ) -> None:
        super().__init__(name=f"process-rpc-{process_id}", ctx=ctx, reply_queue=reply_queue)
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
                self._reply_queue.put((task.identity, resp))
                continue
            resp = self._call(task.endpoint, task.request)
            if resp is None:
                resp = {"ok": False, "error": "timeout"}
            self._publish_process_command(task, resp)
            self._reply_queue.put((task.identity, resp))
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
    ) -> None:
        super().__init__(name="manager-rpc", ctx=ctx, reply_queue=reply_queue)
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
            self._reply_queue.put((task.identity, resp))
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
    ) -> None:
        super().__init__(name=f"device-rpc-{device_id}", ctx=ctx, reply_queue=reply_queue)
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

    @staticmethod
    def _interceptor_error(
        *,
        code: str,
        message: str,
        process_id: str,
        device_id: str,
        action: str,
        interceptor_id: str | None = None,
        rule: str | None = None,
        details: Json | None = None,
    ) -> Json:
        err: Json = {
            "kind": "command_interceptor",
            "code": code,
            "message": message,
            "process_id": process_id,
            "device_id": device_id,
            "action": action,
        }
        if interceptor_id is not None:
            err["interceptor_id"] = interceptor_id
        if rule is not None:
            err["rule"] = rule
        if details is not None:
            err["details"] = details
        return err

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
        if not task.chain:
            return (
                True,
                {
                    "device_id": task.device_id,
                    "action": task.action,
                    "params": task.params,
                },
                None,
            )
        cur_cmd: Json = {
            "device_id": task.device_id,
            "action": task.action,
            "params": task.params,
        }
        for route in task.chain:
            process_id = route.process_id
            endpoint = task.interceptor_endpoints.get(process_id)
            state = task.interceptor_states.get(process_id)
            if not endpoint or (state and state not in _ALLOWED_PROCESS_STATES):
                err = self._interceptor_error(
                    code="INTERCEPTOR_UNAVAILABLE",
                    message=(
                        f"Interceptor {process_id!r} unavailable for "
                        f"{task.device_id}.{task.action}"
                    ),
                    process_id=process_id,
                    device_id=task.device_id,
                    action=task.action,
                )
                self._publish_event(
                    "manager.command_interceptor.error",
                    {"error": err, "command": cur_cmd},
                )
                return False, None, err

            meta: Json = {"request_id": task.request_id, "t_mono": time.monotonic()}
            if task.caller_process_id:
                meta["caller_process_id"] = task.caller_process_id
            req = {"type": "command_interceptor.check", "command": cur_cmd, "meta": meta}
            resp = self._call_interceptor(
                process_id=process_id, endpoint=endpoint, request=req
            )
            if resp is None:
                err = self._interceptor_error(
                    code="INTERCEPTOR_TIMEOUT",
                    message=(
                        f"Interceptor {process_id!r} timed out for "
                        f"{task.device_id}.{task.action}"
                    ),
                    process_id=process_id,
                    device_id=task.device_id,
                    action=task.action,
                )
                self._publish_event(
                    "manager.command_interceptor.error",
                    {"error": err, "command": cur_cmd},
                )
                return False, None, err

            if not isinstance(resp, dict) or resp.get("ok") is False:
                err = self._interceptor_error(
                    code="INTERCEPTOR_BAD_RESPONSE",
                    message=f"Interceptor {process_id!r} returned invalid response",
                    process_id=process_id,
                    device_id=task.device_id,
                    action=task.action,
                    details={"response": resp},
                )
                self._publish_event(
                    "manager.command_interceptor.error",
                    {"error": err, "command": cur_cmd},
                )
                return False, None, err

            allow = resp.get("allow")
            if allow is True:
                if "command" in resp:
                    new_cmd_raw = resp.get("command")
                    if not isinstance(new_cmd_raw, dict):
                        err = self._interceptor_error(
                            code="INTERCEPTOR_BAD_RESPONSE",
                            message=f"Interceptor {process_id!r} returned invalid command",
                            process_id=process_id,
                            device_id=task.device_id,
                            action=task.action,
                        )
                        self._publish_event(
                            "manager.command_interceptor.error",
                            {"error": err, "command": cur_cmd},
                        )
                        return False, None, err
                    new_device = str(new_cmd_raw.get("device_id", task.device_id))
                    new_action = str(new_cmd_raw.get("action", task.action))
                    if new_device != task.device_id or new_action != task.action:
                        err = self._interceptor_error(
                            code="INTERCEPTOR_BAD_RESPONSE",
                            message=(
                                f"Interceptor {process_id!r} attempted to change route"
                            ),
                            process_id=process_id,
                            device_id=task.device_id,
                            action=task.action,
                        )
                        self._publish_event(
                            "manager.command_interceptor.error",
                            {"error": err, "command": cur_cmd},
                        )
                        return False, None, err
                    if "params" in new_cmd_raw:
                        new_params = new_cmd_raw.get("params")
                    else:
                        new_params = cur_cmd.get("params")
                    if not isinstance(new_params, dict):
                        err = self._interceptor_error(
                            code="INTERCEPTOR_BAD_RESPONSE",
                            message=f"Interceptor {process_id!r} returned invalid params",
                            process_id=process_id,
                            device_id=task.device_id,
                            action=task.action,
                        )
                        self._publish_event(
                            "manager.command_interceptor.error",
                            {"error": err, "command": cur_cmd},
                        )
                        return False, None, err
                    new_cmd = {
                        "device_id": task.device_id,
                        "action": task.action,
                        "params": new_params,
                    }
                    if new_cmd != cur_cmd:
                        self._publish_event(
                            "manager.command_interceptor.modified",
                            {
                                "process_id": process_id,
                                "interceptor_id": resp.get("interceptor_id"),
                                "rule": resp.get("rule"),
                                "note": resp.get("note"),
                                "before": cur_cmd,
                                "after": new_cmd,
                            },
                        )
                        cur_cmd = new_cmd
                continue

            if allow is False:
                inner = resp.get("error") or {}
                inner_code = str(inner.get("code", "CONDITION_FAILED"))
                inner_msg = str(inner.get("message", "Command rejected by interceptor"))
                err = self._interceptor_error(
                    code="INTERCEPTOR_REJECTED",
                    message=inner_msg,
                    process_id=process_id,
                    device_id=task.device_id,
                    action=task.action,
                    interceptor_id=resp.get("interceptor_id"),
                    rule=resp.get("rule"),
                    details={
                        "code": inner_code,
                        "message": inner_msg,
                        "details": inner.get("details", {}),
                    },
                )
                self._publish_event(
                    "manager.command_interceptor.error",
                    {"error": err, "command": cur_cmd},
                )
                return False, None, err

            err = self._interceptor_error(
                code="INTERCEPTOR_BAD_RESPONSE",
                message=f"Interceptor {process_id!r} returned invalid response",
                process_id=process_id,
                device_id=task.device_id,
                action=task.action,
                details={"response": resp},
            )
            self._publish_event(
                "manager.command_interceptor.error", {"error": err, "command": cur_cmd}
            )
            return False, None, err

        return True, cur_cmd, None

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
                resp: Json = {"ok": False, "error": "driver not running"}
                self._reply_queue.put((task.identity, resp))
                continue
            ok, new_cmd, err = self._apply_command_interceptors(task)
            if not ok:
                resp = {"ok": False, "error": err}
                self._reply_queue.put((task.identity, resp))
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
                cmd_payload = {
                    "version": 1,
                    "device_id": task.device_id,
                    "action": task.action,
                    "params_json": _safe_json(new_cmd.get("params", task.params)),
                    "ok": False,
                    "status": None,
                    "error": str(e),
                    "result_json": "",
                    "request_id": task.request_id,
                    "caller_process_id": task.caller_process_id,
                    "source_kind": task.source_kind,
                    "source_id": task.source_id,
                    "is_remote_target": False,
                    "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
                }
                self._publish_event("manager.command", cmd_payload)
                resp = {"ok": False, "error": str(e)}
            self._reply_queue.put((task.identity, resp))

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
    ) -> None:
        super().__init__(
            name=f"mirrored-rpc-{route.local_id}", ctx=ctx, reply_queue=reply_queue
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
            self._reply_queue.put((task.identity, resp))
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
        self._route_cache: dict[tuple[str, str], list[CommandInterceptorRoute]] = {}
        self._route_order = 0

        self._device_workers: dict[str, _DeviceWorker] = {}
        self._mirrored_routes: dict[str, MirroredRoute] = {}
        self._mirrored_workers: dict[str, _MirroredDeviceWorker] = {}
        self._process_workers: dict[str, _ProcessWorker] = {}
        self._manager_worker: _ManagerWorker | None = None
        self._reply_queue: queue.Queue = queue.Queue()
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
        return ordered

    def _handle_command_interceptor(self, req: Json) -> Json:
        rtype = str(req.get("type", ""))
        if rtype == "command_interceptor.list":
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

        if rtype == "command_interceptor.register":
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
                identity, resp = self._reply_queue.get_nowait()
            except queue.Empty:
                break
            if self._external_rpc is None:
                continue
            try:
                self._external_rpc.send_multipart([identity, json_dumps(resp)])
            except Exception:
                pass

    def _send_external_response(self, identity: bytes, resp: Json) -> None:
        if self._external_rpc is None:
            return
        self._external_rpc.send_multipart([identity, json_dumps(resp)])

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
            )
            return
        task = _MirroredTask(identity=identity, request=req, route=route)
        self._ensure_mirrored_worker(route.local_id).submit(task)

    def _dispatch_device_command(self, identity: bytes, req: Json) -> None:
        device_id = str(req.get("device_id", ""))
        action = str(req.get("action", ""))
        params = req.get("params", {})
        if not device_id or not action or not isinstance(params, dict):
            resp = {"ok": False, "error": "invalid command"}
            self._send_external_response(identity, resp)
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
            chain=chain,
            interceptor_endpoints=interceptor_endpoints,
            interceptor_states=interceptor_states,
        )
        self._ensure_device_worker(device_id).submit(task)

    def _dispatch_process_rpc(self, identity: bytes, req: Json) -> None:
        process_id = str(req.get("process_id", ""))
        request = req.get("request")
        if not process_id or not isinstance(request, dict):
            resp = {"ok": False, "error": {"code": "invalid_process_rpc"}}
            self._send_external_response(identity, resp)
            return
        action = str(request.get("type", "process.rpc") or "process.rpc")
        params_raw = request.get("params", {})
        params = params_raw if isinstance(params_raw, dict) else {}
        request_id = req.get("request_id")
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
        )
        self._ensure_process_worker(process_id).submit(task)

    def _dispatch_manager_rpc(self, identity: bytes, req: Json) -> None:
        task = _ManagerTask(identity=identity, request=req)
        self._ensure_manager_worker().submit(task)

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

        rtype = req.get("type")
        if rtype == "command":
            self._dispatch_device_command(identity, req)
            return
        if rtype == "process.rpc":
            self._dispatch_process_rpc(identity, req)
            return
        if rtype in {"command_interceptor.register", "command_interceptor.list"}:
            resp = self._handle_command_interceptor(req)
            self._send_external_response(identity, resp)
            return
        if rtype == "process.rpc.advertise":
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
