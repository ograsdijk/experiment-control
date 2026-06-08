from __future__ import annotations

from experiment_control.types import TelemetryOut


def scalar_telemetry(name: str, units: str, *, dtype: str = "float64") -> TelemetryOut:
    """Build a scalar :class:`TelemetryOut` for the dummy driver fixtures.

    ``dtype`` defaults to ``"float64"``, matching ``TelemetryOut``'s own
    default — explicit so callers can override with e.g. ``"int32"``
    without needing to spell out the full constructor.
    """
    return TelemetryOut(signal=name, kind="scalar", units=units, dtype=dtype)
