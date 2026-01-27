from __future__ import annotations

from typing import Any

import numpy as np


def default_for_dtype(dtype_str: str) -> Any:
    if dtype_str.startswith("float"):
        return float("nan")
    if dtype_str.startswith("int") or dtype_str.startswith("uint"):
        return 0
    if dtype_str == "bool":
        return False
    if dtype_str == "str":
        return ""
    return None


def coerce_float(value: Any, *, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError("missing float")
        return float(default)
    return float(value)


def coerce_int(value: Any, *, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("missing int")
        return int(default)
    return int(value)


def coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return bool(value)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        if np.isnan(value):
            return None
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 0)
        except Exception:
            try:
                return int(float(text))
            except Exception:
                return None
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, np.integer)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        if np.isnan(value):
            return False
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off", ""}:
            return False
        return bool(text)
    return bool(value)


def coerce_scalar(value: Any, dtype_str: str) -> Any:
    if isinstance(value, np.generic):
        value = value.item()

    if dtype_str == "str":
        if value is None:
            return ""
        return str(value)

    if value is None:
        return default_for_dtype(dtype_str)

    if dtype_str.startswith("float"):
        try:
            return float(value)
        except Exception:
            return float("nan")

    if dtype_str.startswith("uint"):
        iv = _coerce_int(value)
        if iv is None:
            return 0
        return iv if iv >= 0 else 0

    if dtype_str.startswith("int"):
        iv = _coerce_int(value)
        if iv is None:
            return 0
        return iv

    if dtype_str == "bool":
        return _coerce_bool(value)

    return value
