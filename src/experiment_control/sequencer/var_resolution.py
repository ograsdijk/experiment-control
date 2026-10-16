"""Resolve sequence `vars` and preview template values for the UI."""

from __future__ import annotations

import ast
import math
from typing import Any

from .eval import _TEMPLATE_RE, render_templates, to_attrdict

_BUILTIN_FUNCS = frozenset({"abs", "len", "min", "max"})


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


def resolve_vars(raw_vars: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve vars against other vars (including nested ${...}).

    Returns the resolved vars and, for each var that is still unresolved,
    the error from rendering it one last time.
    """
    resolved = dict(raw_vars)
    if not resolved:
        return resolved, {}
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
    errors: dict[str, str] = {}
    env = _vars_env(resolved)
    for key, value in resolved.items():
        if not contains_template(value):
            continue
        try:
            render_templates(value, env)
        except Exception as e:
            errors[str(key)] = str(e)
    return resolved, errors


def _expression_names(text: str) -> set[str]:
    """Names an expression reads, besides the builtin functions and `vars`."""
    names: set[str] = set()
    for match in _TEMPLATE_RE.finditer(text):
        try:
            tree = ast.parse(match.group(1), mode="eval")
        except SyntaxError:
            continue
        calls = {
            id(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and id(node) not in calls and node.id != "vars":
                names.add(node.id)
    return names


def evaluate_expressions(
    expressions: list[str], resolved_vars: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Evaluate template strings against the resolved vars only.

    An expression that reads names other than vars (loop variables, save_as,
    assign) has no value before the run: it is reported as `runtime` with
    those names instead of as an error.
    """
    env = _vars_env(resolved_vars)
    out: dict[str, dict[str, Any]] = {}
    for text in expressions:
        if not isinstance(text, str) or text in out:
            continue
        runtime_names = sorted(
            name
            for name in _expression_names(text)
            if name not in resolved_vars and name not in _BUILTIN_FUNCS
        )
        if runtime_names:
            out[text] = {"ok": False, "runtime": True, "names": runtime_names}
            continue
        try:
            value = render_templates(text, env)
        except Exception as e:
            out[text] = {"ok": False, "error": str(e)}
            continue
        out[text] = {"ok": True, "value": json_safe(value)}
    return out


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
