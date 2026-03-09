from __future__ import annotations

import math
from typing import Any

import numpy as np


def parse_trace_decimator(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"stride", "mean", "m4"}:
        return value
    return "minmax"


def parse_trace_max_points(raw: Any) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except Exception:
        return None
    return max(32, min(20000, int(value)))


def parse_trace_max_fps(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return max(0.5, min(120.0, float(value)))


def parse_trace_rolling_window(raw: Any) -> int:
    text = str(raw or "").strip()
    if not text:
        return 1
    try:
        value = int(float(text))
    except Exception:
        return 1
    return max(1, min(200, int(value)))


def parse_trace_average_mode(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value == "rolling":
        return "rolling"
    return "block"


def parse_csv_query_list(raw: Any) -> list[str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    values = [part.strip() for part in text.split(",") if part.strip()]
    return values if values else None


def parse_channel_index(raw: Any) -> int:
    text = str(raw or "").strip()
    if not text:
        return 0
    try:
        value = int(float(text))
    except Exception:
        return 0
    return max(0, int(value))


def normalize_shape(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            parsed = int(value)
        except Exception:
            continue
        if parsed <= 0:
            continue
        out.append(parsed)
    return out


def coerce_stream_values_array(values: Any, shape: list[int]) -> np.ndarray | None:
    if isinstance(values, np.ndarray):
        arr = values
    elif isinstance(values, list):
        try:
            arr = np.asarray(values, dtype=np.float64)
        except Exception:
            return None
    else:
        return None
    if arr.ndim == 0:
        arr = arr.reshape(1)
    if shape:
        expected = 1
        for dim in shape:
            expected *= int(dim)
        if expected > 0 and int(arr.size) == int(expected):
            try:
                arr = arr.reshape(tuple(shape))
            except Exception:
                pass
    try:
        arr = arr.astype(np.float64, copy=False)
    except Exception:
        return None
    if arr.size > 0 and not np.isfinite(arr).all():
        return None
    return arr


def select_trace_from_array(array: np.ndarray, channel_index: int) -> np.ndarray:
    arr = np.asarray(array)
    if arr.ndim == 0:
        return arr.reshape(1).astype(np.float64, copy=False)
    if arr.ndim == 1:
        return arr.astype(np.float64, copy=False)
    if arr.ndim == 2:
        rows, cols = int(arr.shape[0]), int(arr.shape[1])
        if rows <= 1 or cols <= 1:
            return arr.reshape(-1).astype(np.float64, copy=False)
        if rows <= cols:
            idx = max(0, min(int(channel_index), rows - 1))
            return arr[idx, :].astype(np.float64, copy=False)
        idx = max(0, min(int(channel_index), cols - 1))
        return arr[:, idx].astype(np.float64, copy=False)
    return arr.reshape(-1).astype(np.float64, copy=False)


def coerce_trace_array(raw: Any) -> np.ndarray | None:
    if isinstance(raw, np.ndarray):
        arr = raw.reshape(-1)
    elif isinstance(raw, list):
        try:
            arr = np.asarray(raw, dtype=np.float64).reshape(-1)
        except Exception:
            return None
    else:
        return None
    if arr.size <= 0:
        return np.asarray([], dtype=np.float64)
    if not np.isfinite(arr).all():
        return None
    return arr.astype(np.float64, copy=False)


def _bucket_ranges(n: int, bucket_count: int) -> list[tuple[int, int]]:
    if n <= 0 or bucket_count <= 0:
        return []
    out: list[tuple[int, int]] = []
    for i in range(bucket_count):
        start = (i * n) // bucket_count
        stop = ((i + 1) * n) // bucket_count
        if stop <= start:
            stop = min(n, start + 1)
        out.append((start, stop))
    return out


def _decimate_trace_stride(points: np.ndarray, max_points: int) -> list[float]:
    n = int(points.size)
    step = max(1, int(math.ceil(float(n) / float(max_points))))
    out = points[::step]
    if out.size > 0 and float(out[-1]) != float(points[-1]):
        out = np.concatenate([out, points[-1:]])
    return out[:max_points].tolist()


def _decimate_trace_mean(points: np.ndarray, max_points: int) -> list[float]:
    n = int(points.size)
    bucket_count = max(1, min(max_points, n))
    out: list[float] = []
    for start, stop in _bucket_ranges(n, bucket_count):
        chunk = points[start:stop]
        if chunk.size <= 0:
            continue
        out.append(float(np.mean(chunk, dtype=np.float64)))
    return out[:max_points]


def _decimate_trace_m4(points: np.ndarray, max_points: int) -> list[float]:
    n = int(points.size)
    bucket_count = max(1, min(max_points // 4, n))
    out: list[float] = []
    for start, stop in _bucket_ranges(n, bucket_count):
        if stop <= start:
            continue
        first_i = start
        last_i = stop - 1
        min_i = start
        max_i = start
        min_v = float(points[start])
        max_v = float(points[start])
        for idx in range(start + 1, stop):
            value = float(points[idx])
            if value < min_v:
                min_v = value
                min_i = idx
            if value > max_v:
                max_v = value
                max_i = idx
        for idx in sorted({first_i, min_i, max_i, last_i}):
            out.append(float(points[idx]))
            if len(out) >= max_points:
                return out[:max_points]
    return out[:max_points]


def _decimate_trace_minmax(points: np.ndarray, max_points: int) -> list[float]:
    n = int(points.size)
    bucket_count = max(1, min(max_points // 2, n))
    out: list[float] = []
    for start, stop in _bucket_ranges(n, bucket_count):
        if stop <= start:
            continue
        min_i = start
        max_i = start
        min_v = float(points[start])
        max_v = float(points[start])
        for idx in range(start + 1, stop):
            value = float(points[idx])
            if value < min_v:
                min_v = value
                min_i = idx
            if value > max_v:
                max_v = value
                max_i = idx
        if min_i <= max_i:
            out.append(float(points[min_i]))
            if max_i != min_i and len(out) < max_points:
                out.append(float(points[max_i]))
        else:
            out.append(float(points[max_i]))
            if max_i != min_i and len(out) < max_points:
                out.append(float(points[min_i]))
        if len(out) >= max_points:
            return out[:max_points]
    return out[:max_points]


def decimate_trace_values(values: Any, *, mode: str, max_points: int) -> Any:
    points = coerce_trace_array(values)
    if points is None:
        return values
    n = int(points.size)
    if max_points <= 0 or n <= max_points:
        return points.tolist()

    decimator = parse_trace_decimator(mode)
    if decimator == "stride":
        return _decimate_trace_stride(points, max_points)
    if decimator == "mean":
        return _decimate_trace_mean(points, max_points)
    if decimator == "m4":
        return _decimate_trace_m4(points, max_points)
    return _decimate_trace_minmax(points, max_points)
