from __future__ import annotations

from typing import Any

from .ast import (
    AdaptiveStep,
    AtomicStep,
    ForStep,
    IfStep,
    ParallelStep,
    RepeatStep,
    SequenceSpec,
    Step,
    WaitUntilStep,
    WhileStep,
)

_COMPARE_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "abs_lt"}
_LOGICAL_OPS = {"and", "or", "not", "always"}


def _diag(*, severity: str, path: str, message: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "message": f"{path}: {message}",
        "line": None,
        "column": None,
        "source": "sequencer.condition",
    }


def _validate_condition(cond: Any, *, path: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []

    if cond is None:
        diagnostics.append(
            _diag(severity="error", path=path, message="condition is required")
        )
        return diagnostics

    if isinstance(cond, (bool, int, float, str)):
        return diagnostics

    if not isinstance(cond, dict):
        diagnostics.append(
            _diag(
                severity="error",
                path=path,
                message=f"unsupported condition type {type(cond).__name__!r}",
            )
        )
        return diagnostics

    if not cond:
        diagnostics.append(
            _diag(
                severity="error",
                path=path,
                message="condition object must not be empty",
            )
        )
        return diagnostics

    keys = [str(key) for key in cond.keys()]
    if len(keys) != 1:
        diagnostics.append(
            _diag(
                severity="error",
                path=path,
                message="condition object must contain exactly one operator",
            )
        )

    for raw_key, value in cond.items():
        key = str(raw_key)
        op_path = f"{path}.{key}"
        if key not in _LOGICAL_OPS and key not in _COMPARE_OPS:
            diagnostics.append(
                _diag(
                    severity="error",
                    path=op_path,
                    message=f"unknown condition operator {key!r}",
                )
            )
            continue

        if key == "always":
            if len(keys) != 1:
                diagnostics.append(
                    _diag(
                        severity="error",
                        path=path,
                        message="'always' must be the only condition operator",
                    )
                )
            continue

        if key in _COMPARE_OPS:
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                diagnostics.append(
                    _diag(
                        severity="error",
                        path=op_path,
                        message=f"'{key}' expects exactly two arguments",
                    )
                )
                continue
            left, right = value
            if left is None or (isinstance(left, str) and not left.strip()):
                diagnostics.append(
                    _diag(
                        severity="error",
                        path=op_path,
                        message=f"left argument is required for '{key}'",
                    )
                )
            if right is None or (isinstance(right, str) and not right.strip()):
                diagnostics.append(
                    _diag(
                        severity="error",
                        path=op_path,
                        message=f"right argument is required for '{key}'",
                    )
                )
            continue

        if key in {"and", "or"}:
            if not isinstance(value, list):
                diagnostics.append(
                    _diag(
                        severity="error",
                        path=op_path,
                        message=f"'{key}' expects a list of conditions",
                    )
                )
                continue
            if len(value) <= 0:
                diagnostics.append(
                    _diag(
                        severity="error",
                        path=op_path,
                        message=f"'{key}' requires at least one clause",
                    )
                )
                continue
            if len(value) == 1:
                diagnostics.append(
                    _diag(
                        severity="warning",
                        path=op_path,
                        message=f"'{key}' has only one clause; consider removing the wrapper",
                    )
                )
            for index, item in enumerate(value):
                diagnostics.extend(
                    _validate_condition(item, path=f"{op_path}[{index}]")
                )
            continue

        # key == "not"
        diagnostics.extend(_validate_condition(value, path=f"{op_path}"))

    return diagnostics


def _iter_step_condition_diagnostics(
    steps: list[Step], *, path: str
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        step_path = f"{path}[{index}]"
        if isinstance(step, WaitUntilStep):
            condition_path = f"{step_path}.wait_until.condition"
            raw = step.raw if isinstance(step.raw, dict) else {}
            if "condition" not in raw:
                diagnostics.append(
                    _diag(
                        severity="error",
                        path=condition_path,
                        message="condition is required",
                    )
                )
            else:
                diagnostics.extend(
                    _validate_condition(raw.get("condition"), path=condition_path)
                )
            continue
        if isinstance(step, IfStep):
            diagnostics.extend(
                _validate_condition(step.condition, path=f"{step_path}.if.condition")
            )
            diagnostics.extend(
                _iter_step_condition_diagnostics(
                    step.then_steps, path=f"{step_path}.if.then"
                )
            )
            if step.else_steps:
                diagnostics.extend(
                    _iter_step_condition_diagnostics(
                        step.else_steps, path=f"{step_path}.if.else"
                    )
                )
            continue
        if isinstance(step, WhileStep):
            diagnostics.extend(
                _validate_condition(step.condition, path=f"{step_path}.while.condition")
            )
            diagnostics.extend(
                _iter_step_condition_diagnostics(
                    step.body, path=f"{step_path}.while.do"
                )
            )
            continue
        if isinstance(step, ForStep):
            diagnostics.extend(
                _iter_step_condition_diagnostics(step.body, path=f"{step_path}.for.do")
            )
            continue
        if isinstance(step, RepeatStep):
            diagnostics.extend(
                _iter_step_condition_diagnostics(
                    step.body, path=f"{step_path}.repeat.do"
                )
            )
            continue
        if isinstance(step, AdaptiveStep):
            diagnostics.extend(
                _iter_step_condition_diagnostics(
                    step.body, path=f"{step_path}.adaptive.do"
                )
            )
            continue
        if isinstance(step, AtomicStep):
            diagnostics.extend(
                _iter_step_condition_diagnostics(
                    step.body, path=f"{step_path}.atomic.do"
                )
            )
            continue
        if isinstance(step, ParallelStep):
            diagnostics.extend(
                _iter_step_condition_diagnostics(
                    step.body, path=f"{step_path}.parallel.do"
                )
            )
            continue
    return diagnostics


def validate_sequence_conditions(spec: SequenceSpec) -> list[dict[str, Any]]:
    return _iter_step_condition_diagnostics(spec.steps, path="steps")


def has_error_diagnostics(diagnostics: list[dict[str, Any]]) -> bool:
    return any(str(item.get("severity", "")).lower() == "error" for item in diagnostics)
