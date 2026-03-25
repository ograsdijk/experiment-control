from __future__ import annotations

from typing import Any

import numpy as np


def _coerce_int64(value: Any, *, missing: Any) -> Any:
    if isinstance(value, (bool, np.bool_)):
        return int(bool(value))
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return int(value)
    return missing


def _coerce_bool(value: Any, *, missing: Any) -> Any:
    if isinstance(value, (bool, np.bool_)):
        return np.uint8(1 if bool(value) else 0)
    if isinstance(value, (int, np.integer)) and int(value) in {0, 1}:
        return np.uint8(int(value))
    return missing


def _coerce_float(value: Any, *, missing: Any) -> Any:
    if isinstance(value, (bool, np.bool_)):
        return float(bool(value))
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    return missing


def _coerce_by_dtype(value: Any, *, dtype: str, missing: Any) -> Any:
    if dtype == "int64":
        return _coerce_int64(value, missing=missing)
    if dtype == "bool":
        return _coerce_bool(value, missing=missing)
    return _coerce_float(value, missing=missing)


def coerce_context_value(
    *,
    name: str,
    value: Any,
    missing_values: dict[str, Any],
    dtype_map: dict[str, str],
) -> Any:
    missing = missing_values.get(name, np.nan)
    dtype = dtype_map.get(name, "float64")
    if value is None:
        return missing
    try:
        return _coerce_by_dtype(value, dtype=dtype, missing=missing)
    except Exception:
        return missing
