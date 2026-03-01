from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..driver import extract_value
from .ast import (
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
    WhileStep,
    WaitUntilStep,
)
from .eval import eval_condition, render_templates, to_attrdict
from .ranges import generate_from_gen


@dataclass
class _Frame:
    steps: list[Step]
    index: int = 0
    on_exit: Callable[[], None] | None = None


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
class _WhileFrame:
    condition: Any
    body: list[Step]


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
    stable_since: float | None = None


class SequencerRuntime:
    def __init__(
        self,
        *,
        call_device: Callable[[str, str, dict[str, Any]], dict[str, Any]],
        get_telemetry: Callable[[str, str], dict[str, Any] | None],
        set_stream_context: Callable[[str, str, int, dict[str, Any]], None],
    ) -> None:
        self._call_device = call_device
        self._get_telemetry = get_telemetry
        self._set_stream_context = set_stream_context

        self._spec: SequenceSpec | None = None
        self._vars: dict[str, Any] = {}
        self._env: dict[str, Any] = {}
        self._stack: list[_Frame | _ForFrame | _RepeatFrame | _WhileFrame] = []
        self._state = "IDLE"
        self._pause_requested = False
        self._stop_requested = False
        self._last_error: str | None = None
        self._sleep_until: float | None = None
        self._wait_state: _WaitState | None = None
        self._atomic_depth = 0
        self._current_step: str | None = None
        self._context_id = -1
        self._estimated_total_steps: int | None = None
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

    def load(self, spec: SequenceSpec) -> None:
        if self._state == "RUNNING":
            raise RuntimeError("Cannot load while running")
        self._spec = spec
        self._vars = self._resolve_initial_vars(spec.vars)
        self._env = {}
        self._stack = []
        self._pause_requested = False
        self._stop_requested = False
        self._last_error = None
        self._sleep_until = None
        self._wait_state = None
        self._atomic_depth = 0
        self._current_step = None
        self._state = "IDLE"
        self._reset_progress()

    def start(self) -> None:
        if self._spec is None:
            raise RuntimeError("No sequence loaded")
        self._stack = [_Frame(self._spec.steps)]
        self._pause_requested = False
        self._stop_requested = False
        self._sleep_until = None
        self._wait_state = None
        self._atomic_depth = 0
        self._current_step = None
        self._reset_progress()
        now = time.monotonic()
        self._run_started_mono = now
        self._run_ended_mono = None
        self._estimated_total_steps = self._estimate_total_steps()
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
        self._last_error = str(reason or "external fault")
        self._state = "ERROR"
        self._run_ended_mono = now
        self._pause_requested = False
        self._stop_requested = False
        self._sleep_until = None
        self._wait_state = None

    def status(self) -> dict[str, Any]:
        effective_state = self._state
        if self._state == "RUNNING" and self._stop_requested:
            effective_state = "STOP_REQUESTED"
        return {
            "state": effective_state,
            "current_step": self._current_step,
            "vars": dict(self._vars),
            "env": dict(self._env),
            "error": self._last_error,
            "last_context_id": int(self._context_id),
            "next_context_id": int(self._context_id + 1),
            "progress": self._progress_snapshot(time.monotonic()),
        }

    def tick(self) -> None:
        if self._state != "RUNNING":
            return

        try:
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

            while self._state == "RUNNING":
                if self._check_stop_pause():
                    return
                step = self._next_step()
                if step is None:
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
            self._state = "STOPPED"
            self._run_ended_mono = now
            return True
        if self._pause_requested and self._atomic_depth == 0:
            self._mark_pause_started(time.monotonic())
            self._state = "PAUSED"
            return True
        return False

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
            "elapsed_s": elapsed_s,
            "completed_steps": completed,
            "total_steps": total_out,
            "percent": percent,
            "eta_s": eta_s,
            "step_ewma_s": self._step_ewma_s,
            "current_step_elapsed_s": current_step_elapsed_s,
        }

    def _estimate_total_steps(self) -> int | None:
        if self._spec is None:
            return None
        try:
            env = dict(self._env)
            total = self._estimate_step_list(self._spec.steps, env)
        except Exception:
            return None
        if total is None:
            return None
        return max(0, int(total))

    def _estimate_step_list(self, steps: list[Step], env: dict[str, Any]) -> int | None:
        total = 0
        for step in steps:
            count = self._estimate_step(step, env)
            if count is None:
                return None
            total += int(count)
        return total

    def _estimate_step(self, step: Step, env: dict[str, Any]) -> int | None:
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
            return 1
        if isinstance(step, AtomicStep):
            inner = self._estimate_step_list(step.body, env)
            if inner is None:
                return None
            return 1 + inner
        if isinstance(step, RepeatStep):
            times = int(render_templates(step.times, self._estimate_env_view(env)))
            if times < 0:
                times = 0
            inner = self._estimate_step_list(step.body, env)
            if inner is None:
                return None
            return 1 + (times * inner)
        if isinstance(step, ForStep):
            records = self._estimate_iterable(
                step.in_expr,
                env=env,
                serpentine_index=(
                    int(env.get("__loop_index"))
                    if isinstance(env.get("__loop_index"), int)
                    else None
                ),
            )
            total = 1
            for index, record in enumerate(records):
                loop_env = dict(env)
                if not isinstance(record, dict):
                    return None
                loop_index = self._record_index(record, index)
                for source, target in step.bind.items():
                    if source not in record:
                        return None
                    loop_env[target] = record[source]
                loop_env["__loop_index"] = loop_index
                inner = self._estimate_step_list(step.body, loop_env)
                if inner is None:
                    return None
                total += inner
            return total
        if isinstance(step, IfStep):
            cond_ok: bool | None = None
            try:
                cond_val = render_templates(step.condition, self._estimate_env_view(env))
                cond_ok = eval_condition(cond_val, self._estimate_env_view(env))
            except Exception:
                cond_ok = None
            if cond_ok is None:
                return None
            branch = step.then_steps if cond_ok else (step.else_steps or [])
            inner = self._estimate_step_list(branch, env)
            if inner is None:
                return None
            return 1 + inner
        if isinstance(step, WhileStep):
            return None
        return None

    def _estimate_env_view(self, env: dict[str, Any]) -> dict[str, Any]:
        out = dict(self._vars)
        out.update(env)
        out["vars"] = to_attrdict(self._vars)
        return out

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

    def _next_step(self) -> Step | None:
        while self._stack:
            frame = self._stack[-1]
            if isinstance(frame, _Frame):
                if frame.index >= len(frame.steps):
                    self._stack.pop()
                    if frame.on_exit:
                        frame.on_exit()
                    continue
                step = frame.steps[frame.index]
                frame.index += 1
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
            if isinstance(frame, _WhileFrame):
                ok = self._eval_condition_safe(frame.condition)
                if not ok:
                    self._stack.pop()
                    continue
                self._stack.append(_Frame(frame.body))
                continue
        return None

    def _execute_step(self, step: Step) -> bool:
        self._current_step = type(step).__name__
        if isinstance(step, ParallelStep):
            self._last_error = "parallel not supported in v1"
            self._state = "ERROR"
            return True
        if isinstance(step, PauseStep):
            self._pause_requested = True
            self._state = "PAUSED"
            return True
        if isinstance(step, SleepStep):
            seconds = float(render_templates(step.seconds, self._env_view()))
            self._sleep_until = time.monotonic() + seconds
            return True
        if isinstance(step, WaitUntilStep):
            self._start_wait_until(step.raw)
            return True
        if isinstance(step, ForStep):
            parent_index = self._env.get("__loop_index")
            serpentine_index = int(parent_index) if parent_index is not None else None
            records = self._resolve_iterable(
                step.in_expr, serpentine_index=serpentine_index
            )
            self._stack.append(_ForFrame(dict(step.bind), records, 0, step.body))
            return False
        if isinstance(step, RepeatStep):
            times = int(render_templates(step.times, self._env_view()))
            self._stack.append(_RepeatFrame(times, step.body))
            return False
        if isinstance(step, IfStep):
            ok = self._eval_condition_safe(step.condition)
            branch = step.then_steps if ok else (step.else_steps or [])
            self._stack.append(_Frame(branch))
            return False
        if isinstance(step, WhileStep):
            self._stack.append(_WhileFrame(step.condition, step.body))
            return False
        if isinstance(step, AtomicStep):
            self._atomic_depth += 1

            def _exit() -> None:
                self._atomic_depth -= 1

            self._stack.append(_Frame(step.body, on_exit=_exit))
            return False
        if isinstance(step, AssignStep):
            for key, value in step.values.items():
                self._env[str(key)] = self._resolve_value(value)
            return False
        if isinstance(step, SetContextStep):
            self._context_id += 1
            ctx_id = self._context_id
            fields = render_templates(step.fields, self._env_view())
            for item in self._normalize_streams(step.streams):
                device, stream = item
                self._set_stream_context(device, stream, ctx_id, fields)
            return False
        if isinstance(step, SetStep):
            value = render_templates(step.value, self._env_view())
            resp = self._call_device(step.device, "set", {"name": step.name, "value": value})
            if not resp.get("ok", False):
                self._last_error = str(resp.get("error"))
                self._state = "ERROR"
                return True
            return False
        if isinstance(step, CallStep):
            params = render_templates(step.params, self._env_view())
            resp = self._call_device(step.device, step.action, params)
            if step.save_as:
                self._env[step.save_as] = resp
            if step.extract and step.assign:
                self._last_error = "extract and assign are mutually exclusive"
                self._state = "ERROR"
                return True
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
                self._last_error = str(resp.get("error"))
                self._state = "ERROR"
                return True
            return False
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

    def _resolve_value(self, value: Any) -> Any:
        env = self._env_view()
        if isinstance(value, dict) and "telemetry" in value:
            spec = value["telemetry"]
            if not isinstance(spec, dict):
                return None
            device = str(spec.get("device", ""))
            signal = str(spec.get("signal", ""))
            if not device or not signal:
                return None
            max_age = float(spec.get("max_age_s", 0))
            sample = self._get_telemetry(device, signal)
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
            device = str(call_spec.get("device", ""))
            action = str(call_spec.get("action", ""))
            params = call_spec.get("params", {}) or {}
            if not isinstance(params, dict):
                params = {}
            resp = self._call_device(device, action, render_templates(params, env))
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
        timeout_s = float(raw.get("timeout_s", 0))
        every_s = float(raw.get("every_s", 0.1))
        stable_for_s = float(raw.get("stable_for_s", 0))
        sample_spec = raw.get("sample", {})
        if isinstance(sample_spec, dict):
            sample_spec = sample_spec
        else:
            sample_spec = {}
        reduce_spec = raw.get("reduce")
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
            reduce_spec=reduce_spec if isinstance(reduce_spec, dict) else None,
            samples=[],
        )

    def _step_wait_until(self, now: float) -> bool:
        if self._check_stop_pause():
            return False
        ws = self._wait_state
        if ws is None:
            return True
        if ws.timeout_s and (now - ws.start_t) > ws.timeout_s:
            self._last_error = "wait_until timeout"
            self._state = "ERROR"
            self._wait_state = None
            return True
        if now < ws.next_sample_t:
            return False

        sample = self._resolve_value(ws.sample_spec)
        sample_ts = None
        if isinstance(ws.sample_spec, dict) and "telemetry" in ws.sample_spec:
            spec = ws.sample_spec.get("telemetry", {})
            if isinstance(spec, dict):
                device = str(spec.get("device", ""))
                signal = str(spec.get("signal", ""))
                if device and signal:
                    cached = self._get_telemetry(device, signal)
                    if isinstance(cached, dict):
                        try:
                            sample_ts = float(cached.get("t_mono"))
                        except Exception:
                            sample_ts = None
        if sample_ts is None:
            ws.samples.append((now, sample))
        else:
            last_ts = ws.samples[-1][0] if ws.samples else None
            if last_ts != sample_ts:
                ws.samples.append((sample_ts, sample))

        reduce_value = sample
        if ws.reduce_spec:
            method = str(ws.reduce_spec.get("method", "mean"))
            window_s = float(ws.reduce_spec.get("window_s", 0))
            if window_s:
                ws.samples = [(t, v) for t, v in ws.samples if (now - t) <= window_s]
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
