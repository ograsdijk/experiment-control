from __future__ import annotations

from collections import deque
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
        graph_edges: list[dict[str, Any]] | None = None,
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

        self._initial_state = str(initial_state)
        self._state = self._initial_state
        now_wall = time.time()
        now_mono = time.monotonic()
        self._state_since_wall = now_wall
        self._state_since_mono = now_mono
        self._last_error: str | None = None
        self._last_transition: Json | None = None
        self._graph_edges = self._normalize_graph_edges(graph_edges)
        if allowed_transitions is None and self._graph_edges:
            allowed_transitions = self._derive_allowed_transitions_from_graph_edges(self._graph_edges)
        self._allowed_transitions = self._normalize_allowed_transitions(allowed_transitions)
        self._history_max = 500
        self._history: list[Json] = []

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

    @staticmethod
    def _normalize_graph_edges(raw: list[dict[str, Any]] | None) -> list[Json]:
        if not isinstance(raw, list):
            return []
        out: list[Json] = []
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            from_state = str(item.get("from_state", "")).strip()
            to_state = str(item.get("to_state", "")).strip()
            if not from_state or not to_state:
                continue
            edge_id = str(item.get("id", "")).strip() or f"edge_{idx}"
            out.append(
                {
                    "id": edge_id,
                    "from_state": from_state,
                    "to_state": to_state,
                    "note": str(item.get("note", "")).strip() or None,
                }
            )
        return out

    @staticmethod
    def _derive_allowed_transitions_from_graph_edges(edges: list[Json]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for edge in edges:
            from_state = str(edge.get("from_state", "")).strip()
            to_state = str(edge.get("to_state", "")).strip()
            if not from_state or not to_state:
                continue
            out.setdefault(from_state, set()).add(to_state)
        return out

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _set_last_error(self, message: str | None) -> None:
        self._last_error = None if message is None else str(message)
        if self._last_error:
            self._append_history_entry(
                {
                    "event": "error",
                    "state": self._state,
                    "message": self._last_error,
                    "ok": False,
                }
            )

    def _append_history_entry(self, entry: Json) -> None:
        ts = {"t_wall": time.time(), "t_mono": time.monotonic()}
        item: Json = dict(entry)
        item.setdefault("ts", ts)
        self._history.append(item)
        if len(self._history) > self._history_max:
            overflow = len(self._history) - self._history_max
            if overflow > 0:
                del self._history[:overflow]

    def allowed_next_states(self, state: str | None = None) -> list[str]:
        cur = self._state if state is None else str(state)
        return sorted(self._allowed_transitions.get(cur, set()))

    def can_transition(self, target_state: str, *, from_state: str | None = None) -> bool:
        src = self._state if from_state is None else str(from_state)
        dst = str(target_state)
        return dst in self._allowed_transitions.get(src, set())

    def graph_edges(self) -> list[Json]:
        return list(self._graph_edges)

    def plan_state_path(
        self,
        target_state: str,
        *,
        from_state: str | None = None,
    ) -> list[Json] | None:
        start = self._state if from_state is None else str(from_state)
        target = str(target_state)
        if not start or not target:
            return None
        if start == target:
            return []
        if not self._graph_edges:
            return None

        adjacency: dict[str, list[Json]] = {}
        for edge in self._graph_edges:
            src = str(edge.get("from_state", "")).strip()
            if not src:
                continue
            adjacency.setdefault(src, []).append(edge)

        queue: deque[str] = deque([start])
        parent: dict[str, tuple[str, Json]] = {}
        visited: set[str] = {start}
        found = False
        while queue:
            cur = queue.popleft()
            for edge in adjacency.get(cur, []):
                nxt = str(edge.get("to_state", "")).strip()
                if not nxt or nxt in visited:
                    continue
                visited.add(nxt)
                parent[nxt] = (cur, edge)
                if nxt == target:
                    found = True
                    queue.clear()
                    break
                queue.append(nxt)
        if not found:
            return None

        path_rev: list[Json] = []
        cursor = target
        while cursor != start:
            prev, edge = parent[cursor]
            path_rev.append(edge)
            cursor = prev
        path_rev.reverse()
        return path_rev

    def plan_state_path_to_any(
        self,
        target_states: list[str] | tuple[str, ...] | set[str],
        *,
        from_state: str | None = None,
    ) -> list[Json] | None:
        targets = {str(state).strip() for state in target_states if str(state).strip()}
        if not targets:
            return None
        start = self._state if from_state is None else str(from_state)
        if start in targets:
            return []
        best_path: list[Json] | None = None
        for target in sorted(targets):
            path = self.plan_state_path(target, from_state=start)
            if path is None:
                continue
            if best_path is None or len(path) < len(best_path):
                best_path = path
        return best_path

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
        self._append_history_entry(
            {
                "event": "transition",
                "from_state": src,
                "to_state": dst,
                "reason": reason,
                "metadata": metadata or {},
                "ok": True,
                "ts": {"t_wall": now_wall, "t_mono": now_mono},
            }
        )
        self._publish_transition_event(src, dst, reason=reason, metadata=metadata)

        self._on_state_enter(src, dst, reason=reason, metadata=metadata)
        return True

    def force_transition(
        self,
        target_state: str,
        *,
        reason: str | None = None,
        metadata: Json | None = None,
        allow_noop: bool = True,
    ) -> bool:
        """
        Force a state transition without validating allowed_transitions.

        This is intended for recovery/reconciliation paths when the
        transition graph cannot represent a discovered state directly.
        """
        dst = str(target_state)
        src = self._state
        if src == dst:
            return bool(allow_noop)

        now_wall = time.time()
        now_mono = time.monotonic()
        meta: Json = dict(metadata or {})
        meta.setdefault("forced", True)
        self._state = dst
        self._state_since_wall = now_wall
        self._state_since_mono = now_mono
        self._last_transition = {
            "from_state": src,
            "to_state": dst,
            "reason": reason,
            "ts": {"t_wall": now_wall, "t_mono": now_mono},
            "metadata": meta,
        }
        self._append_history_entry(
            {
                "event": "transition",
                "from_state": src,
                "to_state": dst,
                "reason": reason,
                "metadata": meta,
                "ok": True,
                "ts": {"t_wall": now_wall, "t_mono": now_mono},
            }
        )
        self._publish_transition_event(src, dst, reason=reason, metadata=meta)
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

    def _status_detail_payload(self) -> Json:
        return {}

    def _active_state_ids(self) -> list[str]:
        return [self._state] if self._state else []

    def _status_payload(self) -> Json:
        detail = self._status_detail_payload()
        if not isinstance(detail, dict):
            detail = {}
        return {
            "state": self._state,
            "active_states": self._active_state_ids(),
            "state_since": {"t_wall": self._state_since_wall, "t_mono": self._state_since_mono},
            "state_age_s": max(0.0, time.monotonic() - self._state_since_mono),
            "last_error": self._last_error,
            "last_transition": self._last_transition,
            "allowed_next_states": self.allowed_next_states(),
            "status_detail": detail,
        }

    def _history_tail_payload(self, *, limit: int, errors_only: bool) -> Json:
        rows = self._history
        if errors_only:
            rows = [row for row in rows if row.get("ok") is False or row.get("event") == "error"]
        if limit > 0 and len(rows) > limit:
            rows = rows[-limit:]
        return {"entries": list(rows), "count": len(rows)}

    def _base_capability_methods(self) -> list[Any]:
        prefix = self._rpc_namespace
        return [
            method(
                f"{prefix}.status",
                params=None,
                doc="Get state machine status.",
            ),
            method(
                f"{prefix}.graph",
                params=None,
                doc="Get static state-machine graph metadata.",
            ),
            method(
                f"{prefix}.history.tail",
                params=[
                    param("limit", required=False, default=100, annotation="int"),
                    param("errors_only", required=False, default=False, annotation="bool"),
                ],
                doc="Get recent state-machine history entries.",
            ),
            method(
                f"{prefix}.stop",
                params=None,
                doc="Stop the process loop.",
            ),
        ]

    def _extra_capability_methods(self) -> list[Any]:
        return []

    def _state_machine_graph_action_transitions(self) -> list[Json]:
        """
        Optional action->transition edges for richer UI graph rendering.

        Each entry may include:
          - action (str, required): full RPC action name (e.g. "vacuum.startup")
          - from_state (str | None)
          - to_state (str | None)
          - note (str | None)
        """
        return []

    def _state_machine_graph_action_effects(self) -> list[Json]:
        """
        Optional action->device-command edges for richer UI graph rendering.

        Each entry may include:
          - action (str, required): full RPC action name
          - device_id (str, required)
          - device_action (str, required)
          - params (dict | None)
          - note (str | None)
        """
        return []

    def _state_machine_graph_payload(self) -> Json:
        prefix = self._rpc_namespace
        state_set: set[str] = set(self._allowed_transitions.keys())
        for dsts in self._allowed_transitions.values():
            state_set.update(dsts)
        if self._state:
            state_set.add(self._state)
        states = sorted(state_set)

        transitions: list[Json] = []
        for from_state in sorted(self._allowed_transitions.keys()):
            for to_state in sorted(self._allowed_transitions[from_state]):
                edge_id = None
                edge_note = None
                for edge in self._graph_edges:
                    if (
                        str(edge.get("from_state", "")).strip() == from_state
                        and str(edge.get("to_state", "")).strip() == to_state
                    ):
                        edge_id = str(edge.get("id", "")).strip() or None
                        edge_note = str(edge.get("note", "")).strip() or None
                        break
                transitions.append(
                    {
                        "from_state": from_state,
                        "to_state": to_state,
                        "edge_id": edge_id,
                        "note": edge_note,
                    }
                )

        action_items: list[Json] = []
        action_index: dict[str, Json] = {}
        for member in self._extra_capability_methods():
            name = str(getattr(member, "name", "")).strip()
            if not name.startswith(f"{prefix}."):
                continue
            if name in {f"{prefix}.status", f"{prefix}.graph", f"{prefix}.history.tail", f"{prefix}.stop"}:
                continue
            params_raw = getattr(member, "params", None)
            params_payload: list[Json] = []
            if isinstance(params_raw, list):
                for p in params_raw:
                    pname = str(getattr(p, "name", "")).strip()
                    if not pname:
                        continue
                    params_payload.append(
                        {
                            "name": pname,
                            "kind": str(getattr(p, "kind", "")),
                            "required": bool(getattr(p, "required", False)),
                            "default": getattr(p, "default", None),
                            "annotation": getattr(p, "annotation", None),
                        }
                    )
            item: Json = {
                "name": name,
                "doc": str(getattr(member, "doc", "") or "") or None,
                "params": params_payload,
                "transitions": [],
                "effects": [],
            }
            action_items.append(item)
            action_index[name] = item

        for raw in self._state_machine_graph_action_transitions():
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", "")).strip()
            if not action:
                continue
            row: Json = {
                "from_state": str(raw.get("from_state", "")).strip() or None,
                "to_state": str(raw.get("to_state", "")).strip() or None,
                "note": str(raw.get("note", "")).strip() or None,
            }
            item = action_index.get(action)
            if item is None:
                item = {
                    "name": action,
                    "doc": None,
                    "params": [],
                    "transitions": [],
                    "effects": [],
                }
                action_items.append(item)
                action_index[action] = item
            transitions_raw = item.get("transitions")
            if isinstance(transitions_raw, list):
                transitions_raw.append(row)

        for raw in self._state_machine_graph_action_effects():
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", "")).strip()
            device_id = str(raw.get("device_id", "")).strip()
            device_action = str(raw.get("device_action", "")).strip()
            if not action or not device_id or not device_action:
                continue
            params_raw = raw.get("params")
            params: Json | None = params_raw if isinstance(params_raw, dict) else None
            row = {
                "device_id": device_id,
                "device_action": device_action,
                "params": params,
                "note": str(raw.get("note", "")).strip() or None,
            }
            item = action_index.get(action)
            if item is None:
                item = {
                    "name": action,
                    "doc": None,
                    "params": [],
                    "transitions": [],
                    "effects": [],
                }
                action_items.append(item)
                action_index[action] = item
            effects_raw = item.get("effects")
            if isinstance(effects_raw, list):
                effects_raw.append(row)

        action_items.sort(key=lambda row: str(row.get("name", "")))
        return {
            "namespace": prefix,
            "initial_state": self._initial_state or None,
            "states": states,
            "transitions": transitions,
            "edges": self.graph_edges(),
            "actions": action_items,
        }

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

        if rtype == f"{prefix}.graph":
            return self._rpc_ok(req, result=self._state_machine_graph_payload())

        if rtype == f"{prefix}.history.tail":
            params = req.get("params", {}) or {}
            if not isinstance(params, dict):
                return self._rpc_invalid_params(req, message="params must be a dict")
            limit_raw = params.get("limit", 100)
            errors_only_raw = params.get("errors_only", False)
            try:
                limit = int(limit_raw)
            except Exception:
                return self._rpc_invalid_params(req, message="limit must be an int")
            limit = max(1, min(limit, self._history_max))
            errors_only = bool(errors_only_raw)
            return self._rpc_ok(
                req,
                result=self._history_tail_payload(limit=limit, errors_only=errors_only),
            )

        if rtype == f"{prefix}.stop":
            self._stop_evt.set()
            return self._rpc_ok(req, result={"status": "stopping"})

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
