from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..types import ExtractorKind


def extract_value(
    value: object, *, kind: ExtractorKind, ref: int | str | None
) -> object:
    if kind == "scalar":
        return value
    if ref is None:
        raise ValueError(f"Extractor kind {kind!r} requires ref")
    if kind == "index":
        return value[ref]  # type: ignore[index]
    if kind == "key":
        return value[ref]  # type: ignore[index]
    if kind == "attr":
        if not isinstance(ref, str):
            raise TypeError("attr extractor requires str ref")
        return getattr(value, ref)
    raise ValueError(f"Unknown extractor kind {kind!r}")


def _identity(value: Any) -> Any:
    return value


def _raise_extractor(msg: str) -> Callable[[Any], Any]:
    def _raise(_: Any) -> Any:
        raise ValueError(msg)

    return _raise


@dataclass(frozen=True, slots=True)
class _TelemetryOutPlan:
    signal: str
    units: str | None
    dtype: str
    extractor: Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class _TelemetryCallPlan:
    func: Callable[..., Any] | None
    attr_name: str | None
    kwargs: dict[str, Any]
    outputs: list[_TelemetryOutPlan]
    method: str  # Original call.method, used as key in telemetry call_errors.


@dataclass(slots=True)
class _ScheduledStreamCallPlan:
    action_name: str
    period_s: float
    next_due_s: float


