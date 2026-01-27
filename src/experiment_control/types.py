from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Literal


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
class StreamOut:
    stream: str
    dtype: str
    shape: tuple[int, ...]
    units: str | None = None
    description: str | None = None
    ring_slots: int = 1024
    attrs: dict[str, Any] = field(default_factory=dict)


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

    def __post_init__(self) -> None:
        if self.kwargs is None:
            object.__setattr__(self, "kwargs", {})
        if self.outputs is None:
            raise ValueError("StreamCall.outputs must be provided (no default).")
        if self.meta is None:
            object.__setattr__(self, "meta", [])


@dataclass(frozen=True, slots=True)
class TelemetryCall:
    """
    Call one device method and map its return value into one or more telemetry signals.
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
