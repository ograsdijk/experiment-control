from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    TryStep,
    UseStep,
    WaitUntilStep,
    WhileStep,
)


@dataclass(frozen=True)
class StepSourceInfo:
    path: str
    line: int | None
    column: int | None
    source: str | None
    kind: str
    summary: str | None
    branch: str | None = None


def step_kind(step: Step) -> str:
    if isinstance(step, CallStep):
        return "call"
    if isinstance(step, SetStep):
        return "set"
    if isinstance(step, SleepStep):
        return "sleep"
    if isinstance(step, WaitUntilStep):
        return "wait_until"
    if isinstance(step, ForStep):
        return "for"
    if isinstance(step, RepeatStep):
        return "repeat"
    if isinstance(step, IfStep):
        return "if"
    if isinstance(step, WhileStep):
        return "while"
    if isinstance(step, AtomicStep):
        return "atomic"
    if isinstance(step, PauseStep):
        return "pause"
    if isinstance(step, ParallelStep):
        return "parallel"
    if isinstance(step, TryStep):
        return "try"
    if isinstance(step, AssignStep):
        return "assign"
    if isinstance(step, SetContextStep):
        return "set_context"
    if isinstance(step, UseStep):
        return "use"
    if isinstance(step, AdaptiveStep):
        return "adaptive"
    return type(step).__name__


def _compact_text(value: Any, *, max_len: int = 80) -> str:
    text = str(value).replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 3)].rstrip() + "..."


def step_summary(step: Step) -> str | None:
    if isinstance(step, CallStep):
        target = step.process or step.device
        prefix = "process " if step.process else ""
        return f"call {prefix}{target}.{step.action}".strip()
    if isinstance(step, SetStep):
        return f"set {step.device}.{step.name}"
    if isinstance(step, SleepStep):
        return f"sleep {_compact_text(step.seconds)}s"
    if isinstance(step, WaitUntilStep):
        timeout = step.raw.get("timeout_s")
        cond = step.raw.get("condition")
        parts = ["wait_until"]
        if cond is not None:
            parts.append(_compact_text(cond, max_len=48))
        if timeout is not None:
            parts.append(f"timeout {timeout}s")
        return " ".join(parts)
    if isinstance(step, ForStep):
        bind = ", ".join(f"{k}->{v}" for k, v in step.bind.items())
        gen = "iterable"
        in_expr = step.in_expr
        if isinstance(in_expr, dict) and isinstance(in_expr.get("gen"), dict):
            gen_keys = [str(key) for key in in_expr["gen"] if key not in {"offset", "shuffle", "seed", "serpentine"}]
            gen = gen_keys[0] if gen_keys else "generator"
        return f"for {bind or 'items'} in {gen}"
    if isinstance(step, RepeatStep):
        return f"repeat {_compact_text(step.times)}"
    if isinstance(step, IfStep):
        return f"if {_compact_text(step.condition, max_len=64)}"
    if isinstance(step, WhileStep):
        return f"while {_compact_text(step.condition, max_len=64)}"
    if isinstance(step, AtomicStep):
        return f"atomic {step.name}" if step.name else "atomic"
    if isinstance(step, PauseStep):
        return f"pause {step.reason}" if step.reason else "pause"
    if isinstance(step, ParallelStep):
        return "parallel"
    if isinstance(step, TryStep):
        return "try"
    if isinstance(step, AssignStep):
        names = ", ".join(str(key) for key in step.values.keys())
        return f"assign {names}" if names else "assign"
    if isinstance(step, SetContextStep):
        return "set_context"
    if isinstance(step, UseStep):
        return f"use {step.sequence_id}"
    if isinstance(step, AdaptiveStep):
        return f"adaptive {step.id}"
    return None


def _walk_source_info(
    steps: list[Step],
    *,
    path: str,
    source: str | None,
    line_map: dict[str, int],
    out: dict[int, StepSourceInfo],
    branch: str | None = None,
) -> None:
    for index, step in enumerate(steps):
        step_path = f"{path}[{index}]"
        out[id(step)] = StepSourceInfo(
            path=step_path,
            line=line_map.get(step_path),
            column=None,
            source=source,
            kind=step_kind(step),
            summary=step_summary(step),
            branch=branch,
        )
        if isinstance(step, ForStep):
            _walk_source_info(step.body, path=f"{step_path}.for.do", source=source, line_map=line_map, out=out)
        elif isinstance(step, RepeatStep):
            _walk_source_info(step.body, path=f"{step_path}.repeat.do", source=source, line_map=line_map, out=out)
        elif isinstance(step, IfStep):
            _walk_source_info(step.then_steps, path=f"{step_path}.if.then", source=source, line_map=line_map, out=out, branch="then")
            _walk_source_info(step.else_steps or [], path=f"{step_path}.if.else", source=source, line_map=line_map, out=out, branch="else")
        elif isinstance(step, WhileStep):
            _walk_source_info(step.body, path=f"{step_path}.while.do", source=source, line_map=line_map, out=out)
        elif isinstance(step, AtomicStep):
            _walk_source_info(step.body, path=f"{step_path}.atomic.do", source=source, line_map=line_map, out=out)
        elif isinstance(step, ParallelStep):
            _walk_source_info(step.body, path=f"{step_path}.parallel.do", source=source, line_map=line_map, out=out)
        elif isinstance(step, TryStep):
            _walk_source_info(step.body, path=f"{step_path}.try.do", source=source, line_map=line_map, out=out)
            _walk_source_info(step.finally_steps, path=f"{step_path}.try.finally", source=source, line_map=line_map, out=out, branch="finally")
        elif isinstance(step, UseStep):
            # A use step's referenced sequence is not part of this YAML source.
            continue
        elif isinstance(step, AdaptiveStep):
            _walk_source_info(step.body, path=f"{step_path}.adaptive.do", source=source, line_map=line_map, out=out)


def build_step_source_info(
    spec: SequenceSpec,
    *,
    source: str | None,
    line_map: dict[str, int],
) -> dict[int, StepSourceInfo]:
    out: dict[int, StepSourceInfo] = {}
    _walk_source_info(spec.steps, path="steps", source=source, line_map=line_map, out=out)
    return out
