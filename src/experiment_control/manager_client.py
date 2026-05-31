from __future__ import annotations

import time
import uuid
from typing import Any

import zmq
from .types import Timestamp
from .utils.zmq_helpers import drain_multipart_nonblocking, json_dumps, json_loads, safe_json_loads

Json = dict[str, Any]


def _drain_stale_replies(sock: zmq.Socket) -> None:
    """Drop any buffered replies left behind by previous timed-out calls.

    DEALER sockets don't auto-correlate. If a prior `call()` raised
    zmq.Again before recv but the manager's reply arrived later, the
    reply sits in the socket's recv buffer; the next send/recv pair
    would pick it up and mis-attribute it to the new request. Drain
    before sending so the recv loop sees only fresh replies.
    """
    while True:
        try:
            if not sock.poll(0, zmq.POLLIN):
                break
            _ = sock.recv(zmq.NOBLOCK)
        except zmq.Again:
            break
        except Exception:
            break


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
        self.telemetry_drained_total = 0
        self.telemetry_last_drain_count = 0
        self.telemetry_last_drain_duration_s = 0.0
        self.telemetry_drain_limited_count = 0
        self.telemetry_parse_errors = 0
        # Set to True by `call()` whenever a call times out or otherwise
        # raises before a successful recv; the NEXT call drains stale
        # replies pre-send. Healthy callers pay zero extra syscalls.
        self._maybe_stale_reply = False

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
        # Build the outbound envelope. Process-side command callers get
        # source_kind/source_id stamped here for the manager's command
        # journal; transport-level request_id is added below for ALL
        # callers so the recv loop can discard stale replies.
        if isinstance(payload, dict):
            needs_command_stamps = (
                payload.get("type") == "command" and self._process_id
            )
            if needs_command_stamps or "request_id" not in payload:
                outbound = dict(payload)
                if needs_command_stamps:
                    if outbound.get("caller_process_id") is None:
                        outbound["caller_process_id"] = self._process_id
                    if outbound.get("source_kind") is None:
                        outbound["source_kind"] = "process"
                    if outbound.get("source_id") is None:
                        outbound["source_id"] = self._process_id
        # DEALER sockets do not auto-correlate replies. A previous call
        # that timed out (zmq.Again before recv) can leave its reply
        # buffered; the next call's bare recv() would mis-attribute it
        # to the new request. Stamp a transport-level request_id
        # (preserving any caller-supplied one) so the recv loop can
        # discard stale frames via request_id mismatch. After a known-
        # timed-out call we additionally drain leftover frames before
        # sending, so a mid-upgrade old manager (which may not echo
        # request_id) can't have its stale reply mistaken for the new
        # call's response.
        if isinstance(outbound, dict):
            if "request_id" not in outbound:
                if outbound is payload:
                    outbound = dict(payload)
                outbound["request_id"] = uuid.uuid4().hex
            expected_request_id = outbound.get("request_id")
        else:
            expected_request_id = None

        self._rpc.setsockopt(zmq.RCVTIMEO, timeout_ms)
        self._rpc.setsockopt(zmq.SNDTIMEO, timeout_ms)
        try:
            if self._maybe_stale_reply:
                _drain_stale_replies(self._rpc)
                self._maybe_stale_reply = False
            self._rpc.send(json_dumps(outbound))
            deadline = time.monotonic() + (timeout_ms / 1000.0)
            while True:
                remaining_ms = (deadline - time.monotonic()) * 1000.0
                if remaining_ms <= 0:
                    self._maybe_stale_reply = True
                    raise zmq.Again()
                if not self._rpc.poll(max(1, int(remaining_ms)), zmq.POLLIN):
                    continue
                raw = self._rpc.recv(zmq.NOBLOCK)
                resp = json_loads(raw)
                if not isinstance(resp, dict):
                    continue
                resp_rid = resp.get("request_id")
                if (
                    expected_request_id is not None
                    and resp_rid is not None
                    and resp_rid != expected_request_id
                ):
                    # Stale reply from a previous timed-out call (its
                    # send-time stamp doesn't match what we're waiting
                    # for); skip. Replies that don't carry request_id
                    # at all are passed through — some manager error
                    # paths (parse_error, invalid_request) reply before
                    # any request body is parsed and can't echo an id.
                    continue
                return resp
        except Exception:
            # Mark the socket as possibly carrying a stale reply so the
            # NEXT call's pre-send drain kicks in. Healthy calls pay no
            # extra syscalls; only post-timeout calls do.
            self._maybe_stale_reply = True
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

    def drain_telemetry(
        self,
        *,
        max_messages: int | None = 1000,
        max_duration_s: float | None = 0.1,
    ) -> dict[str, Any]:
        if self._sub is None:
            return {"count": 0, "limited": False, "duration_s": 0.0, "parse_errors": 0}

        def _handle(_topic_b: bytes, payload_b: bytes) -> bool:
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                return False
            self._handle_telemetry_update(payload)
            return True

        result = drain_multipart_nonblocking(
            self._sub,
            _handle,
            max_messages=max_messages,
            max_duration_s=max_duration_s,
        )
        self.telemetry_drained_total += result.count
        self.telemetry_last_drain_count = result.count
        self.telemetry_last_drain_duration_s = result.duration_s
        self.telemetry_parse_errors += result.parse_errors
        if result.limited:
            self.telemetry_drain_limited_count += 1
        return {
            "count": result.count,
            "limited": result.limited,
            "duration_s": result.duration_s,
            "parse_errors": result.parse_errors,
        }

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

