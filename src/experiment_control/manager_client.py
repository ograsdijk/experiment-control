from __future__ import annotations

import time
from typing import Any

import zmq
from .types import Timestamp
from .utils.zmq_helpers import json_dumps, json_loads, safe_json_loads

Json = dict[str, Any]


class ManagerClient:
    def __init__(
        self,
        *,
        ctx: zmq.Context,
        manager_rpc: str,
        manager_pub: str,
        rpc_timeout_ms: int,
        process_id: str | None = None,
        subscribe_telemetry: bool = True,
    ) -> None:
        self._ctx = ctx
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._rpc_timeout_ms = int(rpc_timeout_ms)
        self._process_id = process_id

        self._rpc = self._ctx.socket(zmq.DEALER)
        self._rpc.setsockopt(zmq.RCVTIMEO, self._rpc_timeout_ms)
        self._rpc.setsockopt(zmq.SNDTIMEO, self._rpc_timeout_ms)
        self._rpc.setsockopt(zmq.LINGER, 0)
        self._rpc.connect(self._manager_rpc)

        self._sub: zmq.Socket | None = None
        if subscribe_telemetry:
            sub = self._ctx.socket(zmq.SUB)
            sub.setsockopt(zmq.SUBSCRIBE, b"manager.telemetry_update")
            sub.setsockopt(zmq.RCVTIMEO, 100)
            sub.setsockopt(zmq.LINGER, 0)
            sub.connect(self._manager_pub)
            self._sub = sub

        self._telemetry_cache: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def sub_socket(self) -> zmq.Socket | None:
        return self._sub

    def close(self) -> None:
        try:
            self._rpc.close(0)
        except Exception:
            pass
        if self._sub is not None:
            try:
                self._sub.close(0)
            except Exception:
                pass

    def advertise_process_rpc(self, *, process_id: str, rpc_endpoint: str) -> None:
        payload = {
            "type": "manager.processes.rpc.advertise",
            "process_id": process_id,
            "rpc_endpoint": rpc_endpoint,
        }
        self.call(payload)

    def call(self, payload: Json, *, timeout_ms: int | None = None) -> Json | None:
        if timeout_ms is None:
            timeout_ms = self._rpc_timeout_ms
        else:
            timeout_ms = int(timeout_ms)
        outbound: Json = payload
        if isinstance(payload, dict) and payload.get("type") == "command" and self._process_id:
            outbound = dict(payload)
            if outbound.get("caller_process_id") is None:
                outbound["caller_process_id"] = self._process_id
            if outbound.get("source_kind") is None:
                outbound["source_kind"] = "process"
            if outbound.get("source_id") is None:
                outbound["source_id"] = self._process_id
        self._rpc.setsockopt(zmq.RCVTIMEO, timeout_ms)
        self._rpc.setsockopt(zmq.SNDTIMEO, timeout_ms)
        try:
            self._rpc.send(json_dumps(outbound))
            raw = self._rpc.recv()
            resp = json_loads(raw)
            return resp if isinstance(resp, dict) else None
        except Exception:
            return None

    def publish_event(
        self,
        *,
        topic: str,
        payload: Json,
        include_process_id: bool = True,
        include_ts: bool = True,
        severity: str | None = None,
        device_id: str | None = None,
    ) -> None:
        data = dict(payload)
        if include_process_id and self._process_id and "process_id" not in data:
            data["process_id"] = self._process_id
        if include_ts and "ts" not in data:
            data["ts"] = {"t_wall": time.time(), "t_mono": time.monotonic()}
        if severity is not None and "severity" not in data:
            data["severity"] = severity
        if device_id is not None and "device_id" not in data:
            data["device_id"] = device_id
        req = {"type": "manager.events.publish", "topic": topic, "payload": data}
        self.call(req)

    def drain_telemetry(self) -> None:
        if self._sub is None:
            return
        while True:
            try:
                _topic_b, payload_b = self._sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break
            try:
                payload = safe_json_loads(payload_b)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                self._handle_telemetry_update(payload)

    def _handle_telemetry_update(self, payload: Json) -> None:
        device_id = str(payload.get("device_id", ""))
        signals = payload.get("signals", {})
        ts_raw = payload.get("ts", {}) or {}
        if not device_id or not isinstance(signals, dict):
            return

        try:
            bundle_ts = Timestamp(
                t_wall=float(ts_raw.get("t_wall")),
                t_mono=float(ts_raw.get("t_mono")),
            )
        except Exception:
            bundle_ts = Timestamp(t_wall=time.time(), t_mono=time.monotonic())

        now_mono = time.monotonic()
        device_cache = self._telemetry_cache.setdefault(device_id, {})
        for name, s in signals.items():
            if not isinstance(name, str) or not isinstance(s, dict):
                continue
            sig_ts = s.get("ts") or {}
            if isinstance(sig_ts, dict):
                t_wall = sig_ts.get("t_wall", bundle_ts.t_wall)
                t_mono = sig_ts.get("t_mono", bundle_ts.t_mono)
            else:
                t_wall = bundle_ts.t_wall
                t_mono = bundle_ts.t_mono
            device_cache[name] = {
                "value": s.get("value"),
                "units": s.get("units"),
                "quality": s.get("quality"),
                "t_wall": t_wall,
                "t_mono": t_mono,
                "t_mono_recv": now_mono,
            }

    def get_latest(self, device_id: str, signal: str) -> dict[str, Any] | None:
        device_cache = self._telemetry_cache.get(device_id, {})
        sample = device_cache.get(signal)
        if sample is None:
            return None
        now = time.monotonic()
        t_mono_recv = sample.get("t_mono_recv")
        t_mono = sample.get("t_mono")
        age_s = None
        if t_mono_recv is not None:
            age_s = now - float(t_mono_recv)
        elif t_mono is not None:
            age_s = now - float(t_mono)
        out = dict(sample)
        out["age_s"] = age_s
        return out

