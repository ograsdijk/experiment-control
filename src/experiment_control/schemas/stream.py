from __future__ import annotations

from typing import Any

from ..types import StreamAxis, StreamCall, StreamField, StreamMeta, StreamOut
from ..utils.config_parsing import (
    ConfigError,
    fmt_path,
    normalize_list,
    optional_dict,
    optional_str,
    require_dict,
    require_list_of_dicts,
    require_str,
)
from .common import calls_to_json

Json = dict[str, Any]


def stream_axis_from_json(
    raw: Any, path: list[str | int] | None = None
) -> StreamAxis | None:
    """Parse an ``x_axis`` block. Shared by config parsing and the gateway."""
    path = list(path) if path is not None else ["x_axis"]
    if raw is None:
        return None
    axis_raw = require_dict(raw, path=path)
    allowed = {
        "units",
        "label",
        "increment",
        "increment_from",
        "rate_from",
        "origin",
        "origin_from",
    }
    unknown = sorted(set(axis_raw) - allowed)
    if unknown:
        raise ConfigError(
            path=fmt_path(path),
            message=f"unknown x_axis keys: {', '.join(unknown)}",
        )

    def _pointer(key: str) -> str | None:
        value = axis_raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                path=fmt_path([*path, key]),
                message="must be a non-empty run-metadata key name",
            )
        return value.strip()

    def _num(key: str) -> float | None:
        value = axis_raw.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(
                path=fmt_path([*path, key]), message="must be a number"
            )
        return float(value)

    try:
        return StreamAxis(
            units=optional_str(axis_raw.get("units"), path=[*path, "units"]),
            label=optional_str(axis_raw.get("label"), path=[*path, "label"]),
            increment=_num("increment"),
            increment_from=_pointer("increment_from"),
            rate_from=_pointer("rate_from"),
            origin=_num("origin"),
            origin_from=_pointer("origin_from"),
        )
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError(path=fmt_path(path), message=str(exc)) from exc


def stream_calls_to_json(calls: list[StreamCall]) -> list[Json]:
    def _output_to_json(o: StreamOut) -> Json:
        payload: Json = {
            "stream": o.stream,
            "kind": o.kind,
            "units": o.units,
            "description": o.description,
            "ring_slots": o.ring_slots,
            "attrs": o.attrs,
        }
        if o.x_axis is not None:
            axis_payload = {
                key: value
                for key, value in (
                    ("units", o.x_axis.units),
                    ("label", o.x_axis.label),
                    ("increment", o.x_axis.increment),
                    ("increment_from", o.x_axis.increment_from),
                    ("rate_from", o.x_axis.rate_from),
                    ("origin", o.x_axis.origin),
                    ("origin_from", o.x_axis.origin_from),
                )
                if value is not None
            }
            payload["x_axis"] = axis_payload
        if o.kind == "records":
            payload["fields"] = [
                {
                    "name": f.name,
                    "dtype": f.dtype,
                    "units": f.units,
                    "description": f.description,
                }
                for f in o.fields
            ]
        else:
            payload["dtype"] = o.dtype
            payload["shape"] = list(o.shape)
        return payload

    payload = calls_to_json(calls, output_to_json=_output_to_json)
    for item, call in zip(payload, calls, strict=True):
        if call.meta:
            item["meta"] = [
                {
                    "name": meta.name,
                    "dtype": meta.dtype,
                    "units": meta.units,
                    "description": meta.description,
                }
                for meta in call.meta
            ]
        if call.period_s is not None:
            item["period_s"] = float(call.period_s)
    return payload


def stream_calls_from_json(raw: object) -> list[StreamCall]:
    def _parse_field(f: Json, path: list[str | int]) -> StreamField:
        name = require_str(f.get("name"), path=[*path, "name"])
        dtype = require_str(f.get("dtype"), path=[*path, "dtype"])
        units = optional_str(f.get("units", None), path=[*path, "units"])
        desc = optional_str(f.get("description", None), path=[*path, "description"])
        return StreamField(name=name, dtype=dtype, units=units, description=desc)

    def _parse_meta(f: Json, path: list[str | int]) -> StreamMeta:
        name = require_str(f.get("name"), path=[*path, "name"])
        dtype = require_str(f.get("dtype"), path=[*path, "dtype"])
        units = optional_str(f.get("units", None), path=[*path, "units"])
        desc = optional_str(f.get("description", None), path=[*path, "description"])
        return StreamMeta(name=name, dtype=dtype, units=units, description=desc)

    def _parse_output(o: Json, path: list[str | int]) -> StreamOut:
        stream_name = require_str(o.get("stream"), path=[*path, "stream"])
        kind = str(o.get("kind", "frame")).strip() or "frame"

        units = optional_str(o.get("units", None), path=[*path, "units"])
        desc = optional_str(o.get("description", None), path=[*path, "description"])
        ring_slots = int(o.get("ring_slots", 1024))
        attrs = optional_dict(o.get("attrs", None), path=[*path, "attrs"])
        x_axis = stream_axis_from_json(o.get("x_axis", None), [*path, "x_axis"])
        if kind == "records":
            if x_axis is not None:
                raise ConfigError(
                    path=fmt_path([*path, "x_axis"]),
                    message="is only valid for frame streams",
                )
            fields_raw = require_list_of_dicts(
                o.get("fields"), path=[*path, "fields"]
            )
            if not fields_raw:
                raise ConfigError(
                    path=f"{path[0]}[{path[1]}].outputs[{path[3]}].fields",
                    message="must be a non-empty list",
                )
            fields = tuple(
                _parse_field(field, [*path, "fields", j])
                for j, field in enumerate(fields_raw)
            )
            return StreamOut(
                stream=stream_name,
                ring_slots=ring_slots,
                attrs=attrs,
                kind="records",
                fields=fields,
                units=units,
                description=desc,
            )

        if kind != "frame":
            raise ConfigError(
                path=f"{path[0]}[{path[1]}].outputs[{path[3]}].kind",
                message="must be 'frame' or 'records'",
            )
        dtype = require_str(o.get("dtype"), path=[*path, "dtype"])

        shape_raw = o.get("shape")
        if not isinstance(shape_raw, list) or not shape_raw:
            raise ConfigError(
                path=f"{path[0]}[{path[1]}].outputs[{path[3]}].shape",
                message="must be a non-empty list",
            )
        shape = tuple(int(x) for x in shape_raw)

        return StreamOut(
            stream=stream_name,
            dtype=dtype,
            shape=shape,
            units=units,
            description=desc,
            ring_slots=ring_slots,
            attrs=attrs,
            x_axis=x_axis,
        )

    calls_raw = normalize_list(raw, path=["stream_calls"])

    calls: list[StreamCall] = []
    try:
        for i, c in enumerate(calls_raw):
            c_obj = require_dict(c, path=["stream_calls", i])
            method = require_str(c_obj.get("method"), path=["stream_calls", i, "method"])
            kwargs = optional_dict(c_obj.get("kwargs"), path=["stream_calls", i, "kwargs"])

            outs = require_list_of_dicts(
                c_obj.get("outputs"), path=["stream_calls", i, "outputs"]
            )
            if not outs:
                raise ConfigError(
                    path=f"stream_calls[{i}].outputs",
                    message="must be a non-empty list",
                )
            outputs = [
                _parse_output(o, ["stream_calls", i, "outputs", j])
                for j, o in enumerate(outs)
            ]
            meta_raw = require_list_of_dicts(
                c_obj.get("meta", []), path=["stream_calls", i, "meta"]
            )
            meta = [
                _parse_meta(item, ["stream_calls", i, "meta", j])
                for j, item in enumerate(meta_raw)
            ]

            period_raw = c_obj.get("period_s", None)
            period_s: float | None = None
            if period_raw is not None:
                period_s = float(period_raw)
                if period_s <= 0:
                    raise ConfigError(
                        path=f"stream_calls[{i}].period_s",
                        message="must be > 0 when provided",
                    )

            calls.append(
                StreamCall(
                    method=method,
                    kwargs=kwargs,
                    outputs=outputs,
                    meta=meta,
                    period_s=period_s,
                )
            )
    except ConfigError as e:
        raise TypeError(str(e)) from None
    except (TypeError, ValueError) as e:
        raise TypeError(str(e)) from None

    return calls


def validate_stream_calls(raw: object) -> None:
    _ = stream_calls_from_json(raw)
