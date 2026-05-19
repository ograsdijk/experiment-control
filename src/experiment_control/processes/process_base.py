from __future__ import annotations

import os
import threading
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import zmq

from ..capabilities import method
from ..manager_client import ManagerClient
from ..types import MemberSpec
from ..utils.zmq_helpers import json_dumps, poll_and_drain, safe_json_loads


class ManagedProcessBase:
    def __init__(
        self,
        *,
        process_id: str | None,
        heartbeat_endpoint: str | None,
        process_data_endpoint: str | None = None,
        heartbeat_period_s: float = 1.0,
        ctx: zmq.Context | None = None,
    ) -> None:
        self._process_id = process_id
        self._heartbeat_endpoint = heartbeat_endpoint
        self._process_data_endpoint = process_data_endpoint
        self._heartbeat_period_s = float(heartbeat_period_s)
        self._ctx = ctx or zmq.Context.instance()
        self._stop_evt = threading.Event()
        self._heartbeat_pub: zmq.Socket | None = None
        self._process_data_pub: zmq.Socket | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._rpc_router: zmq.Socket | None = None
        self._rpc_endpoint: str | None = None
        self._manager: ManagerClient | None = None
        self._poller: zmq.Poller | None = None
        self._progress_lock = threading.Lock()
        self._phase: str | None = None
        self._phase_detail: str | None = None
        self._last_progress_wall: float | None = None
        self._last_progress_mono: float | None = None
        self._last_exception: str | None = None
        self._last_traceback_summary: str | None = None

    @staticmethod
    def _rpc_request_id(req_or_id: dict[str, Any] | Any) -> Any:
        if isinstance(req_or_id, dict):
            return req_or_id.get("request_id")
        return req_or_id

    @classmethod
    def _rpc_ok(cls, req_or_id: dict[str, Any] | Any, *, result: Any = None) -> dict[str, Any]:
        return {
            "request_id": cls._rpc_request_id(req_or_id),
            "ok": True,
            "result": result,
        }

    @classmethod
    def _rpc_err(
        cls,
        req_or_id: dict[str, Any] | Any,
        *,
        code: str,
        message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        err: dict[str, Any] = {"code": str(code)}
        if message is not None:
            err["message"] = str(message)
        if extra:
            err.update(extra)
        return {
            "request_id": cls._rpc_request_id(req_or_id),
            "ok": False,
            "error": err,
        }

    @classmethod
    def _rpc_unknown(cls, req_or_id: dict[str, Any] | Any) -> dict[str, Any]:
        return cls._rpc_err(req_or_id, code="unknown_request")

    @classmethod
    def _rpc_invalid_params(
        cls,
        req_or_id: dict[str, Any] | Any,
        *,
        message: str | None = None,
    ) -> dict[str, Any]:
        return cls._rpc_err(req_or_id, code="invalid_params", message=message)

    def _graceful_stop(self) -> None:
        """Hook for subclasses to implement graceful stop behavior."""
        self._stop_evt.set()

    def _unregister_command_interceptor_routes(self) -> None:
        if not self._process_id:
            raise RuntimeError("Cannot unregister command interceptor routes without process_id")
        if self._manager is None:
            raise RuntimeError("Cannot unregister command interceptor routes before manager init")
        resp = self._manager.call(
            {
                "type": "manager.interceptors.unregister",
                "process_id": self._process_id,
            }
        )
        if not isinstance(resp, dict) or not resp.get("ok", False):
            raise RuntimeError(f"Failed to unregister command interceptor routes: {resp}")

    def _handle_common_rpc(self, req: dict[str, Any]) -> dict[str, Any] | None:
        rtype = str(req.get("type", ""))
        if rtype == "process.stop":
            try:
                self._graceful_stop()
            except Exception as e:
                return self._rpc_err(req, code="stop_failed", message=str(e))
            return self._rpc_ok(req, result={"status": "stopping"})
        return None

    @staticmethod
    def _common_capability_methods() -> list[MemberSpec]:
        return [method("process.stop", params=None, doc="Request graceful process stop.")]

    def _with_common_capabilities(
        self, members: list[MemberSpec]
    ) -> list[MemberSpec]:
        if not all(isinstance(item, MemberSpec) for item in members):
            raise TypeError("members must be list[MemberSpec]")
        names = {item.name for item in members}
        out = list(members)
        for item in self._common_capability_methods():
            if item.name and item.name not in names:
                out.append(item)
        return out

    def _set_phase(self, phase: str, detail: str | None = None) -> None:
        phase_text = str(phase or "").strip() or None
        detail_text = str(detail).strip() if detail is not None else None
        with self._progress_lock:
            self._phase = phase_text
            self._phase_detail = detail_text or None

    def _mark_progress(self, detail: str | None = None) -> None:
        now_wall = time.time()
        now_mono = time.monotonic()
        detail_text = str(detail).strip() if detail is not None else None
        with self._progress_lock:
            if detail_text:
                self._phase_detail = detail_text
            self._last_progress_wall = now_wall
            self._last_progress_mono = now_mono

    def _record_exception(self, exc: BaseException, phase: str | None = None) -> None:
        if phase is not None:
            self._set_phase(phase)
        summary = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        tb_summary = "".join(traceback.format_exception(exc)).strip()
        if len(tb_summary) > 2000:
            tb_summary = tb_summary[-2000:]
        with self._progress_lock:
            self._last_exception = summary
            self._last_traceback_summary = tb_summary

    @contextmanager
    def _phase_context(self, phase: str, detail: str | None = None) -> Iterator[None]:
        self._set_phase(phase, detail)
        try:
            yield
            self._mark_progress()
        except Exception as exc:
            self._record_exception(exc, phase=phase)
            raise

    def _heartbeat_extra_fields(self) -> dict[str, Any]:
        with self._progress_lock:
            out: dict[str, Any] = {}
            if self._phase is not None:
                out["phase"] = self._phase
            if self._phase_detail is not None:
                out["detail"] = self._phase_detail
            if self._last_progress_wall is not None:
                out["last_progress_wall"] = self._last_progress_wall
            if self._last_progress_mono is not None:
                out["last_progress_mono"] = self._last_progress_mono
            if self._last_exception is not None:
                out["last_exception"] = self._last_exception
            if self._last_traceback_summary is not None:
                out["last_traceback_summary"] = self._last_traceback_summary
            return out

    def _start_heartbeat_thread(
        self, *, state_provider: Callable[[], str | None] | None = None
    ) -> None:
        if not self._process_id or not self._heartbeat_endpoint:
            return

        pub = self._ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        try:
            pub.bind(self._heartbeat_endpoint)
        except zmq.ZMQError as e:
            pub.close(0)
            raise RuntimeError(
                f"Heartbeat bind failed for {self._heartbeat_endpoint}: {e}."
                " The endpoint is likely already in use (stale process or duplicate)."
            )

        pid = os.getpid()
        period = max(0.1, float(self._heartbeat_period_s))

        def loop() -> None:
            while not self._stop_evt.is_set():
                msg: dict[str, Any] = {
                    "process_id": self._process_id,
                    "pid": pid,
                    "ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
                }
                if self._rpc_endpoint:
                    msg["rpc_endpoint"] = self._rpc_endpoint
                if state_provider is not None:
                    try:
                        state = state_provider()
                    except Exception:
                        state = None
                    if state is not None:
                        msg["state"] = state
                msg.update(self._heartbeat_extra_fields())
                topic = f"process/{self._process_id}/heartbeat".encode("utf-8")
                try:
                    pub.send_multipart([topic, json_dumps(msg)])
                except Exception:
                    pass
                self._stop_evt.wait(period)

        self._heartbeat_pub = pub
        t = threading.Thread(target=loop, daemon=True)
        self._heartbeat_thread = t
        t.start()

    def _start_process_data_pub(self, *, sndhwm: int = 20_000) -> None:
        if self._process_data_pub is not None:
            return
        if not self._process_data_endpoint:
            return
        pub = self._ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.setsockopt(zmq.SNDHWM, max(1, int(sndhwm)))
        try:
            pub.bind(self._process_data_endpoint)
        except zmq.ZMQError as e:
            pub.close(0)
            raise RuntimeError(
                f"Process data PUB bind failed for {self._process_data_endpoint}: {e}."
                " The endpoint is likely already in use (stale process or duplicate)."
            )
        self._process_data_pub = pub

    def _publish_process_event(
        self,
        *,
        topic: str,
        payload: dict[str, Any],
        include_process_id: bool = True,
        include_ts: bool = True,
    ) -> bool:
        pub = self._process_data_pub
        if pub is None:
            return False
        topic_text = str(topic or "").strip()
        if not topic_text:
            return False
        data = dict(payload)
        if include_process_id and self._process_id and "process_id" not in data:
            data["process_id"] = self._process_id
        if include_ts and "ts" not in data:
            data["ts"] = {"t_wall": time.time(), "t_mono": time.monotonic()}
        try:
            pub.send_multipart(
                [topic_text.encode("utf-8"), json_dumps(data)],
                flags=zmq.NOBLOCK,
            )
            return True
        except zmq.Again:
            return False
        except Exception:
            return False

    def _stop_process_data_pub(self) -> None:
        if self._process_data_pub is None:
            return
        try:
            self._process_data_pub.setsockopt(zmq.LINGER, 0)
        except Exception:
            pass
        try:
            self._process_data_pub.close(0)
        except Exception:
            pass
        self._process_data_pub = None

    def _stop_heartbeat_thread(self, *, timeout_s: float = 2.0) -> None:
        t = self._heartbeat_thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout_s)
        if self._heartbeat_pub is not None:
            try:
                self._heartbeat_pub.setsockopt(zmq.LINGER, 0)
            except Exception:
                pass
            try:
                self._heartbeat_pub.close(0)
            except Exception:
                pass
        self._heartbeat_pub = None
        self._heartbeat_thread = None

    def _close_rpc_router(self) -> None:
        if self._rpc_router is None:
            return
        try:
            self._rpc_router.setsockopt(zmq.LINGER, 0)
        except Exception:
            pass
        try:
            self._rpc_router.close(0)
        except Exception:
            pass
        self._rpc_router = None

    def close(self) -> None:
        self._stop_evt.set()
        try:
            self._stop_process_data_pub()
        except Exception:
            pass
        try:
            self._stop_heartbeat_thread()
        except Exception:
            pass
        if self._manager is not None:
            try:
                self._manager.close()
            except Exception:
                pass
        try:
            self._close_rpc_router()
        except Exception:
            pass

    def _init_rpc_router(self) -> None:
        if self._rpc_router is not None:
            return
        sock = self._ctx.socket(zmq.ROUTER)
        sock.setsockopt(zmq.LINGER, 0)
        sock.bind("tcp://127.0.0.1:*")
        self._rpc_router = sock
        self._rpc_endpoint = sock.getsockopt_string(zmq.LAST_ENDPOINT)

    def _init_manager_client(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        rpc_timeout_ms: int,
        process_id: str | None = None,
        subscribe_telemetry: bool = True,
    ) -> ManagerClient:
        if process_id is None:
            process_id = self._process_id
        client = ManagerClient(
            ctx=self._ctx,
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=int(rpc_timeout_ms),
            process_id=process_id,
            subscribe_telemetry=subscribe_telemetry,
        )
        self._manager = client
        return client

    def _advertise_process_rpc(self) -> None:
        if self._manager is None:
            return
        if self._process_id and self._rpc_endpoint:
            self._manager.advertise_process_rpc(
                process_id=self._process_id,
                rpc_endpoint=self._rpc_endpoint,
            )

    def _init_poller(
        self,
        *,
        include_rpc: bool = True,
        include_sub: bool = True,
        extra: list[tuple[zmq.Socket, int]] | None = None,
    ) -> zmq.Poller:
        poller = zmq.Poller()
        if include_rpc and self._rpc_router is not None:
            poller.register(self._rpc_router, zmq.POLLIN)
        if include_sub and self._manager is not None and self._manager.sub_socket is not None:
            poller.register(self._manager.sub_socket, zmq.POLLIN)
        if extra:
            for sock, flags in extra:
                poller.register(sock, flags)
        self._poller = poller
        return poller

    def _poll_and_drain(self, timeout_ms: int) -> dict[zmq.Socket, int]:
        if self._poller is None:
            return {}
        handlers: dict[zmq.Socket, Callable[[], None]] = {}
        drain_result: dict[str, Any] | None = None
        if self._manager is not None and self._manager.sub_socket is not None:

            def _drain_manager_telemetry() -> None:
                nonlocal drain_result
                self._set_phase("drain_telemetry")
                drain_result = self._manager.drain_telemetry()
                self._mark_progress(
                    f"drained={drain_result['count']} limited={drain_result['limited']}"
                )

            handlers[self._manager.sub_socket] = _drain_manager_telemetry
        if self._rpc_router is not None:
            handlers[self._rpc_router] = self._drain_rpc
        self._set_phase("poll", f"timeout_ms={int(timeout_ms)}")
        events = poll_and_drain(self._poller, timeout_ms, handlers=handlers)
        if drain_result is None:
            self._mark_progress()
        return events

    def _handle_rpc(self, req: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("RPC handler not implemented")

    def _drain_rpc(self) -> None:
        if self._rpc_router is None:
            return
        try:
            identity, payload_b = self._rpc_router.recv_multipart()
        except Exception:
            return
        try:
            req = safe_json_loads(payload_b)
        except Exception:
            req = None
        if not isinstance(req, dict):
            resp = {"request_id": None, "ok": False, "error": {"code": "bad_request"}}
        else:
            try:
                resp = self._handle_rpc(req)
            except NotImplementedError:
                resp = {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "not_implemented"},
                }
            except Exception as e:
                resp = {
                    "request_id": req.get("request_id"),
                    "ok": False,
                    "error": {"code": "rpc_exception", "message": str(e)},
                }
        try:
            self._rpc_router.send_multipart([identity, json_dumps(resp)])
        except Exception:
            pass
