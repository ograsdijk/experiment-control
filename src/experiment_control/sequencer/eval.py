from __future__ import annotations

import ast
import difflib
import re
from typing import Any, Callable


_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")
_MAX_TEMPLATE_DEPTH = 16


class TemplateError(ValueError):
    """A template/expression that cannot be evaluated (unknown name, syntax...)."""


class OperandError(TemplateError):
    """An operator was applied to operands it does not support.

    `none_involved` is True when one of the operands was None, i.e. the
    expression most likely referenced a value that is not available (yet),
    such as a telemetry sample that has not arrived.
    """

    def __init__(self, message: str, *, none_involved: bool = False) -> None:
        super().__init__(message)
        self.none_involved = none_involved


def suggest_names(name: str, candidates: Any) -> list[str]:
    """Close matches of `name` among `candidates` (private names skipped)."""
    pool = [str(c) for c in candidates if not str(c).startswith("_")]
    return difflib.get_close_matches(name, pool, n=3, cutoff=0.6)


def unknown_name_message(name: str, candidates: Any) -> str:
    close = suggest_names(name, candidates)
    if not close:
        return f"Unknown name {name!r}"
    quoted = ", ".join(repr(c) for c in close)
    return f"Unknown name {name!r} (did you mean {quoted}?)"


def unknown_key_message(owner: str, key: Any, allowed: Any, hint: str | None = None) -> str:
    """Message for a key that `owner` does not accept, with a close-match hint."""
    names = sorted(str(a) for a in allowed)
    text = f"{owner} has unknown key {str(key)!r}"
    close = difflib.get_close_matches(str(key), names, n=1, cutoff=0.6)
    if close:
        text += f" (did you mean {close[0]!r}?)"
    if hint:
        text += f"; {hint}"
    text += f". Valid keys: {', '.join(names)}"
    return text


def _describe_operand(node: ast.AST, value: Any) -> str | None:
    if isinstance(node, ast.Name) and isinstance(value, str):
        shown = value if len(value) <= 40 else value[:37] + "..."
        return f"{node.id} is the string {shown!r}"
    if isinstance(node, ast.Name) and value is None:
        return f"{node.id} is None"
    return None


class AttrDict(dict):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name in self:
            return self[name]
        raise AttributeError(name)


def to_attrdict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return AttrDict({k: to_attrdict(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [to_attrdict(v) for v in obj]
    return obj


def _operand_error(
    expr: str,
    operands: list[tuple[ast.AST, Any]],
    exc: Exception,
) -> OperandError:
    if isinstance(exc, ZeroDivisionError):
        return OperandError(f"{exc} in ${{{expr}}}")
    notes = [d for d in (_describe_operand(node, val) for node, val in operands) if d]
    detail = f": {'; '.join(notes)}" if notes else f" ({exc})"
    return OperandError(
        f"unsupported operand types in ${{{expr}}}{detail}",
        none_involved=any(val is None for _, val in operands),
    )


def _eval_expr(expr: str, env: dict[str, Any]) -> Any:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise TemplateError(f"Invalid expression ${{{expr}}}: {exc.msg}") from exc

    def eval_node(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise TemplateError(unknown_name_message(node.id, env))
        if isinstance(node, ast.Attribute):
            base = eval_node(node.value)
            if isinstance(base, AttrDict):
                if node.attr.startswith("_"):
                    raise ValueError("Private attribute access not allowed")
                if node.attr in base:
                    return base[node.attr]
            raise ValueError("Attribute access not allowed")
        if isinstance(node, ast.UnaryOp):
            val = eval_node(node.operand)
            try:
                if isinstance(node.op, ast.UAdd):
                    return +val
                if isinstance(node.op, ast.USub):
                    return -val
            except TypeError as exc:
                raise _operand_error(expr, [(node.operand, val)], exc) from exc
            if isinstance(node.op, ast.Not):
                return not val
            raise ValueError("Unary op not allowed")
        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            try:
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
                if isinstance(node.op, ast.FloorDiv):
                    return left // right
                if isinstance(node.op, ast.Mod):
                    return left % right
                if isinstance(node.op, ast.Pow):
                    return left**right
            except (TypeError, ZeroDivisionError, OverflowError) as exc:
                raise _operand_error(
                    expr, [(node.left, left), (node.right, right)], exc
                ) from exc
            raise ValueError("Binary op not allowed")
        if isinstance(node, ast.BoolOp):
            values = [eval_node(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
            raise ValueError("Bool op not allowed")
        if isinstance(node, ast.Compare):
            left = eval_node(node.left)
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                right = eval_node(comparator)
                ok = None
                try:
                    if isinstance(op, ast.Eq):
                        ok = left == right
                    elif isinstance(op, ast.NotEq):
                        ok = left != right
                    elif isinstance(op, ast.Lt):
                        ok = left < right
                    elif isinstance(op, ast.LtE):
                        ok = left <= right
                    elif isinstance(op, ast.Gt):
                        ok = left > right
                    elif isinstance(op, ast.GtE):
                        ok = left >= right
                    else:
                        raise ValueError("Compare op not allowed")
                except TypeError as exc:
                    raise _operand_error(
                        expr, [(node.left, left), (comparator, right)], exc
                    ) from exc
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Only simple calls allowed")
            if node.keywords:
                raise ValueError("Keyword args not allowed")
            func_name = node.func.id
            func_map: dict[str, Callable[..., Any]] = {
                "abs": abs,
                "len": len,
                "min": min,
                "max": max,
            }
            if func_name not in func_map:
                raise ValueError(f"Function {func_name!r} not allowed")
            args = [eval_node(arg) for arg in node.args]
            try:
                return func_map[func_name](*args)
            except TypeError as exc:
                raise _operand_error(
                    expr, list(zip(node.args, args, strict=True)), exc
                ) from exc
        if isinstance(node, ast.IfExp):
            return eval_node(node.body) if eval_node(node.test) else eval_node(node.orelse)
        raise ValueError("Unsupported expression")

    return eval_node(tree)


def _render_templates(value: Any, env: dict[str, Any], *, depth: int) -> Any:
    if depth > _MAX_TEMPLATE_DEPTH:
        return value
    if isinstance(value, str):
        matches = list(_TEMPLATE_RE.finditer(value))
        if not matches:
            return value
        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            expr = matches[0].group(1)
            out = _eval_expr(expr, env)
            if (
                isinstance(out, str)
                and out != value
                and _TEMPLATE_RE.search(out) is not None
            ):
                return _render_templates(out, env, depth=depth + 1)
            return out
        out = value
        for match in matches:
            expr = match.group(1)
            replacement = _eval_expr(expr, env)
            if isinstance(replacement, str) and _TEMPLATE_RE.search(replacement):
                replacement = _render_templates(replacement, env, depth=depth + 1)
            out = out.replace(match.group(0), str(replacement))
        if out != value and _TEMPLATE_RE.search(out) is not None:
            return _render_templates(out, env, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_render_templates(v, env, depth=depth) for v in value]
    if isinstance(value, dict):
        return {k: _render_templates(v, env, depth=depth) for k, v in value.items()}
    return value


def render_templates(value: Any, env: dict[str, Any]) -> Any:
    return _render_templates(value, env, depth=0)


def _compare(op: str, a: Any, b: Any) -> bool:
    try:
        if op == "gt":
            return bool(a > b)
        if op == "ge":
            return bool(a >= b)
        if op == "lt":
            return bool(a < b)
        return bool(a <= b)
    except TypeError as exc:
        symbol = {"gt": ">", "ge": ">=", "lt": "<", "le": "<="}[op]
        raise OperandError(
            f"cannot compare {a!r} {symbol} {b!r} ({exc})",
            none_involved=a is None or b is None,
        ) from exc


def eval_condition(cond: Any, env: dict[str, Any]) -> bool:
    if isinstance(cond, bool):
        return cond
    if isinstance(cond, (int, float, str)):
        return bool(render_templates(cond, env))
    if isinstance(cond, dict):
        if "always" in cond:
            if len(cond) != 1:
                raise ValueError("always must be the only condition operator")
            return bool(render_templates(cond["always"], env))
        if "eq" in cond:
            a, b = cond["eq"]
            return render_templates(a, env) == render_templates(b, env)
        if "ne" in cond:
            a, b = cond["ne"]
            return render_templates(a, env) != render_templates(b, env)
        if "gt" in cond:
            a, b = cond["gt"]
            return _compare("gt", render_templates(a, env), render_templates(b, env))
        if "ge" in cond:
            a, b = cond["ge"]
            return _compare("ge", render_templates(a, env), render_templates(b, env))
        if "lt" in cond:
            a, b = cond["lt"]
            return _compare("lt", render_templates(a, env), render_templates(b, env))
        if "le" in cond:
            a, b = cond["le"]
            return _compare("le", render_templates(a, env), render_templates(b, env))
        if "and" in cond:
            return all(eval_condition(c, env) for c in cond["and"])
        if "or" in cond:
            return any(eval_condition(c, env) for c in cond["or"])
        if "not" in cond:
            return not eval_condition(cond["not"], env)
        if "abs_lt" in cond:
            a, b = cond["abs_lt"]
            av = render_templates(a, env)
            try:
                av = abs(av)
            except TypeError as exc:
                raise OperandError(
                    f"cannot take abs() of {av!r} ({exc})", none_involved=av is None
                ) from exc
            return _compare("lt", av, render_templates(b, env))
    return False
