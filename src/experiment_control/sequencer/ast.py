from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.yaml_helpers import load_yaml_text

@dataclass(frozen=True)
class SequenceSpec:
    version: int
    meta: dict[str, Any]
    vars: dict[str, Any]
    steps: list["Step"]
    context_columns: dict[str, str] | None = None


@dataclass(frozen=True)
class CallStep:
    device: str
    action: str
    params: dict[str, Any]
    save_as: str | None = None
    extract: dict[str, Any] | None = None
    assign: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class SetStep:
    device: str
    name: str
    value: Any


@dataclass(frozen=True)
class SleepStep:
    seconds: Any


@dataclass(frozen=True)
class WaitUntilStep:
    raw: dict[str, Any]


@dataclass(frozen=True)
class ForStep:
    bind: dict[str, str]
    in_expr: Any
    body: list["Step"]


@dataclass(frozen=True)
class RepeatStep:
    times: Any
    body: list["Step"]


@dataclass(frozen=True)
class IfStep:
    condition: Any
    then_steps: list["Step"]
    else_steps: list["Step"] | None = None


@dataclass(frozen=True)
class WhileStep:
    condition: Any
    body: list["Step"]


@dataclass(frozen=True)
class AtomicStep:
    name: str | None
    body: list["Step"]


@dataclass(frozen=True)
class PauseStep:
    reason: str | None


@dataclass(frozen=True)
class ParallelStep:
    body: list["Step"]


@dataclass(frozen=True)
class AssignStep:
    values: dict[str, Any]


@dataclass(frozen=True)
class SetContextStep:
    streams: Any
    fields: dict[str, Any]


Step = (
    CallStep
    | SetStep
    | SleepStep
    | WaitUntilStep
    | ForStep
    | RepeatStep
    | IfStep
    | WhileStep
    | AtomicStep
    | PauseStep
    | ParallelStep
    | AssignStep
    | SetContextStep
)


def _require_dict(raw: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError(f"{name} must be a dict")
    return raw


def _require_list(raw: Any, *, name: str) -> list[Any]:
    if not isinstance(raw, list):
        raise TypeError(f"{name} must be a list")
    return raw


def _parse_steps(raw: Any) -> list[Step]:
    items = _require_list(raw, name="steps")
    return [_parse_step(item) for item in items]


def _parse_step(raw: Any) -> Step:
    obj = _require_dict(raw, name="step")
    if "call" in obj:
        call = _require_dict(obj["call"], name="call")
        device = str(call.get("device", ""))
        action = str(call.get("action", ""))
        if not device or not action:
            raise TypeError("call.device and call.action are required")
        params = call.get("params", {}) or {}
        if not isinstance(params, dict):
            raise TypeError("call.params must be a dict")
        save_as = obj.get("save_as")
        extract = obj.get("extract")
        assign = obj.get("assign")
        if extract is not None and not isinstance(extract, dict):
            raise TypeError("extract must be a dict")
        if assign is not None and not isinstance(assign, dict):
            raise TypeError("assign must be a dict")
        return CallStep(
            device=device,
            action=action,
            params=params,
            save_as=str(save_as) if save_as is not None else None,
            extract=extract,
            assign=assign,
        )
    if "set" in obj:
        val = _require_dict(obj["set"], name="set")
        device = str(val.get("device", ""))
        name = str(val.get("name", ""))
        if not device or not name:
            raise TypeError("set.device and set.name are required")
        return SetStep(device=device, name=name, value=val.get("value"))
    if "sleep" in obj:
        secs = obj["sleep"]
        return SleepStep(seconds=secs)
    if "wait_until" in obj:
        w = _require_dict(obj["wait_until"], name="wait_until")
        return WaitUntilStep(raw=w)
    if "for" in obj:
        f = _require_dict(obj["for"], name="for")
        bind_raw = f.get("bind")
        bind: dict[str, str] = {}
        if isinstance(bind_raw, str):
            name = str(bind_raw).strip()
            if not name:
                raise TypeError("for.bind must not be empty")
            bind["value"] = name
        elif isinstance(bind_raw, dict):
            for raw_key, raw_value in bind_raw.items():
                key = str(raw_key).strip()
                value = str(raw_value).strip()
                if not key or not value:
                    raise TypeError("for.bind entries must map non-empty names")
                bind[key] = value
        else:
            raise TypeError("for.bind is required")
        if not bind:
            raise TypeError("for.bind must not be empty")
        in_expr = f.get("in")
        body = _parse_steps(f.get("do", []))
        return ForStep(bind=bind, in_expr=in_expr, body=body)
    if "repeat" in obj:
        rep = _require_dict(obj["repeat"], name="repeat")
        times = rep.get("times", 1)
        body = _parse_steps(rep.get("do", []))
        return RepeatStep(times=times, body=body)
    if "if" in obj:
        cond = _require_dict(obj["if"], name="if")
        condition = cond.get("condition")
        then_steps = _parse_steps(cond.get("then", []))
        else_steps_raw = cond.get("else")
        else_steps = _parse_steps(else_steps_raw) if else_steps_raw is not None else None
        return IfStep(condition=condition, then_steps=then_steps, else_steps=else_steps)
    if "while" in obj:
        w = _require_dict(obj["while"], name="while")
        if "condition" not in w:
            raise TypeError("while.condition is required")
        condition = w.get("condition")
        body = _parse_steps(w.get("do", []))
        return WhileStep(condition=condition, body=body)
    if "atomic" in obj:
        atom = _require_dict(obj["atomic"], name="atomic")
        name = atom.get("name")
        body = _parse_steps(atom.get("do", []))
        return AtomicStep(name=str(name) if name is not None else None, body=body)
    if "pause" in obj:
        pause = obj["pause"]
        if isinstance(pause, dict):
            reason = pause.get("reason")
        else:
            reason = pause
        return PauseStep(reason=str(reason) if reason is not None else None)
    if "parallel" in obj:
        par = _require_dict(obj["parallel"], name="parallel")
        body = _parse_steps(par.get("do", []))
        return ParallelStep(body=body)
    if "assign" in obj:
        values = obj.get("assign")
        if not isinstance(values, dict):
            raise TypeError("assign must be a dict")
        return AssignStep(values=values)
    if "set_context" in obj:
        sc = _require_dict(obj["set_context"], name="set_context")
        fields = sc.get("fields", {}) or {}
        if not isinstance(fields, dict):
            raise TypeError("set_context.fields must be a dict")
        return SetContextStep(streams=sc.get("streams", []), fields=fields)

    raise TypeError(f"Unknown step type: {list(obj.keys())}")


def parse_sequence(raw: Any) -> SequenceSpec:
    obj = _require_dict(raw, name="sequence")
    version = int(obj.get("version", 1))
    meta = obj.get("meta", {}) or {}
    vars_raw = obj.get("vars", {}) or {}
    context_columns_raw = obj.get("context_columns")
    if context_columns_raw is None and isinstance(meta, dict):
        context_columns_raw = meta.get("context_columns")
    steps = _parse_steps(obj.get("steps", []))
    if not isinstance(meta, dict):
        raise TypeError("meta must be a dict")
    if not isinstance(vars_raw, dict):
        raise TypeError("vars must be a dict")
    context_columns = None
    if context_columns_raw is not None:
        if not isinstance(context_columns_raw, dict):
            raise TypeError("context_columns must be a dict")
        context_columns = {}
        for key, value in context_columns_raw.items():
            name = str(key)
            dtype = str(value).lower()
            if dtype not in {"float64", "int64", "bool"}:
                raise TypeError(
                    f"context_columns[{name!r}] has unsupported dtype {dtype!r}"
                )
            context_columns[name] = dtype
    return SequenceSpec(
        version=version,
        meta=meta,
        vars=vars_raw,
        steps=steps,
        context_columns=context_columns,
    )


def load_sequence_yaml(text: str) -> SequenceSpec:
    raw = load_yaml_text(text, source="sequence_yaml")
    return parse_sequence(raw)
