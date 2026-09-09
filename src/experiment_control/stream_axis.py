"""Resolution of a declared :class:`StreamAxis` against run metadata.

Pure and I/O-free: callers fetch run metadata however they like (the stream
analysis process and the FastAPI gateway both use the ``collect_run_metadata``
device action) and hand the mapping in here.

Resolution never raises. A stream that declares no axis uses
:data:`IDENTITY_AXIS` — the sample index, which is what every consumer used
before axes existed. A declared axis whose metadata cannot be resolved carries
identity-shaped placeholder coordinates with ``source="unresolved"`` and an
``error``; consumers that require physical coordinates must not treat those
placeholders as valid measurements.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .types import StreamAxis

__all__ = [
    "IDENTITY_AXIS",
    "ResolvedStreamAxis",
    "axis_values",
    "resolve_stream_axis",
]


@dataclass(frozen=True, slots=True)
class ResolvedStreamAxis:
    """A concrete sample axis: ``x[i] = origin + increment * i``."""

    units: str | None
    label: str | None
    increment: float
    origin: float
    source: str
    error: str | None = None

    @property
    def is_identity(self) -> bool:
        return self.source == "identity"

    def to_json(self) -> dict[str, Any]:
        return {
            "x_units": self.units,
            "x_label": self.label,
            "x_increment": self.increment,
            "x_origin": self.origin,
            "x_axis_source": self.source,
            "x_axis_error": self.error,
        }


IDENTITY_AXIS = ResolvedStreamAxis(
    units=None,
    label="sample index",
    increment=1.0,
    origin=0.0,
    source="identity",
)


def _finite(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value):
        return None
    return value


def _resolve_increment(
    axis: StreamAxis, run_metadata: Mapping[str, Any]
) -> tuple[float | None, str, str | None]:
    if axis.increment is not None:
        return float(axis.increment), "declared", None

    if axis.rate_from is not None:
        rate = _finite(run_metadata.get(axis.rate_from))
        if rate is None:
            return None, "unresolved", (
                f"run metadata key {axis.rate_from!r} missing or not a finite number"
            )
        if rate <= 0.0:
            return None, "unresolved", (
                f"run metadata key {axis.rate_from!r} must be a positive rate, got {rate!r}"
            )
        return 1.0 / rate, "run_metadata", None

    if axis.increment_from is not None:
        increment = _finite(run_metadata.get(axis.increment_from))
        if increment is None or increment == 0.0:
            return None, "unresolved", (
                f"run metadata key {axis.increment_from!r} missing or not a "
                "finite non-zero number"
            )
        return increment, "run_metadata", None

    return None, "unresolved", "no increment, increment_from, or rate_from declared"


def _resolve_origin(
    axis: StreamAxis, run_metadata: Mapping[str, Any]
) -> tuple[float, str, str | None]:
    if axis.origin is not None:
        return float(axis.origin), "declared", None
    if axis.origin_from is not None:
        origin = _finite(run_metadata.get(axis.origin_from))
        if origin is None:
            return 0.0, "unresolved", (
                f"run metadata key {axis.origin_from!r} missing or not a finite "
                "number; origin defaulted to 0.0"
            )
        return origin, "run_metadata", None
    return 0.0, "declared", None


def resolve_stream_axis(
    axis: StreamAxis | None,
    run_metadata: Mapping[str, Any] | None,
) -> ResolvedStreamAxis:
    """Resolve ``axis`` without raising, marking failed declarations unresolved."""
    if axis is None:
        return IDENTITY_AXIS

    metadata: Mapping[str, Any] = run_metadata if run_metadata is not None else {}
    increment, source, increment_error = _resolve_increment(axis, metadata)
    if increment is None:
        return ResolvedStreamAxis(
            units=None,
            label=IDENTITY_AXIS.label,
            increment=1.0,
            origin=0.0,
            source="unresolved",
            error=increment_error,
        )

    origin, origin_source, origin_error = _resolve_origin(axis, metadata)
    # A device readback anywhere in the axis makes the whole axis
    # device-sourced; reporting "declared" because only the increment was
    # static would misattribute where the numbers came from.
    if "run_metadata" in (source, origin_source):
        source = "run_metadata"
    return ResolvedStreamAxis(
        units=axis.units,
        label=axis.label,
        increment=increment,
        origin=origin,
        source=source,
        error=origin_error,
    )


def axis_values(axis: ResolvedStreamAxis, n: int) -> np.ndarray:
    """Sample positions for a trace of ``n`` points."""
    count = max(int(n), 0)
    return float(axis.origin) + float(axis.increment) * np.arange(
        count, dtype=np.float64
    )
