from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np


class TelemetryQuality(enum.StrEnum):
    OK = "OK"
    BAD = "BAD"
    MISSING = "MISSING"
    STALE = "STALE"  # derived by manager


class DriverState(enum.StrEnum):
    INIT = "INIT"
    OK = "OK"
    DEGRADED = "DEGRADED"
    FAULT = "FAULT"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class DeviceState(enum.StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    FAULT = "FAULT"
    DISCONNECTED = "DISCONNECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Timestamp:
    t_wall: float
    t_mono: float


ExtractorKind = Literal["scalar", "index", "key", "attr"]


@dataclass(frozen=True, slots=True)
class TelemetryOut:
    """
    Define how to extract one signal from a device method return value.
    """

    signal: str
    kind: ExtractorKind = "scalar"  # scalar means "use the whole return value"
    ref: int | str | None = (
        None  # index for tuple/list, key for dict, attr name for objects
    )
    units: str | None = None
    dtype: str = "float64"


@dataclass(frozen=True, slots=True)
class StreamField:
    name: str
    dtype: str
    units: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("StreamField.name must be non-empty.")
        dtype = str(self.dtype).strip()
        if not dtype:
            raise ValueError("StreamField.dtype must be non-empty.")
        _ = np.dtype(dtype)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "dtype", dtype)


StreamKind = Literal["frame", "records"]


@dataclass(frozen=True, slots=True)
class StreamOut:
    stream: str
    dtype: str = "float64"
    shape: tuple[int, ...] = ()
    units: str | None = None
    description: str | None = None
    ring_slots: int = 1024
    attrs: dict[str, Any] = field(default_factory=dict)
    kind: StreamKind = "frame"
    fields: tuple[StreamField, ...] = ()

    def __post_init__(self) -> None:
        kind = str(self.kind or "frame").strip()
        if kind not in {"frame", "records"}:
            raise ValueError("StreamOut.kind must be 'frame' or 'records'.")
        object.__setattr__(self, "kind", kind)
        if kind == "frame":
            dtype = str(self.dtype).strip()
            if not dtype:
                raise ValueError("Frame StreamOut.dtype must be non-empty.")
            _ = np.dtype(dtype)
            shape = tuple(int(x) for x in self.shape)
            if not shape or any(x <= 0 for x in shape):
                raise ValueError("Frame StreamOut.shape must be a non-empty positive tuple.")
            object.__setattr__(self, "dtype", dtype)
            object.__setattr__(self, "shape", shape)
            object.__setattr__(self, "fields", ())
            return

        fields = tuple(self.fields)
        if not fields:
            raise ValueError("Record StreamOut.fields must be non-empty.")
        names: set[str] = set()
        for field_item in fields:
            if field_item.name in names:
                raise ValueError(f"Duplicate record field name {field_item.name!r}.")
            names.add(field_item.name)
        dtype = np.dtype([(f.name, np.dtype(f.dtype)) for f in fields])
        object.__setattr__(self, "dtype", str(dtype))
        object.__setattr__(self, "shape", ())
        object.__setattr__(self, "fields", fields)

    def numpy_dtype(self) -> np.dtype[Any]:
        if self.kind == "records":
            return np.dtype([(f.name, np.dtype(f.dtype)) for f in self.fields])
        return np.dtype(self.dtype)


@dataclass(frozen=True, slots=True)
class StreamMeta:
    name: str
    dtype: str
    units: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class StreamCall:
    method: str
    kwargs: None | dict[str, Any] = None
    outputs: None | list[StreamOut] = None
    meta: None | list[StreamMeta] = None
    period_s: float | None = None

    def __post_init__(self) -> None:
        if self.kwargs is None:
            object.__setattr__(self, "kwargs", {})
        if self.outputs is None:
            raise ValueError("StreamCall.outputs must be provided (no default).")
        if self.meta is None:
            object.__setattr__(self, "meta", [])
        if self.period_s is not None:
            period = float(self.period_s)
            if period <= 0:
                raise ValueError("StreamCall.period_s must be > 0 when provided.")
            object.__setattr__(self, "period_s", period)


@dataclass(frozen=True, slots=True)
class TelemetryCall:
    """
    Read one device method/property/attribute and map its value into telemetry signals.
    """

    method: str
    kwargs: None | dict[str, Any] = None
    outputs: None | list[TelemetryOut] = None

    def __post_init__(self) -> None:
        if self.kwargs is None:
            object.__setattr__(self, "kwargs", {})
        if self.outputs is None:
            # default: one scalar output named same as method
            object.__setattr__(
                self,
                "outputs",
                [TelemetryOut(signal=self.method, kind="scalar")],
            )


@dataclass(frozen=True, slots=True)
class RunMetaOut:
    key: str
    kind: ExtractorKind = "scalar"
    ref: int | str | None = None
    units: str | None = None
    dtype: str = "float64"


@dataclass(frozen=True, slots=True)
class RunMetaCall:
    method: str
    kwargs: None | dict[str, Any] = None
    outputs: None | list[RunMetaOut] = None

    def __post_init__(self) -> None:
        if self.kwargs is None:
            object.__setattr__(self, "kwargs", {})
        if self.outputs is None:
            object.__setattr__(
                self,
                "outputs",
                [RunMetaOut(key=self.method, kind="scalar")],
            )


@dataclass(frozen=True, slots=True)
class MemberParamSpec:
    name: str
    kind: str
    required: bool
    default: object | None
    annotation: str | None


@dataclass(frozen=True, slots=True)
class MemberSpec:
    name: str
    kind: str
    readable: bool
    settable: bool
    value_annotation: str | None
    doc: str | None
    params: list[MemberParamSpec] | None
    return_annotation: str | None
    source: str
