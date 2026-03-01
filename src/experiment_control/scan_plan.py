from __future__ import annotations

from typing import Any

import numpy as np


_SCAN2D_PATTERNS = {"raster", "serpentine", "random", "center_out"}
_SCAN2D_ORDERS = {"row_major", "col_major"}


def validate_scan2d_pattern(pattern: str) -> str:
    value = str(pattern or "serpentine").strip().lower()
    if value not in _SCAN2D_PATTERNS:
        raise ValueError(
            "scan2d.pattern must be one of raster/serpentine/random/center_out"
        )
    return value


def validate_scan2d_order(order: str) -> str:
    value = str(order or "row_major").strip().lower()
    if value not in _SCAN2D_ORDERS:
        raise ValueError("scan2d.order must be one of row_major/col_major")
    return value


def generate_scan2d_records(
    x_values: list[float],
    y_values: list[float],
    *,
    pattern: str = "serpentine",
    order: str = "row_major",
    seed: int | None = None,
) -> list[dict[str, Any]]:
    if not x_values:
        raise ValueError("scan2d.x must contain at least one point")
    if not y_values:
        raise ValueError("scan2d.y must contain at least one point")

    pattern_name = validate_scan2d_pattern(pattern)
    order_name = validate_scan2d_order(order)

    row_major = order_name == "row_major"
    x_count = len(x_values)
    y_count = len(y_values)

    coords: list[tuple[int, int]] = []
    if row_major:
        for row in range(y_count):
            for col in range(x_count):
                coords.append((row, col))
    else:
        for col in range(x_count):
            for row in range(y_count):
                coords.append((row, col))

    if pattern_name == "serpentine":
        coords = _apply_serpentine(coords, x_count=x_count, y_count=y_count, row_major=row_major)
    elif pattern_name == "random":
        rng = np.random.default_rng(seed)
        rng.shuffle(coords)
    elif pattern_name == "center_out":
        coords = sorted(coords, key=lambda item: _center_out_sort_key(item, x_count=x_count, y_count=y_count))

    total = len(coords)
    records: list[dict[str, Any]] = []
    x_denom = max(1, x_count - 1)
    y_denom = max(1, y_count - 1)
    for index, (row, col) in enumerate(coords):
        records.append(
            {
                "x": x_values[col],
                "y": y_values[row],
                "row": row,
                "col": col,
                "index": index,
                "u": (col / x_denom) if x_count > 1 else 0.0,
                "v": (row / y_denom) if y_count > 1 else 0.0,
                "count": total,
            }
        )
    return records


def _apply_serpentine(
    coords: list[tuple[int, int]],
    *,
    x_count: int,
    y_count: int,
    row_major: bool,
) -> list[tuple[int, int]]:
    if row_major:
        out: list[tuple[int, int]] = []
        for row in range(y_count):
            row_coords = [(row, col) for col in range(x_count)]
            if row % 2 == 1:
                row_coords.reverse()
            out.extend(row_coords)
        return out

    out = []
    for col in range(x_count):
        col_coords = [(row, col) for row in range(y_count)]
        if col % 2 == 1:
            col_coords.reverse()
        out.extend(col_coords)
    return out


def _center_out_sort_key(
    coord: tuple[int, int], *, x_count: int, y_count: int
) -> tuple[float, int, int]:
    row, col = coord
    row_center = (y_count - 1) / 2.0
    col_center = (x_count - 1) / 2.0
    dr = row - row_center
    dc = col - col_center
    radius2 = (dr * dr) + (dc * dc)
    return (radius2, row, col)
