from __future__ import annotations

import ast
import re
from typing import Any, Callable


_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")
_MAX_TEMPLATE_DEPTH = 16


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


def _eval_expr(expr: str, env: dict[str, Any]) -> Any:
    tree = ast.parse(expr, mode="eval")

    def eval_node(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise ValueError(f"Unknown name {node.id!r}")
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
            if isinstance(node.op, ast.UAdd):
                return +val
            if isinstance(node.op, ast.USub):
                return -val
            if isinstance(node.op, ast.Not):
                return not val
            raise ValueError("Unary op not allowed")
        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
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
            return func_map[func_name](*args)
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
            return render_templates(a, env) > render_templates(b, env)
        if "ge" in cond:
            a, b = cond["ge"]
            return render_templates(a, env) >= render_templates(b, env)
        if "lt" in cond:
            a, b = cond["lt"]
            return render_templates(a, env) < render_templates(b, env)
        if "le" in cond:
            a, b = cond["le"]
            return render_templates(a, env) <= render_templates(b, env)
        if "and" in cond:
            return all(eval_condition(c, env) for c in cond["and"])
        if "or" in cond:
            return any(eval_condition(c, env) for c in cond["or"])
        if "not" in cond:
            return not eval_condition(cond["not"], env)
        if "abs_lt" in cond:
            a, b = cond["abs_lt"]
            return abs(render_templates(a, env)) < render_templates(b, env)
    return False
