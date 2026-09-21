from __future__ import annotations

import time
from collections import deque
from typing import Any

import zmq

from ..capabilities import method, param
from ..types import MemberSpec
from ..utils.responses import is_response_ok
from ..utils.zmq_helpers import safe_json_loads
from .target_spot_coords import SpotCoordinateGenerator
from .target_spot_stat import AbsorptionStatTracker
from .state_machine_base import StateMachineProcessBase

Json = dict[str, Any]

_VALID_STRATEGIES = ("random", "grid")
_VALID_STAT_MODES = ("rolling", "block")
_VALID_COUPLING_MODES = ("pause", "continuous")

_STATE_HOLDING = "HOLDING"
_STATE_EVALUATING = "EVALUATING"
_STATE_JUMPING = "JUMPING"
_STATE_PAUSE_REQUESTED = "PAUSE_REQUESTED"
_STATE_MOVING = "MOVING"
_STATE_SETTLING = "SETTLING"
_STATE_RESUMING = "RESUMING"
_STATE_ERROR = "ERROR"

_DEFAULT_CONFIG: Json = {
    "range_spec": {
        "center": {"x": 0, "y": 0},
        "size": {"width": 1000, "height": 1000},
        # scan2d always discretizes onto a grid (even for strategy="random",
        # which then samples from it) - pitch is the grid resolution.
        "pitch": 25,
    },
    "strategy": "random",
    "grid_pattern": "serpentine",
    "stat_mode": "rolling",
    "stat_window": 20,
    "threshold": 2.0,
    "coupling_mode": "pause",
    "settle_s": 0.5,
    # Long enough to cover a full acquisition burst (n_traces PXIe frames)
    # before concluding the sequencer failed to pause - the old 15.0s
    # default was tight for that.
    "pause_ack_timeout_s": 30.0,
    # Shot-count floor, not a wall-clock duration: how many raw abs_integral
    # samples must have been collected at this spot before a threshold-
    # triggered jump is allowed. Defaults to stat_window so a jump can never
    # fire on a stat estimate built from fewer samples than a full window -
    # a force_jump still bypasses this immediately, same as before.
    "min_dwell_shots": 20,
}

_VISITED_MAX = 2000
# Only the most recent _SAMPLES_RETENTION visited entries keep their raw
# per-hold sample buffer (bounds memory given the 2000-entry _VISITED_MAX
# cap); older entries keep the existing summary fields with `samples`
# omitted. UI-only, not persisted - fine to lose on restart.
_SAMPLES_RETENTION = 200
_HOLD_SAMPLES_MAXLEN = 300


class TargetSpotControllerProcess(StateMachineProcessBase):
    """Holds the detection mirror at one spot, watching a rolling/block mean
    of the raw (ungated) per-shot `abs_integral` scalar, and jumps to a new
    spot (drawn from an operator-set coordinate range, by a selectable
    strategy) once that mean drops below a threshold, or when force-jumped.

    Mirror positioning is deliberately NOT owned by the sequencer (see
    `spb_raster_scan.yaml`, which now only triggers acquisition and reads
    `target_spot.get_current_spot` to tag each frame). Coupling to the
    sequencer's acquisition is a runtime toggle (`coupling_mode`):

    - "pause" (default): before a jump, pause the sequencer (`sequencer.pause`
      / `sequencer.status` RPCs, already provided by `SequencerProcess`),
      move + settle, then resume it (`sequencer.resume`).
    - "continuous": the sequencer is never paused; `settled` is published in
      this process's own telemetry so the acquisition side can tag/identify
      frames whose acquisition window overlapped a jump.
    """

    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str = "target_spot_controller",
        rpc_timeout_ms: int = 2000,
        heartbeat_endpoint: str | None = None,
        heartbeat_period_s: float = 1.0,
        tick_s: float = 0.2,
        mirror_device_id: str = "zabertmm",
        sequencer_process_id: str = "sequencer",
        stream_analysis_workspace_id: str = "detection_fluorescence",
        stream_analysis_output_id: str = "abs_integral",
        ctx: zmq.Context | None = None,
    ) -> None:
        graph_edges = [
            {"id": "holding_to_evaluating", "from_state": _STATE_HOLDING, "to_state": _STATE_EVALUATING},
            {"id": "evaluating_to_holding", "from_state": _STATE_EVALUATING, "to_state": _STATE_HOLDING},
            {"id": "evaluating_to_jumping", "from_state": _STATE_EVALUATING, "to_state": _STATE_JUMPING},
            {"id": "jumping_to_pause_requested", "from_state": _STATE_JUMPING, "to_state": _STATE_PAUSE_REQUESTED},
            {"id": "jumping_to_moving", "from_state": _STATE_JUMPING, "to_state": _STATE_MOVING},
            {"id": "pause_requested_to_moving", "from_state": _STATE_PAUSE_REQUESTED, "to_state": _STATE_MOVING},
            {"id": "moving_to_settling", "from_state": _STATE_MOVING, "to_state": _STATE_SETTLING},
            {"id": "settling_to_resuming", "from_state": _STATE_SETTLING, "to_state": _STATE_RESUMING},
            {"id": "settling_to_holding", "from_state": _STATE_SETTLING, "to_state": _STATE_HOLDING},
            {"id": "resuming_to_holding", "from_state": _STATE_RESUMING, "to_state": _STATE_HOLDING},
        ]
        for src in (
            _STATE_HOLDING,
            _STATE_EVALUATING,
            _STATE_JUMPING,
            _STATE_PAUSE_REQUESTED,
            _STATE_MOVING,
            _STATE_SETTLING,
            _STATE_RESUMING,
        ):
            graph_edges.append({"id": f"{src.lower()}_to_error", "from_state": src, "to_state": _STATE_ERROR})

        super().__init__(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            process_id=process_id,
            rpc_namespace="target_spot",
            rpc_timeout_ms=rpc_timeout_ms,
            heartbeat_endpoint=heartbeat_endpoint,
            heartbeat_period_s=heartbeat_period_s,
            tick_s=tick_s,
            initial_state=_STATE_HOLDING,
            graph_edges=graph_edges,
            subscribe_telemetry=True,
            ctx=ctx,
        )

        self._mirror_device_id = str(mirror_device_id)
        self._sequencer_process_id = str(sequencer_process_id)
        self._stream_analysis_workspace_id = str(stream_analysis_workspace_id)
        self._stream_analysis_output_id = str(stream_analysis_output_id)

        self._config: Json = dict(_DEFAULT_CONFIG)
        self._coord_gen = SpotCoordinateGenerator(
            range_spec=self._config["range_spec"],
            strategy=self._config["strategy"],
            grid_pattern=self._config["grid_pattern"],
        )
        self._stat_tracker = AbsorptionStatTracker(
            mode=self._config["stat_mode"], window=self._config["stat_window"]
        )

        self._current_spot: Json | None = None
        self._pending_spot: Json | None = None
        self._next_spot_index = 0
        self._visited: list[Json] = []
        self._force_requested = False
        self._force_reason: str | None = None
        self._pause_deadline_mono = 0.0

        # Raw per-shot samples for the *current* hold, attached to the
        # visited entry once it closes (§2). `_first_window_stat` (§5) is
        # the tracker's value the first time a full window of samples
        # accumulates at this spot - captured once, held until the jump.
        self._hold_samples: deque[Json] = deque(maxlen=_HOLD_SAMPLES_MAXLEN)
        self._first_window_stat: float | None = None

        # Own SUB socket on the same manager pub/sub topic
        # `SequencerProcess` already subscribes to for adaptive-study
        # feedback (`sequencer.py`), so this controller reacts to raw
        # `abs_integral` shots the same way, independent of the sequencer.
        self._analysis_sub = self._ctx.socket(zmq.SUB)
        self._analysis_sub.setsockopt(zmq.SUBSCRIBE, b"manager.stream_analysis.output")
        self._analysis_sub.setsockopt(zmq.RCVTIMEO, 100)
        self._analysis_sub.setsockopt(zmq.LINGER, 0)
        self._analysis_sub.connect(self._manager_pub)
        # StateMachineProcessBase.__init__ already called _init_poller() once
        # (without this socket); rebuild it now that _analysis_sub exists.
        self._init_poller(extra=[(self._analysis_sub, zmq.POLLIN)])

        self._try_seed_current_spot_from_telemetry()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def _try_seed_current_spot_from_telemetry(self) -> None:
        """Best-effort: if the mirror is already reporting a position, adopt
        it as the current spot instead of forcing a jump on startup."""
        manager = self._manager
        if manager is None:
            return
        x_sample = manager.get_latest(self._mirror_device_id, "x")
        y_sample = manager.get_latest(self._mirror_device_id, "y")
        if not x_sample or not y_sample:
            return
        try:
            x = int(round(float(x_sample.get("value"))))
            y = int(round(float(y_sample.get("value"))))
        except (TypeError, ValueError):
            return
        self._current_spot = {"x": x, "y": y, "spot_index": self._next_spot_index, "settled": True}
        self._next_spot_index += 1
        self._open_visited_entry(self._current_spot)

    # ------------------------------------------------------------------
    # Main loop: StateMachineProcessBase.run() drives rpc_router + the
    # manager's telemetry sub socket automatically via _poll_and_drain, but
    # has no hook for extra sockets, so this override adds a drain step for
    # `_analysis_sub` around the same tick loop.
    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            self._advertise_process_telemetry_schema()
            next_tick = time.monotonic() + self._tick_s
            while not self._stop_evt.is_set():
                now = time.monotonic()
                timeout_s = max(0.0, next_tick - now)
                timeout_ms = int(min(timeout_s, self._tick_s) * 1000)
                events = self._poll_and_drain(timeout_ms)
                self._drain_analysis_sub(events)
                now = time.monotonic()
                if now >= next_tick:
                    self._tick_state(now)
                    next_tick = now + self._tick_s
        finally:
            try:
                self._analysis_sub.close(0)
            except Exception:
                pass
            self.close()

    def _drain_analysis_sub(self, events: dict[Any, int]) -> None:
        if not (int(events.get(self._analysis_sub, 0)) & zmq.POLLIN):
            return
        while True:
            try:
                _topic_b, payload_b = self._analysis_sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("workspace_id", "")) != self._stream_analysis_workspace_id:
                continue
            if str(payload.get("output_id", "")) != self._stream_analysis_output_id:
                continue
            value = payload.get("value")
            if isinstance(value, (int, float)):
                v = float(value)
                self._stat_tracker.add_sample(v)
                self._hold_samples.append({"t": time.time(), "value": v})
                if (
                    self._first_window_stat is None
                    and self._stat_tracker.sample_count >= int(self._config["stat_window"])
                ):
                    self._first_window_stat = self._stat_tracker.current_value

    # ------------------------------------------------------------------
    # State machine tick
    # ------------------------------------------------------------------
    def _tick_state(self, now_mono: float) -> None:
        try:
            if self._current_spot is None:
                self._begin_jump(reason="startup", stat_at_departure=None)
            elif self._state == _STATE_HOLDING:
                self._tick_holding(now_mono)
            elif self._state == _STATE_PAUSE_REQUESTED:
                self._tick_pause_requested(now_mono)
        except Exception as exc:
            self._set_last_error(str(exc))
            self.force_transition(_STATE_ERROR, reason=str(exc))
        self._publish_process_telemetry()

    def _tick_holding(self, now_mono: float) -> None:
        stat_value = self._stat_tracker.current_value
        dwell_ok = self._stat_tracker.sample_count >= int(self._config["min_dwell_shots"])
        below_threshold = stat_value is not None and stat_value < float(self._config["threshold"])
        should_jump = self._force_requested or (dwell_ok and below_threshold)
        if not should_jump:
            return
        reason = (self._force_reason or "forced") if self._force_requested else "threshold"
        self._force_requested = False
        self._force_reason = None
        self._begin_jump(reason=str(reason), stat_at_departure=stat_value)

    def _begin_jump(self, *, reason: str, stat_at_departure: float | None) -> None:
        self.transition(_STATE_EVALUATING, reason=reason, allow_noop=True)
        next_spot = self._coord_gen.next_spot()
        self._pending_spot = {
            "x": int(round(float(next_spot["x"]))),
            "y": int(round(float(next_spot["y"]))),
            "spot_index": self._next_spot_index,
        }
        self._next_spot_index += 1
        self._close_visited_entry(stat_at_departure=stat_at_departure, reason=reason)
        self.transition(_STATE_JUMPING, reason=reason)

        if self._config["coupling_mode"] == "pause":
            self._request_sequencer_action("sequencer.pause")
            self._pause_deadline_mono = time.monotonic() + float(self._config["pause_ack_timeout_s"])
            self.transition(_STATE_PAUSE_REQUESTED, reason="awaiting sequencer pause ack")
            return
        self._perform_move_and_settle(resume_after=False)

    def _tick_pause_requested(self, now_mono: float) -> None:
        status = self._sequencer_status()
        if status is not None and str(status.get("state", "")).upper() == "PAUSED":
            self._perform_move_and_settle(resume_after=True)
            return
        if now_mono >= self._pause_deadline_mono:
            raise RuntimeError("timed out waiting for sequencer to acknowledge pause")

    def _perform_move_and_settle(self, *, resume_after: bool) -> None:
        spot = self._pending_spot
        if spot is None:
            raise RuntimeError("_perform_move_and_settle called with no pending spot")
        self.transition(_STATE_MOVING, reason="moving")
        # Publish `settled: false` before the (blocking) move so continuous-
        # mode acquisition, which reads this telemetry per frame, can tag
        # frames whose acquisition window overlaps the move.
        self._publish_process_telemetry(settled_override=False)
        self.command(self._mirror_device_id, "move_absolute", {"x": spot["x"], "y": spot["y"]})

        self.transition(_STATE_SETTLING, reason="settle dwell")
        settle_s = max(0.0, float(self._config["settle_s"]))
        if settle_s > 0:
            time.sleep(settle_s)

        self._current_spot = dict(spot, settled=True)
        self._open_visited_entry(self._current_spot)
        self._stat_tracker.reset()
        self._hold_samples.clear()
        self._first_window_stat = None

        if resume_after:
            self.transition(_STATE_RESUMING, reason="resuming sequencer")
            self._request_sequencer_action("sequencer.resume")

        self.transition(_STATE_HOLDING, reason="settled")

    # ------------------------------------------------------------------
    # Cross-process RPC to the sequencer (same `manager.processes.rpc`
    # envelope WatchdogProcess.ProcessAction already uses).
    # ------------------------------------------------------------------
    def _request_sequencer_action(self, action: str) -> None:
        manager = self._manager
        if manager is None:
            raise RuntimeError("manager not initialized")
        req = {
            "type": "manager.processes.rpc",
            "process_id": self._sequencer_process_id,
            "request": {"type": action, "params": {}},
            "caller_process_id": self._process_id,
        }
        resp = manager.call(req, timeout_ms=self._rpc_timeout_ms)
        if not isinstance(resp, dict) or not is_response_ok(resp):
            raise RuntimeError(f"{action} failed: {resp}")

    def _sequencer_status(self) -> Json | None:
        manager = self._manager
        if manager is None:
            return None
        req = {
            "type": "manager.processes.rpc",
            "process_id": self._sequencer_process_id,
            "request": {"type": "sequencer.status", "params": {}},
            "caller_process_id": self._process_id,
        }
        try:
            resp = manager.call(req, timeout_ms=self._rpc_timeout_ms)
        except Exception:
            return None
        if not isinstance(resp, dict) or not is_response_ok(resp):
            return None
        result = resp.get("result")
        return result if isinstance(result, dict) else None

    # ------------------------------------------------------------------
    # Visited-spot history
    # ------------------------------------------------------------------
    def _open_visited_entry(self, spot: Json) -> None:
        self._visited.append(
            {
                "spot_index": spot["spot_index"],
                "x": spot["x"],
                "y": spot["y"],
                "entered_wall": time.time(),
                "departed_wall": None,
                "stat_at_departure": None,
                "stat_first_window": None,
                "departure_reason": None,
            }
        )
        if len(self._visited) > _VISITED_MAX:
            del self._visited[: len(self._visited) - _VISITED_MAX]

    def _close_visited_entry(self, *, stat_at_departure: float | None, reason: str) -> None:
        if not self._visited:
            return
        entry = self._visited[-1]
        if entry.get("departed_wall") is not None:
            return
        entry["departed_wall"] = time.time()
        entry["stat_at_departure"] = stat_at_departure
        # Fall back to stat_at_departure if the hold ended before a first
        # full window of samples ever accumulated (e.g. a quick forced
        # jump) - every entry gets both fields populated, so a short hold
        # never reads as "different" by construction, only genuinely
        # different holds do.
        entry["stat_first_window"] = (
            self._first_window_stat if self._first_window_stat is not None else stat_at_departure
        )
        entry["departure_reason"] = reason
        entry["samples"] = list(self._hold_samples)

        # §2: bound memory by dropping the raw sample buffer from whichever
        # entry just aged out of the retention window (O(1), not a full
        # rescan) - older entries keep their summary fields regardless.
        if len(self._visited) > _SAMPLES_RETENTION:
            aged_out_idx = len(self._visited) - 1 - _SAMPLES_RETENTION
            if 0 <= aged_out_idx < len(self._visited):
                self._visited[aged_out_idx].pop("samples", None)

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------
    def process_telemetry_schema(self) -> list[Json] | None:
        return [
            {"name": "state", "dtype": "str:16", "units": ""},
            {"name": "coupling_mode", "dtype": "str:16", "units": ""},
            {"name": "current_x", "dtype": "int64", "units": "microsteps"},
            {"name": "current_y", "dtype": "int64", "units": "microsteps"},
            {"name": "current_spot_index", "dtype": "int64", "units": ""},
            {"name": "settled", "dtype": "bool", "units": ""},
            {"name": "abs_stat_value", "dtype": "float64", "units": "a.u."},
            {"name": "abs_stat_n", "dtype": "int64", "units": ""},
            {"name": "threshold", "dtype": "float64", "units": "a.u."},
            {"name": "visited_count", "dtype": "int64", "units": ""},
        ]

    def _publish_process_telemetry(self, *, settled_override: bool | None = None) -> None:
        spot = self._current_spot or {}
        stat_value = self._stat_tracker.current_value
        settled = settled_override if settled_override is not None else bool(spot.get("settled", False))
        self.publish_telemetry(
            {
                "state": self._state,
                "coupling_mode": str(self._config["coupling_mode"]),
                "current_x": int(spot.get("x", 0)),
                "current_y": int(spot.get("y", 0)),
                "current_spot_index": int(spot.get("spot_index", -1)),
                "settled": settled,
                "abs_stat_value": float(stat_value) if stat_value is not None else float("nan"),
                "abs_stat_n": self._stat_tracker.sample_count,
                "threshold": float(self._config["threshold"]),
                "visited_count": len(self._visited),
            }
        )

    # ------------------------------------------------------------------
    # Capabilities / RPC
    # ------------------------------------------------------------------
    def _extra_capability_methods(self) -> list[MemberSpec]:
        p = self._rpc_namespace
        return [
            method(f"{p}.get_current_spot", params=None, doc="Get the mirror's current spot."),
            method(
                f"{p}.get_visited",
                params=[param("limit", required=False, default=200, annotation="int")],
                doc="Get visited-spot history, most recent last.",
            ),
            method(f"{p}.get_config", params=None, doc="Get the controller's current config."),
            method(
                f"{p}.set_config",
                params=[param("config", required=True, default=None, annotation="dict")],
                doc="Hot-swap controller config (partial update; unspecified fields unchanged).",
            ),
            method(
                f"{p}.force_jump",
                params=[param("reason", required=False, default=None, annotation="str")],
                doc="Force an immediate jump to a new spot, bypassing the threshold check.",
            ),
            method(
                f"{p}.set_mode",
                params=[param("coupling_mode", required=True, default=None, annotation="str")],
                doc="Shorthand for set_config({coupling_mode: ...}).",
            ),
            method(f"{p}.clear_error", params=None, doc="Clear ERROR state and return to HOLDING."),
        ]

    def _status_detail_payload(self) -> Json:
        return {
            "current_spot": self._current_spot,
            "config": dict(self._config),
            "abs_stat": self._stat_tracker.status_payload(),
            "visited_count": len(self._visited),
        }

    def _handle_rpc(self, req: Json) -> Json:
        base = self.handle_state_machine_rpc(req)
        if base is not None:
            return base

        rtype = str(req.get("type", ""))
        p = self._rpc_namespace
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")

        if rtype == f"{p}.get_current_spot":
            return self.rpc_ok(req, result=dict(self._current_spot or {}))

        if rtype == f"{p}.get_visited":
            try:
                limit = max(1, int(params.get("limit", 200)))
            except Exception:
                return self.rpc_invalid_params(req, message="limit must be an int")
            rows = self._visited[-limit:] if limit < len(self._visited) else list(self._visited)
            return self.rpc_ok(req, result={"entries": rows, "count": len(rows)})

        if rtype == f"{p}.get_config":
            return self.rpc_ok(req, result=dict(self._config))

        if rtype == f"{p}.set_config":
            patch = params.get("config")
            if not isinstance(patch, dict):
                return self.rpc_invalid_params(req, message="config must be a dict")
            try:
                self._apply_config_patch(patch)
            except (TypeError, ValueError) as exc:
                return self.rpc_invalid_params(req, message=str(exc))
            return self.rpc_ok(req, result=dict(self._config))

        if rtype == f"{p}.force_jump":
            self._force_requested = True
            reason = params.get("reason")
            self._force_reason = str(reason) if reason else "forced"
            return self.rpc_ok(req, result={"accepted": True})

        if rtype == f"{p}.set_mode":
            coupling_mode = params.get("coupling_mode")
            if coupling_mode not in _VALID_COUPLING_MODES:
                return self.rpc_invalid_params(
                    req, message=f"coupling_mode must be one of {_VALID_COUPLING_MODES}"
                )
            self._apply_config_patch({"coupling_mode": coupling_mode})
            return self.rpc_ok(req, result=dict(self._config))

        if rtype == f"{p}.clear_error":
            if self._state == _STATE_ERROR:
                self.force_transition(_STATE_HOLDING, reason="clear_error")
                self._set_last_error(None)
            return self.rpc_ok(req, result={"state": self._state})

        return self.rpc_err(req, code="not_implemented", message=f"unknown action {rtype!r}")

    def rpc_invalid_params(self, req: Json, *, message: str) -> Json:
        return self.rpc_err(req, code="invalid_params", message=message)

    def _apply_config_patch(self, patch: Json) -> None:
        next_config = dict(self._config)
        for key, value in patch.items():
            if key not in _DEFAULT_CONFIG:
                raise ValueError(f"unknown config field {key!r}")
            next_config[key] = value

        if next_config["strategy"] not in _VALID_STRATEGIES:
            raise ValueError(f"strategy must be one of {_VALID_STRATEGIES}")
        if next_config["stat_mode"] not in _VALID_STAT_MODES:
            raise ValueError(f"stat_mode must be one of {_VALID_STAT_MODES}")
        if next_config["coupling_mode"] not in _VALID_COUPLING_MODES:
            raise ValueError(f"coupling_mode must be one of {_VALID_COUPLING_MODES}")
        stat_window = int(next_config["stat_window"])
        if stat_window < 1:
            raise ValueError("stat_window must be >= 1")
        threshold = float(next_config["threshold"])
        min_dwell_shots = int(next_config["min_dwell_shots"])
        if min_dwell_shots < 0:
            raise ValueError("min_dwell_shots must be >= 0")
        settle_s = float(next_config["settle_s"])
        pause_ack_timeout_s = float(next_config["pause_ack_timeout_s"])
        if not isinstance(next_config["range_spec"], dict):
            raise ValueError("range_spec must be a dict")

        range_or_strategy_changed = (
            patch.get("range_spec") is not None
            or patch.get("strategy") is not None
            or patch.get("grid_pattern") is not None
        )
        stat_changed = patch.get("stat_mode") is not None or patch.get("stat_window") is not None

        next_config["stat_window"] = stat_window
        next_config["threshold"] = threshold
        next_config["min_dwell_shots"] = min_dwell_shots
        next_config["settle_s"] = settle_s
        next_config["pause_ack_timeout_s"] = pause_ack_timeout_s
        self._config = next_config

        if range_or_strategy_changed:
            self._coord_gen.reconfigure(
                range_spec=self._config["range_spec"],
                strategy=self._config["strategy"],
                grid_pattern=self._config["grid_pattern"],
            )
        if stat_changed:
            self._stat_tracker.reconfigure(mode=self._config["stat_mode"], window=self._config["stat_window"])
