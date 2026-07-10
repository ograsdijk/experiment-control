from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..driver import extract_value
from .ast import (
    AdaptiveStep,
    AssignStep,
    AtomicStep,
    CallStep,
    ForStep,
    IfStep,
    ParallelStep,
    PauseStep,
    RepeatStep,
    SequenceSpec,
    SetContextStep,
    SetStep,
    SleepStep,
    Step,
    UseStep,
    TryStep,
    WhileStep,
    WaitUntilStep,
)
from .eval import eval_condition, render_templates, to_attrdict
from .ranges import generate_from_gen
from .source_info import StepSourceInfo, step_kind, step_summary


@dataclass
class _Frame:
    steps: list[Step]
    index: int = 0
    on_exit: Callable[[], None] | None = None
    run_during_unwind: bool = False


@dataclass
class _ForFrame:
    bind: dict[str, str]
    records: list[dict[str, Any]]
    index: int
    body: list[Step]


@dataclass
class _RepeatFrame:
    remaining: int
    body: list[Step]


@dataclass
class _TryFrame:
    finally_steps: list[Step]
    finalizing: bool = False


@dataclass
class _WhileFrame:
    condition: Any
    body: list[Step]


@dataclass
class _AdaptiveFrame:
    step: AdaptiveStep
    study_id: str
    controller: Any
    rendered_controller: dict[str, Any]
    rendered_space: dict[str, Any]
    started_t: float
    proposal: dict[str, Any] | None = None
    trials_completed: int = 0
    best_score: float | None = None
    no_improve_trials: int = 0


@dataclass
class _AdaptiveObserveState:
    frame: _AdaptiveFrame
    proposal: dict[str, Any]
    trial: dict[str, Any]
    repeats: int
    metrics_spec: dict[str, Any]
    current_repeat: int = 0
    started_t: float = 0.0


@dataclass
class _WaitState:
    start_t: float
    timeout_s: float
    every_s: float
    next_sample_t: float
    stable_for_s: float
    condition: Any
    sample_spec: dict[str, Any]
    reduce_spec: dict[str, Any] | None
    samples: list[tuple[float, Any]]
    max_samples: int
    stable_since: float | None = None


@dataclass
class _StepEstimate:
    total: int | None
    reason: str | None = None


class _NoStepReady:
    pass


_NO_STEP_READY = _NoStepReady()
StepResult = Step | _NoStepReady | None


class SequencerRuntime:
    def __init__(
        self,
        *,
        call_device: Callable[[str, str, dict[str, Any]], dict[str, Any]],
        get_telemetry: Callable[[str, str], dict[str, Any] | None],
        set_stream_context: Callable[[str, str, int, dict[str, Any]], None],
        resolve_use: Callable[[str], SequenceSpec] | None = None,
        call_process: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
        get_process_telemetry: Callable[[str, str], dict[str, Any] | None] | None = None,
        expect_streams: Callable[[list[tuple[str, str]], int], None] | None = None,
    ) -> None:
        self._call_device = call_device
        self._get_telemetry = get_telemetry
        self._set_stream_context = set_stream_context
        self._resolve_use = resolve_use
        # Process RPC + process telemetry sampling. Optional so existing
        # constructions (and tests) that only wire device callbacks keep working;
        # a sequence that targets `process:` without these wired fails clearly.
        self._call_process = call_process
        self._get_process_telemetry = get_process_telemetry
        self._expect_streams = expect_streams

        self._spec: SequenceSpec | None = None
        self._vars: dict[str, Any] = {}
        self._base_vars: dict[str, Any] = {}
        self._env: dict[str, Any] = {}
        self._stack: list[
            _Frame | _ForFrame | _RepeatFrame | _TryFrame | _WhileFrame | _AdaptiveFrame
        ] = []
        self._state = "IDLE"
        self._pause_requested = False
        self._stop_requested = False
        self._last_error: str | None = None
        self._pending_terminal_state: str | None = None
        self._pending_terminal_error: str | None = None
        self._cleanup_failed = False
        self._cleanup_errors: list[dict[str, Any]] = []
        self._sleep_until: float | None = None
        self._wait_state: _WaitState | None = None
        self._adaptive_observe_state: _AdaptiveObserveState | None = None
        self._atomic_depth = 0
        self._current_step: str | None = None
        self._current_step_detail: dict[str, Any] | None = None
        self._last_error_detail: dict[str, Any] | None = None
        self._step_source_info: dict[int, StepSourceInfo] = {}
        self._context_id = -1
        self._analysis_outputs: list[dict[str, Any]] = []
        self._estimated_total_steps: int | None = None
        self._progress_estimate_reason: str | None = None
        self._completed_steps = 0
        self._step_ewma_s: float | None = None
        self._step_ewma_alpha = 0.2
        self._eta_ewma_s: float | None = None
        self._eta_ewma_alpha = 0.1
        self._eta_min_completed_steps = 5
        self._run_started_mono: float | None = None
        self._run_ended_mono: float | None = None
        self._paused_total_s = 0.0
        self._paused_started_mono: float | None = None
        self._active_step_started_elapsed_s: float | None = None
        self._adaptive_studies: dict[str, dict[str, Any]] = {}
        self._adaptive_start_modes: dict[str, str] = {}
        self._run_id = 0
        self._next_run_id = 1
        self._loop_mode = "once"
        self._loops_target: int | None = None
        self._loops_completed = 0
        self._vars_override_active: dict[str, Any] = {}
        self._use_stack: list[str] = []
        self._step_handlers: dict[type[Any], Callable[[Any], bool]] = {}
        self._register_step_handlers()

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_loaded(self) -> bool:
        return self._spec is not None

    def _resolve_initial_vars(self, raw_vars: dict[str, Any]) -> dict[str, Any]:
        # Resolve vars against other vars (including nested ${...}) before first tick.
        resolved = dict(raw_vars)
        if not resolved:
            return resolved
        max_passes = max(4, len(resolved) * 4)
        for _ in range(max_passes):
            changed = False
            env = dict(resolved)
            env["vars"] = to_attrdict(resolved)
            for key, value in list(resolved.items()):
                try:
                    rendered = render_templates(value, env)
                except Exception:
                    continue
                if rendered != value:
                    resolved[key] = rendered
                    changed = True
            if not changed:
                break
        return resolved

    def load(
        self,
        spec: SequenceSpec,
        *,
        step_source_info: dict[int, StepSourceInfo] | None = None,
    ) -> None:
        if self._state == "RUNNING":
            raise RuntimeError("Cannot load while running")
        self._spec = spec
        self._base_vars = dict(spec.vars)
        self._vars = self._resolve_initial_vars(spec.vars)
        self._env = {}
        self._stack = []
        self._pause_requested = False
        self._stop_requested = False
        self._last_error = None
        self._last_error_detail = None
        self._pending_terminal_state = None
        self._pending_terminal_error = None
        self._cleanup_failed = False
        self._cleanup_errors = []
        self._sleep_until = None
        self._wait_state = None
        self._adaptive_observe_state = None
        self._atomic_depth = 0
        self._current_step = None
        self._current_step_detail = None
        self._step_source_info = dict(step_source_info or {})
        self._state = "IDLE"
        self._adaptive_start_modes = {}
        self._loop_mode = "once"
        self._loops_target = None
        self._loops_completed = 0
        self._vars_override_active = {}
        self._use_stack = []
        self._reset_progress()

    def start(
        self,
        adaptive: dict[str, Any] | None = None,
        *,
        repeat_count: int | None = None,
        continuous: bool = False,
        vars_override: dict[str, Any] | None = None,
    ) -> None:
        if self._spec is None:
            raise RuntimeError("No sequence loaded")
        if continuous:
            loop_mode = "continuous"
            loops_target = None
        else:
            if repeat_count is None:
                loops_target = 1
            else:
                try:
                    loops_target = int(repeat_count)
                except Exception as exc:
                    raise TypeError("sequencer.start repeat_count must be an integer") from exc
                if loops_target <= 0:
                    raise ValueError("sequencer.start repeat_count must be > 0")
            loop_mode = "repeat" if loops_target > 1 else "once"

        vars_override_map: dict[str, Any] = {}
        if vars_override is not None:
            if not isinstance(vars_override, dict):
                raise TypeError("sequencer.start vars_override must be a dict")
            for raw_key, raw_value in vars_override.items():
                key = str(raw_key).strip()
                if not key:
                    raise ValueError("sequencer.start vars_override keys must be non-empty")
                vars_override_map[key] = raw_value
        if self._spec.vars:
            unknown = sorted(key for key in vars_override_map if key not in self._spec.vars)
            if unknown:
                raise ValueError(
                    "sequencer.start vars_override contains unknown keys: "
                    + ", ".join(unknown)
                )
        elif vars_override_map:
            raise ValueError(
                "sequencer.start vars_override cannot be used when sequence.vars is empty"
            )

        merged_vars = dict(self._base_vars)
        merged_vars.update(vars_override_map)
        self._vars = self._resolve_initial_vars(merged_vars)
        self._vars_override_active = dict(vars_override_map)

        self._run_id = self._next_run_id
        self._next_run_id += 1
        self._loop_mode = loop_mode
        self._loops_target = loops_target
        self._loops_completed = 0
        self._use_stack = []

        self._stack = [_Frame(self._spec.steps)]
        self._adaptive_start_modes = self._normalize_adaptive_start_modes(adaptive)
        self._pause_requested = False
        self._stop_requested = False
        self._last_error = None
        self._last_error_detail = None
        self._pending_terminal_state = None
        self._pending_terminal_error = None
        self._cleanup_failed = False
        self._cleanup_errors = []
        self._env = {}
        self._sleep_until = None
        self._wait_state = None
        self._adaptive_observe_state = None
        self._atomic_depth = 0
        self._current_step = None
        self._current_step_detail = None
        self._reset_progress()
        now = time.monotonic()
        self._run_started_mono = now
        self._run_ended_mono = None
        estimate = self._estimate_total_steps()
        total_per_loop = estimate.total
        if (
            total_per_loop is not None
            and isinstance(self._loops_target, int)
            and self._loops_target >= 0
        ):
            self._estimated_total_steps = int(total_per_loop) * int(self._loops_target)
            self._progress_estimate_reason = None
        else:
            self._estimated_total_steps = None
            self._progress_estimate_reason = (
                "continuous run has no fixed loop count"
                if total_per_loop is not None and self._loop_mode == "continuous"
                else estimate.reason
            )
        self._state = "RUNNING"

    def request_pause(self) -> None:
        self._pause_requested = True

    def resume(self) -> None:
        if self._state == "PAUSED":
            self._mark_pause_ended(time.monotonic())
            self._pause_requested = False
            self._state = "RUNNING"

    def request_stop(self) -> None:
        self._stop_requested = True

    def fail(self, reason: str) -> None:
        if self._state not in {"RUNNING", "PAUSED"}:
            return
        now = time.monotonic()
        self._mark_pause_ended(now)
        self._pause_requested = False
        self._stop_requested = False
        self._sleep_until = None
        self._wait_state = None
        self._adaptive_observe_state = None
        if self._begin_terminal_unwind("ERROR", str(reason or "external fault")):
            self._state = "RUNNING"
            return
        self._last_error = str(reason or "external fault")
        self._state = "ERROR"
        self._run_ended_mono = now

    def status(self) -> dict[str, Any]:
        effective_state = self._state
        if self._state == "RUNNING" and self._stop_requested:
            effective_state = "STOP_REQUESTED"
        return {
            "run_id": int(self._run_id),
            "state": effective_state,
            "current_step": self._current_step,
            "current_step_detail": self._current_step_detail,
            "loop_mode": self._loop_mode,
            "loops_completed": int(self._loops_completed),
            "loops_target": self._loops_target,
            "vars": dict(self._vars),
            "vars_override": dict(self._vars_override_active),
            "env": dict(self._env),
            "error": self._last_error,
            "error_detail": self._last_error_detail,
            "cleanup_active": self._pending_terminal_state is not None,
            "last_context_id": int(self._context_id),
            "next_context_id": int(self._context_id + 1),
            "progress": self._progress_snapshot(time.monotonic()),
            "loaded_adaptive_ids": self._adaptive_step_ids(),
            "adaptive_studies": self._adaptive_studies_snapshot(),
        }

    def adaptive_status(self) -> dict[str, Any]:
        return {
            "loaded_adaptive_ids": self._adaptive_step_ids(),
            "adaptive_studies": self._adaptive_studies_snapshot(),
        }

    def clear_adaptive_studies(self, study_id: str | None = None) -> int:
        if study_id is None:
            count = len(self._adaptive_studies)
            self._adaptive_studies.clear()
            return count
        if study_id in self._adaptive_studies:
            del self._adaptive_studies[study_id]
            return 1
        return 0

    def record_analysis_output(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        self._analysis_outputs.append(dict(payload))
        if len(self._analysis_outputs) > 512:
            del self._analysis_outputs[0 : len(self._analysis_outputs) - 512]

    def next_poll_timeout_ms(self, ceiling_ms: int = 50, floor_ms: int = 1) -> int:
        """Time (in ms) until the next thing this runtime needs to act on.

        The outer process loop (`sequencer.py: run()`) blocks on an RPC/telemetry
        poll between ticks. If a `sleep` step or `wait_until` sample is due sooner
        than the fixed poll ceiling, using the ceiling as the poll timeout would
        quantize that deadline onto the poll cadence (F15). Report the earliest
        of any pending `_sleep_until` deadline or `_wait_state.next_sample_t`,
        clamped to `[floor_ms, ceiling_ms]`, so the caller can wake up exactly
        when needed instead of waiting out the full ceiling.

        When nothing is pending (no active sleep/wait), returns `ceiling_ms`
        unchanged so RPC responsiveness for pause/stop/status doesn't regress.
        """
        if self._state != "RUNNING":
            return ceiling_ms
        deadlines = []
        if self._sleep_until is not None:
            deadlines.append(self._sleep_until)
        if self._wait_state is not None:
            deadlines.append(self._wait_state.next_sample_t)
        if not deadlines:
            return ceiling_ms
        remaining_s = min(deadlines) - time.monotonic()
        remaining_ms = int(math.ceil(remaining_s * 1000.0))
        return max(floor_ms, min(ceiling_ms, remaining_ms))

    def tick(self) -> None:
        if self._state != "RUNNING":
            return

        try:
            if self._pending_terminal_state is not None and not self._stack:
                self._finish_pending_terminal()
                return

            if self._check_stop_pause():
                return

            now = time.monotonic()
            if self._sleep_until is not None:
                if now < self._sleep_until:
                    return
                self._sleep_until = None
                self._finish_active_step(now)

            if self._wait_state is not None:
                if not self._step_wait_until(now):
                    return
                self._finish_active_step(time.monotonic())

            if self._adaptive_observe_state is not None:
                if not self._step_adaptive_observation(now):
                    return

            while self._state == "RUNNING":
                if self._check_stop_pause():
                    return
                step = self._next_step()
                if isinstance(step, _NoStepReady):
                    return
                if step is None:
                    if self._pending_terminal_state is not None:
                        self._finish_pending_terminal()
                        return
                    if self._state == "RUNNING":
                        self._loops_completed += 1
                        if self._should_continue_run_loops():
                            self._prepare_next_run_loop()
                            continue
                        self._state = "STOPPED"
                        self._run_ended_mono = time.monotonic()
                    return
                self._begin_active_step(time.monotonic())
                if self._execute_step(step):
                    if self._sleep_until is None and self._wait_state is None:
                        self._finish_active_step(time.monotonic())
                    return
                self._finish_active_step(time.monotonic())
        except Exception as e:
            if not self._handle_runtime_exception(e):
                if self._last_error_detail is None:
                    self._last_error_detail = self._make_error_detail(str(e))
                self._last_error = str(e)
                self._state = "ERROR"
                self._run_ended_mono = time.monotonic()

    def _env_view(self) -> dict[str, Any]:
        env = dict(self._env)
        env.update(self._vars)
        env["vars"] = to_attrdict(self._vars)
        return env

    def _record_index(self, record: dict[str, Any], fallback: int) -> int:
        raw = record.get("index", fallback)
        try:
            return int(raw)
        except Exception:
            return fallback

    def _assign_bound_record(self, bind: dict[str, str], record: dict[str, Any]) -> None:
        if not isinstance(record, dict):
            raise TypeError("for loop items must resolve to records")
        for source, target in bind.items():
            if source not in record:
                raise KeyError(f"loop record is missing bound field {source!r}")
            self._env[target] = record[source]

    def _records_from_iterable(self, items: list[Any]) -> list[dict[str, Any]]:
        total = len(items)
        denom = max(1, total - 1)
        records: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if isinstance(item, dict):
                record = dict(item)
                record.setdefault("index", index)
                record.setdefault("count", total)
                if "u" not in record:
                    record["u"] = (index / denom) if total > 1 else 0.0
            else:
                record = {
                    "value": item,
                    "index": index,
                    "u": (index / denom) if total > 1 else 0.0,
                    "count": total,
                }
            records.append(record)
        return records

    def _eval_condition_safe(self, cond: Any) -> bool:
        try:
            cond_val = self._resolve_value(cond)
            return eval_condition(cond_val, self._env_view())
        except Exception:
            return False

    def _check_stop_pause(self) -> bool:
        if self._stop_requested and self._atomic_depth == 0:
            now = time.monotonic()
            self._mark_pause_ended(now)
            self._stop_requested = False
            if not self._begin_terminal_unwind("STOPPED", None):
                self._state = "STOPPED"
                self._run_ended_mono = now
            return True
        if self._pause_requested and self._atomic_depth == 0:
            self._mark_pause_started(time.monotonic())
            self._state = "PAUSED"
            return True
        return False

    def _handle_runtime_exception(self, exc: Exception) -> bool:
        message = str(exc)
        if self._pending_terminal_state is not None:
            self._note_cleanup_failure(message)
            return True
        detail = self._make_error_detail(message)
        self._last_error_detail = detail
        return self._begin_terminal_unwind("ERROR", message)

    def _note_cleanup_failure(self, message: str) -> None:
        self._cleanup_failed = True
        detail = self._make_error_detail(str(message))
        self._cleanup_errors.append(detail)
        cleanup_message = f"cleanup failed: {message}"
        if self._last_error_detail is not None:
            self._last_error_detail["cleanup_errors"] = list(self._cleanup_errors)
        if self._pending_terminal_error:
            if cleanup_message not in self._pending_terminal_error:
                self._pending_terminal_error = (
                    f"{self._pending_terminal_error}; {cleanup_message}"
                )
        else:
            self._pending_terminal_error = cleanup_message

    def _begin_terminal_unwind(self, state: str, error: str | None) -> bool:
        if self._pending_terminal_state is not None:
            return bool(self._stack)
        if not self._has_terminal_unwind_work():
            return False
        self._pending_terminal_state = state
        self._pending_terminal_error = error
        self._cleanup_failed = False
        self._cleanup_errors = []
        self._sleep_until = None
        self._wait_state = None
        self._adaptive_observe_state = None
        self._current_step = None
        self._current_step_detail = None
        return True

    def _has_terminal_unwind_work(self) -> bool:
        for frame in reversed(self._stack):
            if isinstance(frame, _Frame) and frame.on_exit is not None:
                return True
            if isinstance(frame, _TryFrame) and not frame.finalizing and frame.finally_steps:
                return True
        return False

    def _finish_pending_terminal(self) -> None:
        state = self._pending_terminal_state
        error = self._pending_terminal_error
        cleanup_failed = self._cleanup_failed
        self._pending_terminal_state = None
        self._pending_terminal_error = None
        self._cleanup_failed = False
        self._sleep_until = None
        self._wait_state = None
        self._adaptive_observe_state = None
        self._state = "ERROR" if cleanup_failed else str(state or "STOPPED")
        self._last_error = error if self._state == "ERROR" else None
        if self._state != "ERROR":
            self._last_error_detail = None
        self._run_ended_mono = time.monotonic()

    def _should_continue_run_loops(self) -> bool:
        if self._state != "RUNNING":
            return False
        if self._loop_mode == "continuous":
            return True
        if isinstance(self._loops_target, int):
            return self._loops_completed < self._loops_target
        return False

    def _prepare_next_run_loop(self) -> None:
        if self._spec is None:
            self._state = "STOPPED"
            self._run_ended_mono = time.monotonic()
            return
        self._stack = [_Frame(self._spec.steps)]
        self._env = {}
        self._sleep_until = None
        self._wait_state = None
        self._adaptive_observe_state = None
        self._pending_terminal_state = None
        self._pending_terminal_error = None
        self._cleanup_failed = False
        self._cleanup_errors = []
        self._atomic_depth = 0
        self._current_step = None
        self._current_step_detail = None
        self._use_stack = []

    def _reset_progress(self) -> None:
        self._estimated_total_steps = None
        self._completed_steps = 0
        self._step_ewma_s = None
        self._eta_ewma_s = None
        self._run_started_mono = None
        self._run_ended_mono = None
        self._paused_total_s = 0.0
        self._paused_started_mono = None
        self._active_step_started_elapsed_s = None
        self._progress_estimate_reason = None

    def _mark_pause_started(self, now: float) -> None:
        if self._paused_started_mono is None:
            self._paused_started_mono = now

    def _mark_pause_ended(self, now: float) -> None:
        if self._paused_started_mono is None:
            return
        self._paused_total_s += max(0.0, now - self._paused_started_mono)
        self._paused_started_mono = None

    def _elapsed_run_s(self, now: float) -> float:
        if self._run_started_mono is None:
            return 0.0
        effective_now = self._run_ended_mono if self._run_ended_mono is not None else now
        paused = self._paused_total_s
        if self._paused_started_mono is not None:
            paused += max(0.0, effective_now - self._paused_started_mono)
        return max(0.0, effective_now - self._run_started_mono - paused)

    def _begin_active_step(self, now: float) -> None:
        if self._active_step_started_elapsed_s is None:
            self._active_step_started_elapsed_s = self._elapsed_run_s(now)

    def _finish_active_step(self, now: float) -> None:
        if self._active_step_started_elapsed_s is None:
            return
        end_elapsed = self._elapsed_run_s(now)
        duration_s = max(0.0, end_elapsed - self._active_step_started_elapsed_s)
        self._active_step_started_elapsed_s = None
        self._completed_steps += 1
        if self._step_ewma_s is None:
            self._step_ewma_s = duration_s
        else:
            alpha = self._step_ewma_alpha
            self._step_ewma_s = (alpha * duration_s) + ((1.0 - alpha) * self._step_ewma_s)

    def _progress_snapshot(self, now: float) -> dict[str, Any]:
        elapsed_s = self._elapsed_run_s(now)
        total = self._estimated_total_steps
        completed = max(0, int(self._completed_steps))
        total_out = int(total) if isinstance(total, int) and total >= 0 else None
        if total_out is not None:
            completed = min(completed, total_out)
        percent: float | None = None
        eta_s: float | None = None
        if total_out is not None and total_out > 0:
            percent = (completed / total_out) * 100.0
            remaining = max(0, total_out - completed)
            if remaining == 0:
                self._eta_ewma_s = 0.0
                eta_s = 0.0
            elif (
                completed >= self._eta_min_completed_steps
                and self._step_ewma_s is not None
                and self._step_ewma_s > 0
            ):
                raw_eta_s = float(remaining) * self._step_ewma_s
                if self._eta_ewma_s is None:
                    self._eta_ewma_s = raw_eta_s
                else:
                    alpha = self._eta_ewma_alpha
                    self._eta_ewma_s = (alpha * raw_eta_s) + (
                        (1.0 - alpha) * self._eta_ewma_s
                    )
                eta_s = self._eta_ewma_s
            else:
                self._eta_ewma_s = None
        current_step_elapsed_s: float | None = None
        if self._active_step_started_elapsed_s is not None:
            current_step_elapsed_s = max(
                0.0, elapsed_s - self._active_step_started_elapsed_s
            )
        return {
            "run_id": int(self._run_id),
            "elapsed_s": elapsed_s,
            "completed_steps": completed,
            "total_steps": total_out,
            "total_steps_known": total_out is not None,
            "estimate_reason": self._progress_estimate_reason,
            "percent": percent,
            "eta_s": eta_s,
            "step_ewma_s": self._step_ewma_s,
            "current_step_elapsed_s": current_step_elapsed_s,
            "loop_mode": self._loop_mode,
            "loops_completed": int(self._loops_completed),
            "loops_target": self._loops_target,
        }

    def _estimate_total_steps(self) -> _StepEstimate:
        if self._spec is None:
            return _StepEstimate(None, "no sequence loaded")
        try:
            env = dict(self._env)
            total = self._estimate_step_list(self._spec.steps, env)
        except Exception as exc:
            return _StepEstimate(None, f"step estimate failed: {exc}")
        if total.total is None:
            return total
        return _StepEstimate(max(0, int(total.total)), None)

    def _estimate_step_list(
        self, steps: list[Step], env: dict[str, Any]
    ) -> _StepEstimate:
        total = 0
        for step in steps:
            count = self._estimate_step(step, env)
            if count.total is None:
                return count
            total += int(count.total)
        return _StepEstimate(total)

    def _estimate_step(self, step: Step, env: dict[str, Any]) -> _StepEstimate:
        if getattr(step, "disabled", False):
            return _StepEstimate(0)
        if isinstance(
            step,
            (
                CallStep,
                SetStep,
                SleepStep,
                WaitUntilStep,
                PauseStep,
                ParallelStep,
                AssignStep,
                SetContextStep,
            ),
        ):
            return _StepEstimate(1)
        if isinstance(step, TryStep):
            body = self._estimate_step_list(step.body, env)
            cleanup = self._estimate_step_list(step.finally_steps, env)
            if body.total is None:
                return body
            if cleanup.total is None:
                return cleanup
            return _StepEstimate(1 + body.total + cleanup.total)
        if isinstance(step, AdaptiveStep):
            return _StepEstimate(None, "adaptive step has unknown trial count")
        if isinstance(step, UseStep):
            spec = self._resolve_use_spec(step.sequence_id)
            merged = self._merged_use_vars(spec, step.args, env=env)
            nested_env = dict(env)
            nested_env.update(merged)
            nested_env["vars"] = to_attrdict(merged)
            inner = self._estimate_step_list(spec.steps, nested_env)
            if inner.total is None:
                return inner
            return _StepEstimate(1 + inner.total)
        if isinstance(step, AtomicStep):
            inner = self._estimate_step_list(step.body, env)
            if inner.total is None:
                return inner
            return _StepEstimate(1 + inner.total)
        if isinstance(step, RepeatStep):
            try:
                times = int(render_templates(step.times, self._estimate_env_view(env)))
            except Exception as exc:
                return _StepEstimate(None, f"repeat count could not render: {exc}")
            if times < 0:
                times = 0
            inner = self._estimate_step_list(step.body, env)
            if inner.total is None:
                return inner
            return _StepEstimate(1 + (times * inner.total))
        if isinstance(step, ForStep):
            loop_index_raw = env.get("__loop_index")
            try:
                records = self._estimate_iterable(
                    step.in_expr,
                    env=env,
                    serpentine_index=loop_index_raw if isinstance(loop_index_raw, int) else None,
                )
            except Exception as exc:
                count = self._estimate_gen_count_only(step.in_expr, env)
                if count is None:
                    path = self._step_source_info.get(id(step))
                    where = f" at {path.path}" if path else ""
                    return _StepEstimate(None, f"for generator{where} could not render: {exc}")
                records = [
                    {"value": None, "index": index, "count": count, "u": 0.0}
                    for index in range(count)
                ]
            total = 1
            for index, record in enumerate(records):
                loop_env = dict(env)
                if not isinstance(record, dict):
                    return _StepEstimate(None, "for loop items could not be estimated")
                loop_index = self._record_index(record, index)
                for source, target in step.bind.items():
                    if source not in record:
                        return _StepEstimate(None, "for loop binding could not be estimated")
                    loop_env[target] = record[source]
                loop_env["__loop_index"] = loop_index
                inner = self._estimate_step_list(step.body, loop_env)
                if inner.total is None:
                    return inner
                total += inner.total
            return _StepEstimate(total)
        if isinstance(step, IfStep):
            cond_ok: bool | None = None
            try:
                cond_val = render_templates(step.condition, self._estimate_env_view(env))
                cond_ok = eval_condition(cond_val, self._estimate_env_view(env))
            except Exception:
                cond_ok = None
            if cond_ok is None:
                return _StepEstimate(None, "if condition could not be evaluated at start")
            branch = step.then_steps if cond_ok else (step.else_steps or [])
            inner = self._estimate_step_list(branch, env)
            if inner.total is None:
                return inner
            return _StepEstimate(1 + inner.total)
        if isinstance(step, WhileStep):
            return _StepEstimate(None, "while loop has unknown iteration count")
        return _StepEstimate(None, f"{step_kind(step)} step could not be estimated")

    def _estimate_env_view(self, env: dict[str, Any]) -> dict[str, Any]:
        out = dict(self._vars)
        out.update(env)
        out["vars"] = to_attrdict(self._vars)
        return out

    # Generator kinds whose element count is fixed by `num` alone: the
    # sampled values (center, span, start, stop, ...) can vary without
    # changing how many records come out. Used as a fallback when the full
    # generator spec can't be rendered yet (e.g. `center` comes from an
    # `assign` step that reads a live device and hasn't run at estimate
    # time), so the progress bar isn't hidden for something count-only
    # estimation can still answer.
    _GEN_COUNT_ONLY_KEYS = ("linspace", "logspace", "geomspace", "triangle", "centered_triangle")

    def _estimate_gen_count_only(self, value: Any, env: dict[str, Any]) -> int | None:
        if not isinstance(value, dict):
            return None
        gen = value.get("gen")
        if not isinstance(gen, dict):
            return None
        env_view = self._estimate_env_view(env)
        base_count: int | None = None
        for kind in self._GEN_COUNT_ONLY_KEYS:
            spec = gen.get(kind)
            if not isinstance(spec, dict) or "num" not in spec:
                continue
            try:
                num = int(render_templates(spec["num"], env_view))
            except Exception:
                return None
            if num < 1:
                return None
            if kind == "triangle":
                base_count = 2 * num
            elif kind == "centered_triangle":
                base_count = 2 * num + 1
            else:
                base_count = num
            break
        if base_count is None:
            return None
        sample_spec = gen.get("sample")
        if isinstance(sample_spec, dict) and "count" in sample_spec:
            try:
                return int(render_templates(sample_spec["count"], env_view))
            except Exception:
                return None
        return base_count

    def _estimate_iterable(
        self,
        value: Any,
        *,
        env: dict[str, Any],
        serpentine_index: int | None,
    ) -> list[dict[str, Any]]:
        env_view = self._estimate_env_view(env)
        rendered = render_templates(value, env_view)
        if isinstance(rendered, dict) and "gen" in rendered:
            gen = rendered["gen"]
            if isinstance(gen, dict):
                return generate_from_gen(
                    gen,
                    env=env_view,
                    serpentine_index=serpentine_index,
                )
        if isinstance(rendered, list):
            return self._records_from_iterable(rendered)
        return self._records_from_iterable([rendered])

    def _next_step(self) -> StepResult:
        if self._pending_terminal_state is not None:
            return self._next_terminal_unwind_step()

        while self._stack:
            frame = self._stack[-1]
            if isinstance(frame, _Frame):
                if frame.index >= len(frame.steps):
                    self._stack.pop()
                    if frame.on_exit:
                        frame.on_exit()
                        if self._state != "RUNNING":
                            return None
                        if self._adaptive_observe_state is not None:
                            return _NO_STEP_READY
                    continue
                step = frame.steps[frame.index]
                frame.index += 1
                if getattr(step, "disabled", False):
                    continue
                return step
            if isinstance(frame, _ForFrame):
                if frame.index >= len(frame.records):
                    self._stack.pop()
                    continue
                record = frame.records[frame.index]
                frame.index += 1
                self._assign_bound_record(frame.bind, record)
                self._env["__loop_index"] = self._record_index(record, frame.index - 1)
                self._stack.append(_Frame(frame.body))
                continue
            if isinstance(frame, _RepeatFrame):
                if frame.remaining <= 0:
                    self._stack.pop()
                    continue
                frame.remaining -= 1
                self._stack.append(_Frame(frame.body))
                continue
            if isinstance(frame, _TryFrame):
                if frame.finalizing:
                    self._stack.pop()
                    continue
                frame.finalizing = True
                if frame.finally_steps:
                    self._stack.append(_Frame(frame.finally_steps, run_during_unwind=True))
                continue
            if isinstance(frame, _WhileFrame):
                ok = self._eval_condition_safe(frame.condition)
                if not ok:
                    self._stack.pop()
                    continue
                self._stack.append(_Frame(frame.body))
                continue
            if isinstance(frame, _AdaptiveFrame):
                if frame.proposal is not None:
                    return _NO_STEP_READY
                if self._adaptive_should_stop(frame):
                    self._stack.pop()
                    continue
                should_stop = getattr(frame.controller, "should_stop", None)
                if callable(should_stop) and bool(should_stop()):
                    self._stack.pop()
                    continue
                proposal = self._prepare_adaptive_proposal(frame)
                frame.proposal = proposal
                self._bind_adaptive_proposal(frame.step, proposal)
                adaptive_frame = frame

                def on_adaptive_exit() -> None:
                    self._after_adaptive_body(adaptive_frame)

                self._stack.append(
                    _Frame(
                        frame.step.body,
                        on_exit=on_adaptive_exit,
                    )
                )
                continue
        return None

    def _next_terminal_unwind_step(self) -> StepResult:
        while self._stack:
            frame = self._stack[-1]
            if isinstance(frame, _Frame):
                if frame.run_during_unwind:
                    if frame.index >= len(frame.steps):
                        self._stack.pop()
                        if frame.on_exit:
                            frame.on_exit()
                            self._sleep_until = None
                            self._wait_state = None
                            self._adaptive_observe_state = None
                        continue
                    step = frame.steps[frame.index]
                    frame.index += 1
                    if getattr(step, "disabled", False):
                        continue
                    return step
                self._stack.pop()
                if frame.on_exit:
                    frame.on_exit()
                    self._sleep_until = None
                    self._wait_state = None
                    self._adaptive_observe_state = None
                continue
            if isinstance(frame, _TryFrame):
                if frame.finalizing:
                    self._stack.pop()
                    continue
                frame.finalizing = True
                if frame.finally_steps:
                    self._stack.append(_Frame(frame.finally_steps, run_during_unwind=True))
                continue
            self._stack.pop()
        return None

    def _execute_step(self, step: Step) -> bool:
        self._current_step = type(step).__name__
        self._current_step_detail = self._format_step_detail(step)
        handler = self._resolve_step_handler(step)
        if handler is None:
            return False
        return handler(step)

    def _format_step_detail(self, step: Step) -> dict[str, Any]:
        source = self._step_source_info.get(id(step))
        return {
            "kind": source.kind if source else step_kind(step),
            "summary": source.summary if source else step_summary(step),
            "path": source.path if source else None,
            "line": source.line if source else None,
            "column": source.column if source else None,
            "source": source.source if source else None,
            "branch": source.branch if source else None,
        }

    def _update_current_step_detail(self, **values: Any) -> None:
        if self._current_step_detail is None:
            self._current_step_detail = {}
        self._current_step_detail.update(values)

    def _make_error_detail(self, message: str) -> dict[str, Any]:
        step = dict(self._current_step_detail) if self._current_step_detail else None
        detail: dict[str, Any] = {
            "message": str(message),
            "formatted": "",
            "step": step,
            "cleanup_errors": [],
        }
        detail["formatted"] = self._format_error_text(detail)
        return detail

    def _format_error_text(self, detail: dict[str, Any]) -> str:
        message = str(detail.get("message") or "sequencer error")
        step = detail.get("step")
        if not isinstance(step, dict):
            return message
        parts: list[str] = []
        summary = step.get("summary")
        if summary:
            parts.append(str(summary))
        source = step.get("source")
        line = step.get("line")
        path = step.get("path")
        branch = step.get("branch")
        if source or line or path:
            location = str(source or "sequence")
            if line is not None:
                location += f":{line}"
            if path:
                location += f" ({path})"
            parts.append(location)
        if branch:
            parts.append(f"branch {branch}")
        if not parts:
            return message
        return f"{message} [{'; '.join(parts)}]"

    @staticmethod
    def _response_error_text(resp: dict[str, Any]) -> str:
        error = resp.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if message and code:
                return f"{message} ({code})"
            if message:
                return str(message)
            if code:
                return str(code)
        if error:
            return str(error)
        return "request failed"

    def _register_step_handlers(self) -> None:
        self._step_handlers = {
            ParallelStep: self._execute_parallel_step,
            PauseStep: self._execute_pause_step,
            SleepStep: self._execute_sleep_step,
            WaitUntilStep: self._execute_wait_until_step,
            ForStep: self._execute_for_step,
            RepeatStep: self._execute_repeat_step,
            IfStep: self._execute_if_step,
            WhileStep: self._execute_while_step,
            AtomicStep: self._execute_atomic_step,
            TryStep: self._execute_try_step,
            AssignStep: self._execute_assign_step,
            SetContextStep: self._execute_set_context_step,
            UseStep: self._execute_use_step,
            AdaptiveStep: self._execute_adaptive_step,
            SetStep: self._execute_set_step,
            CallStep: self._execute_call_step,
        }

    def _resolve_step_handler(self, step: Step) -> Callable[[Any], bool] | None:
        step_type = type(step)
        cached = self._step_handlers.get(step_type)
        if cached is not None:
            return cached
        for registered_type, handler in self._step_handlers.items():
            if isinstance(step, registered_type):
                self._step_handlers[step_type] = handler
                return handler
        return None

    def _fail_step(self, message: str) -> bool:
        detail = self._make_error_detail(str(message))
        if self._pending_terminal_state is not None:
            self._note_cleanup_failure(str(message))
            return True
        self._last_error_detail = detail
        if self._begin_terminal_unwind("ERROR", str(message)):
            return True
        self._last_error = str(message)
        self._state = "ERROR"
        return True

    def _execute_parallel_step(self, step: ParallelStep) -> bool:
        del step
        return self._fail_step("parallel not supported in v1")

    def _execute_pause_step(self, step: PauseStep) -> bool:
        del step
        self._pause_requested = True
        self._state = "PAUSED"
        return True

    def _execute_sleep_step(self, step: SleepStep) -> bool:
        seconds = float(render_templates(step.seconds, self._env_view()))
        self._sleep_until = time.monotonic() + seconds
        return True

    def _execute_wait_until_step(self, step: WaitUntilStep) -> bool:
        self._start_wait_until(step.raw)
        return True

    def _execute_for_step(self, step: ForStep) -> bool:
        parent_index = self._env.get("__loop_index")
        serpentine_index = int(parent_index) if parent_index is not None else None
        records = self._resolve_iterable(step.in_expr, serpentine_index=serpentine_index)
        self._stack.append(_ForFrame(dict(step.bind), records, 0, step.body))
        return False

    def _execute_repeat_step(self, step: RepeatStep) -> bool:
        times = int(render_templates(step.times, self._env_view()))
        self._stack.append(_RepeatFrame(times, step.body))
        return False

    def _execute_if_step(self, step: IfStep) -> bool:
        ok = self._eval_condition_safe(step.condition)
        branch = step.then_steps if ok else (step.else_steps or [])
        self._stack.append(_Frame(branch))
        return False

    def _execute_while_step(self, step: WhileStep) -> bool:
        self._stack.append(_WhileFrame(step.condition, step.body))
        return False

    def _execute_atomic_step(self, step: AtomicStep) -> bool:
        self._atomic_depth += 1

        def _exit() -> None:
            self._atomic_depth -= 1

        self._stack.append(_Frame(step.body, on_exit=_exit))
        return False

    def _execute_try_step(self, step: TryStep) -> bool:
        self._stack.append(_TryFrame(step.finally_steps))
        self._stack.append(_Frame(step.body))
        return False

    def _execute_assign_step(self, step: AssignStep) -> bool:
        for key, value in step.values.items():
            self._env[str(key)] = self._resolve_value(value)
        return False

    def _execute_set_context_step(self, step: SetContextStep) -> bool:
        self._context_id += 1
        ctx_id = self._context_id
        fields = render_templates(step.fields, self._env_view())
        try:
            streams = self._normalize_streams(step.streams)
            if streams and self._expect_streams is not None:
                self._expect_streams(streams, ctx_id)
            for device, stream in streams:
                self._set_stream_context(device, stream, ctx_id, fields)
        except Exception as e:
            return self._fail_step(f"set_context failed: {e}")
        return False

    def _execute_use_step(self, step: UseStep) -> bool:
        sequence_name = str(step.sequence_id).strip()
        if sequence_name in self._use_stack:
            cycle = " -> ".join([*self._use_stack, sequence_name])
            raise RuntimeError(f"recursive use sequence detected: {cycle}")
        spec = self._resolve_use_spec(step.sequence_id)
        merged_vars = self._merged_use_vars(spec, step.args)
        previous_vars = dict(self._vars)
        self._use_stack.append(sequence_name)

        def _restore_vars() -> None:
            self._vars = previous_vars
            if self._use_stack and self._use_stack[-1] == sequence_name:
                self._use_stack.pop()
            elif sequence_name in self._use_stack:
                self._use_stack.remove(sequence_name)

        self._vars = merged_vars
        self._stack.append(_Frame(spec.steps, on_exit=_restore_vars))
        return False

    def _execute_adaptive_step(self, step: AdaptiveStep) -> bool:
        if step.constraints:
            return self._fail_step("adaptive.constraints are not supported in v1")
        rendered_controller = render_templates(step.controller, self._env_view())
        if not isinstance(rendered_controller, dict):
            return self._fail_step("adaptive.controller must render to a dict")
        rendered_space = render_templates(step.space, self._env_view())
        if not isinstance(rendered_space, dict):
            return self._fail_step("adaptive.space must render to a dict")
        controller = self._create_adaptive_controller(
            step,
            rendered_controller=rendered_controller,
            rendered_space=rendered_space,
        )
        replayed_trials = self._prepare_adaptive_study(
            step=step,
            controller=controller,
            rendered_controller=rendered_controller,
            rendered_space=rendered_space,
        )
        self._stack.append(
            _AdaptiveFrame(
                step=step,
                study_id=step.id,
                controller=controller,
                rendered_controller=rendered_controller,
                rendered_space=rendered_space,
                started_t=time.monotonic(),
                trials_completed=replayed_trials,
            )
        )
        return False

    def _execute_set_step(self, step: SetStep) -> bool:
        device = str(render_templates(step.device, self._env_view()) or "").strip()
        self._update_current_step_detail(device=device, name=step.name)
        if not device:
            return self._fail_step("set.device rendered empty")
        value = render_templates(step.value, self._env_view())
        resp = self._call_device(device, "set", {"name": step.name, "value": value})
        if not resp.get("ok", False):
            return self._fail_step(self._response_error_text(resp))
        return False

    def _dispatch_call(
        self, *, device: str, process: str | None, action: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Route a call to a device command or a process RPC."""
        if process:
            if self._call_process is None:
                return {
                    "ok": False,
                    "error": "process calls not supported (call_process not wired)",
                }
            return self._call_process(process, action, params)
        return self._call_device(device, action, params)

    def _dispatch_get_telemetry(
        self, *, device: str, process: str | None, signal: str
    ) -> dict[str, Any] | None:
        """Read the latest sample from device telemetry or process telemetry."""
        if process:
            if self._get_process_telemetry is None:
                return None
            return self._get_process_telemetry(process, signal)
        return self._get_telemetry(device, signal)

    def _render_call_target(
        self, call_spec: dict[str, Any]
    ) -> tuple[str, str | None, str]:
        env = self._env_view()
        device = str(render_templates(call_spec.get("device", ""), env) or "").strip()
        process = str(render_templates(call_spec.get("process", ""), env) or "").strip()
        action = str(render_templates(call_spec.get("action", ""), env) or "").strip()
        if device and process:
            raise ValueError("call may set only one of device / process after rendering")
        if (not device and not process) or not action:
            raise ValueError("call target rendered empty device/process or action")
        return device, process or None, action

    def _execute_call_step(self, step: CallStep) -> bool:
        device, process, action = self._render_call_target(
            {"device": step.device, "process": step.process or "", "action": step.action}
        )
        self._update_current_step_detail(
            target_kind="process" if process else "device",
            device=device or None,
            process=process,
            action=action,
        )
        params = render_templates(step.params, self._env_view())
        resp = self._dispatch_call(
            device=device,
            process=process,
            action=action,
            params=params,
        )
        if step.save_as:
            self._env[step.save_as] = resp
        if step.extract and step.assign:
            return self._fail_step("extract and assign are mutually exclusive")
        if step.extract:
            value = extract_value(
                resp.get("result"),
                kind=step.extract.get("kind", "scalar"),
                ref=step.extract.get("ref"),
            )
            target = step.save_as or "value"
            self._env[target] = value
        if step.assign:
            result = resp.get("result")
            for key, spec in step.assign.items():
                if not isinstance(spec, dict):
                    continue
                self._env[key] = extract_value(
                    result,
                    kind=spec.get("kind", "scalar"),
                    ref=spec.get("ref"),
                )
        if not resp.get("ok", False):
            return self._fail_step(self._response_error_text(resp))
        return False

    def _normalize_streams(self, streams: Any) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if isinstance(streams, list):
            for item in streams:
                if isinstance(item, dict):
                    device = str(item.get("device", ""))
                    stream = str(item.get("stream", ""))
                    if device and stream:
                        out.append((device, stream))
                elif isinstance(item, str):
                    if "." in item:
                        dev, stream = item.split(".", 1)
                        out.append((dev, stream))
                    elif "/" in item:
                        dev, stream = item.split("/", 1)
                        out.append((dev, stream))
        return out

    def _resolve_iterable(
        self, value: Any, *, serpentine_index: int | None
    ) -> list[dict[str, Any]]:
        env = self._env_view()
        rendered = render_templates(value, env)
        if isinstance(rendered, dict) and "gen" in rendered:
            gen = rendered["gen"]
            if isinstance(gen, dict):
                return generate_from_gen(gen, env=env, serpentine_index=serpentine_index)
        if isinstance(rendered, list):
            return self._records_from_iterable(rendered)
        return self._records_from_iterable([rendered])

    def _resolve_use_spec(self, sequence_id: str) -> SequenceSpec:
        sequence_name = str(sequence_id or "").strip()
        if not sequence_name:
            raise ValueError("use.id must not be empty")
        if self._resolve_use is None:
            raise RuntimeError(
                f"use step {sequence_name!r} requires a configured sequence library"
            )
        resolved = self._resolve_use(sequence_name)
        if not isinstance(resolved, SequenceSpec):
            raise TypeError(
                f"use step {sequence_name!r} resolver returned invalid sequence spec"
            )
        return resolved

    def _merged_use_vars(
        self,
        spec: SequenceSpec,
        args: dict[str, Any] | None,
        *,
        env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = dict(self._vars)
        merged.update(spec.vars)
        if args:
            if not isinstance(args, dict):
                raise TypeError("use.args must be a dict")
            source_env = dict(env) if isinstance(env, dict) else self._env_view()
            rendered_args = render_templates(args, source_env)
            if not isinstance(rendered_args, dict):
                raise TypeError("use.args must render to a dict")
            merged.update(rendered_args)
        return self._resolve_initial_vars(merged)

    def _create_adaptive_controller(
        self,
        step: AdaptiveStep,
        *,
        rendered_controller: dict[str, Any],
        rendered_space: dict[str, Any],
    ) -> Any:
        from ..adaptive import create_adaptive_controller

        repeats = self._coerce_positive_int(
            step.observe.get("repeats", 1),
            name="adaptive.observe.repeats",
        )
        return create_adaptive_controller(
            controller_spec=rendered_controller,
            space=rendered_space,
            repeats=repeats,
        )

    def _adaptive_step_ids(self) -> list[str]:
        if self._spec is None:
            return []
        return self._collect_adaptive_step_ids(self._spec.steps, seen_use_ids=set())

    def _collect_adaptive_step_ids(
        self, steps: list[Step], *, seen_use_ids: set[str]
    ) -> list[str]:
        out: list[str] = []
        for step in steps:
            if isinstance(step, AdaptiveStep):
                out.append(step.id)
                out.extend(
                    self._collect_adaptive_step_ids(step.body, seen_use_ids=seen_use_ids)
                )
            elif isinstance(step, ForStep):
                out.extend(
                    self._collect_adaptive_step_ids(step.body, seen_use_ids=seen_use_ids)
                )
            elif isinstance(step, RepeatStep):
                out.extend(
                    self._collect_adaptive_step_ids(step.body, seen_use_ids=seen_use_ids)
                )
            elif isinstance(step, IfStep):
                out.extend(
                    self._collect_adaptive_step_ids(
                        step.then_steps, seen_use_ids=seen_use_ids
                    )
                )
                out.extend(
                    self._collect_adaptive_step_ids(
                        step.else_steps or [], seen_use_ids=seen_use_ids
                    )
                )
            elif isinstance(step, WhileStep):
                out.extend(
                    self._collect_adaptive_step_ids(step.body, seen_use_ids=seen_use_ids)
                )
            elif isinstance(step, AtomicStep):
                out.extend(
                    self._collect_adaptive_step_ids(step.body, seen_use_ids=seen_use_ids)
                )
            elif isinstance(step, ParallelStep):
                out.extend(
                    self._collect_adaptive_step_ids(step.body, seen_use_ids=seen_use_ids)
                )
            elif isinstance(step, TryStep):
                out.extend(
                    self._collect_adaptive_step_ids(step.body, seen_use_ids=seen_use_ids)
                )
                out.extend(
                    self._collect_adaptive_step_ids(
                        step.finally_steps,
                        seen_use_ids=seen_use_ids,
                    )
                )
            elif isinstance(step, UseStep):
                if step.sequence_id in seen_use_ids:
                    continue
                seen_use_ids.add(step.sequence_id)
                try:
                    spec = self._resolve_use_spec(step.sequence_id)
                except Exception:
                    continue
                out.extend(
                    self._collect_adaptive_step_ids(
                        spec.steps,
                        seen_use_ids=seen_use_ids,
                    )
                )
        return out

    def _normalize_adaptive_start_modes(
        self, raw: dict[str, Any] | None
    ) -> dict[str, str]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise TypeError("sequencer.start adaptive override must be a dict")
        out: dict[str, str] = {}
        for raw_key, raw_value in raw.items():
            study_id = str(raw_key).strip()
            if not study_id:
                raise TypeError("sequencer.start adaptive override keys must be non-empty")
            if not isinstance(raw_value, dict):
                raise TypeError(
                    f"sequencer.start adaptive override for {study_id!r} must be a dict"
                )
            mode = str(raw_value.get("mode", "reset") or "reset").strip().lower()
            if mode not in {"reset", "resume", "warm_start"}:
                raise ValueError(
                    f"unsupported adaptive start mode {mode!r} for {study_id!r}"
                )
            out[study_id] = mode
        return out

    def _adaptive_studies_snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for study_id, raw in self._adaptive_studies.items():
            if not isinstance(raw, dict):
                continue
            trials = raw.get("trials")
            out[study_id] = {
                "controller_kind": str(raw.get("controller_kind", "") or "") or None,
                "trial_count": len(trials) if isinstance(trials, list) else 0,
                "last_mode": str(raw.get("last_mode", "") or "") or None,
            }
        return out

    def _prepare_adaptive_study(
        self,
        *,
        step: AdaptiveStep,
        controller: Any,
        rendered_controller: dict[str, Any],
        rendered_space: dict[str, Any],
    ) -> int:
        study_id = step.id
        mode = self._adaptive_start_modes.get(study_id, "reset")
        controller_kind = str(rendered_controller.get("kind", "") or "").strip()
        reusable_trials: list[dict[str, Any]] = []
        if mode in {"resume", "warm_start"}:
            reusable_trials = self._select_reusable_adaptive_trials(
                study_id=study_id,
                controller_kind=controller_kind,
                rendered_space=rendered_space,
            )
        self._adaptive_studies[study_id] = {
            "controller_kind": controller_kind,
            "space": self._clone_adaptive_value(rendered_space),
            "trials": [self._clone_adaptive_trial(item) for item in reusable_trials],
            "last_mode": mode,
            "last_updated_mono": time.monotonic(),
        }
        replayed = 0
        for trial in reusable_trials:
            if self._replay_adaptive_trial(controller, trial):
                replayed += 1
        return replayed

    def _select_reusable_adaptive_trials(
        self,
        *,
        study_id: str,
        controller_kind: str,
        rendered_space: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw = self._adaptive_studies.get(study_id)
        if not isinstance(raw, dict):
            return []
        if str(raw.get("controller_kind", "") or "").strip() != controller_kind:
            return []
        old_space = raw.get("space")
        if not isinstance(old_space, dict):
            return []
        if set(old_space.keys()) != set(rendered_space.keys()):
            return []
        trials = raw.get("trials")
        if not isinstance(trials, list):
            return []
        out: list[dict[str, Any]] = []
        for raw_trial in trials:
            if not isinstance(raw_trial, dict) or not bool(raw_trial.get("ok")):
                continue
            if self._trial_matches_adaptive_space(raw_trial, rendered_space):
                out.append(self._clone_adaptive_trial(raw_trial))
        return out

    def _trial_matches_adaptive_space(
        self, trial: dict[str, Any], rendered_space: dict[str, Any]
    ) -> bool:
        params = trial.get("params")
        if not isinstance(params, dict):
            return False
        for name, spec in rendered_space.items():
            if not isinstance(spec, dict) or name not in params:
                return False
            raw_value = params.get(name)
            param_type = str(spec.get("type", "") or "").strip().lower()
            if param_type == "categorical":
                choices = spec.get("choices")
                if not isinstance(choices, list) or raw_value not in choices:
                    return False
                continue
            if param_type not in {"float", "int"}:
                return False
            try:
                min_raw = spec.get("min")
                max_raw = spec.get("max")
                if not isinstance(raw_value, (str, bytes, bytearray, int, float)):
                    raise TypeError
                if not isinstance(min_raw, (str, bytes, bytearray, int, float)):
                    raise TypeError
                if not isinstance(max_raw, (str, bytes, bytearray, int, float)):
                    raise TypeError
                value = float(raw_value)
                lower = float(min_raw)
                upper = float(max_raw)
            except Exception:
                return False
            if value < lower or value > upper or upper <= lower:
                return False
        return True

    def _replay_adaptive_trial(self, controller: Any, trial: dict[str, Any]) -> bool:
        params = trial.get("params")
        if not isinstance(params, dict) or not params:
            return False
        proposal = {
            "params_raw": dict(trial.get("params_raw") or params),
            "params": dict(params),
            "meta": dict(trial.get("proposal_meta") or {}),
        }
        try:
            controller.tell(proposal, self._clone_adaptive_trial(trial))
        except Exception:
            return False
        return True

    def _clone_adaptive_trial(self, trial: dict[str, Any]) -> dict[str, Any]:
        cloned = self._clone_adaptive_value(trial)
        if isinstance(cloned, dict):
            return cloned
        return {}

    def _clone_adaptive_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._clone_adaptive_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._clone_adaptive_value(item) for item in value]
        return value

    def _coerce_positive_int(self, raw: Any, *, name: str) -> int:
        try:
            value = int(render_templates(raw, self._env_view()))
        except Exception as exc:
            raise TypeError(f"{name} must be an integer") from exc
        if value <= 0:
            raise ValueError(f"{name} must be > 0")
        return value

    def _adaptive_should_stop(self, frame: _AdaptiveFrame) -> bool:
        stopping = frame.step.stopping or {}
        max_trials = stopping.get("max_trials")
        if max_trials is not None:
            try:
                limit = int(render_templates(max_trials, self._env_view()))
            except Exception as exc:
                raise TypeError("adaptive.stopping.max_trials must be an integer") from exc
            if limit >= 0 and frame.trials_completed >= limit:
                return True

        max_runtime_s = stopping.get("max_runtime_s")
        if max_runtime_s is not None:
            try:
                limit_s = float(render_templates(max_runtime_s, self._env_view()))
            except Exception as exc:
                raise TypeError(
                    "adaptive.stopping.max_runtime_s must be a number"
                ) from exc
            if limit_s >= 0 and (time.monotonic() - frame.started_t) >= limit_s:
                return True

        target_score = stopping.get("target_score")
        if target_score is not None and frame.best_score is not None:
            try:
                target = float(render_templates(target_score, self._env_view()))
            except Exception as exc:
                raise TypeError(
                    "adaptive.stopping.target_score must be a number"
                ) from exc
            direction = str(
                frame.rendered_controller.get("direction", "maximize") or "maximize"
            )
            if direction == "minimize":
                if frame.best_score <= target:
                    return True
            else:
                if frame.best_score >= target:
                    return True

        patience = stopping.get("patience")
        if patience is not None:
            try:
                limit = int(render_templates(patience, self._env_view()))
            except Exception as exc:
                raise TypeError("adaptive.stopping.patience must be an integer") from exc
            if limit >= 0 and frame.no_improve_trials >= limit:
                return True

        return False

    def _prepare_adaptive_proposal(self, frame: _AdaptiveFrame) -> dict[str, Any]:
        raw = frame.controller.suggest()
        if not isinstance(raw, dict):
            raise TypeError("adaptive controller must return a dict proposal")
        params_raw = raw.get("params_raw") or {}
        if not isinstance(params_raw, dict) or not params_raw:
            raise TypeError("adaptive controller proposal must include params_raw")
        meta = raw.get("meta") or {}
        if not isinstance(meta, dict):
            raise TypeError("adaptive controller proposal meta must be a dict")
        params = self._apply_adaptive_space(frame.rendered_space, params_raw)
        return {
            "params_raw": dict(params_raw),
            "params": params,
            "meta": dict(meta),
        }

    def _apply_adaptive_space(
        self, space: dict[str, Any], params_raw: dict[str, Any]
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for name, spec in space.items():
            if not isinstance(spec, dict):
                raise TypeError(f"adaptive.space.{name} must be a dict")
            if name not in params_raw:
                raise KeyError(f"adaptive controller proposal is missing parameter {name!r}")
            params[name] = self._coerce_adaptive_param(
                name=name,
                spec=spec,
                raw_value=params_raw[name],
            )
        return params

    def _coerce_adaptive_param(
        self,
        *,
        name: str,
        spec: dict[str, Any],
        raw_value: Any,
    ) -> Any:
        param_type = str(spec.get("type", "") or "").strip().lower()
        if param_type == "categorical":
            choices = spec.get("choices")
            if not isinstance(choices, list) or not choices:
                raise TypeError(
                    f"adaptive.space.{name}.choices must be a non-empty list"
                )
            if raw_value not in choices:
                raise ValueError(
                    f"adaptive controller proposed invalid value {raw_value!r} for {name!r}"
                )
            return raw_value

        if param_type not in {"float", "int"}:
            raise ValueError(
                f"adaptive.space.{name}.type {param_type!r} is not supported in v1"
            )

        try:
            min_raw = spec.get("min")
            max_raw = spec.get("max")
            if not isinstance(raw_value, (str, bytes, bytearray, int, float)):
                raise TypeError
            if not isinstance(min_raw, (str, bytes, bytearray, int, float)):
                raise TypeError
            if not isinstance(max_raw, (str, bytes, bytearray, int, float)):
                raise TypeError
            value = float(raw_value)
            lower = float(min_raw)
            upper = float(max_raw)
        except Exception as exc:
            raise TypeError(
                f"adaptive.space.{name} requires numeric min/max and numeric values"
            ) from exc
        if upper <= lower:
            raise ValueError(f"adaptive.space.{name} requires max > min")

        snap = bool(spec.get("snap", False))
        step = spec.get("step")
        if snap and step is not None:
            try:
                step_size = float(step)
            except Exception as exc:
                raise TypeError(f"adaptive.space.{name}.step must be numeric") from exc
            if step_size <= 0:
                raise ValueError(f"adaptive.space.{name}.step must be > 0")
            try:
                origin = float(spec.get("origin", lower))
            except Exception as exc:
                raise TypeError(f"adaptive.space.{name}.origin must be numeric") from exc
            value = origin + round((value - origin) / step_size) * step_size

        value = max(lower, min(upper, value))

        if param_type == "int":
            return int(round(value))
        return value

    def _bind_adaptive_proposal(
        self, step: AdaptiveStep, proposal: dict[str, Any]
    ) -> None:
        params = proposal.get("params") or {}
        meta = proposal.get("meta") or {}
        if not isinstance(params, dict) or not isinstance(meta, dict):
            raise TypeError("adaptive proposal params/meta must be dicts")
        source_values: dict[str, Any] = dict(params)
        source_values.update(meta)
        for source, target in step.bind.items():
            if source not in source_values:
                raise KeyError(f"adaptive proposal is missing bound field {source!r}")
            self._env[target] = source_values[source]

    def _after_adaptive_body(self, frame: _AdaptiveFrame) -> None:
        proposal = frame.proposal
        if proposal is None:
            return
        if self._adaptive_observe_uses_analysis(frame.step.observe):
            self._start_adaptive_observation(frame, proposal)
            return
        trial = self._collect_adaptive_trial(frame.step, proposal)
        self._finalize_adaptive_trial(frame, proposal, trial)

    def _adaptive_observe_uses_analysis(self, observe: dict[str, Any]) -> bool:
        metrics_spec = observe.get("metrics")
        if not isinstance(metrics_spec, dict):
            return False
        for source_spec in metrics_spec.values():
            if not isinstance(source_spec, dict):
                continue
            if str(source_spec.get("kind", "") or "").strip() == "analysis_output":
                return True
        return False

    def _start_adaptive_observation(
        self,
        frame: _AdaptiveFrame,
        proposal: dict[str, Any],
    ) -> None:
        trial: dict[str, Any] = {
            "ok": True,
            "context_id": int(self._context_id),
            "params_raw": dict(proposal.get("params_raw") or {}),
            "params": dict(proposal.get("params") or {}),
            "proposal_meta": dict(proposal.get("meta") or {}),
            "state": {},
            "metrics": {},
            "replicates": {},
            "aggregates": {},
            "score": None,
        }
        try:
            if frame.step.state:
                trial["state"] = self._collect_adaptive_state(frame.step.state)
        except Exception as exc:
            trial["ok"] = False
            trial["error"] = {"message": str(exc)}
            self._finalize_adaptive_trial(frame, proposal, trial)
            return

        metrics_spec = frame.step.observe.get("metrics")
        if not isinstance(metrics_spec, dict) or not metrics_spec:
            trial["ok"] = False
            trial["error"] = {"message": "adaptive.observe.metrics must be a non-empty dict"}
            self._finalize_adaptive_trial(frame, proposal, trial)
            return

        repeats = self._coerce_positive_int(
            frame.step.observe.get("repeats", 1),
            name="adaptive.observe.repeats",
        )
        trial["replicates"] = {str(name): [] for name in metrics_spec}
        self._adaptive_observe_state = _AdaptiveObserveState(
            frame=frame,
            proposal=proposal,
            trial=trial,
            repeats=repeats,
            metrics_spec={str(name): spec for name, spec in metrics_spec.items()},
            current_repeat=0,
            started_t=time.monotonic(),
        )

    def _step_adaptive_observation(self, now: float) -> bool:
        state = self._adaptive_observe_state
        if state is None:
            return True
        trial = state.trial
        if not bool(trial.get("ok")):
            self._adaptive_observe_state = None
            self._finalize_adaptive_trial(state.frame, state.proposal, trial)
            return True

        if state.current_repeat < state.repeats:
            try:
                if not self._collect_adaptive_repeat(state, now):
                    return False
            except Exception as exc:
                trial["ok"] = False
                trial["error"] = {"message": str(exc)}
                self._adaptive_observe_state = None
                self._finalize_adaptive_trial(state.frame, state.proposal, trial)
                return True
            return False

        try:
            metrics, aggregates = self._finalize_adaptive_metrics_from_replicates(
                trial.get("replicates") or {}
            )
            trial["metrics"] = metrics
            trial["aggregates"] = aggregates
            trial["score"] = self._compute_adaptive_score(
                state.frame.step.observe,
                metrics,
                aggregates,
            )
            if trial["score"] is None:
                raise RuntimeError("adaptive step requires a numeric score in v1")
            trial["score"] = float(trial["score"])
        except Exception as exc:
            trial["ok"] = False
            trial["error"] = {"message": str(exc)}

        self._adaptive_observe_state = None
        self._finalize_adaptive_trial(state.frame, state.proposal, trial)
        return True

    def _collect_adaptive_repeat(self, state: _AdaptiveObserveState, now: float) -> bool:
        del now
        repeat_values: dict[str, Any] = {}
        pending_analysis = False
        for name, source_spec in state.metrics_spec.items():
            if not isinstance(source_spec, dict):
                raise TypeError("adaptive source specs must be dicts")
            kind = str(source_spec.get("kind", "") or "").strip()
            if kind != "analysis_output":
                continue
            value = self._try_sample_analysis_output(source_spec, state.trial)
            if value is _NO_STEP_READY:
                pending_analysis = True
                break
            repeat_values[name] = value

        if pending_analysis:
            return False

        for name, source_spec in state.metrics_spec.items():
            if name in repeat_values:
                continue
            repeat_values[name] = self._sample_adaptive_source(source_spec)

        replicates = state.trial.get("replicates") or {}
        if not isinstance(replicates, dict):
            raise TypeError("adaptive trial replicates must be a dict")
        for name, value in repeat_values.items():
            bucket = replicates.setdefault(name, [])
            if not isinstance(bucket, list):
                raise TypeError("adaptive trial replicate buckets must be lists")
            bucket.append(value)
        state.current_repeat += 1
        return state.current_repeat >= state.repeats

    def _try_sample_analysis_output(
        self,
        source_spec: dict[str, Any],
        trial: dict[str, Any],
    ) -> Any:
        config = source_spec.get("config") or {}
        if not isinstance(config, dict):
            raise TypeError("adaptive analysis_output config must be a dict")
        rendered = render_templates(config, self._env_view())
        if not isinstance(rendered, dict):
            raise TypeError("adaptive analysis_output config must render to a dict")
        workspace_id = str(rendered.get("workspace_id", "") or "").strip()
        output_id = str(rendered.get("output_id", "") or "").strip()
        if not workspace_id or not output_id:
            raise ValueError(
                "adaptive analysis_output source requires workspace_id and output_id"
            )
        require_current_context = bool(rendered.get("require_current_context", True))
        timeout_s = float(rendered.get("timeout_s", 5.0) or 0.0)
        context_id = trial.get("context_id")
        current_context = int(context_id) if context_id is not None else None

        for index, payload in enumerate(list(self._analysis_outputs)):
            if not isinstance(payload, dict):
                continue
            if str(payload.get("workspace_id", "") or "").strip() != workspace_id:
                continue
            if str(payload.get("output_id", "") or "").strip() != output_id:
                continue

            payload_context = payload.get("context_id")
            if require_current_context:
                if payload_context is None:
                    continue
                try:
                    payload_context_i = int(payload_context)
                except Exception:
                    continue
                if current_context is None:
                    continue
                if payload_context_i < current_context:
                    try:
                        del self._analysis_outputs[index]
                    except Exception:
                        pass
                    return _NO_STEP_READY
                if payload_context_i != current_context:
                    continue

            try:
                matched = self._analysis_outputs[index]
                del self._analysis_outputs[index]
            except Exception:
                matched = payload
            return self._extract_analysis_output_value(matched, rendered)

        if timeout_s > 0:
            error = trial.get("error")
            if error is None:
                trial["error"] = {}
            started_mono = trial.setdefault("_observe_started_mono", time.monotonic())
            if (time.monotonic() - float(started_mono)) >= timeout_s:
                raise RuntimeError(
                    f"adaptive analysis_output timed out for {workspace_id}.{output_id}"
                )
        return _NO_STEP_READY

    def _extract_analysis_output_value(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
    ) -> Any:
        extract = config.get("extract")
        value = payload.get("value")
        if isinstance(extract, dict):
            return extract_value(
                value,
                kind=extract.get("kind", "scalar"),
                ref=extract.get("ref"),
            )
        if "value" in payload:
            return value
        return payload

    def _finalize_adaptive_trial(
        self,
        frame: _AdaptiveFrame,
        proposal: dict[str, Any],
        trial: dict[str, Any],
    ) -> None:
        trial.pop("_observe_started_mono", None)
        self._update_adaptive_tracking(frame, trial)
        self._publish_adaptive_trial_env(trial)
        try:
            if bool(trial.get("ok")) or not frame.step.fail_on_trial_error:
                frame.controller.tell(proposal, trial)
            self._store_adaptive_trial(frame, trial)
        finally:
            frame.proposal = None
            frame.trials_completed += 1

        if not bool(trial.get("ok")) and frame.step.fail_on_trial_error:
            error = trial.get("error") or {}
            message = str(error.get("message") or "adaptive trial failed")
            self._last_error = message
            self._state = "ERROR"

    def _collect_adaptive_trial(
        self,
        step: AdaptiveStep,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        trial: dict[str, Any] = {
            "ok": True,
            "context_id": int(self._context_id),
            "params_raw": dict(proposal.get("params_raw") or {}),
            "params": dict(proposal.get("params") or {}),
            "proposal_meta": dict(proposal.get("meta") or {}),
            "state": {},
            "metrics": {},
            "replicates": {},
            "aggregates": {},
            "score": None,
        }
        try:
            if step.state:
                trial["state"] = self._collect_adaptive_state(step.state)
            metrics, replicates, aggregates = self._collect_adaptive_metrics(step.observe)
            trial["metrics"] = metrics
            trial["replicates"] = replicates
            trial["aggregates"] = aggregates
            trial["score"] = self._compute_adaptive_score(step.observe, metrics, aggregates)
            if trial["score"] is None:
                raise RuntimeError("adaptive step requires a numeric score in v1")
            try:
                trial["score"] = float(trial["score"])
            except Exception as exc:
                raise RuntimeError("adaptive score must be numeric") from exc
        except Exception as exc:
            trial["ok"] = False
            trial["error"] = {
                "message": str(exc),
            }
        return trial

    def _store_adaptive_trial(self, frame: _AdaptiveFrame, trial: dict[str, Any]) -> None:
        record = self._adaptive_studies.setdefault(
            frame.study_id,
            {
                "controller_kind": str(
                    frame.rendered_controller.get("kind", "") or ""
                ).strip(),
                "space": self._clone_adaptive_value(frame.rendered_space),
                "trials": [],
                "last_mode": self._adaptive_start_modes.get(frame.study_id, "reset"),
                "last_updated_mono": time.monotonic(),
            },
        )
        trials = record.get("trials")
        if not isinstance(trials, list):
            trials = []
            record["trials"] = trials
        trials.append(self._clone_adaptive_trial(trial))
        record["last_updated_mono"] = time.monotonic()

    def _collect_adaptive_state(self, state_spec: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, source_spec in state_spec.items():
            out[str(name)] = self._sample_adaptive_source(source_spec)
        return out

    def _collect_adaptive_metrics(
        self, observe: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, list[Any]], dict[str, dict[str, Any]]]:
        metrics_spec = observe.get("metrics")
        if not isinstance(metrics_spec, dict) or not metrics_spec:
            raise TypeError("adaptive.observe.metrics must be a non-empty dict")
        repeats = self._coerce_positive_int(
            observe.get("repeats", 1),
            name="adaptive.observe.repeats",
        )
        metrics: dict[str, Any] = {}
        replicates: dict[str, list[Any]] = {str(name): [] for name in metrics_spec}
        for _ in range(repeats):
            for raw_name, source_spec in metrics_spec.items():
                name = str(raw_name)
                replicates[name].append(self._sample_adaptive_source(source_spec))

        metrics, aggregates = self._finalize_adaptive_metrics_from_replicates(replicates)
        return metrics, replicates, aggregates

    def _finalize_adaptive_metrics_from_replicates(
        self,
        replicates: dict[str, list[Any]],
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        aggregates: dict[str, dict[str, Any]] = {}
        metrics: dict[str, Any] = {}
        for name, values in replicates.items():
            stats = self._build_adaptive_aggregates(values)
            aggregates[name] = stats
            if "mean" in stats:
                metrics[name] = stats["mean"]
            elif values:
                metrics[name] = values[-1]
            else:
                metrics[name] = None
        return metrics, aggregates

    def _build_adaptive_aggregates(self, values: list[Any]) -> dict[str, Any]:
        numeric_values: list[float] = []
        n_ok = 0
        for item in values:
            if item is None:
                continue
            n_ok += 1
            try:
                numeric_values.append(float(item))
            except Exception:
                continue

        out: dict[str, Any] = {
            "n": len(values),
            "n_ok": n_ok,
        }
        if not numeric_values:
            return out

        count = len(numeric_values)
        mean_value = statistics.fmean(numeric_values)
        std_value = statistics.stdev(numeric_values) if count > 1 else 0.0
        out.update(
            {
                "mean": mean_value,
                "std": std_value,
                "min": min(numeric_values),
                "max": max(numeric_values),
                "median": statistics.median(numeric_values),
                "sem": (std_value / math.sqrt(count)) if count > 0 else None,
            }
        )
        return out

    def _compute_adaptive_score(
        self,
        observe: dict[str, Any],
        metrics: dict[str, Any],
        aggregates: dict[str, dict[str, Any]],
    ) -> Any:
        score_spec = observe.get("score")
        env = self._env_view()
        for name, value in metrics.items():
            env[name] = value
        for name, stats in aggregates.items():
            for stat_name, stat_value in stats.items():
                env[f"{name}_{stat_name}"] = stat_value
        if score_spec is not None:
            return render_templates(score_spec, env)
        if len(metrics) == 1:
            return next(iter(metrics.values()))
        return None

    def _publish_adaptive_trial_env(self, trial: dict[str, Any]) -> None:
        metrics = trial.get("metrics") or {}
        if isinstance(metrics, dict):
            for name, value in metrics.items():
                self._env[str(name)] = value
        aggregates = trial.get("aggregates") or {}
        if isinstance(aggregates, dict):
            for name, stats in aggregates.items():
                if not isinstance(stats, dict):
                    continue
                for stat_name, stat_value in stats.items():
                    self._env[f"{name}_{stat_name}"] = stat_value
        if "score" in trial:
            self._env["score"] = trial.get("score")

    def _update_adaptive_tracking(self, frame: _AdaptiveFrame, trial: dict[str, Any]) -> None:
        score = trial.get("score")
        if score is None:
            frame.no_improve_trials += 1
            return
        try:
            score_value = float(score)
        except Exception:
            frame.no_improve_trials += 1
            return
        if frame.best_score is None:
            frame.best_score = score_value
            frame.no_improve_trials = 0
            return
        direction = str(
            frame.rendered_controller.get("direction", "maximize") or "maximize"
        )
        improved = (
            score_value < frame.best_score if direction == "minimize" else score_value > frame.best_score
        )
        if improved:
            frame.best_score = score_value
            frame.no_improve_trials = 0
        else:
            frame.no_improve_trials += 1

    def _sample_adaptive_source(self, source_spec: Any) -> Any:
        if not isinstance(source_spec, dict):
            raise TypeError("adaptive source specs must be dicts")
        kind = str(source_spec.get("kind", "") or "").strip()
        config = source_spec.get("config") or {}
        if not isinstance(config, dict):
            raise TypeError("adaptive source config must be a dict")
        rendered = render_templates(config, self._env_view())
        if not isinstance(rendered, dict):
            raise TypeError("adaptive source config must render to a dict")

        if kind == "analysis_output":
            raise RuntimeError(
                "adaptive analysis_output sources are asynchronous and must be sampled by the adaptive runtime"
            )

        if kind == "telemetry":
            return self._sample_adaptive_telemetry(rendered)

        if kind == "call":
            return self._sample_adaptive_call(rendered)

        raise ValueError(f"unsupported adaptive source kind {kind!r}")

    def _sample_adaptive_telemetry(self, config: dict[str, Any]) -> Any:
        device = str(config.get("device", "") or "").strip()
        process = str(config.get("process", "") or "").strip()
        signal = str(config.get("signal", "") or "").strip()
        if (not device and not process) or not signal:
            raise ValueError(
                "adaptive telemetry source requires device-or-process and signal"
            )
        target = process or device
        timeout_s = float(config.get("timeout_s", 0.0) or 0.0)
        max_age_s = float(config.get("max_age_s", 0.0) or 0.0)
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            sample = self._dispatch_get_telemetry(
                device=device, process=process or None, signal=signal
            )
            if sample:
                age = time.monotonic() - float(sample.get("t_mono", 0.0) or 0.0)
                if (not max_age_s) or age <= max_age_s:
                    return sample.get("value")
            if timeout_s <= 0.0 or time.monotonic() >= deadline:
                raise RuntimeError(
                    f"adaptive telemetry source timed out for {target}.{signal}"
                )
            time.sleep(0.01)

    def _sample_adaptive_call(self, config: dict[str, Any]) -> Any:
        device, process, action = self._render_call_target(config)
        params = config.get("params", {}) or {}
        if not isinstance(params, dict):
            raise TypeError("adaptive call source params must be a dict")
        resp = self._dispatch_call(
            device=device,
            process=process,
            action=action,
            params=render_templates(params, self._env_view()),
        )
        if not resp.get("ok", False):
            raise RuntimeError(str(resp.get("error", "adaptive call source failed")))
        extract = config.get("extract")
        if isinstance(extract, dict):
            return extract_value(
                resp.get("result"),
                kind=extract.get("kind", "scalar"),
                ref=extract.get("ref"),
            )
        return resp.get("result")

    def _resolve_value(self, value: Any) -> Any:
        env = self._env_view()
        if isinstance(value, dict) and "telemetry" in value:
            spec = render_templates(value["telemetry"], env)
            if not isinstance(spec, dict):
                return None
            device = str(spec.get("device", ""))
            process = str(spec.get("process", ""))
            signal = str(spec.get("signal", ""))
            if (not device and not process) or not signal:
                return None
            max_age_raw = spec.get("max_age_s", 0)
            max_age = float(max_age_raw if max_age_raw is not None else 0)
            sample = self._dispatch_get_telemetry(
                device=device, process=process or None, signal=signal
            )
            if not sample:
                return None
            age = time.monotonic() - float(sample.get("t_mono", 0))
            if max_age and age > max_age:
                return None
            return sample.get("value")
        if isinstance(value, dict) and "call" in value:
            call_spec = value["call"]
            if not isinstance(call_spec, dict):
                return None
            call_device, call_process, action = self._render_call_target(call_spec)
            params = call_spec.get("params", {}) or {}
            if not isinstance(params, dict):
                params = {}
            resp = self._dispatch_call(
                device=call_device,
                process=call_process,
                action=action,
                params=render_templates(params, env),
            )
            # Raise on failed call instead of silently returning
            # `resp.get("result")` (which on failure is typically None
            # or a stale/partial dict). Without this check, sequencer
            # assign / wait-until / condition values would treat a
            # failed call as a successful None reading, corrupting
            # downstream state. Mirrors _sample_adaptive_call. The outer
            # step-execution loop catches the exception and transitions
            # the sequencer to ERROR with _last_error set.
            if not resp.get("ok", False):
                target = call_process or call_device
                raise RuntimeError(
                    f"call {target}.{action} failed: "
                    f"{resp.get('error', 'unknown error')!s}"
                )
            extract = call_spec.get("extract")
            if isinstance(extract, dict):
                return extract_value(
                    resp.get("result"),
                    kind=extract.get("kind", "scalar"),
                    ref=extract.get("ref"),
                )
            return resp.get("result")
        if isinstance(value, dict):
            return {k: self._resolve_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(v) for v in value]
        return render_templates(value, env)

    def _start_wait_until(self, raw: dict[str, Any]) -> None:
        env = self._env_view()

        def _render_float(value: Any, default: Any) -> float:
            rendered = render_templates(value if value is not None else default, env)
            return float(rendered)

        timeout_s = _render_float(raw.get("timeout_s", 0), 0)
        every_s = _render_float(raw.get("every_s", 0.1), 0.1)
        stable_for_s = _render_float(raw.get("stable_for_s", 0), 0)
        sample_spec_raw = raw.get("sample", {})
        sample_spec = render_templates(sample_spec_raw, env)
        if not isinstance(sample_spec, dict):
            sample_spec = {}
        reduce_spec = raw.get("reduce")
        reduce_spec_dict = render_templates(reduce_spec, env) if isinstance(reduce_spec, dict) else None
        if not isinstance(reduce_spec_dict, dict):
            reduce_spec_dict = None
        max_samples_raw = (
            reduce_spec_dict.get("max_samples") if reduce_spec_dict is not None else None
        )
        try:
            max_samples = (
                int(render_templates(max_samples_raw, env))
                if max_samples_raw is not None
                else 10000
            )
        except Exception:
            max_samples = 10000
        max_samples = max(1, max_samples)
        condition = raw.get("condition")
        now = time.monotonic()
        self._wait_state = _WaitState(
            start_t=now,
            timeout_s=timeout_s,
            every_s=every_s,
            next_sample_t=now,
            stable_for_s=stable_for_s,
            condition=condition,
            sample_spec=sample_spec,
            reduce_spec=reduce_spec_dict,
            samples=[],
            max_samples=max_samples,
        )

    def _step_wait_until(self, now: float) -> bool:
        if self._check_stop_pause():
            return False
        ws = self._wait_state
        if ws is None:
            return True
        if ws.timeout_s and (now - ws.start_t) > ws.timeout_s:
            self._wait_state = None
            self._fail_step("wait_until timeout")
            return True
        if now < ws.next_sample_t:
            return False

        sample = self._resolve_value(ws.sample_spec)
        sample_ts = None
        if isinstance(ws.sample_spec, dict) and "telemetry" in ws.sample_spec:
            spec = ws.sample_spec.get("telemetry", {})
            if isinstance(spec, dict):
                device = str(spec.get("device", ""))
                process = str(spec.get("process", ""))
                signal = str(spec.get("signal", ""))
                if (device or process) and signal:
                    cached = self._dispatch_get_telemetry(
                        device=device, process=process or None, signal=signal
                    )
                    if isinstance(cached, dict):
                        try:
                            t_mono_raw = cached.get("t_mono")
                            if not isinstance(t_mono_raw, (str, bytes, bytearray, int, float)):
                                raise TypeError
                            sample_ts = float(t_mono_raw)
                        except Exception:
                            sample_ts = None
        if sample_ts is None:
            ws.samples.append((now, sample))
        else:
            last_ts = ws.samples[-1][0] if ws.samples else None
            if last_ts != sample_ts:
                ws.samples.append((sample_ts, sample))

        reduce_value = sample
        method = str((ws.reduce_spec or {}).get("method", "mean"))
        if ws.reduce_spec:
            window_s = float(ws.reduce_spec.get("window_s", 0))
            if window_s:
                ws.samples = [(t, v) for t, v in ws.samples if (now - t) <= window_s]
        if len(ws.samples) > ws.max_samples:
            del ws.samples[: len(ws.samples) - ws.max_samples]
        if ws.reduce_spec:
            values = [v for _, v in ws.samples if v is not None]
            if values:
                if method == "mean":
                    reduce_value = sum(values) / len(values)
                elif method == "min":
                    reduce_value = min(values)
                elif method == "max":
                    reduce_value = max(values)
                else:
                    reduce_value = values[-1]

        self._env["sample"] = sample
        self._env["samples"] = [v for _, v in ws.samples]
        self._env["sample_reduced"] = reduce_value

        ok = self._eval_condition_safe(ws.condition)

        if ok:
            if ws.stable_for_s:
                if ws.stable_since is None:
                    ws.stable_since = now
                if (now - ws.stable_since) >= ws.stable_for_s:
                    self._wait_state = None
                    return True
            else:
                self._wait_state = None
                return True
        else:
            ws.stable_since = None

        ws.next_sample_t = now + ws.every_s
        return False
