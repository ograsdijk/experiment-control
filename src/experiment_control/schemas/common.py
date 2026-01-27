from __future__ import annotations

from typing import Any, Callable, TypeVar

from ..utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    require_dict,
    require_list_of_dicts,
    require_str,
)

Json = dict[str, Any]

_TCall = TypeVar("_TCall")
_TOut = TypeVar("_TOut")


def calls_to_json(
    calls: list[Any],
    *,
    output_to_json: Callable[[_TOut], Json],
) -> list[Json]:
    return [
        {
            "method": c.method,
            "kwargs": c.kwargs or {},
            "outputs": [output_to_json(o) for o in (c.outputs or [])],
        }
        for c in calls
    ]


def calls_from_json(
    raw: object,
    *,
    label: str,
    parse_output: Callable[[Json, list[str | int]], _TOut],
    call_factory: Callable[[str, dict[str, Any], list[_TOut] | None], _TCall],
    outputs_required: bool,
) -> list[_TCall]:
    calls_raw = normalize_list(raw, path=[label])

    calls: list[_TCall] = []
    try:
        for i, c in enumerate(calls_raw):
            c_obj = require_dict(c, path=[label, i])

            method = require_str(c_obj.get("method"), path=[label, i, "method"])
            kwargs = optional_dict(c_obj.get("kwargs"), path=[label, i, "kwargs"])

            outs_raw = c_obj.get("outputs", None)
            outputs: list[_TOut] | None
            if outs_raw is None:
                outputs = None
            else:
                outs = require_list_of_dicts(outs_raw, path=[label, i, "outputs"])
                if outputs_required and not outs:
                    raise ConfigError(
                        path=f"{label}[{i}].outputs",
                        message="must be a non-empty list",
                    )
                outputs = [
                    parse_output(o, [label, i, "outputs", j])
                    for j, o in enumerate(outs)
                ]

            calls.append(call_factory(method, kwargs, outputs))
    except ConfigError as e:
        raise TypeError(str(e)) from None

    return calls
