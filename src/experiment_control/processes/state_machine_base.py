from __future__ import annotations

import time
from typing import Any

import zmq

from ..capabilities import capabilities_payload, method, param
from .process_base import ManagedProcessBase

Json = dict[str, Any]


class StateMachineProcessBase(ManagedProcessBase):
    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str,
        rpc_namespace: str,
        rpc_timeout_ms: int = 2000,
        heartbeat_endpoint: str | None = None,
        heartbeat_period_s: float = 1.0,
        tick_s: float = 0.2,
        initial_state: str = "IDLE",
        allowed_transitions: dict[str, set[str] | list[str] | tuple[str, ...]] | None = None,
        subscribe_telemetry: bool = True,
        ctx: zmq.Context | None = None,
    ) -> None:
        super().__init__(
            process_id=process_id,
            heartbeat_endpoint=heartbeat_endpoint,
            heartbeat_period_s=heartbeat_period_s,
            ctx=ctx,
        )
        self._manager_rpc = manager_rpc
        self._manager_pub = manager_pub
        self._rpc_timeout_ms = int(rpc_timeout_ms)
        self._rpc_namespace = str(rpc_namespace).strip()
        if not self._rpc_namespace:
            raise ValueError("rpc_namespace must be a non-empty string")
        self._tick_s = max(0.01, float(tick_s))

        self._state = str(initial_state)
        now_wall = time.time()
        now_mono = time.monotonic()
        self._state_since_wall = now_wall
        self._state_since_mono = now_mono
        self._last_error: str | None = None
        self._last_transition: Json | None = None
        self._allowed_transitions = self._normalize_allowed_transitions(allowed_transitions)

        self._init_rpc_router()
        self._manager = self._init_manager_client(
            manager_rpc=self._manager_rpc,
            manager_pub=self._manager_pub,
            rpc_timeout_ms=self._rpc_timeout_ms,
            process_id=self._process_id,
            subscribe_telemetry=subscribe_telemetry,
        )
        self._init_poller()
        self._advertise_process_rpc()
        self._start_heartbeat_thread(state_provider=lambda: self._state)

    @staticmethod
    def _normalize_allowed_transitions(
        raw: dict[str, set[str] | list[str] | tuple[str, ...]] | None,
    ) -> dict[str, set[str]]:
        if raw is None:
            return {}
        out: dict[str, set[str]] = {}
        for src, dst in raw.items():
            src_s = str(src)
            if isinstance(dst, (set, list, tuple)):
                out[src_s] = {str(x) for x in dst}
            else:
                raise TypeError("allowed_transitions values must be set[str] | list[str] | tuple[str, ...]")
        return out

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _set_last_error(self, message: str | None) -> None:
        self._last_error = None if message is None else str(message)

    def allowed_next_states(self, state: str | None = None) -> list[str]:
        cur = self._state if state is None else str(state)
        return sorted(self._allowed_transitions.get(cur, set()))

    def can_transition(self, target_state: str, *, from_state: str | None = None) -> bool:
        src = self._state if from_state is None else str(from_state)
        dst = str(target_state)
        return dst in self._allowed_transitions.get(src, set())

    def transition(
        self,
        target_state: str,
        *,
        reason: str | None = None,
        metadata: Json | None = None,
        allow_noop: bool = True,
    ) -> bool:
        dst = str(target_state)
        src = self._state
        if src == dst:
            return bool(allow_noop)
        if not self.can_transition(dst, from_state=src):
            return False

        self._on_state_exit(src, dst, reason=reason, metadata=metadata)

        now_wall = time.time()
        now_mono = time.monotonic()
        self._state = dst
        self._state_since_wall = now_wall
        self._state_since_mono = now_mono
        self._last_transition = {
            "from_state": src,
            "to_state": dst,
            "reason": reason,
            "ts": {"t_wall": now_wall, "t_mono": now_mono},
            "metadata": metadata or {},
        }
        self._publish_transition_event(src, dst, reason=reason, metadata=metadata)

        self._on_state_enter(src, dst, reason=reason, metadata=metadata)
        return True

    def _publish_transition_event(
        self,
        from_state: str,
        to_state: str,
        *,
        reason: str | None,
        metadata: Json | None,
    ) -> None:
        if self._manager is None:
            return
        payload: Json = {
            "process_id": self._process_id,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
        }
        if metadata:
            payload["metadata"] = metadata
        try:
            self._manager.publish_event(
                topic="manager.state_machine.transition",
                payload=payload,
            )
        except Exception:
            pass

    def _on_state_exit(
        self,
        from_state: str,
        to_state: str,
        *,
        reason: str | None,
        metadata: Json | None,
    ) -> None:
        fn = getattr(self, f"_on_exit_{from_state.lower()}", None)
        if callable(fn):
            fn(to_state=to_state, reason=reason, metadata=metadata)

    def _on_state_enter(
        self,
        from_state: str,
        to_state: str,
        *,
        reason: str | None,
        metadata: Json | None,
    ) -> None:
        fn = getattr(self, f"_on_enter_{to_state.lower()}", None)
        if callable(fn):
            fn(from_state=from_state, reason=reason, metadata=metadata)

    def _status_payload(self) -> Json:
        return {
            "state": self._state,
            "state_since": {"t_wall": self._state_since_wall, "t_mono": self._state_since_mono},
            "last_error": self._last_error,
            "last_transition": self._last_transition,
            "allowed_next_states": self.allowed_next_states(),
        }

    def _base_capability_methods(self) -> list[Any]:
        prefix = self._rpc_namespace
        return [
            method(
                f"{prefix}.status",
                params=None,
                doc="Get state machine status.",
            ),
            method(
                f"{prefix}.transition",
                params=[
                    param("target_state", required=True, default=None, annotation="str"),
                    param("reason", required=False, default=None, annotation="str"),
                ],
                doc="Transition state machine to target_state if allowed.",
            ),
            method(
                f"{prefix}.stop",
                params=None,
                doc="Stop the process loop.",
            ),
        ]

    def _extra_capability_methods(self) -> list[Any]:
        return []

    def _handle_state_machine_rpc(self, req: Json) -> Json | None:
        rtype = str(req.get("type", ""))
        common = self._handle_common_rpc(req)
        if common is not None:
            return common

        if rtype == "process.capabilities":
            members = self._base_capability_methods() + self._extra_capability_methods()
            members = self._with_common_capabilities(list(members))
            return self._rpc_ok(req, result=capabilities_payload(members))

        prefix = self._rpc_namespace
        if rtype == f"{prefix}.status":
            return self._rpc_ok(req, result=self._status_payload())

        if rtype == f"{prefix}.stop":
            self._stop_evt.set()
            return self._rpc_ok(req, result={"status": "stopping"})

        if rtype == f"{prefix}.transition":
            params = req.get("params", {}) or {}
            if not isinstance(params, dict):
                return self._rpc_invalid_params(req, message="params must be a dict")
            target = params.get("target_state")
            if target is None or not str(target).strip():
                return self._rpc_err(req, code="missing_target_state")
            reason_raw = params.get("reason")
            reason = None if reason_raw is None else str(reason_raw)
            dst = str(target).strip()
            if not self.transition(dst, reason=reason):
                return self._rpc_err(
                    req,
                    code="invalid_transition",
                    extra={
                        "from_state": self._state,
                        "to_state": dst,
                        "allowed_next_states": self.allowed_next_states(),
                    },
                )
            return self._rpc_ok(req, result=self._status_payload())

        return None

    def _tick_state(self, now_mono: float) -> None:
        raise NotImplementedError("_tick_state must be implemented by subclasses")

    def run(self) -> None:
        try:
            next_tick = time.monotonic() + self._tick_s
            while not self._stop_evt.is_set():
                now = time.monotonic()
                timeout_s = max(0.0, next_tick - now)
                timeout_ms = int(min(timeout_s, self._tick_s) * 1000)
                self._poll_and_drain(timeout_ms)
                now = time.monotonic()
                if now >= next_tick:
                    self._tick_state(now)
                    next_tick = now + self._tick_s
        finally:
            self.close()
