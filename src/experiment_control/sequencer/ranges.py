from __future__ import annotations

from typing import Any

import numpy as np

from ..scan_plan import generate_scan2d_records, validate_scan2d_order, validate_scan2d_pattern
from .eval import render_templates


def _apply_modifiers(
    values: list[float] | list[Any],
    *,
    offset: float | None,
    shuffle: bool,
    seed: int | None,
    serpentine: bool,
    serpentine_index: int | None,
) -> list[Any]:
    out = list(values)
    if offset is not None:
        out = [v + offset for v in out]
    if shuffle:
        rng = np.random.default_rng(seed)
        rng.shuffle(out)
    if serpentine and serpentine_index is not None and serpentine_index % 2 == 1:
        out = list(reversed(out))
    return out


def _wrap_scalar_records(values: list[Any]) -> list[dict[str, Any]]:
    total = len(values)
    denom = max(1, total - 1)
    records: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        records.append(
            {
                "value": value,
                "index": index,
                "u": (index / denom) if total > 1 else 0.0,
                "count": total,
            }
        )
    return records


def _coerce_int(value: Any, *, name: str, minimum: int) -> int:
    try:
        out = int(value)
    except Exception as exc:
        raise TypeError(f"{name} must be an integer") from exc
    if out < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return out


def _coerce_float(value: Any, *, name: str, positive: bool = False) -> float:
    try:
        out = float(value)
    except Exception as exc:
        raise TypeError(f"{name} must be a number") from exc
    if positive and out <= 0:
        raise ValueError(f"{name} must be > 0")
    return out


def _normalize_step_counts(raw: Any) -> tuple[int, int]:
    if isinstance(raw, (int, float)):
        count = _coerce_int(raw, name="scan2d.steps", minimum=1)
        return count, count
    if not isinstance(raw, dict):
        raise TypeError("scan2d.steps must be a number or an object with x/y")
    x_count = _coerce_int(raw.get("x"), name="scan2d.steps.x", minimum=1)
    y_count = _coerce_int(raw.get("y"), name="scan2d.steps.y", minimum=1)
    return x_count, y_count


def _normalize_pitch(raw: Any) -> tuple[float, float]:
    if isinstance(raw, (int, float)):
        pitch = _coerce_float(raw, name="scan2d.pitch", positive=True)
        return pitch, pitch
    if not isinstance(raw, dict):
        raise TypeError("scan2d.pitch must be a number or an object with x/y")
    x_pitch = _coerce_float(raw.get("x"), name="scan2d.pitch.x", positive=True)
    y_pitch = _coerce_float(raw.get("y"), name="scan2d.pitch.y", positive=True)
    return x_pitch, y_pitch


def _normalize_center(raw: Any) -> tuple[float, float]:
    if not isinstance(raw, dict):
        raise TypeError("scan2d.center must be an object with x/y")
    cx = _coerce_float(raw.get("x"), name="scan2d.center.x")
    cy = _coerce_float(raw.get("y"), name="scan2d.center.y")
    return cx, cy


def _normalize_size(raw: dict[str, Any]) -> tuple[float, float]:
    if "size" in raw:
        size = raw["size"]
        if isinstance(size, (int, float)):
            width = _coerce_float(size, name="scan2d.size", positive=True)
            return width, width
        if isinstance(size, dict):
            width = _coerce_float(size.get("width"), name="scan2d.size.width", positive=True)
            height = _coerce_float(size.get("height"), name="scan2d.size.height", positive=True)
            return width, height
        raise TypeError("scan2d.size must be a number or an object with width/height")

    width = _coerce_float(raw.get("width"), name="scan2d.width", positive=True)
    height = _coerce_float(raw.get("height"), name="scan2d.height", positive=True)
    return width, height


def _linspace_from_pitch(start: float, stop: float, pitch: float) -> list[float]:
    span = stop - start
    count = max(1, int(round(span / pitch)) + 1)
    return list(np.linspace(start, stop, count))


def _explicit_axis_values(axis_raw: Any, *, name: str) -> list[float]:
    if not isinstance(axis_raw, dict):
        raise TypeError(f"scan2d.{name} must be an object")
    if "linspace" not in axis_raw or len(axis_raw) != 1:
        raise TypeError(f"scan2d.{name} must contain exactly one linspace spec")
    params = axis_raw["linspace"]
    if not isinstance(params, dict):
        raise TypeError(f"scan2d.{name}.linspace must be an object")
    start = _coerce_float(params.get("start"), name=f"scan2d.{name}.linspace.start")
    stop = _coerce_float(params.get("stop"), name=f"scan2d.{name}.linspace.stop")
    num = _coerce_int(params.get("num"), name=f"scan2d.{name}.linspace.num", minimum=1)
    return list(np.linspace(start, stop, num))


def _generate_scan2d_from_spec(spec: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(spec, dict):
        raise TypeError("gen.scan2d must be a dict")

    pattern = validate_scan2d_pattern(str(spec.get("pattern", "serpentine")))
    order = validate_scan2d_order(str(spec.get("order", "row_major")))
    seed_raw = spec.get("seed")
    seed = int(seed_raw) if seed_raw is not None else None
    if seed is not None and pattern != "random":
        raise ValueError("scan2d.seed is only valid when pattern=random")

    has_explicit_axes = "x" in spec or "y" in spec
    has_convenience = (
        "center" in spec
        or "size" in spec
        or "width" in spec
        or "height" in spec
        or "steps" in spec
        or "pitch" in spec
    )
    if has_explicit_axes and has_convenience:
        raise ValueError("scan2d cannot mix explicit x/y axes with center/size shorthand")

    if has_explicit_axes:
        if "x" not in spec or "y" not in spec:
            raise ValueError("scan2d explicit form requires both x and y")
        x_values = _explicit_axis_values(spec["x"], name="x")
        y_values = _explicit_axis_values(spec["y"], name="y")
        return generate_scan2d_records(
            x_values,
            y_values,
            pattern=pattern,
            order=order,
            seed=seed,
        )

    if "center" not in spec:
        raise ValueError("scan2d shorthand requires center")
    if ("steps" in spec) == ("pitch" in spec):
        raise ValueError("scan2d shorthand requires exactly one of steps or pitch")

    cx, cy = _normalize_center(spec["center"])
    width, height = _normalize_size(spec)
    x_start = cx - (width / 2.0)
    x_stop = cx + (width / 2.0)
    y_start = cy - (height / 2.0)
    y_stop = cy + (height / 2.0)

    if "steps" in spec:
        x_count, y_count = _normalize_step_counts(spec["steps"])
        x_values = list(np.linspace(x_start, x_stop, x_count))
        y_values = list(np.linspace(y_start, y_stop, y_count))
    else:
        x_pitch, y_pitch = _normalize_pitch(spec["pitch"])
        x_values = _linspace_from_pitch(x_start, x_stop, x_pitch)
        y_values = _linspace_from_pitch(y_start, y_stop, y_pitch)

    return generate_scan2d_records(
        x_values,
        y_values,
        pattern=pattern,
        order=order,
        seed=seed,
    )


def generate_from_gen(
    gen_spec: dict[str, Any],
    *,
    env: dict[str, Any],
    serpentine_index: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(gen_spec, dict):
        raise TypeError("gen spec must be a dict")

    if "scan2d" in gen_spec:
        if len(gen_spec) != 1:
            raise ValueError("scan2d must be the only generator in its gen spec")
        rendered_scan = render_templates(gen_spec["scan2d"], env)
        return _generate_scan2d_from_spec(rendered_scan)

    offset = gen_spec.get("offset")
    shuffle = bool(gen_spec.get("shuffle", False))
    seed = gen_spec.get("seed")
    serpentine = bool(gen_spec.get("serpentine", False))

    offset_val = None
    if offset is not None:
        offset_val = float(render_templates(offset, env))

    if seed is not None:
        seed = int(render_templates(seed, env))

    if "range" in gen_spec:
        params = render_templates(gen_spec["range"], env)
        start = float(params.get("start", 0))
        stop = float(params.get("stop", 0))
        step = float(params.get("step", 1))
        if step == 0:
            raise ValueError("range.step must be non-zero")
        values = list(np.arange(start, stop + 0.0, step))
        return _wrap_scalar_records(
            _apply_modifiers(
                values,
                offset=offset_val,
                shuffle=shuffle,
                seed=seed,
                serpentine=serpentine,
                serpentine_index=serpentine_index,
            )
        )
    if "linspace" in gen_spec:
        params = render_templates(gen_spec["linspace"], env)
        start = float(params.get("start", 0))
        stop = float(params.get("stop", 0))
        num = int(params.get("num", 1))
        values = list(np.linspace(start, stop, num))
        return _wrap_scalar_records(
            _apply_modifiers(
                values,
                offset=offset_val,
                shuffle=shuffle,
                seed=seed,
                serpentine=serpentine,
                serpentine_index=serpentine_index,
            )
        )
    if "triangle" in gen_spec:
        params = render_templates(gen_spec["triangle"], env)
        start = float(params.get("start", 0))
        stop = float(params.get("stop", 0))
        num = int(params.get("num", 1))
        if num < 2:
            raise ValueError("triangle.num must be >= 2")
        forward = list(np.linspace(start, stop, num))
        backward = list(np.linspace(stop, start, num))
        values = forward + backward
        return _wrap_scalar_records(
            _apply_modifiers(
                values,
                offset=offset_val,
                shuffle=shuffle,
                seed=seed,
                serpentine=serpentine,
                serpentine_index=serpentine_index,
            )
        )
    if "logspace" in gen_spec:
        params = render_templates(gen_spec["logspace"], env)
        start = float(params.get("start", 0))
        stop = float(params.get("stop", 0))
        num = int(params.get("num", 1))
        base = float(params.get("base", 10.0))
        values = list(np.logspace(start, stop, num, base=base))
        return _wrap_scalar_records(
            _apply_modifiers(
                values,
                offset=offset_val,
                shuffle=shuffle,
                seed=seed,
                serpentine=serpentine,
                serpentine_index=serpentine_index,
            )
        )
    if "geomspace" in gen_spec:
        params = render_templates(gen_spec["geomspace"], env)
        start = float(params.get("start", 1))
        stop = float(params.get("stop", 1))
        num = int(params.get("num", 1))
        values = list(np.geomspace(start, stop, num))
        return _wrap_scalar_records(
            _apply_modifiers(
                values,
                offset=offset_val,
                shuffle=shuffle,
                seed=seed,
                serpentine=serpentine,
                serpentine_index=serpentine_index,
            )
        )
    if "values" in gen_spec:
        params = render_templates(gen_spec["values"], env)
        if not isinstance(params, list):
            raise TypeError("gen.values must be a list")
        values = list(params)
        return _wrap_scalar_records(
            _apply_modifiers(
                values,
                offset=offset_val,
                shuffle=shuffle,
                seed=seed,
                serpentine=serpentine,
                serpentine_index=serpentine_index,
            )
        )

    raise ValueError(
        "gen spec must include one of "
        "range/linspace/triangle/logspace/geomspace/values/scan2d"
    )
