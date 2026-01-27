from __future__ import annotations

from typing import Any

import numpy as np

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


def generate_from_gen(
    gen_spec: dict[str, Any],
    *,
    env: dict[str, Any],
    serpentine_index: int | None = None,
) -> list[Any]:
    if not isinstance(gen_spec, dict):
        raise TypeError("gen spec must be a dict")

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
        return _apply_modifiers(
            values,
            offset=offset_val,
            shuffle=shuffle,
            seed=seed,
            serpentine=serpentine,
            serpentine_index=serpentine_index,
        )
    if "linspace" in gen_spec:
        params = render_templates(gen_spec["linspace"], env)
        start = float(params.get("start", 0))
        stop = float(params.get("stop", 0))
        num = int(params.get("num", 1))
        values = list(np.linspace(start, stop, num))
        return _apply_modifiers(
            values,
            offset=offset_val,
            shuffle=shuffle,
            seed=seed,
            serpentine=serpentine,
            serpentine_index=serpentine_index,
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
        return _apply_modifiers(
            values,
            offset=offset_val,
            shuffle=shuffle,
            seed=seed,
            serpentine=serpentine,
            serpentine_index=serpentine_index,
        )
    if "logspace" in gen_spec:
        params = render_templates(gen_spec["logspace"], env)
        start = float(params.get("start", 0))
        stop = float(params.get("stop", 0))
        num = int(params.get("num", 1))
        base = float(params.get("base", 10.0))
        values = list(np.logspace(start, stop, num, base=base))
        return _apply_modifiers(
            values,
            offset=offset_val,
            shuffle=shuffle,
            seed=seed,
            serpentine=serpentine,
            serpentine_index=serpentine_index,
        )
    if "geomspace" in gen_spec:
        params = render_templates(gen_spec["geomspace"], env)
        start = float(params.get("start", 1))
        stop = float(params.get("stop", 1))
        num = int(params.get("num", 1))
        values = list(np.geomspace(start, stop, num))
        return _apply_modifiers(
            values,
            offset=offset_val,
            shuffle=shuffle,
            seed=seed,
            serpentine=serpentine,
            serpentine_index=serpentine_index,
        )
    if "values" in gen_spec:
        params = render_templates(gen_spec["values"], env)
        if not isinstance(params, list):
            raise TypeError("gen.values must be a list")
        values = list(params)
        return _apply_modifiers(
            values,
            offset=offset_val,
            shuffle=shuffle,
            seed=seed,
            serpentine=serpentine,
            serpentine_index=serpentine_index,
        )

    raise ValueError(
        "gen spec must include one of "
        "range/linspace/triangle/logspace/geomspace/values"
    )
