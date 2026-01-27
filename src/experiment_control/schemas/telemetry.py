from __future__ import annotations

from typing import Any, cast

from ..utils.config_parsing import optional_str, require_kind, require_str
from ..types import ExtractorKind, TelemetryCall, TelemetryOut
from .common import calls_from_json, calls_to_json

Json = dict[str, Any]


def telemetry_calls_to_json(calls: list[TelemetryCall]) -> list[Json]:
    def _output_to_json(o: TelemetryOut) -> Json:
        return {
            "signal": o.signal,
            "kind": o.kind,
            "ref": o.ref,
            "units": o.units,
            "dtype": o.dtype,
        }

    return calls_to_json(calls, output_to_json=_output_to_json)


def telemetry_calls_from_json(raw: object) -> list[TelemetryCall]:
    def _parse_output(o: Json, path: list[str | int]) -> TelemetryOut:
        signal = require_str(o.get("signal"), path=[*path, "signal"])
        kind = require_kind(o.get("kind"), path=[*path, "kind"])
        ref = o.get("ref", None)
        units = optional_str(o.get("units", None), path=[*path, "units"])
        dtype = optional_str(o.get("dtype", "float64"), path=[*path, "dtype"])

        return TelemetryOut(
            signal=signal,
            kind=cast(ExtractorKind, kind),
            ref=ref,
            units=units,
            dtype=(dtype or "float64"),
        )

    return calls_from_json(
        raw,
        label="telemetry_calls",
        parse_output=_parse_output,
        call_factory=lambda method, kwargs, outputs: TelemetryCall(
            method=method, kwargs=kwargs, outputs=outputs
        ),
        outputs_required=False,
    )


def validate_telemetry_calls(raw: object) -> None:
    _ = telemetry_calls_from_json(raw)
