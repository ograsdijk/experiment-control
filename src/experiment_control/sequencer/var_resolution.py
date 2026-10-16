"""Resolve sequence `vars` and preview template values for the UI."""

from __future__ import annotations

import json
import math
from typing import Any

from .eval import _TEMPLATE_RE, UnknownNameError, render_templates, to_attrdict


def contains_template(value: Any) -> bool:
    if isinstance(value, str):
        return _TEMPLATE_RE.search(value) is not None
    if isinstance(value, list):
        return any(contains_template(v) for v in value)
    if isinstance(value, dict):
        return any(contains_template(v) for v in value.values())
    return False


def _vars_env(resolved: dict[str, Any]) -> dict[str, Any]:
    env = dict(resolved)
    env["vars"] = to_attrdict(resolved)
    return env


def resolve_vars(raw_vars: dict[str, Any]) -> dict[str, Any]:
    """Resolve vars against other vars (including nested ${...}).

    A var that cannot be resolved yet keeps its template: it may read a name
    that only exists while running (a loop variable), and is rendered again
    in the step that uses it.
    """
    resolved = dict(raw_vars)
    if not resolved:
        return resolved
    max_passes = max(4, len(resolved) * 4)
    for _ in range(max_passes):
        changed = False
        env = _vars_env(resolved)
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


def unresolved_vars(
    resolved: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Why each var that still holds a template is unresolved.

    Returns the vars that read a name other than a var (set while running,
    e.g. `label: run_${i}` inside `for i`) with that name, and the error of
    every other one.
    """
    runtime: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    env = _vars_env(resolved)
    for key, value in resolved.items():
        if not contains_template(value):
            continue
        try:
            render_templates(value, env)
        except UnknownNameError as e:
            if e.name in resolved:
                errors[str(key)] = str(e)
            else:
                runtime[str(key)] = [e.name]
        except Exception as e:
            errors[str(key)] = str(e)
    return runtime, errors


def evaluate_expressions(
    expressions: list[str], resolved_vars: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Evaluate template strings against the resolved vars only.

    An expression that needs a name other than a var (loop variable, save_as,
    assign) has no value before the run: it is reported as `runtime` with
    that name instead of as an error.
    """
    env = _vars_env(resolved_vars)
    out: dict[str, dict[str, Any]] = {}
    for text in expressions:
        if not isinstance(text, str) or text in out:
            continue
        try:
            value = render_templates(text, env)
        except UnknownNameError as e:
            out[text] = {"ok": False, "runtime": True, "names": [e.name]}
            continue
        except Exception as e:
            out[text] = {"ok": False, "error": str(e)}
            continue
        out[text] = {"ok": True, "value": json_safe(value)}
    return out


def formula_text(value: Any) -> str:
    """A derived var's value as written: the string, or JSON for a list/dict."""
    return value if isinstance(value, str) else json.dumps(json_safe(value))


def json_safe(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)
