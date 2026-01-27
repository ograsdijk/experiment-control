from __future__ import annotations

from typing import Any

from ..utils.config_parsing import ConfigError, optional_dict, optional_str, require_str
from ..types import StreamCall, StreamOut
from .common import calls_from_json, calls_to_json

Json = dict[str, Any]


def stream_calls_to_json(calls: list[StreamCall]) -> list[Json]:
    def _output_to_json(o: StreamOut) -> Json:
        return {
            "stream": o.stream,
            "dtype": o.dtype,
            "shape": list(o.shape),
            "units": o.units,
            "description": o.description,
            "ring_slots": o.ring_slots,
            "attrs": o.attrs,
        }

    return calls_to_json(calls, output_to_json=_output_to_json)


def stream_calls_from_json(raw: object) -> list[StreamCall]:
    def _parse_output(o: Json, path: list[str | int]) -> StreamOut:
        stream_name = require_str(o.get("stream"), path=[*path, "stream"])
        dtype = require_str(o.get("dtype"), path=[*path, "dtype"])

        shape_raw = o.get("shape")
        if not isinstance(shape_raw, list) or not shape_raw:
            raise ConfigError(
                path=f"{path[0]}[{path[1]}].outputs[{path[3]}].shape",
                message="must be a non-empty list",
            )
        shape = tuple(int(x) for x in shape_raw)

        units = optional_str(o.get("units", None), path=[*path, "units"])
        desc = optional_str(o.get("description", None), path=[*path, "description"])
        ring_slots = int(o.get("ring_slots", 1024))
        attrs = optional_dict(o.get("attrs", None), path=[*path, "attrs"])

        return StreamOut(
            stream=stream_name,
            dtype=dtype,
            shape=shape,
            units=units,
            description=desc,
            ring_slots=ring_slots,
            attrs=attrs,
        )

    return calls_from_json(
        raw,
        label="stream_calls",
        parse_output=_parse_output,
        call_factory=lambda method, kwargs, outputs: StreamCall(
            method=method, kwargs=kwargs, outputs=outputs
        ),
        outputs_required=True,
    )


def validate_stream_calls(raw: object) -> None:
    _ = stream_calls_from_json(raw)
