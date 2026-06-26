from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.config_parsing import (
    ConfigError,
    normalize_list,
    optional_dict,
    require_str,
)
from .rules_constants import ON_UNKNOWN_VALUES, WATCHDOG_SEVERITIES

Json = dict[str, Any]


def _fmt_path(parts: list[str | int]) -> str:
    out: list[str] = []
    for p in parts:
        if isinstance(p, int):
            out.append(f"[{p}]")
        else:
            if not out:
                out.append(p)
            else:
                out.append(f".{p}")
    return "".join(out) if out else "<root>"


@dataclass(frozen=True)
class TelemetryBinding:
    alias: str
    device_id: str
    signal: str
    max_age_s: float
    required: bool = True
    # When set, this binding reads PROCESS telemetry (manager
    # process_telemetry cache) for `process_id` instead of device
    # telemetry. Exactly one of device_id / process_id identifies the
    # source; for a process binding `device_id` is "". Only consumers
    # that opt in (parse_telemetry_bindings(allow_process=True)) accept
    # process bindings.
    process_id: str | None = None

    @property
    def source_kind(self) -> str:
        return "process" if self.process_id is not None else "device"

    @property
    def source_id(self) -> str:
        return self.process_id if self.process_id is not None else self.device_id


def parse_version(
    obj: Json, *, allow_type: bool = False
) -> int:
    if allow_type and "version" not in obj and "type" in obj:
        version = obj.get("type", 1)
    else:
        version = obj.get("version", 1)
    try:
        version_int = int(version)
    except Exception:
        raise ConfigError(path="<root>", message="version must be an int") from None
    if version_int != 1:
        raise ConfigError(path="<root>", message="version must be 1")
    return version_int


def parse_on_unknown(
    raw: Any, *, path: list[str | int], default: str
) -> str:
    if raw is None:
        return default
    if not isinstance(raw, str):
        raise ConfigError(path=_fmt_path(path), message="on_unknown must be a string")
    value = raw.strip().lower()
    if value not in ON_UNKNOWN_VALUES:
        raise ConfigError(
            path=_fmt_path(path),
            message="on_unknown must be ignore or trigger",
        )
    return value


def parse_severity(raw: Any, *, path: list[str | int]) -> str:
    if raw is None:
        return "info"
    if not isinstance(raw, str):
        raise ConfigError(path=_fmt_path(path), message="severity must be a string")
    value = raw.strip().lower()
    if value not in WATCHDOG_SEVERITIES:
        raise ConfigError(
            path=_fmt_path(path),
            message="severity must be one of info/warn/critical",
        )
    return value


def parse_telemetry_bindings(
    inputs: Json,
    *,
    path: list[str | int],
    default_max_age_s: float,
    require_nonempty: bool,
    allow_process: bool = False,
) -> list[TelemetryBinding]:
    inputs_obj = optional_dict(inputs, path=path)
    telemetry_raw = normalize_list(inputs_obj.get("telemetry"), path=[*path, "telemetry"])
    if require_nonempty and not telemetry_raw:
        raise ConfigError(
            path=_fmt_path([*path, "telemetry"]),
            message="telemetry inputs are required",
        )
    telemetry: list[TelemetryBinding] = []
    for i, binding_raw in enumerate(telemetry_raw):
        if not isinstance(binding_raw, dict):
            raise ConfigError(
                path=_fmt_path([*path, "telemetry", i]),
                message="must be an object/dict",
            )
        alias = require_str(binding_raw.get("as"), path=[*path, "telemetry", i, "as"])
        has_device = binding_raw.get("device") is not None
        has_process = binding_raw.get("process") is not None
        if has_device and has_process:
            raise ConfigError(
                path=_fmt_path([*path, "telemetry", i]),
                message="binding must have exactly one of 'device' or 'process'",
            )
        if not has_device and not has_process:
            raise ConfigError(
                path=_fmt_path([*path, "telemetry", i]),
                message="binding must have 'device' or 'process'",
            )
        if has_process and not allow_process:
            raise ConfigError(
                path=_fmt_path([*path, "telemetry", i, "process"]),
                message="process telemetry bindings are not supported here; use 'device'",
            )
        if has_process:
            process_id: str | None = require_str(
                binding_raw.get("process"), path=[*path, "telemetry", i, "process"]
            )
            dev = ""
        else:
            process_id = None
            dev = require_str(
                binding_raw.get("device"), path=[*path, "telemetry", i, "device"]
            )
        signal = require_str(
            binding_raw.get("signal"), path=[*path, "telemetry", i, "signal"]
        )
        max_age = binding_raw.get("max_age_s", default_max_age_s)
        try:
            max_age_val = float(max_age)
        except Exception:
            raise ConfigError(
                path=_fmt_path([*path, "telemetry", i, "max_age_s"]),
                message="max_age_s must be a number",
            ) from None
        required_raw = binding_raw.get("required", True)
        if not isinstance(required_raw, bool):
            raise ConfigError(
                path=_fmt_path([*path, "telemetry", i, "required"]),
                message="required must be a boolean",
            )
        telemetry.append(
            TelemetryBinding(
                alias=alias,
                device_id=dev,
                signal=signal,
                max_age_s=max_age_val,
                required=required_raw,
                process_id=process_id,
            )
        )
    return telemetry
