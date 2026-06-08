from __future__ import annotations

import argparse
import re
from typing import Any

import yaml

from ..sequencer.ast import (
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
    load_sequence_yaml,
)
from ..sequencer.eval import render_templates, to_attrdict


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control.cli.sequence_plan")
    p.add_argument("path", help="Path to a sequence YAML file.")
    p.add_argument(
        "--format",
        choices=("text", "mermaid", "both"),
        default="text",
        help="Output format.",
    )
    p.add_argument(
        "--resolve",
        action="store_true",
        help="Attempt to resolve ${...} templates using vars.",
    )
    return p.parse_args(argv)


def _format_for_bind(bind: dict[str, str]) -> str:
    """Render a ForStep.bind dict as a human-readable loop-variable list.

    The single-name form (`bind = {"value": "x"}`) renders as `x`; the
    multi-name form renders as `key=name, key=name` so the rendered text
    distinguishes the field name from the bound alias.
    """
    if not bind:
        return "?"
    if len(bind) == 1 and "value" in bind:
        return str(bind["value"])
    return ", ".join(f"{key}={name}" for key, name in bind.items())


def _render_templates_safe(value: Any, env: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return render_templates(value, env)
        except Exception:
            return value
    if isinstance(value, list):
        return [_render_templates_safe(v, env) for v in value]
    if isinstance(value, dict):
        return {k: _render_templates_safe(v, env) for k, v in value.items()}
    return value


_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")


def _strip_templates(text: str) -> str:
    return _TEMPLATE_RE.sub(r"\1", text)


def _format_value(
    value: Any, *, resolve: bool, env: dict[str, Any], strip_templates: bool = False
) -> str:
    if resolve:
        value = _render_templates_safe(value, env)

    if isinstance(value, str):
        return _strip_templates(value) if strip_templates else value
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        text = yaml.safe_dump(
            value, default_flow_style=True, sort_keys=False, width=200
        )
        return _strip_templates(text.strip()) if strip_templates else text.strip()
    return str(value)


def _expr_to_str(value: Any, *, resolve: bool, env: dict[str, Any]) -> str:
    if resolve:
        value = _render_templates_safe(value, env)
    if isinstance(value, str):
        return _strip_templates(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(_expr_to_str(v, resolve=resolve, env=env) for v in value) + "]"
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            items.append(f"{k}: {_expr_to_str(v, resolve=resolve, env=env)}")
        return "{" + ", ".join(items) + "}"
    return str(value)


def _format_condition(cond: Any, *, resolve: bool, env: dict[str, Any]) -> str:
    if isinstance(cond, dict):
        if "always" in cond:
            val = cond.get("always")
            return f"always == {_expr_to_str(val, resolve=resolve, env=env)}"
        if "eq" in cond:
            a, b = cond["eq"]
            return f"{_expr_to_str(a, resolve=resolve, env=env)} == {_expr_to_str(b, resolve=resolve, env=env)}"
        if "ne" in cond:
            a, b = cond["ne"]
            return f"{_expr_to_str(a, resolve=resolve, env=env)} != {_expr_to_str(b, resolve=resolve, env=env)}"
        if "gt" in cond:
            a, b = cond["gt"]
            return f"{_expr_to_str(a, resolve=resolve, env=env)} > {_expr_to_str(b, resolve=resolve, env=env)}"
        if "ge" in cond:
            a, b = cond["ge"]
            return f"{_expr_to_str(a, resolve=resolve, env=env)} >= {_expr_to_str(b, resolve=resolve, env=env)}"
        if "lt" in cond:
            a, b = cond["lt"]
            return f"{_expr_to_str(a, resolve=resolve, env=env)} < {_expr_to_str(b, resolve=resolve, env=env)}"
        if "le" in cond:
            a, b = cond["le"]
            return f"{_expr_to_str(a, resolve=resolve, env=env)} <= {_expr_to_str(b, resolve=resolve, env=env)}"
        if "abs_lt" in cond:
            a, b = cond["abs_lt"]
            return f"abs({_expr_to_str(a, resolve=resolve, env=env)}) < {_expr_to_str(b, resolve=resolve, env=env)}"
        if "and" in cond:
            parts = [_format_condition(c, resolve=resolve, env=env) for c in cond["and"]]
            return " and ".join(f"({p})" for p in parts)
        if "or" in cond:
            parts = [_format_condition(c, resolve=resolve, env=env) for c in cond["or"]]
            return " or ".join(f"({p})" for p in parts)
        if "not" in cond:
            inner = _format_condition(cond["not"], resolve=resolve, env=env)
            return f"not ({inner})"
    return _expr_to_str(cond, resolve=resolve, env=env)


def _format_params_inline(
    params: dict[str, Any], *, resolve: bool, env: dict[str, Any]
) -> str:
    parts = []
    for key, value in params.items():
        parts.append(f"{key}={_expr_to_str(value, resolve=resolve, env=env)}")
    return ", ".join(parts)


def _format_call_signature(
    device: str,
    action: str,
    params: dict[str, Any],
    *,
    resolve: bool,
    env: dict[str, Any],
) -> str:
    if params:
        params_str = _format_params_inline(params, resolve=resolve, env=env)
        return f"{device}.{action}({params_str})"
    return f"{device}.{action}()"


def _format_wait_until(
    raw: dict[str, Any], *, resolve: bool, env: dict[str, Any]
) -> list[str]:
    lines = ["wait until"]
    sample = raw.get("sample")
    if sample is not None:
        if isinstance(sample, dict) and "telemetry" in sample:
            telem = sample.get("telemetry", {})
            if isinstance(telem, dict):
                device = telem.get("device", "")
                signal = telem.get("signal", "")
                if device and signal:
                    lines.append(f"sample = telemetry {device}.{signal}")
                else:
                    lines.append(
                        f"sample = telemetry {_expr_to_str(telem, resolve=resolve, env=env)}"
                    )
            else:
                lines.append(
                    f"sample = telemetry {_expr_to_str(telem, resolve=resolve, env=env)}"
                )
        elif isinstance(sample, dict) and "call" in sample:
            call_spec = sample.get("call", {})
            if isinstance(call_spec, dict):
                device = str(call_spec.get("device", ""))
                action = str(call_spec.get("action", ""))
                params = call_spec.get("params", {}) or {}
                if not isinstance(params, dict):
                    params = {}
                lines.append(
                    f"sample = call {_format_call_signature(device, action, params, resolve=resolve, env=env)}"
                )
            else:
                lines.append(
                    f"sample = call {_expr_to_str(call_spec, resolve=resolve, env=env)}"
                )
        else:
            lines.append(
                f"sample = {_expr_to_str(sample, resolve=resolve, env=env)}"
            )

    reduce_spec = raw.get("reduce")
    if isinstance(reduce_spec, dict):
        method = reduce_spec.get("method", "mean")
        window_s = reduce_spec.get("window_s", 0)
        if window_s:
            lines.append(f"reduce = {method}(window={window_s}s)")
        else:
            lines.append(f"reduce = {method}()")

    condition = raw.get("condition")
    if condition is not None:
        lines.append(
            f"until {_format_condition(condition, resolve=resolve, env=env)}"
        )

    every_s = raw.get("every_s", 0)
    stable_for_s = raw.get("stable_for_s", 0)
    timeout_s = raw.get("timeout_s", 0)
    timing_parts: list[str] = []
    if every_s:
        timing_parts.append(f"every {every_s}s")
    if stable_for_s:
        timing_parts.append(f"stable for {stable_for_s}s")
    if timeout_s:
        timing_parts.append(f"timeout {timeout_s}s")
    if timing_parts:
        lines.append(" | ".join(timing_parts))

    return lines


def _text_lines_for_steps(
    steps: list[Step], *, resolve: bool, env: dict[str, Any], indent: int
) -> list[str]:
    lines: list[str] = []
    if not steps:
        lines.append(("  " * indent) + "- (empty)")
        return lines
    for step in steps:
        lines.extend(_text_lines_for_step(step, resolve=resolve, env=env, indent=indent))
    return lines


def _text_lines_for_step(
    step: Step, *, resolve: bool, env: dict[str, Any], indent: int
) -> list[str]:
    prefix = ("  " * indent) + "- "
    lines: list[str] = []

    if isinstance(step, CallStep):
        params = _format_value(step.params, resolve=resolve, env=env)
        line = f"{prefix}call: {step.device}.{step.action} params={params}"
        if step.save_as is not None:
            line += f" save_as={step.save_as}"
        if step.extract is not None:
            extract = _format_value(step.extract, resolve=resolve, env=env)
            line += f" extract={extract}"
        if step.assign is not None:
            assign = _format_value(step.assign, resolve=resolve, env=env)
            line += f" assign={assign}"
        lines.append(line)
        return lines

    if isinstance(step, SetStep):
        value = _format_value(step.value, resolve=resolve, env=env)
        lines.append(f"{prefix}set: {step.device}.{step.name} = {value}")
        return lines

    if isinstance(step, SleepStep):
        lines.append(f"{prefix}sleep: {step.seconds}s")
        return lines

    if isinstance(step, WaitUntilStep):
        raw = _format_value(step.raw, resolve=resolve, env=env)
        lines.append(f"{prefix}wait_until: {raw}")
        return lines

    if isinstance(step, ForStep):
        expr = _format_value(step.in_expr, resolve=resolve, env=env)
        lines.append(f"{prefix}for {_format_for_bind(step.bind)} in {expr}:")
        lines.extend(
            _text_lines_for_steps(step.body, resolve=resolve, env=env, indent=indent + 1)
        )
        return lines

    if isinstance(step, RepeatStep):
        times = _format_value(step.times, resolve=resolve, env=env)
        lines.append(f"{prefix}repeat {times} times:")
        lines.extend(
            _text_lines_for_steps(step.body, resolve=resolve, env=env, indent=indent + 1)
        )
        return lines

    if isinstance(step, IfStep):
        cond = _format_value(step.condition, resolve=resolve, env=env)
        lines.append(f"{prefix}if {cond}:")
        lines.append(("  " * (indent + 1)) + "- then:")
        lines.extend(
            _text_lines_for_steps(
                step.then_steps, resolve=resolve, env=env, indent=indent + 2
            )
        )
        if step.else_steps is not None:
            lines.append(("  " * (indent + 1)) + "- else:")
            lines.extend(
                _text_lines_for_steps(
                    step.else_steps, resolve=resolve, env=env, indent=indent + 2
                )
            )
        return lines

    if isinstance(step, WhileStep):
        cond = _format_value(step.condition, resolve=resolve, env=env)
        lines.append(f"{prefix}while {cond}:")
        lines.extend(
            _text_lines_for_steps(step.body, resolve=resolve, env=env, indent=indent + 1)
        )
        return lines

    if isinstance(step, AtomicStep):
        name = f" {step.name}" if step.name else ""
        lines.append(f"{prefix}atomic{name}:")
        lines.extend(
            _text_lines_for_steps(step.body, resolve=resolve, env=env, indent=indent + 1)
        )
        return lines

    if isinstance(step, ParallelStep):
        lines.append(f"{prefix}parallel:")
        lines.extend(
            _text_lines_for_steps(step.body, resolve=resolve, env=env, indent=indent + 1)
        )
        return lines

    if isinstance(step, PauseStep):
        if step.reason:
            lines.append(f"{prefix}pause: {step.reason}")
        else:
            lines.append(f"{prefix}pause")
        return lines

    if isinstance(step, AssignStep):
        values = _format_value(step.values, resolve=resolve, env=env)
        lines.append(f"{prefix}assign: {values}")
        return lines

    if isinstance(step, SetContextStep):
        streams = _format_value(step.streams, resolve=resolve, env=env)
        fields = _format_value(step.fields, resolve=resolve, env=env)
        lines.append(f"{prefix}set_context: streams={streams} fields={fields}")
        return lines

    lines.append(f"{prefix}{type(step).__name__}")
    return lines


class _MermaidBuilder:
    def __init__(self, *, resolve: bool, env: dict[str, Any]) -> None:
        self._resolve = resolve
        self._env = env
        self._next_node_id = 0
        self._nodes: dict[str, tuple[str, str]] = {}
        self._edges: list[tuple[str, str, str | None]] = []

    def _new_node(self, label: str, *, shape: str = "rect") -> str:
        node_id = f"n{self._next_node_id}"
        self._next_node_id += 1
        self._nodes[node_id] = (label, shape)
        return node_id
    def _add_edge(self, src: str, dst: str, label: str | None = None) -> None:
        self._edges.append((src, dst, label))

    def _format_label(self, value: Any) -> str:
        return _format_value(
            value, resolve=self._resolve, env=self._env, strip_templates=True
        )

    @staticmethod
    def _join_lines(*lines: str) -> str:
        return "<br/>".join(line for line in lines if line)

    def build_steps(self, steps: list[Step]) -> tuple[str, str]:
        entry = None
        prev_exit = None
        for step in steps:
            s_entry, s_exit = self.build_step(step)
            if entry is None:
                entry = s_entry
            if prev_exit is not None:
                self._add_edge(prev_exit, s_entry)
            prev_exit = s_exit
        if entry is None:
            empty = self._new_node("(empty)")
            return empty, empty
        return entry, prev_exit or entry

    def build_step(self, step: Step) -> tuple[str, str]:
        if isinstance(step, CallStep):
            parts = ["call", f"{step.device}.{step.action}"]
            if step.params:
                params_inline = _format_params_inline(
                    step.params, resolve=self._resolve, env=self._env
                )
                parts.append(f"params: {params_inline}")
            if step.save_as is not None:
                parts.append(f"save_as: {step.save_as}")
            if step.extract is not None:
                parts.append(
                    f"extract: {_expr_to_str(step.extract, resolve=self._resolve, env=self._env)}"
                )
            if step.assign is not None:
                parts.append(
                    f"assign: {_expr_to_str(step.assign, resolve=self._resolve, env=self._env)}"
                )
            label = self._join_lines(*parts)
            node = self._new_node(label, shape="subroutine")
            return node, node

        if isinstance(step, SetStep):
            label = self._join_lines(
                "set",
                f"{step.device}.{step.name}",
                f"value: {_expr_to_str(step.value, resolve=self._resolve, env=self._env)}",
            )
            node = self._new_node(label, shape="subroutine")
            return node, node

        if isinstance(step, SleepStep):
            node = self._new_node(
                self._join_lines("sleep", f"{step.seconds}s"), shape="round"
            )
            return node, node

        if isinstance(step, WaitUntilStep):
            lines = _format_wait_until(step.raw, resolve=self._resolve, env=self._env)
            node = self._new_node(self._join_lines(*lines), shape="round")
            return node, node

        if isinstance(step, ForStep):
            expr = _expr_to_str(step.in_expr, resolve=self._resolve, env=self._env)
            loop = self._new_node(
                f"for {_format_for_bind(step.bind)} in {expr}", shape="circle"
            )
            body_entry, body_exit = self.build_steps(step.body)
            join = self._new_node("end for", shape="circle")
            self._add_edge(loop, body_entry, "iter")
            self._add_edge(body_exit, loop, "next")
            self._add_edge(loop, join, "done")
            return loop, join

        if isinstance(step, RepeatStep):
            times = _expr_to_str(step.times, resolve=self._resolve, env=self._env)
            loop = self._new_node(f"repeat {times} times", shape="circle")
            body_entry, body_exit = self.build_steps(step.body)
            join = self._new_node("end repeat", shape="circle")
            self._add_edge(loop, body_entry, "iter")
            self._add_edge(body_exit, loop, "next")
            self._add_edge(loop, join, "done")
            return loop, join

        if isinstance(step, IfStep):
            cond = _format_condition(step.condition, resolve=self._resolve, env=self._env)
            gate = self._new_node(self._join_lines("if", cond), shape="diamond")
            join = self._new_node("end", shape="rect")
            then_entry, then_exit = self.build_steps(step.then_steps)
            self._add_edge(gate, then_entry, "true")
            self._add_edge(then_exit, join)
            if step.else_steps is not None:
                else_entry, else_exit = self.build_steps(step.else_steps)
                self._add_edge(gate, else_entry, "false")
                self._add_edge(else_exit, join)
            else:
                self._add_edge(gate, join, "false")
            return gate, join

        if isinstance(step, WhileStep):
            cond = _format_condition(step.condition, resolve=self._resolve, env=self._env)
            loop = self._new_node(f"while {cond}", shape="circle")
            body_entry, body_exit = self.build_steps(step.body)
            join = self._new_node("end while", shape="circle")
            self._add_edge(loop, body_entry, "true")
            self._add_edge(body_exit, loop, "next")
            self._add_edge(loop, join, "false")
            return loop, join

        if isinstance(step, AtomicStep):
            name = f" {step.name}" if step.name else ""
            start = self._new_node(f"atomic{name}", shape="rect")
            join = self._new_node("end", shape="rect")
            body_entry, body_exit = self.build_steps(step.body)
            self._add_edge(start, body_entry)
            self._add_edge(body_exit, join)
            return start, join

        if isinstance(step, ParallelStep):
            start = self._new_node("parallel", shape="rect")
            join = self._new_node("end", shape="rect")
            body_entry, body_exit = self.build_steps(step.body)
            self._add_edge(start, body_entry)
            self._add_edge(body_exit, join)
            return start, join

        if isinstance(step, PauseStep):
            label = f"pause {step.reason}" if step.reason else "pause"
            node = self._new_node(label, shape="round")
            return node, node

        if isinstance(step, AssignStep):
            values = _expr_to_str(step.values, resolve=self._resolve, env=self._env)
            node = self._new_node(self._join_lines("assign", values), shape="rect")
            return node, node

        if isinstance(step, SetContextStep):
            streams = self._format_label(step.streams)
            fields = self._format_label(step.fields)
            label = self._join_lines(
                "set_context",
                f"streams: {streams}",
                f"fields: {fields}",
            )
            node = self._new_node(label, shape="subroutine")
            return node, node

        node = self._new_node(type(step).__name__, shape="rect")
        return node, node

    def render(self) -> str:
        lines = ["flowchart TD"]
        for node_id, (label, shape) in self._nodes.items():
            lines.append(f"  {_format_node(node_id, label, shape=shape)}")
        for src, dst, label in self._edges:
            if label:
                lines.append(
                    f'  {src} -->|{_escape_mermaid(label)}| {dst}'
                )
            else:
                lines.append(f"  {src} --> {dst}")
        return "\n".join(lines)


def _escape_mermaid(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', '\\"')


def _format_node(node_id: str, label: str, *, shape: str) -> str:
    text = _escape_mermaid(label)
    if shape == "diamond":
        return f'{node_id}{{"{text}"}}'
    if shape == "round":
        return f'{node_id}("{text}")'
    if shape == "circle":
        return f'{node_id}(("{text}"))'
    if shape == "subroutine":
        return f'{node_id}[["{text}"]]'
    return f'{node_id}["{text}"]'


def _render_text(spec: SequenceSpec, *, resolve: bool) -> str:
    env = dict(spec.vars)
    env["vars"] = to_attrdict(spec.vars)
    lines = [f"sequence (version {spec.version})"]
    if spec.vars:
        lines.append(f"vars: {_format_value(spec.vars, resolve=resolve, env=env)}")
    if spec.context_columns:
        lines.append(
            f"context_columns: {_format_value(spec.context_columns, resolve=resolve, env=env)}"
        )
    lines.append("steps:")
    lines.extend(
        _text_lines_for_steps(spec.steps, resolve=resolve, env=env, indent=1)
    )
    return "\n".join(lines)


def _render_mermaid(spec: SequenceSpec, *, resolve: bool) -> str:
    env = dict(spec.vars)
    env["vars"] = to_attrdict(spec.vars)
    builder = _MermaidBuilder(resolve=resolve, env=env)
    builder.build_steps(spec.steps)
    return builder.render()


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    with open(ns.path, "r", encoding="utf-8") as f:
        spec = load_sequence_yaml(f.read())

    if ns.format in ("text", "both"):
        print(_render_text(spec, resolve=ns.resolve))
    if ns.format == "both":
        print()
    if ns.format in ("mermaid", "both"):
        print(_render_mermaid(spec, resolve=ns.resolve))


if __name__ == "__main__":
    main()
