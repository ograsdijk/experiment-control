from __future__ import annotations

import argparse
import bisect
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any

import numpy as np
import zmq
try:
    from scipy.optimize import curve_fit
except Exception:  # pragma: no cover - optional dependency at runtime
    curve_fit = None  # type: ignore[assignment]

from ..capabilities import capabilities_payload, method, param
from ..shm.shm_ring import ShmRingWriter, now_mono_ns, now_wall_ns
from ..shm.shm_ring import ShmRingReader
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import safe_json_loads
from .manager_client_helper import ManagerClientHelper
from .process_base import ManagedProcessBase

Json = dict[str, Any]
SAMPLE_INDEX_INPUT_TOKEN = "__sample_index__"


@dataclass(frozen=True)
class OpSpec:
    input_types: dict[str, str]
    output_type: str
    optional_input_types: dict[str, str] = field(default_factory=dict)
    stateful: bool = False


@dataclass
class NodeSpec:
    node_id: str
    op: str
    params: Json
    inputs: dict[str, str]


@dataclass(frozen=True)
class PublishOutput:
    output_id: str
    node_id: str
    kind: str


@dataclass
class CompiledWorkspace:
    workspace_id: str
    enabled: bool
    nodes: dict[str, NodeSpec]
    order: list[str]
    stream_source_node_id: str
    stream_key: tuple[str, str]
    node_output_types: dict[str, str]
    outputs: list[PublishOutput]


@dataclass
class WorkspaceRuntime:
    compiled: CompiledWorkspace
    raw_config: Json
    node_state: dict[str, Any]
    processed_samples: int = 0
    dropped_samples: int = 0
    revision: int = 1
    etag: str = ""


class WorkspaceRevisionConflict(RuntimeError):
    def __init__(
        self,
        *,
        workspace_id: str,
        expected_revision: int | None,
        current_revision: int | None,
    ) -> None:
        message = (
            f"workspace revision conflict for {workspace_id!r}: "
            f"expected {expected_revision!r}, current {current_revision!r}"
        )
        super().__init__(message)
        self.workspace_id = workspace_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision


@dataclass
class BinStatsState:
    auto_range: bool
    max_bin_count: int
    x_min: float | None
    x_max: float | None
    centers: np.ndarray | None
    counts: np.ndarray
    sums: np.ndarray
    sums_sq: np.ndarray
    samples_x: list[float]
    samples_y: list[float]
    dropped_samples: int = 0
    auto_bins_dirty: bool = False

    @classmethod
    def from_params(cls, params: Json) -> BinStatsState:
        bin_count = _normalize_int(params.get("bin_count"))
        if bin_count is None or bin_count <= 0:
            raise ValueError("aggregate.bin_stats requires bin_count > 0")
        auto_range = _normalize_bool(params.get("auto_range"), default=False)
        if auto_range:
            return cls(
                auto_range=True,
                max_bin_count=int(bin_count),
                x_min=None,
                x_max=None,
                centers=None,
                counts=np.zeros(0, dtype=np.int64),
                sums=np.zeros(0, dtype=np.float64),
                sums_sq=np.zeros(0, dtype=np.float64),
                samples_x=[],
                samples_y=[],
                dropped_samples=0,
                auto_bins_dirty=False,
            )
        x_min = _normalize_float(params.get("x_min"))
        x_max = _normalize_float(params.get("x_max"))
        if x_min is None or x_max is None or x_max <= x_min:
            raise ValueError("aggregate.bin_stats requires x_min < x_max")
        return cls(
            auto_range=False,
            max_bin_count=int(bin_count),
            x_min=float(x_min),
            x_max=float(x_max),
            centers=None,
            counts=np.zeros(int(bin_count), dtype=np.int64),
            sums=np.zeros(int(bin_count), dtype=np.float64),
            sums_sq=np.zeros(int(bin_count), dtype=np.float64),
            samples_x=[],
            samples_y=[],
            dropped_samples=0,
            auto_bins_dirty=False,
        )

    def reset(self) -> None:
        self.x_min = self.x_min if not self.auto_range else None
        self.x_max = self.x_max if not self.auto_range else None
        self.centers = None
        self.counts.fill(0)
        self.sums.fill(0.0)
        self.sums_sq.fill(0.0)
        self.samples_x.clear()
        self.samples_y.clear()
        if self.auto_range:
            self.counts = np.zeros(0, dtype=np.int64)
            self.sums = np.zeros(0, dtype=np.float64)
            self.sums_sq = np.zeros(0, dtype=np.float64)
        self.dropped_samples = 0
        self.auto_bins_dirty = False

    def update(self, x_raw: Any, y_raw: Any) -> Json:
        last_sample = self.update_sample(x_raw, y_raw)
        return self.payload(last_sample=last_sample)

    def update_sample(self, x_raw: Any, y_raw: Any) -> Json | None:
        x = _normalize_float(x_raw)
        y = _normalize_float(y_raw)
        if x is None or y is None:
            self.dropped_samples += 1
            return None
        if self.auto_range:
            self.samples_x.append(float(x))
            self.samples_y.append(float(y))
            # For auto-range we avoid re-binning on every sample; defer to emit tick.
            self.auto_bins_dirty = True
            idx = None
            return {"x": float(x), "y": float(y), "bin_index": idx}

        idx = self._bin_index_runtime(float(x))
        if idx is None:
            self.dropped_samples += 1
            return None
        self.counts[idx] += 1
        self.sums[idx] += float(y)
        self.sums_sq[idx] += float(y) * float(y)
        return {"x": float(x), "y": float(y), "bin_index": idx}

    def _bin_index_runtime(self, x: float) -> int | None:
        if not math.isfinite(x):
            return None
        if self.counts.size <= 0:
            return None
        if self.x_min is None or self.x_max is None:
            return None
        if x < self.x_min or x > self.x_max:
            return None
        span = self.x_max - self.x_min
        if span <= 0:
            return None
        if x == self.x_max:
            return int(self.counts.size) - 1
        frac = (x - self.x_min) / span
        idx = int(frac * int(self.counts.size))
        if idx < 0 or idx >= int(self.counts.size):
            return None
        return idx

    def _recompute_auto_bins(self) -> None:
        if not self.samples_x:
            self.x_min = None
            self.x_max = None
            self.centers = np.zeros(0, dtype=np.float64)
            self.counts = np.zeros(0, dtype=np.int64)
            self.sums = np.zeros(0, dtype=np.float64)
            self.sums_sq = np.zeros(0, dtype=np.float64)
            return
        xs = np.asarray(self.samples_x, dtype=np.float64)
        ys = np.asarray(self.samples_y, dtype=np.float64)
        min_x = float(np.min(xs))
        max_x = float(np.max(xs))
        grouped: dict[str, tuple[float, list[float]]] = {}
        for x_value, y_value in zip(self.samples_x, self.samples_y):
            key = format(float(x_value), ".15g")
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = (float(x_value), [float(y_value)])
            else:
                grouped[key][1].append(float(y_value))
        unique_count = len(grouped)
        if unique_count <= int(self.max_bin_count):
            sorted_items = sorted(grouped.values(), key=lambda item: item[0])
            centers = np.asarray([item[0] for item in sorted_items], dtype=np.float64)
            counts = np.asarray(
                [len(item[1]) for item in sorted_items], dtype=np.int64
            )
            sums = np.asarray(
                [float(sum(item[1])) for item in sorted_items], dtype=np.float64
            )
            sums_sq = np.asarray(
                [float(sum((v * v) for v in item[1])) for item in sorted_items],
                dtype=np.float64,
            )
            self.centers = centers
            self.counts = counts
            self.sums = sums
            self.sums_sq = sums_sq
            self.x_min = float(min_x)
            self.x_max = float(max_x)
            return

        active_bins = max(1, min(int(self.max_bin_count), int(unique_count)))
        min_use = min_x
        max_use = max_x
        if not (max_use > min_use):
            eps = max(abs(min_use) * 1e-9, 1e-9)
            min_use -= eps
            max_use += eps
        span = max_use - min_use
        frac = np.clip((xs - min_use) / span, 0.0, 1.0)
        idx = np.minimum(np.floor(frac * active_bins).astype(np.int64), active_bins - 1)
        self.counts = np.bincount(idx, minlength=active_bins).astype(np.int64, copy=False)
        self.sums = np.bincount(idx, weights=ys, minlength=active_bins).astype(
            np.float64, copy=False
        )
        self.sums_sq = np.bincount(idx, weights=ys * ys, minlength=active_bins).astype(
            np.float64, copy=False
        )
        edges = np.linspace(min_use, max_use, active_bins + 1, dtype=np.float64)
        self.centers = (edges[:-1] + edges[1:]) * 0.5
        self.x_min = float(min_use)
        self.x_max = float(max_use)

    def payload(self, *, last_sample: Json | None) -> Json:
        if self.auto_range and self.auto_bins_dirty:
            self._recompute_auto_bins()
            self.auto_bins_dirty = False
        centers_arr = self.centers
        if centers_arr is None:
            if self.counts.size > 0 and self.x_min is not None and self.x_max is not None:
                edges = np.linspace(
                    float(self.x_min),
                    float(self.x_max),
                    int(self.counts.size) + 1,
                    dtype=np.float64,
                )
                centers = (edges[:-1] + edges[1:]) * 0.5
            else:
                centers = np.zeros(0, dtype=np.float64)
        else:
            centers = np.asarray(centers_arr, dtype=np.float64)
        if self.counts.size > 0:
            counts_f = self.counts.astype(np.float64)
            with np.errstate(divide="ignore", invalid="ignore"):
                mean = np.where(self.counts > 0, self.sums / counts_f, np.nan)
                var = np.where(self.counts > 0, self.sums_sq / counts_f - mean * mean, np.nan)
                var = np.where(var < 0, 0.0, var)
                std = np.sqrt(var)
                sem = np.where(self.counts > 0, std / np.sqrt(counts_f), np.nan)
        else:
            mean = np.zeros(0, dtype=np.float64)
            std = np.zeros(0, dtype=np.float64)
            sem = np.zeros(0, dtype=np.float64)
        out: Json = {
            "auto_range": bool(self.auto_range),
            "x_min": float(self.x_min) if self.x_min is not None else None,
            "x_max": float(self.x_max) if self.x_max is not None else None,
            "bin_count": int(self.max_bin_count),
            "active_bin_count": int(self.counts.size),
            "max_bin_count": int(self.max_bin_count),
            "populated_bin_count": int(np.count_nonzero(self.counts)),
            "x_bins": centers.tolist(),
            "count": self.counts.tolist(),
            "mean": mean.tolist(),
            "std": std.tolist(),
            "sem": sem.tolist(),
            "dropped_samples": int(self.dropped_samples),
        }
        if last_sample is not None:
            out["last_sample"] = last_sample
        return _sanitize_json(out)


@dataclass
class Bin2DStatsState:
    x_auto_range: bool
    y_auto_range: bool
    x_max_bin_count: int
    y_max_bin_count: int
    x_min: float | None
    x_max: float | None
    y_min: float | None
    y_max: float | None
    counts: np.ndarray
    sums: np.ndarray
    sums_sq: np.ndarray
    mins: np.ndarray
    maxs: np.ndarray
    samples_x: list[float]
    samples_y: list[float]
    samples_z: list[float]
    dropped_samples: int = 0
    _invalid_sample_count: int = 0
    auto_bins_dirty: bool = False

    @classmethod
    def from_params(cls, params: Json) -> Bin2DStatsState:
        x_bin_count = _normalize_int(params.get("x_bin_count"))
        y_bin_count = _normalize_int(params.get("y_bin_count"))
        if x_bin_count is None or x_bin_count <= 0:
            raise ValueError("aggregate.bin2d_stats requires x_bin_count > 0")
        if y_bin_count is None or y_bin_count <= 0:
            raise ValueError("aggregate.bin2d_stats requires y_bin_count > 0")
        x_auto_range = _normalize_bool(params.get("x_auto_range"), default=False)
        y_auto_range = _normalize_bool(params.get("y_auto_range"), default=False)
        x_min = _normalize_float(params.get("x_min"))
        x_max = _normalize_float(params.get("x_max"))
        y_min = _normalize_float(params.get("y_min"))
        y_max = _normalize_float(params.get("y_max"))
        if not x_auto_range and (x_min is None or x_max is None or x_max <= x_min):
            raise ValueError("aggregate.bin2d_stats requires x_min < x_max")
        if not y_auto_range and (y_min is None or y_max is None or y_max <= y_min):
            raise ValueError("aggregate.bin2d_stats requires y_min < y_max")

        init_x_bins = 0 if x_auto_range or y_auto_range else int(x_bin_count)
        init_y_bins = 0 if x_auto_range or y_auto_range else int(y_bin_count)
        shape = (init_x_bins, init_y_bins)
        mins = np.full(shape, np.inf, dtype=np.float64)
        maxs = np.full(shape, -np.inf, dtype=np.float64)
        return cls(
            x_auto_range=x_auto_range,
            y_auto_range=y_auto_range,
            x_max_bin_count=int(x_bin_count),
            y_max_bin_count=int(y_bin_count),
            x_min=None if x_auto_range else float(x_min),
            x_max=None if x_auto_range else float(x_max),
            y_min=None if y_auto_range else float(y_min),
            y_max=None if y_auto_range else float(y_max),
            counts=np.zeros(shape, dtype=np.int64),
            sums=np.zeros(shape, dtype=np.float64),
            sums_sq=np.zeros(shape, dtype=np.float64),
            mins=mins,
            maxs=maxs,
            samples_x=[],
            samples_y=[],
            samples_z=[],
            dropped_samples=0,
            _invalid_sample_count=0,
            auto_bins_dirty=False,
        )

    def reset(self) -> None:
        if self.x_auto_range:
            self.x_min = None
            self.x_max = None
        if self.y_auto_range:
            self.y_min = None
            self.y_max = None
        self.counts = np.zeros_like(self.counts)
        self.sums = np.zeros_like(self.sums)
        self.sums_sq = np.zeros_like(self.sums_sq)
        self.mins = np.full(self.counts.shape, np.inf, dtype=np.float64)
        self.maxs = np.full(self.counts.shape, -np.inf, dtype=np.float64)
        self.samples_x.clear()
        self.samples_y.clear()
        self.samples_z.clear()
        if self.x_auto_range or self.y_auto_range:
            self.counts = np.zeros((0, 0), dtype=np.int64)
            self.sums = np.zeros((0, 0), dtype=np.float64)
            self.sums_sq = np.zeros((0, 0), dtype=np.float64)
            self.mins = np.full((0, 0), np.inf, dtype=np.float64)
            self.maxs = np.full((0, 0), -np.inf, dtype=np.float64)
        self.dropped_samples = 0
        self._invalid_sample_count = 0
        self.auto_bins_dirty = False

    def update(self, x_raw: Any, y_raw: Any, z_raw: Any) -> Json:
        last_sample = self.update_sample(x_raw, y_raw, z_raw)
        return self.payload(last_sample=last_sample)

    @staticmethod
    def _bin_index_runtime(value: float, lo: float | None, hi: float | None, bins: int) -> int | None:
        if not math.isfinite(value):
            return None
        if lo is None or hi is None:
            return None
        if bins <= 0:
            return None
        if value < lo or value > hi:
            return None
        span = hi - lo
        if span <= 0:
            return None
        if value == hi:
            return bins - 1
        frac = (value - lo) / span
        idx = int(frac * bins)
        if idx < 0 or idx >= bins:
            return None
        return idx

    def update_sample(self, x_raw: Any, y_raw: Any, z_raw: Any) -> Json | None:
        x = _normalize_float(x_raw)
        y = _normalize_float(y_raw)
        z = _normalize_float(z_raw)
        if x is None or y is None or z is None:
            self._invalid_sample_count += 1
            self.dropped_samples += 1
            return None

        if self.x_auto_range or self.y_auto_range:
            self.samples_x.append(float(x))
            self.samples_y.append(float(y))
            self.samples_z.append(float(z))
            self.auto_bins_dirty = True
            return {"x": float(x), "y": float(y), "z": float(z), "bin_x": None, "bin_y": None}

        x_bins = int(self.counts.shape[0])
        y_bins = int(self.counts.shape[1])
        idx_x = self._bin_index_runtime(float(x), self.x_min, self.x_max, x_bins)
        idx_y = self._bin_index_runtime(float(y), self.y_min, self.y_max, y_bins)
        if idx_x is None or idx_y is None:
            self.dropped_samples += 1
            return None
        self.counts[idx_x, idx_y] += 1
        self.sums[idx_x, idx_y] += float(z)
        self.sums_sq[idx_x, idx_y] += float(z) * float(z)
        self.mins[idx_x, idx_y] = min(self.mins[idx_x, idx_y], float(z))
        self.maxs[idx_x, idx_y] = max(self.maxs[idx_x, idx_y], float(z))
        return {"x": float(x), "y": float(y), "z": float(z), "bin_x": idx_x, "bin_y": idx_y}

    def _range_from_samples(
        self,
        values: np.ndarray,
        *,
        axis: str,
        auto: bool,
        fixed_min: float | None,
        fixed_max: float | None,
    ) -> tuple[float | None, float | None, int]:
        if axis not in {"x", "y"}:
            raise ValueError("axis must be 'x' or 'y'")
        max_bins = self.x_max_bin_count if axis == "x" else self.y_max_bin_count
        if values.size <= 0:
            if auto:
                return None, None, 0
            return fixed_min, fixed_max, 0
        if auto:
            lo = float(np.min(values))
            hi = float(np.max(values))
            if not (hi > lo):
                eps = max(abs(lo) * 1e-9, 1e-9)
                lo -= eps
                hi += eps
            unique_count = len({format(float(v), ".15g") for v in values.tolist()})
            active_bins = max(1, min(int(max_bins), int(unique_count)))
            return lo, hi, active_bins
        if fixed_min is None or fixed_max is None or fixed_max <= fixed_min:
            return None, None, 0
        return float(fixed_min), float(fixed_max), int(max_bins)

    def _recompute_auto_bins(self) -> None:
        if not self.samples_x:
            self.x_min = None if self.x_auto_range else self.x_min
            self.x_max = None if self.x_auto_range else self.x_max
            self.y_min = None if self.y_auto_range else self.y_min
            self.y_max = None if self.y_auto_range else self.y_max
            self.counts = np.zeros((0, 0), dtype=np.int64)
            self.sums = np.zeros((0, 0), dtype=np.float64)
            self.sums_sq = np.zeros((0, 0), dtype=np.float64)
            self.mins = np.full((0, 0), np.inf, dtype=np.float64)
            self.maxs = np.full((0, 0), -np.inf, dtype=np.float64)
            self.dropped_samples = int(self._invalid_sample_count)
            return

        xs = np.asarray(self.samples_x, dtype=np.float64)
        ys = np.asarray(self.samples_y, dtype=np.float64)
        zs = np.asarray(self.samples_z, dtype=np.float64)
        if xs.size <= 0 or ys.size <= 0 or zs.size <= 0:
            self.counts = np.zeros((0, 0), dtype=np.int64)
            self.sums = np.zeros((0, 0), dtype=np.float64)
            self.sums_sq = np.zeros((0, 0), dtype=np.float64)
            self.mins = np.full((0, 0), np.inf, dtype=np.float64)
            self.maxs = np.full((0, 0), -np.inf, dtype=np.float64)
            self.dropped_samples = int(self._invalid_sample_count)
            return

        x_min_use, x_max_use, x_bins = self._range_from_samples(
            xs,
            axis="x",
            auto=self.x_auto_range,
            fixed_min=self.x_min,
            fixed_max=self.x_max,
        )
        y_min_use, y_max_use, y_bins = self._range_from_samples(
            ys,
            axis="y",
            auto=self.y_auto_range,
            fixed_min=self.y_min,
            fixed_max=self.y_max,
        )
        if x_bins <= 0 or y_bins <= 0 or x_min_use is None or x_max_use is None or y_min_use is None or y_max_use is None:
            self.counts = np.zeros((0, 0), dtype=np.int64)
            self.sums = np.zeros((0, 0), dtype=np.float64)
            self.sums_sq = np.zeros((0, 0), dtype=np.float64)
            self.mins = np.full((0, 0), np.inf, dtype=np.float64)
            self.maxs = np.full((0, 0), -np.inf, dtype=np.float64)
            self.dropped_samples = int(self._invalid_sample_count + xs.size)
            return

        x_span = x_max_use - x_min_use
        y_span = y_max_use - y_min_use
        x_frac = (xs - x_min_use) / x_span
        y_frac = (ys - y_min_use) / y_span
        valid = (
            np.isfinite(xs)
            & np.isfinite(ys)
            & np.isfinite(zs)
            & (x_frac >= 0.0)
            & (x_frac <= 1.0)
            & (y_frac >= 0.0)
            & (y_frac <= 1.0)
        )
        x_idx = np.minimum(np.floor(np.clip(x_frac, 0.0, 1.0) * x_bins).astype(np.int64), x_bins - 1)
        y_idx = np.minimum(np.floor(np.clip(y_frac, 0.0, 1.0) * y_bins).astype(np.int64), y_bins - 1)

        counts = np.zeros((x_bins, y_bins), dtype=np.int64)
        sums = np.zeros((x_bins, y_bins), dtype=np.float64)
        sums_sq = np.zeros((x_bins, y_bins), dtype=np.float64)
        mins = np.full((x_bins, y_bins), np.inf, dtype=np.float64)
        maxs = np.full((x_bins, y_bins), -np.inf, dtype=np.float64)

        if np.any(valid):
            xv = x_idx[valid]
            yv = y_idx[valid]
            zv = zs[valid]
            np.add.at(counts, (xv, yv), 1)
            np.add.at(sums, (xv, yv), zv)
            np.add.at(sums_sq, (xv, yv), zv * zv)
            for bx, by, v in zip(xv.tolist(), yv.tolist(), zv.tolist()):
                mins[bx, by] = min(mins[bx, by], float(v))
                maxs[bx, by] = max(maxs[bx, by], float(v))

        self.x_min = float(x_min_use)
        self.x_max = float(x_max_use)
        self.y_min = float(y_min_use)
        self.y_max = float(y_max_use)
        self.counts = counts
        self.sums = sums
        self.sums_sq = sums_sq
        self.mins = mins
        self.maxs = maxs
        dropped_from_range = int(np.count_nonzero(~valid))
        self.dropped_samples = int(self._invalid_sample_count + dropped_from_range)

    def payload(self, *, last_sample: Json | None) -> Json:
        if (self.x_auto_range or self.y_auto_range) and self.auto_bins_dirty:
            self._recompute_auto_bins()
            self.auto_bins_dirty = False

        x_bins = int(self.counts.shape[0])
        y_bins = int(self.counts.shape[1])
        if x_bins > 0 and y_bins > 0 and self.x_min is not None and self.x_max is not None:
            x_edges = np.linspace(float(self.x_min), float(self.x_max), x_bins + 1, dtype=np.float64)
            x_centers = (x_edges[:-1] + x_edges[1:]) * 0.5
        else:
            x_centers = np.zeros(0, dtype=np.float64)
        if x_bins > 0 and y_bins > 0 and self.y_min is not None and self.y_max is not None:
            y_edges = np.linspace(float(self.y_min), float(self.y_max), y_bins + 1, dtype=np.float64)
            y_centers = (y_edges[:-1] + y_edges[1:]) * 0.5
        else:
            y_centers = np.zeros(0, dtype=np.float64)

        counts_f = self.counts.astype(np.float64, copy=False) if self.counts.size > 0 else np.zeros((0, 0), dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(self.counts > 0, self.sums / counts_f, np.nan)
            var = np.where(self.counts > 0, self.sums_sq / counts_f - mean * mean, np.nan)
            var = np.where(var < 0, 0.0, var)
            std = np.sqrt(var)
            sem = np.where(self.counts > 0, std / np.sqrt(counts_f), np.nan)
            min_grid = np.where(self.counts > 0, self.mins, np.nan)
            max_grid = np.where(self.counts > 0, self.maxs, np.nan)

        out: Json = {
            "x_auto_range": bool(self.x_auto_range),
            "y_auto_range": bool(self.y_auto_range),
            "x_min": float(self.x_min) if self.x_min is not None else None,
            "x_max": float(self.x_max) if self.x_max is not None else None,
            "y_min": float(self.y_min) if self.y_min is not None else None,
            "y_max": float(self.y_max) if self.y_max is not None else None,
            "x_bin_count": int(self.x_max_bin_count),
            "y_bin_count": int(self.y_max_bin_count),
            "x_active_bin_count": int(x_bins),
            "y_active_bin_count": int(y_bins),
            "x_max_bin_count": int(self.x_max_bin_count),
            "y_max_bin_count": int(self.y_max_bin_count),
            "populated_bin_count": int(np.count_nonzero(self.counts)),
            "x_bins": x_centers.tolist(),
            "y_bins": y_centers.tolist(),
            "count": self.counts.tolist(),
            "sum": self.sums.tolist(),
            "mean": mean.tolist(),
            "std": std.tolist(),
            "sem": sem.tolist(),
            "min": min_grid.tolist(),
            "max": max_grid.tolist(),
            "dropped_samples": int(self.dropped_samples),
        }
        if last_sample is not None:
            out["last_sample"] = last_sample
        return _sanitize_json(out)


@dataclass
class TraceRollingMeanState:
    window_traces: int
    traces: deque[np.ndarray]
    sum_trace: np.ndarray | None

    @classmethod
    def from_params(cls, params: Json) -> TraceRollingMeanState:
        window = _normalize_int(params.get("window_traces"))
        if window is None or window <= 0:
            raise ValueError("trace.rolling_mean requires window_traces > 0")
        return cls(
            window_traces=int(window),
            traces=deque(),
            sum_trace=None,
        )

    def reset(self) -> None:
        self.traces.clear()
        self.sum_trace = None

    def update(self, trace_raw: Any) -> np.ndarray | None:
        trace = _coerce_trace(trace_raw)
        if trace is None:
            return None
        trace = trace.astype(np.float64, copy=False)
        if trace.size <= 0:
            return np.asarray([], dtype=np.float64)
        if self.window_traces <= 1:
            return trace
        if self.sum_trace is None or int(self.sum_trace.size) != int(trace.size):
            self.traces.clear()
            self.sum_trace = np.zeros(int(trace.size), dtype=np.float64)
        incoming = trace.astype(np.float64, copy=True)
        assert self.sum_trace is not None
        if len(self.traces) >= int(self.window_traces):
            oldest = self.traces.popleft()
            self.sum_trace -= oldest
        self.traces.append(incoming)
        self.sum_trace += incoming
        denom = max(1, len(self.traces))
        return self.sum_trace / float(denom)


@dataclass
class FitCurve1DState:
    model: str
    baseline_mode: str
    every_n: int
    sigma_y: float | None = None
    dense_eval_points: int | None = None
    sample_count: int = 0
    last_fit: dict[str, Any] | None = None

    @classmethod
    def from_params(cls, params: Json) -> FitCurve1DState:
        model, baseline_mode, every_n, sigma_y, dense_eval_points = (
            _validate_fit_curve_params(params)
        )
        return cls(
            model=model,
            baseline_mode=baseline_mode,
            every_n=every_n,
            sigma_y=sigma_y,
            dense_eval_points=dense_eval_points,
        )

    def reset(self) -> None:
        self.sample_count = 0
        self.last_fit = None


OPS: dict[str, OpSpec] = {
    "source.stream": OpSpec(input_types={}, output_type="trace", stateful=False),
    "source.context_field": OpSpec(input_types={}, output_type="scalar", stateful=False),
    "source.telemetry_nearest": OpSpec(
        input_types={}, output_type="scalar", stateful=False
    ),
    "scalar.add": OpSpec(
        input_types={"a": "scalar", "b": "scalar"}, output_type="scalar", stateful=False
    ),
    "scalar.subtract": OpSpec(
        input_types={"a": "scalar", "b": "scalar"}, output_type="scalar", stateful=False
    ),
    "scalar.multiply": OpSpec(
        input_types={"a": "scalar", "b": "scalar"}, output_type="scalar", stateful=False
    ),
    "scalar.divide": OpSpec(
        input_types={"a": "scalar", "b": "scalar"}, output_type="scalar", stateful=False
    ),
    "scalar.threshold": OpSpec(
        input_types={"x": "scalar"}, output_type="scalar", stateful=False
    ),
    "trace.divide": OpSpec(
        input_types={"a": "trace", "b": "trace"}, output_type="trace", stateful=False
    ),
    "trace.add_scalar": OpSpec(
        input_types={"trace": "trace", "scalar": "scalar"},
        output_type="trace",
        stateful=False,
    ),
    "trace.subtract_scalar": OpSpec(
        input_types={"trace": "trace", "scalar": "scalar"},
        output_type="trace",
        stateful=False,
    ),
    "trace.multiply_scalar": OpSpec(
        input_types={"trace": "trace", "scalar": "scalar"},
        output_type="trace",
        stateful=False,
    ),
    "trace.divide_scalar": OpSpec(
        input_types={"trace": "trace", "scalar": "scalar"},
        output_type="trace",
        stateful=False,
    ),
    "trace.rolling_mean": OpSpec(
        input_types={"trace": "trace"},
        output_type="trace",
        stateful=True,
    ),
    "trace.decimate": OpSpec(
        input_types={"trace": "trace"},
        output_type="trace",
        stateful=False,
    ),
    "trace.crop": OpSpec(input_types={"trace": "trace"}, output_type="trace", stateful=False),
    "trace.subtract_background": OpSpec(
        input_types={"trace": "trace"}, output_type="trace", stateful=False
    ),
    "trace.integrate": OpSpec(
        input_types={"trace": "trace"}, output_type="scalar", stateful=False
    ),
    "fit.curve_1d": OpSpec(
        input_types={"x": "trace", "y": "trace"},
        output_type="fit_1d",
        optional_input_types={"gate": "scalar"},
        stateful=True,
    ),
    "fit.yhat": OpSpec(
        input_types={"fit": "fit_1d"}, output_type="trace", stateful=False
    ),
    "fit.xhat": OpSpec(
        input_types={"fit": "fit_1d"}, output_type="trace", stateful=False
    ),
    "fit.yhat_dense": OpSpec(
        input_types={"fit": "fit_1d"}, output_type="trace", stateful=False
    ),
    "fit.xhat_dense": OpSpec(
        input_types={"fit": "fit_1d"}, output_type="trace", stateful=False
    ),
    "fit.param": OpSpec(
        input_types={"fit": "fit_1d"}, output_type="scalar", stateful=False
    ),
    "fit.params": OpSpec(
        input_types={"fit": "fit_1d"}, output_type="params_map", stateful=False
    ),
    "fit.from_hist_agg": OpSpec(
        input_types={"hist": "hist_agg"},
        output_type="fit_1d",
        optional_input_types={"gate": "scalar"},
        stateful=True,
    ),
    "aggregate.bin_stats": OpSpec(
        input_types={"x": "scalar", "y": "scalar"},
        output_type="hist_agg",
        optional_input_types={"gate": "scalar"},
        stateful=True,
    ),
    "aggregate.bin2d_stats": OpSpec(
        input_types={"x": "scalar", "y": "scalar", "z": "scalar"},
        output_type="hist2d",
        optional_input_types={"gate": "scalar"},
        stateful=True,
    ),
}


OP_PARAM_SCHEMAS: dict[str, list[Json]] = {
    "source.stream": [
        {"name": "device_id", "kind": "string", "required": True},
        {"name": "stream", "kind": "string", "required": True},
        {
            "name": "channel_mode",
            "kind": "string",
            "required": False,
            "default": "single",
        },
        {"name": "channel_index", "kind": "integer", "required": False, "default": 0},
        {
            "name": "channel_indices",
            "kind": "string",
            "required": False,
            "default": "",
        },
    ],
    "source.context_field": [
        {"name": "field", "kind": "string", "required": True},
    ],
    "source.telemetry_nearest": [
        {"name": "device_id", "kind": "string", "required": True},
        {"name": "signal", "kind": "string", "required": True},
        {"name": "max_dt_s", "kind": "number", "required": False, "default": 2.0},
    ],
    "scalar.threshold": [
        {"name": "threshold", "kind": "number", "required": True},
        {"name": "mode", "kind": "string", "required": False, "default": "gt"},
    ],
    "trace.rolling_mean": [
        {"name": "window_traces", "kind": "integer", "required": True, "default": 1},
    ],
    "trace.decimate": [
        {"name": "method", "kind": "string", "required": False, "default": "minmax"},
        {"name": "target_points", "kind": "integer", "required": True},
    ],
    "trace.crop": [
        {"name": "start_idx", "kind": "integer", "required": False, "default": 0},
        {"name": "stop_idx", "kind": "integer", "required": False},
    ],
    "trace.subtract_background": [
        {"name": "bg_start_idx", "kind": "integer", "required": True},
        {"name": "bg_stop_idx", "kind": "integer", "required": True},
    ],
    "fit.curve_1d": [
        {"name": "model", "kind": "string", "required": False, "default": "gaussian"},
        {
            "name": "baseline_mode",
            "kind": "string",
            "required": False,
            "default": "none",
        },
        {"name": "every_n", "kind": "integer", "required": False, "default": 1},
        {"name": "sigma_y", "kind": "number", "required": False},
        {"name": "dense_eval_points", "kind": "integer", "required": False},
    ],
    "fit.yhat": [],
    "fit.xhat": [],
    "fit.yhat_dense": [],
    "fit.xhat_dense": [],
    "fit.param": [
        {"name": "name", "kind": "string", "required": False, "default": "center"},
        {"name": "field", "kind": "string", "required": False, "default": "value"},
    ],
    "fit.params": [],
    "fit.from_hist_agg": [
        {"name": "y_source", "kind": "string", "required": False, "default": "mean"},
        {"name": "model", "kind": "string", "required": False, "default": "gaussian"},
        {
            "name": "baseline_mode",
            "kind": "string",
            "required": False,
            "default": "none",
        },
        {"name": "every_n", "kind": "integer", "required": False, "default": 1},
        {"name": "sigma_y", "kind": "number", "required": False},
        {"name": "dense_eval_points", "kind": "integer", "required": False},
        {
            "name": "chi2_sigma_source",
            "kind": "string",
            "required": False,
            "default": "sem",
        },
        {"name": "min_count", "kind": "integer", "required": False, "default": 1},
        {"name": "x_min", "kind": "number", "required": False},
        {"name": "x_max", "kind": "number", "required": False},
    ],
    "aggregate.bin_stats": [
        {"name": "auto_range", "kind": "boolean", "required": False, "default": False},
        {"name": "x_min", "kind": "number", "required": False},
        {"name": "x_max", "kind": "number", "required": False},
        {"name": "bin_count", "kind": "integer", "required": True},
    ],
    "aggregate.bin2d_stats": [
        {"name": "x_auto_range", "kind": "boolean", "required": False, "default": False},
        {"name": "x_min", "kind": "number", "required": False},
        {"name": "x_max", "kind": "number", "required": False},
        {"name": "x_bin_count", "kind": "integer", "required": True},
        {"name": "y_auto_range", "kind": "boolean", "required": False, "default": False},
        {"name": "y_min", "kind": "number", "required": False},
        {"name": "y_max", "kind": "number", "required": False},
        {"name": "y_bin_count", "kind": "integer", "required": True},
    ],
}


def operator_catalog_payload() -> list[Json]:
    out: list[Json] = []
    for op_id in sorted(OPS.keys()):
        spec = OPS[op_id]
        out.append(
            {
                "op": op_id,
                "inputs": dict(spec.input_types),
                "optional_inputs": dict(spec.optional_input_types),
                "output_type": spec.output_type,
                "stateful": bool(spec.stateful),
                "params": list(OP_PARAM_SCHEMAS.get(op_id, [])),
            }
        )
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("experiment_control stream_analysis")
    add_manager_args(p)
    add_process_id_arg(p, default="stream_analysis")
    add_rpc_timeout_arg(p, default_ms=2000)
    add_heartbeat_args(p, default_period_s=1.0)
    p.add_argument("--process-data-endpoint", type=str, default=None)
    p.add_argument("--workspace-store-path", type=str, default=None)
    p.add_argument("--max-payload-points", type=int, default=200_000)
    p.add_argument("--max-events-per-cycle", type=int, default=12)
    p.add_argument("--max-hist-output-hz", type=float, default=20.0)
    p.add_argument("--max-trace-output-hz", type=float, default=10.0)
    p.add_argument("--rcvhwm", type=int, default=20_000)
    return p.parse_args(argv)


def _normalize_id(raw: Any) -> str | None:
    text = str(raw or "").strip()
    return text or None


def _normalize_int(raw: Any) -> int | None:
    try:
        return int(raw)
    except Exception:
        return None


def _normalize_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return value


def _normalize_bool(raw: Any, *, default: bool = False) -> bool:
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return [_sanitize_json(item) for item in value.tolist()]
    if isinstance(value, np.floating):
        value = float(value)
    elif isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return None
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {k: _sanitize_json(v) for k, v in value.items()}
    return value


def _coerce_trace(trace_raw: Any) -> np.ndarray | None:
    if trace_raw is None:
        return None
    arr = np.asarray(trace_raw)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    arr = arr.reshape(-1)
    try:
        arr = arr.astype(np.float64, copy=False)
    except Exception:
        return None
    if arr.size <= 0:
        return np.asarray([], dtype=np.float64)
    return arr


def _parse_trace_decimator(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"stride", "mean", "m4"}:
        return value
    return "minmax"


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


def _validate_trace_decimate_params(params: Json) -> tuple[str, int]:
    method = _parse_trace_decimator(params.get("method"))
    target_points = _normalize_int(params.get("target_points"))
    if target_points is None or target_points <= 0:
        raise ValueError("trace.decimate requires target_points > 0")
    target_points = max(4, min(200_000, int(target_points)))
    return method, target_points


def _as_channel_matrix(array: np.ndarray) -> np.ndarray:
    arr = np.asarray(array)
    if arr.ndim == 0:
        return arr.reshape(1, 1).astype(np.float64, copy=False)
    if arr.ndim == 1:
        return arr.reshape(1, -1).astype(np.float64, copy=False)
    if arr.ndim == 2:
        rows, cols = int(arr.shape[0]), int(arr.shape[1])
        if rows <= 1 or cols <= 1:
            return arr.reshape(1, -1).astype(np.float64, copy=False)
        if rows <= cols:
            return arr.astype(np.float64, copy=False)
        return arr.T.astype(np.float64, copy=False)
    return arr.reshape(1, -1).astype(np.float64, copy=False)


def _parse_channel_indices(raw: Any) -> list[int] | None:
    if raw is None:
        return None
    items: list[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return None
        items = [part for part in re.split(r"[,\s;]+", text) if part]
    out: list[int] = []
    seen: set[int] = set()
    for item in items:
        idx = _normalize_int(item)
        if idx is None or idx < 0:
            continue
        if idx in seen:
            continue
        seen.add(idx)
        out.append(int(idx))
    if not out:
        return None
    return out


def _parse_stream_source_mode(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"average", "mean"}:
        return "average"
    if value == "sum":
        return "sum"
    return "single"


def _select_trace(array: np.ndarray, channel_index: int) -> tuple[np.ndarray, int, int]:
    matrix = _as_channel_matrix(array)
    channel_count = max(1, int(matrix.shape[0]))
    idx = max(0, min(int(channel_index), channel_count - 1))
    return matrix[idx, :], channel_count, idx


def _reduce_trace_channels(
    array: np.ndarray,
    *,
    reducer: str,
    channel_indices_raw: Any,
) -> tuple[np.ndarray, int, list[int]]:
    matrix = _as_channel_matrix(array)
    channel_count = max(1, int(matrix.shape[0]))
    requested = _parse_channel_indices(channel_indices_raw)
    if requested is None:
        selected = list(range(channel_count))
    else:
        selected = [idx for idx in requested if 0 <= idx < channel_count]
        if not selected:
            selected = list(range(channel_count))
    subset = matrix[np.asarray(selected, dtype=np.int64), :]
    if reducer == "sum":
        reduced = np.sum(subset, axis=0, dtype=np.float64)
    else:
        reduced = np.mean(subset, axis=0, dtype=np.float64)
    return np.asarray(reduced, dtype=np.float64).reshape(-1), channel_count, selected


def execute_trace_crop(trace_raw: Any, params: Json) -> np.ndarray | None:
    trace = _coerce_trace(trace_raw)
    if trace is None:
        return None
    n = int(trace.size)
    start = _normalize_int(params.get("start_idx"))
    stop = _normalize_int(params.get("stop_idx"))
    if start is None:
        start = 0
    if stop is None:
        stop = n
    start = max(0, min(start, n))
    stop = max(0, min(stop, n))
    if stop <= start:
        return np.asarray([], dtype=np.float64)
    return trace[start:stop]


def execute_trace_subtract_background(trace_raw: Any, params: Json) -> np.ndarray | None:
    trace = _coerce_trace(trace_raw)
    if trace is None:
        return None
    bg_start = _normalize_int(params.get("bg_start_idx"))
    bg_stop = _normalize_int(params.get("bg_stop_idx"))
    n = int(trace.size)
    if bg_start is None or bg_stop is None:
        return trace
    bg_start = max(0, min(bg_start, n))
    bg_stop = max(0, min(bg_stop, n))
    if bg_stop <= bg_start:
        return trace
    window = trace[bg_start:bg_stop]
    if window.size <= 0:
        return trace
    bg = float(np.mean(window, dtype=np.float64))
    return trace - bg


def execute_trace_integrate(trace_raw: Any) -> float | None:
    trace = _coerce_trace(trace_raw)
    if trace is None:
        return None
    return float(np.sum(trace, dtype=np.float64))


def execute_trace_decimate(trace_raw: Any, params: Json) -> np.ndarray | None:
    trace = _coerce_trace(trace_raw)
    if trace is None:
        return None
    method, target_points = _validate_trace_decimate_params(params)
    n = int(trace.size)
    if n <= 0 or n <= target_points:
        return trace
    points = trace.astype(np.float64, copy=False)

    if method == "stride":
        step = max(1, int(math.ceil(float(n) / float(target_points))))
        out = points[::step]
        if out.size > 0 and float(out[-1]) != float(points[-1]):
            out = np.concatenate([out, points[-1:]])
        return out[:target_points]

    if method == "mean":
        bucket_count = max(1, min(target_points, n))
        out = np.zeros(bucket_count, dtype=np.float64)
        idx = 0
        for start, stop in _bucket_ranges(n, bucket_count):
            chunk = points[start:stop]
            if chunk.size <= 0:
                continue
            out[idx] = float(np.mean(chunk, dtype=np.float64))
            idx += 1
        return out[:idx]

    if method == "m4":
        bucket_count = max(1, min(max(1, target_points // 4), n))
        out = np.zeros(target_points, dtype=np.float64)
        out_count = 0
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
                out[out_count] = float(points[idx])
                out_count += 1
                if out_count >= target_points:
                    return out[:out_count]
        return out[:out_count]

    # default: minmax
    bucket_count = max(1, min(max(1, target_points // 2), n))
    out = np.zeros(target_points, dtype=np.float64)
    out_count = 0
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
            out[out_count] = float(points[min_i])
            out_count += 1
            if max_i != min_i and out_count < target_points:
                out[out_count] = float(points[max_i])
                out_count += 1
        else:
            out[out_count] = float(points[max_i])
            out_count += 1
            if max_i != min_i and out_count < target_points:
                out[out_count] = float(points[min_i])
                out_count += 1
        if out_count >= target_points:
            return out[:out_count]
    return out[:out_count]


def execute_trace_divide(a_raw: Any, b_raw: Any) -> np.ndarray | None:
    a = _coerce_trace(a_raw)
    b = _coerce_trace(b_raw)
    if a is None or b is None:
        return None
    if int(a.size) != int(b.size):
        return None
    out = np.zeros_like(a, dtype=np.float64)
    mask = np.isfinite(a) & np.isfinite(b) & (b != 0.0)
    out[mask] = a[mask] / b[mask]
    return out


def execute_trace_scalar_math(trace_raw: Any, scalar_raw: Any, *, op: str) -> np.ndarray | None:
    trace = _coerce_trace(trace_raw)
    scalar = _normalize_float(scalar_raw)
    if trace is None or scalar is None:
        return None
    if op == "add":
        return trace + float(scalar)
    if op == "subtract":
        return trace - float(scalar)
    if op == "multiply":
        return trace * float(scalar)
    if op == "divide":
        if scalar == 0.0:
            return np.zeros_like(trace, dtype=np.float64)
        return trace / float(scalar)
    return None


def execute_scalar_binary(a_raw: Any, b_raw: Any, *, op: str) -> float | None:
    a = _normalize_float(a_raw)
    b = _normalize_float(b_raw)
    if a is None or b is None:
        return None
    if op == "add":
        return float(a + b)
    if op == "subtract":
        return float(a - b)
    if op == "multiply":
        return float(a * b)
    if op == "divide":
        if b == 0.0:
            return None
        return float(a / b)
    return None


def _parse_threshold_mode(raw: Any) -> str:
    mode = str(raw if raw is not None else "gt").strip().lower()
    aliases = {
        ">": "gt",
        "gt": "gt",
        ">=": "gte",
        "ge": "gte",
        "gte": "gte",
        "<": "lt",
        "lt": "lt",
        "<=": "lte",
        "le": "lte",
        "lte": "lte",
    }
    normalized = aliases.get(mode)
    if normalized is None:
        raise ValueError(
            "scalar.threshold mode must be one of gt, gte, lt, lte, >, >=, <, <="
        )
    return normalized


def _validate_scalar_threshold_params(params: Json) -> tuple[float, str]:
    threshold = _normalize_float(params.get("threshold"))
    if threshold is None:
        raise ValueError("scalar.threshold requires numeric threshold")
    mode = _parse_threshold_mode(params.get("mode", "gt"))
    return float(threshold), mode


def execute_scalar_threshold(
    x_raw: Any,
    *,
    threshold: float,
    mode: str,
) -> float | None:
    x = _normalize_float(x_raw)
    if x is None:
        return None
    if mode == "gt":
        passed = x > threshold
    elif mode == "gte":
        passed = x >= threshold
    elif mode == "lt":
        passed = x < threshold
    elif mode == "lte":
        passed = x <= threshold
    else:
        return None
    return 1.0 if passed else 0.0


def _parse_fit_model(raw: Any) -> str:
    model = str(raw if raw is not None else "gaussian").strip().lower()
    if model in {"gaussian", "lorentzian"}:
        return model
    raise ValueError("fit.curve_1d model must be one of gaussian, lorentzian")


def _parse_fit_baseline_mode(raw: Any) -> str:
    mode = str(raw if raw is not None else "none").strip().lower()
    if mode in {"none", "constant", "linear"}:
        return mode
    raise ValueError("fit.curve_1d baseline_mode must be one of none, constant, linear")


def _validate_fit_curve_params(
    params: Json,
) -> tuple[str, str, int, float | None, int | None]:
    model = _parse_fit_model(params.get("model"))
    baseline_mode = _parse_fit_baseline_mode(params.get("baseline_mode"))
    every_n = _normalize_int(params.get("every_n"))
    if every_n is None:
        every_n = 1
    if every_n <= 0:
        raise ValueError("fit.curve_1d requires every_n >= 1")
    sigma_y = _normalize_float(params.get("sigma_y"))
    if sigma_y is not None and sigma_y <= 0:
        raise ValueError("fit.curve_1d requires sigma_y > 0 when provided")
    dense_eval_points = _normalize_int(params.get("dense_eval_points"))
    if dense_eval_points is not None and dense_eval_points < 2:
        raise ValueError("fit.curve_1d requires dense_eval_points >= 2 when provided")
    return model, baseline_mode, int(every_n), sigma_y, dense_eval_points


def _fit_curve_build_models(
    *,
    model: str,
    baseline_mode: str,
) -> tuple[Any, Any, list[str]]:
    def _model_gaussian(x: np.ndarray, amp: float, center: float, sigma: float) -> np.ndarray:
        sigma_eff = max(abs(float(sigma)), 1e-18)
        z = (x - float(center)) / sigma_eff
        return float(amp) * np.exp(-0.5 * z * z)

    def _model_lorentzian(x: np.ndarray, amp: float, center: float, gamma: float) -> np.ndarray:
        gamma_eff = max(abs(float(gamma)), 1e-18)
        d = x - float(center)
        return float(amp) * (gamma_eff * gamma_eff) / (d * d + gamma_eff * gamma_eff)

    if model == "gaussian":
        core = _model_gaussian
        names = ["amplitude", "center", "sigma"]
    else:
        core = _model_lorentzian
        names = ["amplitude", "center", "gamma"]

    if baseline_mode == "none":
        return core, core, names

    if baseline_mode == "constant":
        def func(x: np.ndarray, *p: float) -> np.ndarray:
            return core(x, float(p[0]), float(p[1]), float(p[2])) + float(p[3])

        return func, func, names + ["baseline_const"]

    def func(x: np.ndarray, *p: float) -> np.ndarray:
        return (
            core(x, float(p[0]), float(p[1]), float(p[2]))
            + float(p[3])
            + float(p[4]) * x
        )

    return func, func, names + ["baseline_const", "baseline_slope"]


def _fit_curve_initial_guess(
    *,
    x: np.ndarray,
    y: np.ndarray,
    model: str,
    baseline_mode: str,
) -> np.ndarray:
    x_min = float(np.min(x))
    x_max = float(np.max(x))
    span = max(x_max - x_min, 1e-12)
    y_med = float(np.median(y))
    y_max = float(np.max(y))
    y_min = float(np.min(y))
    amp_guess = y_max - y_med
    if abs(amp_guess) < 1e-12:
        amp_guess = y_max - y_min
    if abs(amp_guess) < 1e-12:
        amp_guess = float(np.max(np.abs(y))) if y.size > 0 else 1.0
    if abs(amp_guess) < 1e-12:
        amp_guess = 1.0
    center_guess = float(x[int(np.argmax(y))])
    width_guess = span / 8.0 if model == "gaussian" else span / 10.0
    width_guess = max(width_guess, 1e-9)
    if baseline_mode == "none":
        return np.asarray([amp_guess, center_guess, width_guess], dtype=np.float64)
    if baseline_mode == "constant":
        return np.asarray([amp_guess, center_guess, width_guess, y_med], dtype=np.float64)
    slope_guess = (float(y[-1]) - float(y[0])) / span if y.size >= 2 else 0.0
    return np.asarray(
        [amp_guess, center_guess, width_guess, y_med, slope_guess], dtype=np.float64
    )


def _fit_curve_run(
    *,
    x_raw: Any,
    y_raw: Any,
    model: str,
    baseline_mode: str,
    sigma_y: float | None = None,
    sigma_trace_raw: Any = None,
    dense_eval_points: int | None = None,
) -> dict[str, Any] | None:
    x = _coerce_trace(x_raw)
    y = _coerce_trace(y_raw)
    if x is None or y is None:
        return None
    if int(x.size) != int(y.size):
        return None
    if int(x.size) < 4:
        return None
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    y = np.asarray(y, dtype=np.float64).reshape(-1)
    mask = np.isfinite(x) & np.isfinite(y)
    if not np.any(mask):
        return None
    x = x[mask]
    y = y[mask]
    if int(x.size) < 4:
        return None
    if curve_fit is None:
        return None

    fit_func, eval_func, param_names = _fit_curve_build_models(
        model=model,
        baseline_mode=baseline_mode,
    )
    p0 = _fit_curve_initial_guess(
        x=x,
        y=y,
        model=model,
        baseline_mode=baseline_mode,
    )
    try:
        popt, pcov = curve_fit(fit_func, x, y, p0=p0, maxfev=8000)
    except Exception:
        return None
    yhat = np.asarray(eval_func(x, *popt), dtype=np.float64).reshape(-1)
    x_dense: np.ndarray | None = None
    yhat_dense: np.ndarray | None = None
    if dense_eval_points is not None and dense_eval_points >= 2:
        x_dense = np.linspace(
            float(np.min(x)),
            float(np.max(x)),
            int(dense_eval_points),
            dtype=np.float64,
        )
        yhat_dense = np.asarray(eval_func(x_dense, *popt), dtype=np.float64).reshape(-1)
    params = {name: float(val) for name, val in zip(param_names, popt.tolist())}
    stderr: dict[str, float] = {}
    if isinstance(pcov, np.ndarray) and pcov.ndim == 2:
        diag = np.diag(pcov)
        for i, name in enumerate(param_names):
            if i >= int(diag.size):
                break
            var = float(diag[i])
            if math.isfinite(var) and var >= 0.0:
                stderr[name] = float(math.sqrt(var))
    # Reduced chi^2 is only meaningful when a noise scale is available.
    if x.size > 0 and y.size == yhat.size:
        resid = y - yhat
        dof = int(y.size) - int(len(param_names))
        if dof > 0:
            sigma_vec: np.ndarray | None = None
            sigma_trace = _coerce_trace(sigma_trace_raw)
            if sigma_trace is not None and int(sigma_trace.size) == int(y.size):
                sigma_vec = np.asarray(sigma_trace, dtype=np.float64).reshape(-1)
            elif sigma_y is not None and sigma_y > 0:
                sigma_vec = np.full(int(y.size), float(sigma_y), dtype=np.float64)
            if sigma_vec is not None:
                valid = np.isfinite(sigma_vec) & (sigma_vec > 0)
                if np.any(valid):
                    w_resid = resid[valid] / sigma_vec[valid]
                    if w_resid.size > 0:
                        chi2 = float(np.sum(w_resid * w_resid, dtype=np.float64))
                        if math.isfinite(chi2):
                            params["reduced_chi2"] = float(chi2 / float(dof))
    params["model"] = model
    params["baseline_mode"] = baseline_mode
    out: dict[str, Any] = {
        "x": x,
        "yhat": yhat,
        "params": params,
        "stderr": stderr,
    }
    if x_dense is not None and yhat_dense is not None and x_dense.size == yhat_dense.size:
        out["x_dense"] = x_dense
        out["yhat_dense"] = yhat_dense
    return out


def execute_fit_curve_1d(
    *,
    state: FitCurve1DState,
    x_raw: Any,
    y_raw: Any,
    gate_raw: Any,
) -> dict[str, Any] | None:
    if not _gate_open(gate_raw, default=True):
        return state.last_fit
    state.sample_count += 1
    every_n = max(1, int(state.every_n))
    should_fit = state.sample_count == 1 or (state.sample_count % every_n == 0)
    if not should_fit:
        return state.last_fit
    fit_result = _fit_curve_run(
        x_raw=x_raw,
        y_raw=y_raw,
        model=state.model,
        baseline_mode=state.baseline_mode,
        sigma_y=state.sigma_y,
        dense_eval_points=state.dense_eval_points,
    )
    if fit_result is not None:
        state.last_fit = fit_result
    return state.last_fit


def execute_fit_yhat(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("yhat"))


def execute_fit_xhat(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("x"))


def execute_fit_yhat_dense(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("yhat_dense"))


def execute_fit_xhat_dense(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("x_dense"))


def _normalize_fit_param_name(raw: Any) -> str:
    text = str(raw if raw is not None else "center").strip().lower()
    aliases = {
        "amp": "amplitude",
        "a": "amplitude",
        "mu": "center",
        "x0": "center",
        "sigma": "sigma",
        "gamma": "gamma",
        "width": "width",
        "fwhm": "fwhm",
        "baseline": "baseline_const",
        "offset": "baseline_const",
        "slope": "baseline_slope",
    }
    return aliases.get(text, text)


def execute_fit_param(fit_raw: Any, params: Json) -> float | None:
    if not isinstance(fit_raw, dict):
        return None
    field = str(params.get("field", "value")).strip().lower()
    if field in {"", "value"}:
        fit_params = fit_raw.get("params")
    elif field in {"stderr", "error", "std_err", "stddev"}:
        fit_params = fit_raw.get("stderr")
    else:
        return None
    if not isinstance(fit_params, dict):
        return None
    name = _normalize_fit_param_name(params.get("name", "center"))
    return _normalize_float(fit_params.get(name))


def execute_fit_params(fit_raw: Any) -> dict[str, dict[str, float | None]] | None:
    if not isinstance(fit_raw, dict):
        return None
    params_raw = fit_raw.get("params")
    stderr_raw = fit_raw.get("stderr")
    params_map_raw = params_raw if isinstance(params_raw, dict) else None
    stderr_map_raw = stderr_raw if isinstance(stderr_raw, dict) else None
    if params_map_raw is None and stderr_map_raw is None:
        return None
    params_map = (
        {str(key): value for key, value in params_map_raw.items()}
        if params_map_raw is not None
        else None
    )
    stderr_map = (
        {str(key): value for key, value in stderr_map_raw.items()}
        if stderr_map_raw is not None
        else None
    )
    keys: set[str] = set()
    if params_map is not None:
        keys.update(params_map.keys())
    if stderr_map is not None:
        keys.update(stderr_map.keys())
    out: dict[str, dict[str, float | None]] = {}
    for key in sorted(keys):
        if not key:
            continue
        value = (
            _normalize_float(params_map.get(key))
            if params_map is not None
            else None
        )
        stderr = (
            _normalize_float(stderr_map.get(key))
            if stderr_map is not None
            else None
        )
        if value is None and stderr is None:
            continue
        out[key] = {
            "value": float(value) if value is not None else None,
            "stderr": float(stderr) if stderr is not None else None,
        }
    return out or None


def _parse_fit_hist_y_source(raw: Any) -> str:
    source = str(raw if raw is not None else "mean").strip().lower()
    if source in {"mean", "std", "sem", "count"}:
        return source
    raise ValueError("fit.from_hist_agg y_source must be one of mean, std, sem, count")


def _parse_fit_hist_sigma_source(raw: Any) -> str:
    source = str(raw if raw is not None else "sem").strip().lower()
    if source in {"sem", "std", "none"}:
        return source
    raise ValueError("fit.from_hist_agg chi2_sigma_source must be one of sem, std, none")


def _validate_fit_from_hist_params(
    params: Json,
) -> tuple[
    str,
    str,
    int,
    float | None,
    int | None,
    str,
    str,
    int,
    float | None,
    float | None,
]:
    model, baseline_mode, every_n, _sigma_y, dense_eval_points = (
        _validate_fit_curve_params(params)
    )
    y_source = _parse_fit_hist_y_source(params.get("y_source", "mean"))
    chi2_sigma_source = _parse_fit_hist_sigma_source(
        params.get("chi2_sigma_source", "sem")
    )
    min_count = _normalize_int(params.get("min_count"))
    if min_count is None:
        min_count = 1
    if min_count < 0:
        raise ValueError("fit.from_hist_agg requires min_count >= 0")
    x_min = _normalize_float(params.get("x_min"))
    x_max = _normalize_float(params.get("x_max"))
    if x_min is not None and x_max is not None and not (x_max > x_min):
        raise ValueError("fit.from_hist_agg requires x_max > x_min when both are set")
    return (
        model,
        baseline_mode,
        every_n,
        _sigma_y,
        dense_eval_points,
        y_source,
        chi2_sigma_source,
        int(min_count),
        x_min,
        x_max,
    )


def _hist_agg_to_xy(
    hist_raw: Any,
    *,
    y_source: str,
    chi2_sigma_source: str,
    min_count: int,
    x_min: float | None,
    x_max: float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None] | None:
    if not isinstance(hist_raw, dict):
        return None
    x_raw = hist_raw.get("x_bins")
    y_raw = hist_raw.get(y_source)
    c_raw = hist_raw.get("count")
    sigma_raw = None
    if chi2_sigma_source in {"sem", "std"}:
        sigma_raw = hist_raw.get(chi2_sigma_source)
    if not isinstance(x_raw, list) or not isinstance(y_raw, list) or not isinstance(c_raw, list):
        return None
    n = min(len(x_raw), len(y_raw), len(c_raw))
    if n <= 0:
        return None
    x_vals: list[float] = []
    y_vals: list[float] = []
    sigma_vals: list[float] = []
    for i in range(n):
        x = _normalize_float(x_raw[i])
        y = _normalize_float(y_raw[i])
        c = _normalize_float(c_raw[i])
        if x is None or y is None or c is None:
            continue
        if int(c) < int(min_count):
            continue
        if x_min is not None and x < x_min:
            continue
        if x_max is not None and x > x_max:
            continue
        if chi2_sigma_source != "none":
            if not isinstance(sigma_raw, list) or i >= len(sigma_raw):
                continue
            sigma = _normalize_float(sigma_raw[i])
            if sigma is None or sigma <= 0:
                continue
            sigma_vals.append(float(sigma))
        x_vals.append(float(x))
        y_vals.append(float(y))
    if len(x_vals) < 4:
        return None
    x_arr = np.asarray(x_vals, dtype=np.float64)
    y_arr = np.asarray(y_vals, dtype=np.float64)
    order = np.argsort(x_arr)
    x_arr = x_arr[order]
    y_arr = y_arr[order]
    sigma_arr: np.ndarray | None = None
    if chi2_sigma_source != "none":
        sigma_arr = np.asarray(sigma_vals, dtype=np.float64)
        sigma_arr = sigma_arr[order]
    return x_arr, y_arr, sigma_arr


def execute_fit_from_hist_agg(
    *,
    state: FitCurve1DState,
    hist_raw: Any,
    gate_raw: Any,
    y_source: str,
    chi2_sigma_source: str,
    min_count: int,
    x_min: float | None,
    x_max: float | None,
) -> dict[str, Any] | None:
    if not _gate_open(gate_raw, default=True):
        return state.last_fit
    state.sample_count += 1
    every_n = max(1, int(state.every_n))
    should_fit = state.sample_count == 1 or (state.sample_count % every_n == 0)
    if not should_fit:
        return state.last_fit
    xy = _hist_agg_to_xy(
        hist_raw,
        y_source=y_source,
        chi2_sigma_source=chi2_sigma_source,
        min_count=min_count,
        x_min=x_min,
        x_max=x_max,
    )
    if xy is None:
        return state.last_fit
    x_arr, y_arr, sigma_arr = xy
    fit_result = _fit_curve_run(
        x_raw=x_arr,
        y_raw=y_arr,
        model=state.model,
        baseline_mode=state.baseline_mode,
        sigma_y=state.sigma_y,
        sigma_trace_raw=sigma_arr,
        dense_eval_points=state.dense_eval_points,
    )
    if fit_result is not None:
        state.last_fit = fit_result
    return state.last_fit


def _gate_open(gate_raw: Any, *, default: bool = True) -> bool:
    if gate_raw is None:
        return bool(default)
    if isinstance(gate_raw, bool):
        return gate_raw
    gate = _normalize_float(gate_raw)
    if gate is None:
        return False
    return bool(gate != 0.0)


def _node_signature(op: str) -> OpSpec:
    if op not in OPS:
        raise ValueError(f"unknown operator {op!r}")
    return OPS[op]


def _is_special_input_source(op: str, port: str, source_id: str) -> bool:
    return (
        op == "fit.curve_1d"
        and port == "x"
        and str(source_id).strip() == SAMPLE_INDEX_INPUT_TOKEN
    )


def _normalize_node(raw: Any, *, index: int) -> NodeSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"graph.nodes[{index}] must be an object")
    node_id = _normalize_id(raw.get("id"))
    if node_id is None:
        raise ValueError(f"graph.nodes[{index}].id is required")
    op = _normalize_id(raw.get("op"))
    if op is None:
        raise ValueError(f"graph.nodes[{index}].op is required")
    params_raw = raw.get("params")
    if params_raw is None:
        params: Json = {}
    elif isinstance(params_raw, dict):
        params = dict(params_raw)
    else:
        raise ValueError(f"graph.nodes[{index}].params must be an object")

    inputs_raw = raw.get("inputs")
    if inputs_raw is None:
        inputs: dict[str, str] = {}
    elif isinstance(inputs_raw, dict):
        inputs = {}
        for key, value in inputs_raw.items():
            port = _normalize_id(key)
            source = _normalize_id(value)
            if port is None or source is None:
                raise ValueError(f"graph.nodes[{index}].inputs entries must be strings")
            inputs[port] = source
    else:
        raise ValueError(f"graph.nodes[{index}].inputs must be an object")

    # Legacy migration: old dedicated stream reducer ops are folded into source.stream.
    if op == "source.stream_average":
        op = "source.stream"
        params.setdefault("channel_mode", "average")
    elif op == "source.stream_sum":
        op = "source.stream"
        params.setdefault("channel_mode", "sum")

    return NodeSpec(node_id=node_id, op=op, params=params, inputs=inputs)


def compile_workspace_graph(config: Json) -> CompiledWorkspace:
    workspace_id = _normalize_id(config.get("workspace_id"))
    if workspace_id is None:
        raise ValueError("workspace_id is required")
    enabled = bool(config.get("enabled", True))

    graph_raw = config.get("graph")
    if not isinstance(graph_raw, dict):
        raise ValueError("graph is required and must be an object")
    nodes_raw = graph_raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ValueError("graph.nodes must be a non-empty list")

    nodes_list = [_normalize_node(raw, index=i) for i, raw in enumerate(nodes_raw)]
    nodes: dict[str, NodeSpec] = {}
    for node in nodes_list:
        if node.node_id in nodes:
            raise ValueError(f"duplicate node id {node.node_id!r}")
        nodes[node.node_id] = node

    deps: dict[str, set[str]] = {}
    for node in nodes_list:
        dependencies: set[str] = set()
        for port, source_id in node.inputs.items():
            if _is_special_input_source(node.op, port, source_id):
                continue
            dependencies.add(source_id)
        missing = [dep for dep in dependencies if dep not in nodes]
        if missing:
            raise ValueError(f"node {node.node_id!r} references unknown inputs {missing}")
        deps[node.node_id] = dependencies

    try:
        sorter = TopologicalSorter(deps)
        order = list(sorter.static_order())
    except Exception as exc:
        raise ValueError(f"graph is cyclic: {exc}") from exc

    out_type: dict[str, str] = {}
    source_stream_nodes: list[str] = []
    for node_id in order:
        node = nodes[node_id]
        sig = _node_signature(node.op)
        required_inputs = set(sig.input_types.keys())
        optional_inputs = set(sig.optional_input_types.keys())
        expected_inputs = required_inputs | optional_inputs
        given_inputs = set(node.inputs.keys())
        missing_inputs = sorted(required_inputs - given_inputs)
        unknown_inputs = sorted(given_inputs - expected_inputs)
        if missing_inputs or unknown_inputs:
            raise ValueError(
                f"node {node.node_id!r} inputs mismatch; "
                f"missing required {missing_inputs}, unknown {unknown_inputs}, "
                f"allowed {sorted(expected_inputs)}"
            )
        for port, source_id in node.inputs.items():
            if _is_special_input_source(node.op, port, source_id):
                continue
            expected_type = sig.input_types.get(port) or sig.optional_input_types.get(
                port
            )
            if expected_type is None:
                raise ValueError(
                    f"node {node.node_id!r} has unknown input port {port!r}"
                )
            source_type = out_type.get(source_id)
            if source_type != expected_type:
                raise ValueError(
                    f"node {node.node_id!r} input {port!r} expects {expected_type}, got {source_type} from {source_id!r}"
                )
        if node.op == "source.stream":
            source_stream_nodes.append(node.node_id)
            did = _normalize_id(node.params.get("device_id"))
            stream = _normalize_id(node.params.get("stream"))
            if did is None or stream is None:
                raise ValueError(
                    f"node {node.node_id!r} {node.op} requires device_id and stream"
                )
            _ = _parse_stream_source_mode(node.params.get("channel_mode"))
            _ = _parse_channel_indices(node.params.get("channel_indices"))
        if node.op == "source.context_field":
            field = _normalize_id(node.params.get("field"))
            if field is None:
                raise ValueError(
                    f"node {node.node_id!r} source.context_field requires field"
                )
        if node.op == "source.telemetry_nearest":
            did = _normalize_id(node.params.get("device_id"))
            signal = _normalize_id(node.params.get("signal"))
            if did is None or signal is None:
                raise ValueError(
                    f"node {node.node_id!r} source.telemetry_nearest requires device_id and signal"
                )
            max_dt_s = _normalize_float(node.params.get("max_dt_s", 2.0))
            if max_dt_s is None or max_dt_s <= 0:
                raise ValueError(
                    f"node {node.node_id!r} source.telemetry_nearest requires max_dt_s > 0"
                )
        if node.op == "scalar.threshold":
            _ = _validate_scalar_threshold_params(node.params)
        if node.op == "trace.rolling_mean":
            _ = TraceRollingMeanState.from_params(node.params)
        if node.op == "trace.decimate":
            _ = _validate_trace_decimate_params(node.params)
        if node.op == "fit.curve_1d":
            _ = _validate_fit_curve_params(node.params)
        if node.op == "fit.from_hist_agg":
            _ = _validate_fit_from_hist_params(node.params)
        if node.op == "fit.param":
            _ = _normalize_fit_param_name(node.params.get("name", "center"))
        if node.op == "aggregate.bin_stats":
            _ = BinStatsState.from_params(node.params)
        if node.op == "aggregate.bin2d_stats":
            _ = Bin2DStatsState.from_params(node.params)
        out_type[node.node_id] = sig.output_type

    if len(source_stream_nodes) != 1:
        raise ValueError("graph must contain exactly one source.stream node")

    source_node = nodes[source_stream_nodes[0]]
    stream_key = (
        str(source_node.params["device_id"]).strip(),
        str(source_node.params["stream"]).strip(),
    )

    publish_raw = config.get("publish")
    publish = publish_raw if isinstance(publish_raw, dict) else {}
    outputs_raw = publish.get("outputs")
    if outputs_raw is None:
        outputs_raw = []
    if not isinstance(outputs_raw, list):
        raise ValueError("publish.outputs must be a list")
    outputs: list[PublishOutput] = []
    output_ids: set[str] = set()
    for idx, item in enumerate(outputs_raw):
        if not isinstance(item, dict):
            raise ValueError(f"publish.outputs[{idx}] must be an object")
        output_id = _normalize_id(item.get("output_id"))
        if output_id is None:
            raise ValueError(f"publish.outputs[{idx}].output_id is required")
        if output_id in output_ids:
            raise ValueError(f"duplicate publish output_id {output_id!r}")
        node_id = _normalize_id(item.get("node_id"))
        if node_id is None:
            raise ValueError(f"publish.outputs[{idx}].node_id is required")
        if node_id not in nodes:
            raise ValueError(
                f"publish.outputs[{idx}].node_id references unknown node {node_id!r}"
            )
        kind = out_type.get(node_id)
        if kind is None:
            raise ValueError(
                f"publish.outputs[{idx}].node_id has no output type {node_id!r}"
            )
        if kind not in {
            "scalar",
            "hist_agg",
            "hist2d",
            "trace",
            "params_map",
            "fit_1d",
        }:
            raise ValueError(
                f"publish.outputs[{idx}].node_id type {kind!r} is not publishable in v1"
            )
        output_ids.add(output_id)
        outputs.append(PublishOutput(output_id=output_id, node_id=node_id, kind=kind))

    return CompiledWorkspace(
        workspace_id=workspace_id,
        enabled=enabled,
        nodes=nodes,
        order=order,
        stream_source_node_id=source_stream_nodes[0],
        stream_key=stream_key,
        node_output_types=dict(out_type),
        outputs=outputs,
    )


class StreamAnalysisProcess(ManagedProcessBase):
    def __init__(
        self,
        *,
        manager_rpc: str,
        manager_pub: str,
        process_id: str,
        rpc_timeout_ms: int,
        heartbeat_endpoint: str | None,
        process_data_endpoint: str | None = None,
        heartbeat_period_s: float = 1.0,
        max_payload_points: int = 200_000,
        max_events_per_cycle: int = 12,
        max_hist_output_hz: float = 20.0,
        max_trace_output_hz: float = 10.0,
        rcvhwm: int = 20_000,
        workspace_store_path: str | None = None,
    ) -> None:
        super().__init__(
            process_id=process_id,
            heartbeat_endpoint=heartbeat_endpoint,
            process_data_endpoint=process_data_endpoint,
            heartbeat_period_s=heartbeat_period_s,
        )
        self._manager_helper = ManagerClientHelper(
            manager_rpc=manager_rpc,
            manager_pub=manager_pub,
            rpc_timeout_ms=int(rpc_timeout_ms),
        )
        self._manager = self._manager_helper.init_client(
            ctx=self._ctx,
            process_id=self._process_id,
            subscribe_telemetry=False,
        )
        self._sub = self._manager_helper.open_sub(
            ctx=self._ctx,
            topics=("manager.chunk_ready", "manager.telemetry_update"),
            rcvtimeo_ms=200,
        )
        self._sub.setsockopt(zmq.RCVHWM, max(1, int(rcvhwm)))

        self._max_payload_points = max(1, int(max_payload_points))
        self._max_events_per_cycle = max(1, int(max_events_per_cycle))
        max_hist_hz = float(max_hist_output_hz)
        if not math.isfinite(max_hist_hz) or max_hist_hz <= 0:
            self._hist_output_period_s = 0.0
        else:
            self._hist_output_period_s = 1.0 / max_hist_hz
        max_trace_hz = float(max_trace_output_hz)
        if not math.isfinite(max_trace_hz) or max_trace_hz <= 0:
            self._trace_output_period_s = 0.0
        else:
            self._trace_output_period_s = 1.0 / max_trace_hz

        self._workspaces: dict[str, WorkspaceRuntime] = {}
        self._stream_to_workspaces: dict[tuple[str, str], set[str]] = {}
        self._readers: dict[tuple[str, str], ShmRingReader] = {}
        self._last_seq: dict[tuple[str, str], int] = {}
        self._trace_writers: dict[tuple[str, str], ShmRingWriter] = {}
        self._trace_writer_slot_count = 128
        self._stream_context: dict[
            tuple[str, str], tuple[int | None, dict[str, Any] | None]
        ] = {}
        # Per-stream context keyed by stream sequence number. This lets us
        # apply the correct context to each ring event when we process backlog.
        self._context_by_seq: dict[
            tuple[str, str], dict[int, tuple[int | None, dict[str, Any] | None]]
        ] = {}
        self._context_cache_limit = 8192
        self._telemetry_history: dict[tuple[str, str], list[tuple[float, float]]] = {}
        self._telemetry_history_max_points = 4096
        self._telemetry_history_max_age_s = 300.0

        self._processed_updates = 0
        self._dropped_updates = 0
        self._hist_last_emit_mono: dict[str, float] = {}
        self._trace_last_emit_mono: dict[str, float] = {}
        self._workspace_store_path = self._normalize_workspace_store_path(
            workspace_store_path
        )
        self._workspace_store_dirty = False
        self._workspace_store_last_loaded_wall_s: float | None = None
        self._workspace_store_last_saved_wall_s: float | None = None
        self._workspace_store_last_error: str | None = None

        self._init_rpc_router()
        self._init_poller(
            include_rpc=True,
            include_sub=False,
            extra=[(self._sub, zmq.POLLIN)],
        )
        self._autoload_workspace_store()
        self._advertise_process_rpc()
        self._start_process_data_pub()
        self._start_heartbeat_thread(state_provider=lambda: "RUNNING")

    @staticmethod
    def _normalize_workspace_store_path(raw: Any) -> Path | None:
        text = str(raw or "").strip()
        if not text:
            return None
        return Path(text).expanduser().resolve()

    @staticmethod
    def _workspace_etag(workspace_id: str, revision: int) -> str:
        return f"{workspace_id}:{int(revision)}"

    @staticmethod
    def _yaml_dump(payload: Any) -> str:
        try:
            import yaml  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - dependency error
            raise RuntimeError(f"PyYAML missing: {exc}") from exc
        return str(yaml.safe_dump(payload, sort_keys=False))

    def _workspace_store_status_payload(self) -> Json:
        path = self._workspace_store_path
        exists = bool(path is not None and path.exists())
        return {
            "path": str(path) if path is not None else None,
            "exists": exists,
            "dirty": bool(self._workspace_store_dirty),
            "workspace_count": len(self._workspaces),
            "last_loaded_wall_s": self._workspace_store_last_loaded_wall_s,
            "last_saved_wall_s": self._workspace_store_last_saved_wall_s,
            "last_error": self._workspace_store_last_error,
        }

    def _serialize_workspace_store_payload(self) -> Json:
        workspaces: list[Json] = []
        for workspace_id in sorted(self._workspaces.keys()):
            runtime = self._workspaces[workspace_id]
            raw = dict(runtime.raw_config)
            raw["workspace_id"] = runtime.compiled.workspace_id
            raw["enabled"] = bool(runtime.compiled.enabled)
            if not isinstance(raw.get("graph"), dict):
                raw["graph"] = {}
            if not isinstance(raw.get("publish"), dict):
                raw["publish"] = {}
            workspaces.append(_sanitize_json(raw))
        return {
            "version": 1,
            "workspaces": workspaces,
        }

    @staticmethod
    def _parse_workspace_store_payload(raw: Any) -> list[Json]:
        if raw is None:
            return []
        entries: list[Json] = []
        source_items: list[tuple[str | None, Any]] = []
        if isinstance(raw, list):
            source_items = [(None, item) for item in raw]
        elif isinstance(raw, dict):
            if "workspaces" in raw:
                workspaces_raw = raw.get("workspaces")
                if workspaces_raw is None:
                    source_items = []
                elif isinstance(workspaces_raw, list):
                    source_items = [(None, item) for item in workspaces_raw]
                elif isinstance(workspaces_raw, dict):
                    source_items = list(workspaces_raw.items())
                else:
                    raise ValueError("workspace store workspaces must be list or mapping")
            elif "workspace_id" in raw and "graph" in raw:
                source_items = [(None, raw)]
            elif len(raw) == 0:
                source_items = []
            else:
                raise ValueError("workspace store must contain workspaces list or mapping")
        else:
            raise ValueError("workspace store payload must be a dict or list")

        for key, item in source_items:
            if not isinstance(item, dict):
                raise ValueError("workspace entry must be an object")
            cfg = dict(item)
            if _normalize_id(cfg.get("workspace_id")) is None and key is not None:
                cfg["workspace_id"] = str(key).strip()
            workspace_id = _normalize_id(cfg.get("workspace_id"))
            if workspace_id is None:
                raise ValueError("workspace entry missing workspace_id")
            entries.append(cfg)
        entries.sort(key=lambda item: str(item.get("workspace_id", "")))
        return entries

    def _clear_workspaces(self, *, mark_dirty: bool, publish: bool) -> list[str]:
        removed = sorted(self._workspaces.keys())
        self._workspaces.clear()
        for key in list(self._trace_writers.keys()):
            self._drop_trace_writer(key)
        self._trace_writers.clear()
        self._hist_last_emit_mono.clear()
        self._trace_last_emit_mono.clear()
        self._rebuild_stream_index()
        if mark_dirty and self._workspace_store_path is not None:
            self._workspace_store_dirty = True
        if publish:
            self._publish_workspace_status(
                "*",
                status="cleared",
                details={"removed": removed},
            )
        return removed

    def _reload_workspace_store(self, *, strict_missing: bool) -> Json:
        path = self._workspace_store_path
        if path is None:
            raise ValueError("workspace_store_path is not configured")
        try:
            if not path.exists():
                if strict_missing:
                    raise FileNotFoundError(str(path))
                self._workspace_store_last_error = None
                self._workspace_store_last_loaded_wall_s = time.time()
                self._workspace_store_dirty = False
                return {"loaded_workspace_ids": []}
            loaded = load_yaml_file(path)
            configs = self._parse_workspace_store_payload(loaded)
            self._clear_workspaces(mark_dirty=False, publish=False)
            loaded_ids: list[str] = []
            for cfg in configs:
                runtime = self._put_workspace_from_config(
                    cfg,
                    expected_revision=None,
                    mark_dirty=False,
                    publish=True,
                )
                loaded_ids.append(runtime.compiled.workspace_id)
            self._workspace_store_last_error = None
            self._workspace_store_last_loaded_wall_s = time.time()
            self._workspace_store_dirty = False
            return {"loaded_workspace_ids": loaded_ids}
        except Exception as exc:
            self._workspace_store_last_error = str(exc)
            raise

    def _autoload_workspace_store(self) -> None:
        if self._workspace_store_path is None:
            return
        try:
            self._reload_workspace_store(strict_missing=False)
        except Exception as exc:
            self._workspace_store_last_error = str(exc)
            self._publish_error(
                workspace_id=None,
                code="workspace_store_load_failed",
                message=str(exc),
                details={"path": str(self._workspace_store_path)},
            )

    def _save_workspace_store(self, *, path_override: Any = None) -> Json:
        override = self._normalize_workspace_store_path(path_override)
        if override is not None:
            self._workspace_store_path = override
        path = self._workspace_store_path
        if path is None:
            raise ValueError("workspace_store_path is not configured")
        try:
            payload = self._serialize_workspace_store_payload()
            text = self._yaml_dump(payload)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            self._workspace_store_last_error = None
            self._workspace_store_last_saved_wall_s = time.time()
            self._workspace_store_dirty = False
            return {"saved_path": str(path), "workspace_count": len(self._workspaces)}
        except Exception as exc:
            self._workspace_store_last_error = str(exc)
            raise

    def _workspace_summary(self, workspace: WorkspaceRuntime) -> Json:
        c = workspace.compiled
        return {
            "workspace_id": c.workspace_id,
            "name": str(workspace.raw_config.get("name") or c.workspace_id),
            "enabled": bool(c.enabled),
            "revision": int(workspace.revision),
            "etag": str(workspace.etag),
            "stream": {"device_id": c.stream_key[0], "stream": c.stream_key[1]},
            "node_count": len(c.nodes),
            "node_output_types": dict(c.node_output_types),
            "outputs": [
                {
                    "output_id": out.output_id,
                    "node_id": out.node_id,
                    "kind": out.kind,
                }
                for out in c.outputs
            ],
            "processed_samples": int(workspace.processed_samples),
            "dropped_samples": int(workspace.dropped_samples),
            "graph": workspace.raw_config.get("graph", {}),
            "publish": workspace.raw_config.get("publish", {}),
        }

    def _publish_manager_event(
        self,
        *,
        topic: str,
        payload: Json,
        include_process_id: bool = True,
        include_ts: bool = True,
        severity: str | None = None,
        device_id: str | None = None,
    ) -> None:
        safe_payload = _sanitize_json(payload)
        if severity is not None and "severity" not in safe_payload:
            safe_payload["severity"] = str(severity)
        if device_id is not None and "device_id" not in safe_payload:
            safe_payload["device_id"] = str(device_id)
        sent = self._publish_process_event(
            topic=str(topic),
            payload=safe_payload,
            include_process_id=include_process_id,
            include_ts=include_ts,
        )
        if not sent:
            self._dropped_updates += 1

    def _publish_workspace_status(
        self,
        workspace_id: str,
        *,
        status: str,
        details: Json | None = None,
    ) -> None:
        payload: Json = {
            "workspace_id": workspace_id,
            "status": status,
        }
        if details:
            payload.update(details)
        self._publish_manager_event(
            topic="manager.stream_analysis.workspace_status",
            payload=payload,
        )

    def _publish_error(
        self,
        *,
        workspace_id: str | None,
        code: str,
        message: str,
        details: Json | None = None,
    ) -> None:
        payload: Json = {
            "code": str(code),
            "message": str(message),
        }
        if workspace_id is not None:
            payload["workspace_id"] = workspace_id
        if details:
            payload["details"] = details
        self._publish_manager_event(
            topic="manager.stream_analysis.error",
            payload=payload,
            severity="error",
        )

    def _rebuild_stream_index(self) -> None:
        out: dict[tuple[str, str], set[str]] = {}
        for workspace in self._workspaces.values():
            c = workspace.compiled
            if not c.enabled:
                continue
            out.setdefault(c.stream_key, set()).add(c.workspace_id)
        self._stream_to_workspaces = out

    def _put_workspace_from_config(
        self,
        config: Json,
        *,
        expected_revision: int | None,
        mark_dirty: bool,
        publish: bool,
    ) -> WorkspaceRuntime:
        compiled = compile_workspace_graph(config)
        existing = self._workspaces.get(compiled.workspace_id)
        current_revision = existing.revision if existing is not None else None
        if expected_revision is not None:
            if existing is None:
                if int(expected_revision) != 0:
                    raise WorkspaceRevisionConflict(
                        workspace_id=compiled.workspace_id,
                        expected_revision=int(expected_revision),
                        current_revision=None,
                    )
            elif int(expected_revision) != int(current_revision):
                raise WorkspaceRevisionConflict(
                    workspace_id=compiled.workspace_id,
                    expected_revision=int(expected_revision),
                    current_revision=int(current_revision),
                )
        node_state: dict[str, Any] = {}
        if existing is not None:
            graph_unchanged = (
                set(existing.compiled.nodes.keys()) == set(compiled.nodes.keys())
            )
            if graph_unchanged:
                for node_id, old_node in existing.compiled.nodes.items():
                    new_node = compiled.nodes.get(node_id)
                    if new_node is None:
                        graph_unchanged = False
                        break
                    if (
                        old_node.op != new_node.op
                        or old_node.params != new_node.params
                        or old_node.inputs != new_node.inputs
                    ):
                        graph_unchanged = False
                        break
            if not graph_unchanged:
                existing = None

        if existing is not None:
            for node_id, state in existing.node_state.items():
                old_node = existing.compiled.nodes.get(node_id)
                new_node = compiled.nodes.get(node_id)
                if old_node is None or new_node is None:
                    continue
                if old_node.op != new_node.op:
                    continue
                if old_node.params != new_node.params:
                    continue
                node_state[node_id] = state

        runtime = WorkspaceRuntime(
            compiled=compiled,
            raw_config={},
            node_state=node_state,
            processed_samples=existing.processed_samples if existing else 0,
            dropped_samples=existing.dropped_samples if existing else 0,
            revision=(existing.revision + 1) if existing is not None else 1,
            etag=self._workspace_etag(
                compiled.workspace_id,
                (existing.revision + 1) if existing is not None else 1,
            ),
        )
        raw_cfg = _sanitize_json(dict(config))
        raw_cfg["workspace_id"] = compiled.workspace_id
        raw_cfg["enabled"] = bool(compiled.enabled)
        if not isinstance(raw_cfg.get("graph"), dict):
            raw_cfg["graph"] = {}
        if not isinstance(raw_cfg.get("publish"), dict):
            raw_cfg["publish"] = {}
        runtime.raw_config = raw_cfg

        for node_id in compiled.order:
            node = compiled.nodes[node_id]
            spec = OPS[node.op]
            if not spec.stateful:
                continue
            if node_id not in runtime.node_state:
                if node.op == "aggregate.bin_stats":
                    runtime.node_state[node_id] = BinStatsState.from_params(node.params)
                elif node.op == "aggregate.bin2d_stats":
                    runtime.node_state[node_id] = Bin2DStatsState.from_params(node.params)
                elif node.op == "trace.rolling_mean":
                    runtime.node_state[node_id] = TraceRollingMeanState.from_params(node.params)
                elif node.op == "fit.curve_1d":
                    runtime.node_state[node_id] = FitCurve1DState.from_params(node.params)
                elif node.op == "fit.from_hist_agg":
                    runtime.node_state[node_id] = FitCurve1DState.from_params(node.params)

        self._workspaces[compiled.workspace_id] = runtime
        self._rebuild_stream_index()
        self._reconcile_trace_writers()
        if mark_dirty and self._workspace_store_path is not None:
            self._workspace_store_dirty = True
        if publish:
            self._publish_workspace_status(
                compiled.workspace_id,
                status="updated",
                details={"workspace": self._workspace_summary(runtime)},
            )
        return runtime

    def _delete_workspace(
        self,
        workspace_id: str,
        *,
        expected_revision: int | None,
        mark_dirty: bool,
        publish: bool,
    ) -> bool:
        existing = self._workspaces.get(workspace_id)
        current_revision = existing.revision if existing is not None else None
        if expected_revision is not None:
            if existing is None or int(expected_revision) != int(current_revision):
                raise WorkspaceRevisionConflict(
                    workspace_id=workspace_id,
                    expected_revision=int(expected_revision),
                    current_revision=(
                        int(current_revision) if current_revision is not None else None
                    ),
                )
        removed = self._workspaces.pop(workspace_id, None)
        self._reconcile_trace_writers()
        self._hist_last_emit_mono.pop(workspace_id, None)
        self._trace_last_emit_mono.pop(workspace_id, None)
        self._rebuild_stream_index()
        if removed is None:
            return False
        if mark_dirty and self._workspace_store_path is not None:
            self._workspace_store_dirty = True
        if publish:
            self._publish_workspace_status(workspace_id, status="deleted")
        return True

    def _allow_trace_outputs_for_workspace(
        self, workspace: WorkspaceRuntime, *, now_mono: float
    ) -> bool:
        if not any(out.kind == "trace" for out in workspace.compiled.outputs):
            return False
        if self._trace_output_period_s <= 0:
            return True
        workspace_id = workspace.compiled.workspace_id
        prev = self._trace_last_emit_mono.get(workspace_id)
        if prev is None or (now_mono - prev) >= self._trace_output_period_s:
            self._trace_last_emit_mono[workspace_id] = now_mono
            return True
        return False

    def _allow_hist_outputs_for_workspace(
        self, workspace: WorkspaceRuntime, *, now_mono: float
    ) -> bool:
        if not any(out.kind in {"hist_agg", "hist2d"} for out in workspace.compiled.outputs):
            return False
        if self._hist_output_period_s <= 0:
            return True
        workspace_id = workspace.compiled.workspace_id
        prev = self._hist_last_emit_mono.get(workspace_id)
        if prev is None or (now_mono - prev) >= self._hist_output_period_s:
            self._hist_last_emit_mono[workspace_id] = now_mono
            return True
        return False

    @staticmethod
    def _trace_writer_key(workspace_id: str, output_id: str) -> tuple[str, str]:
        return (str(workspace_id).strip(), str(output_id).strip())

    def _active_trace_output_keys(self) -> set[tuple[str, str]]:
        active: set[tuple[str, str]] = set()
        for workspace in self._workspaces.values():
            if not workspace.compiled.enabled:
                continue
            for output in workspace.compiled.outputs:
                if output.kind != "trace":
                    continue
                active.add(
                    self._trace_writer_key(
                        workspace.compiled.workspace_id,
                        output.output_id,
                    )
                )
        return active

    def _drop_trace_writer(self, key: tuple[str, str]) -> None:
        writer = self._trace_writers.pop(key, None)
        if writer is None:
            return
        try:
            writer.close()
        except Exception:
            pass
        try:
            writer.unlink()
        except Exception:
            pass

    def _reconcile_trace_writers(self) -> None:
        active = self._active_trace_output_keys()
        for key in list(self._trace_writers.keys()):
            if key not in active:
                self._drop_trace_writer(key)

    @staticmethod
    def _safe_shm_token(raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return "na"
        out = []
        for ch in text:
            if ch.isalnum():
                out.append(ch.lower())
            else:
                out.append("_")
        token = "".join(out).strip("_")
        if not token:
            token = "na"
        return token[:28]

    def _new_trace_shm_name(self, workspace_id: str, output_id: str) -> str:
        ws = self._safe_shm_token(workspace_id)
        out = self._safe_shm_token(output_id)
        suffix = uuid.uuid4().hex[:12]
        return f"ecsa_{ws}_{out}_{suffix}"

    def _ensure_trace_writer(
        self, *, workspace_id: str, output_id: str, point_count: int
    ) -> ShmRingWriter:
        key = self._trace_writer_key(workspace_id, output_id)
        point_count = max(1, int(point_count))
        expected_shape = (point_count,)
        writer = self._trace_writers.get(key)
        if writer is not None:
            if (
                tuple(int(x) for x in writer.layout.shape) == expected_shape
                and writer.layout.dtype == np.dtype("float64")
            ):
                return writer
            self._drop_trace_writer(key)

        name = self._new_trace_shm_name(workspace_id, output_id)
        writer = ShmRingWriter.create(
            name=name,
            dtype="float64",
            shape=expected_shape,
            slot_count=int(self._trace_writer_slot_count),
            layout_version=2,
        )
        self._trace_writers[key] = writer
        return writer

    @staticmethod
    def _normalize_chunk_payload(raw: Any) -> tuple[str, str, str] | None:
        if not isinstance(raw, dict):
            return None
        device_id = _normalize_id(raw.get("device_id"))
        stream = _normalize_id(raw.get("stream"))
        shm_name = _normalize_id(raw.get("shm_name"))
        if device_id is None or stream is None or shm_name is None:
            return None
        return device_id, stream, shm_name

    def _ensure_reader(self, key: tuple[str, str], shm_name: str) -> ShmRingReader | None:
        reader = self._readers.get(key)
        if reader is not None and reader.name == shm_name:
            return reader
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
        try:
            attached = ShmRingReader.attach(shm_name)
        except Exception:
            self._readers.pop(key, None)
            self._last_seq.pop(key, None)
            self._stream_context.pop(key, None)
            self._context_by_seq.pop(key, None)
            return None
        self._readers[key] = attached
        self._last_seq[key] = 0
        self._stream_context.pop(key, None)
        self._context_by_seq.pop(key, None)
        return attached

    def _remember_context_for_seq(
        self,
        *,
        key: tuple[str, str],
        seq: int | None,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
    ) -> None:
        if seq is None:
            return
        bucket = self._context_by_seq.setdefault(key, {})
        bucket[int(seq)] = (
            int(context_id) if context_id is not None else None,
            dict(context_fields) if isinstance(context_fields, dict) else None,
        )
        if len(bucket) > self._context_cache_limit:
            trim = len(bucket) - self._context_cache_limit
            for stale_seq in sorted(bucket.keys())[:trim]:
                bucket.pop(stale_seq, None)

    def _pop_context_for_seq(
        self, *, key: tuple[str, str], seq: int | None
    ) -> tuple[int | None, dict[str, Any] | None]:
        if seq is None:
            return None, None
        bucket = self._context_by_seq.get(key)
        if not bucket:
            return None, None
        item = bucket.pop(int(seq), None)
        if not bucket:
            self._context_by_seq.pop(key, None)
        if item is None:
            return None, None
        return item

    def _prune_context_cache(self, *, key: tuple[str, str], last_seq: int) -> None:
        bucket = self._context_by_seq.get(key)
        if not bucket:
            return
        stale = [seq for seq in bucket.keys() if int(seq) <= int(last_seq)]
        for seq in stale:
            bucket.pop(seq, None)
        if not bucket:
            self._context_by_seq.pop(key, None)

    @staticmethod
    def _decode_array(reader: ShmRingReader, event: Json) -> np.ndarray | None:
        payload = event.get("payload")
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            return None
        try:
            arr = np.frombuffer(payload, dtype=reader.layout.dtype)
            arr = arr.reshape(tuple(int(v) for v in reader.layout.shape))
        except Exception:
            return None
        return arr

    def _record_telemetry_sample(
        self, *, device_id: str, signal: str, t_mono_s: float, value: float
    ) -> None:
        key = (device_id, signal)
        samples = self._telemetry_history.setdefault(key, [])
        item = (float(t_mono_s), float(value))
        if not samples or item[0] >= samples[-1][0]:
            samples.append(item)
        else:
            idx = bisect.bisect_left(samples, item)
            samples.insert(idx, item)

        latest_t = samples[-1][0]
        max_age_s = float(self._telemetry_history_max_age_s)
        if math.isfinite(max_age_s) and max_age_s > 0:
            cutoff = latest_t - max_age_s
            trim_idx = bisect.bisect_left(samples, (cutoff, -math.inf))
            if trim_idx > 0:
                del samples[:trim_idx]

        max_points = int(self._telemetry_history_max_points)
        if max_points > 0 and len(samples) > max_points:
            del samples[: len(samples) - max_points]

    def _ingest_telemetry_update(self, msg: Json) -> None:
        device_id = _normalize_id(msg.get("device_id"))
        signals = msg.get("signals")
        if device_id is None or not isinstance(signals, dict):
            return
        bundle_ts = msg.get("ts")
        bundle_t_mono_s = (
            _normalize_float(bundle_ts.get("t_mono"))
            if isinstance(bundle_ts, dict)
            else None
        )
        for signal_name, raw_signal in signals.items():
            signal = _normalize_id(signal_name)
            if signal is None or not isinstance(raw_signal, dict):
                continue
            value = _normalize_float(raw_signal.get("value"))
            if value is None:
                continue
            sig_ts = raw_signal.get("ts")
            t_mono_s = (
                _normalize_float(sig_ts.get("t_mono"))
                if isinstance(sig_ts, dict)
                else None
            )
            if t_mono_s is None:
                t_mono_s = bundle_t_mono_s
            if t_mono_s is None:
                t_mono_s = time.monotonic()
            self._record_telemetry_sample(
                device_id=device_id, signal=signal, t_mono_s=t_mono_s, value=value
            )

    def _lookup_telemetry_nearest(
        self,
        *,
        device_id: str,
        signal: str,
        event_t_mono_s: float | None,
        max_dt_s: float | None,
    ) -> float | None:
        samples = self._telemetry_history.get((device_id, signal))
        if not samples:
            return None
        target_t = (
            float(event_t_mono_s)
            if event_t_mono_s is not None and math.isfinite(event_t_mono_s)
            else float(time.monotonic())
        )
        idx = bisect.bisect_left(samples, (target_t, -math.inf))
        candidates: list[tuple[float, float]] = []
        if idx > 0:
            candidates.append(samples[idx - 1])
        if idx < len(samples):
            candidates.append(samples[idx])
        if not candidates:
            return None
        best_t, best_value = min(candidates, key=lambda item: abs(item[0] - target_t))
        cutoff = _normalize_float(max_dt_s)
        if cutoff is None:
            cutoff = 2.0
        if cutoff > 0 and abs(best_t - target_t) > cutoff:
            return None
        return float(best_value)

    def _execute_workspace_event(
        self,
        *,
        workspace: WorkspaceRuntime,
        array: np.ndarray,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        include_hist_outputs: bool = True,
        include_trace_outputs: bool = True,
    ) -> list[Json]:
        values: dict[str, Any] = {}
        source_channel_count = 1
        source_channel_index = 0

        for node_id in workspace.compiled.order:
            node = workspace.compiled.nodes[node_id]
            op = node.op
            if op == "source.stream":
                mode = _parse_stream_source_mode(node.params.get("channel_mode"))
                if mode == "single":
                    selected = _parse_channel_indices(node.params.get("channel_indices"))
                    if selected and len(selected) > 0:
                        channel_index = int(selected[0])
                    else:
                        channel_index = _normalize_int(node.params.get("channel_index"))
                        if channel_index is None:
                            channel_index = 0
                    trace, channel_count, actual_index = _select_trace(array, channel_index)
                    source_channel_index = actual_index
                    source_channel_count = channel_count
                else:
                    reducer = "sum" if mode == "sum" else "mean"
                    trace, channel_count, selected = _reduce_trace_channels(
                        array,
                        reducer=reducer,
                        channel_indices_raw=node.params.get("channel_indices"),
                    )
                    source_channel_count = channel_count
                    source_channel_index = int(selected[0]) if selected else 0
                if trace.size > self._max_payload_points:
                    trace = trace[: self._max_payload_points]
                    workspace.dropped_samples += 1
                    self._dropped_updates += 1
                values[node_id] = trace
                continue

            if op == "source.context_field":
                field = _normalize_id(node.params.get("field"))
                scalar = None
                if field is not None and isinstance(context_fields, dict):
                    scalar = _normalize_float(context_fields.get(field))
                values[node_id] = scalar
                continue

            if op == "source.telemetry_nearest":
                did = _normalize_id(node.params.get("device_id"))
                signal = _normalize_id(node.params.get("signal"))
                max_dt_s = _normalize_float(node.params.get("max_dt_s", 2.0))
                if max_dt_s is None or max_dt_s <= 0:
                    max_dt_s = 2.0
                scalar = None
                if did is not None and signal is not None:
                    scalar = self._lookup_telemetry_nearest(
                        device_id=did,
                        signal=signal,
                        event_t_mono_s=event_t_mono_s,
                        max_dt_s=max_dt_s,
                    )
                values[node_id] = scalar
                continue

            if op == "scalar.add":
                values[node_id] = execute_scalar_binary(
                    values.get(node.inputs["a"]),
                    values.get(node.inputs["b"]),
                    op="add",
                )
                continue

            if op == "scalar.subtract":
                values[node_id] = execute_scalar_binary(
                    values.get(node.inputs["a"]),
                    values.get(node.inputs["b"]),
                    op="subtract",
                )
                continue

            if op == "scalar.multiply":
                values[node_id] = execute_scalar_binary(
                    values.get(node.inputs["a"]),
                    values.get(node.inputs["b"]),
                    op="multiply",
                )
                continue

            if op == "scalar.divide":
                values[node_id] = execute_scalar_binary(
                    values.get(node.inputs["a"]),
                    values.get(node.inputs["b"]),
                    op="divide",
                )
                continue

            if op == "scalar.threshold":
                threshold, mode = _validate_scalar_threshold_params(node.params)
                values[node_id] = execute_scalar_threshold(
                    values.get(node.inputs["x"]),
                    threshold=threshold,
                    mode=mode,
                )
                continue

            if op == "trace.divide":
                values[node_id] = execute_trace_divide(
                    values.get(node.inputs["a"]),
                    values.get(node.inputs["b"]),
                )
                continue

            if op == "trace.add_scalar":
                values[node_id] = execute_trace_scalar_math(
                    values.get(node.inputs["trace"]),
                    values.get(node.inputs["scalar"]),
                    op="add",
                )
                continue

            if op == "trace.subtract_scalar":
                values[node_id] = execute_trace_scalar_math(
                    values.get(node.inputs["trace"]),
                    values.get(node.inputs["scalar"]),
                    op="subtract",
                )
                continue

            if op == "trace.multiply_scalar":
                values[node_id] = execute_trace_scalar_math(
                    values.get(node.inputs["trace"]),
                    values.get(node.inputs["scalar"]),
                    op="multiply",
                )
                continue

            if op == "trace.divide_scalar":
                values[node_id] = execute_trace_scalar_math(
                    values.get(node.inputs["trace"]),
                    values.get(node.inputs["scalar"]),
                    op="divide",
                )
                continue

            if op == "trace.rolling_mean":
                src = values.get(node.inputs["trace"])
                state = workspace.node_state.get(node_id)
                if not isinstance(state, TraceRollingMeanState):
                    state = TraceRollingMeanState.from_params(node.params)
                    workspace.node_state[node_id] = state
                values[node_id] = state.update(src)
                continue

            if op == "trace.decimate":
                src = values.get(node.inputs["trace"])
                values[node_id] = execute_trace_decimate(src, node.params)
                continue

            if op == "trace.crop":
                src = values.get(node.inputs["trace"])
                values[node_id] = execute_trace_crop(src, node.params)
                continue

            if op == "trace.subtract_background":
                src = values.get(node.inputs["trace"])
                values[node_id] = execute_trace_subtract_background(src, node.params)
                continue

            if op == "trace.integrate":
                src = values.get(node.inputs["trace"])
                values[node_id] = execute_trace_integrate(src)
                continue

            if op == "fit.curve_1d":
                y_raw = values.get(node.inputs["y"])
                x_input = str(node.inputs.get("x") or "").strip()
                if x_input == SAMPLE_INDEX_INPUT_TOKEN:
                    y_trace = _coerce_trace(y_raw)
                    x_raw = (
                        np.arange(int(y_trace.size), dtype=np.float64)
                        if y_trace is not None
                        else None
                    )
                else:
                    x_raw = values.get(node.inputs["x"])
                state = workspace.node_state.get(node_id)
                if not isinstance(state, FitCurve1DState):
                    state = FitCurve1DState.from_params(node.params)
                    workspace.node_state[node_id] = state
                gate_val = values.get(node.inputs["gate"]) if "gate" in node.inputs else None
                values[node_id] = execute_fit_curve_1d(
                    state=state,
                    x_raw=x_raw,
                    y_raw=y_raw,
                    gate_raw=gate_val,
                )
                continue

            if op == "fit.yhat":
                fit_val = values.get(node.inputs["fit"])
                values[node_id] = execute_fit_yhat(fit_val)
                continue

            if op == "fit.xhat":
                fit_val = values.get(node.inputs["fit"])
                values[node_id] = execute_fit_xhat(fit_val)
                continue

            if op == "fit.yhat_dense":
                fit_val = values.get(node.inputs["fit"])
                values[node_id] = execute_fit_yhat_dense(fit_val)
                continue

            if op == "fit.xhat_dense":
                fit_val = values.get(node.inputs["fit"])
                values[node_id] = execute_fit_xhat_dense(fit_val)
                continue

            if op == "fit.param":
                fit_val = values.get(node.inputs["fit"])
                values[node_id] = execute_fit_param(fit_val, node.params)
                continue

            if op == "fit.params":
                fit_val = values.get(node.inputs["fit"])
                values[node_id] = execute_fit_params(fit_val)
                continue

            if op == "fit.from_hist_agg":
                hist_val = values.get(node.inputs["hist"])
                state = workspace.node_state.get(node_id)
                if not isinstance(state, FitCurve1DState):
                    state = FitCurve1DState.from_params(node.params)
                    workspace.node_state[node_id] = state
                (
                    _model,
                    _baseline_mode,
                    _every_n,
                    _sigma_y,
                    _dense_eval_points,
                    y_source,
                    chi2_sigma_source,
                    min_count,
                    x_min,
                    x_max,
                ) = (
                    _validate_fit_from_hist_params(node.params)
                )
                gate_val = values.get(node.inputs["gate"]) if "gate" in node.inputs else None
                values[node_id] = execute_fit_from_hist_agg(
                    state=state,
                    hist_raw=hist_val,
                    gate_raw=gate_val,
                    y_source=y_source,
                    chi2_sigma_source=chi2_sigma_source,
                    min_count=min_count,
                    x_min=x_min,
                    x_max=x_max,
                )
                continue

            if op == "aggregate.bin_stats":
                x_val = values.get(node.inputs["x"])
                y_val = values.get(node.inputs["y"])
                state = workspace.node_state.get(node_id)
                if not isinstance(state, BinStatsState):
                    state = BinStatsState.from_params(node.params)
                    workspace.node_state[node_id] = state
                gate_val = (
                    values.get(node.inputs["gate"])
                    if "gate" in node.inputs
                    else None
                )
                if _gate_open(gate_val, default=True):
                    last_sample = state.update_sample(x_val, y_val)
                else:
                    last_sample = None
                values[node_id] = (
                    state.payload(last_sample=last_sample)
                    if include_hist_outputs
                    else None
                )
                continue

            if op == "aggregate.bin2d_stats":
                x_val = values.get(node.inputs["x"])
                y_val = values.get(node.inputs["y"])
                z_val = values.get(node.inputs["z"])
                state = workspace.node_state.get(node_id)
                if not isinstance(state, Bin2DStatsState):
                    state = Bin2DStatsState.from_params(node.params)
                    workspace.node_state[node_id] = state
                gate_val = (
                    values.get(node.inputs["gate"])
                    if "gate" in node.inputs
                    else None
                )
                if _gate_open(gate_val, default=True):
                    last_sample = state.update_sample(x_val, y_val, z_val)
                else:
                    last_sample = None
                values[node_id] = (
                    state.payload(last_sample=last_sample)
                    if include_hist_outputs
                    else None
                )
                continue

            raise RuntimeError(f"unexpected op {op!r}")

        outputs: list[Json] = []
        for output in workspace.compiled.outputs:
            raw_value = values.get(output.node_id)
            if output.kind == "scalar":
                scalar = _normalize_float(raw_value)
                if scalar is None:
                    continue
                value: Any = float(scalar)
            elif output.kind == "hist_agg":
                if not include_hist_outputs:
                    continue
                if not isinstance(raw_value, dict):
                    continue
                value = _sanitize_json(dict(raw_value))
            elif output.kind == "hist2d":
                if not include_hist_outputs:
                    continue
                if not isinstance(raw_value, dict):
                    continue
                value = _sanitize_json(dict(raw_value))
            elif output.kind == "params_map":
                if not isinstance(raw_value, dict):
                    continue
                value = _sanitize_json(dict(raw_value))
            elif output.kind == "fit_1d":
                if not isinstance(raw_value, dict):
                    continue
                value = _sanitize_json(dict(raw_value))
            elif output.kind == "trace":
                if not include_trace_outputs:
                    continue
                trace = _coerce_trace(raw_value)
                if trace is None:
                    continue
                truncated = False
                if trace.size > self._max_payload_points:
                    trace = trace[: self._max_payload_points]
                    truncated = True
                value = trace
            else:
                continue
            payload: Json = {
                "workspace_id": workspace.compiled.workspace_id,
                "output_id": output.output_id,
                "node_id": output.node_id,
                "kind": output.kind,
                "channel_index": int(source_channel_index),
                "channel_count": int(source_channel_count),
                "value": value,
            }
            if output.kind == "trace":
                payload["point_count"] = int(trace.size)
                if truncated:
                    payload["truncated"] = True
            outputs.append(payload)
        return outputs

    def _publish_output_update(
        self,
        *,
        output: Json,
        seq: int | None,
        t0_mono_ns: int | None,
        t0_wall_ns: int | None,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
        device_id: str,
        stream: str,
    ) -> None:
        kind = str(output.get("kind") or "").strip()
        if kind == "trace":
            workspace_id = _normalize_id(output.get("workspace_id"))
            output_id = _normalize_id(output.get("output_id"))
            node_id = _normalize_id(output.get("node_id"))
            trace_raw = output.get("value")
            trace = _coerce_trace(trace_raw)
            if (
                workspace_id is None
                or output_id is None
                or node_id is None
                or trace is None
                or trace.size <= 0
            ):
                return
            writer = self._ensure_trace_writer(
                workspace_id=workspace_id,
                output_id=output_id,
                point_count=int(trace.size),
            )
            t0_mono_out = (
                int(t0_mono_ns) if t0_mono_ns is not None else int(now_mono_ns())
            )
            t0_wall_out = (
                int(t0_wall_ns) if t0_wall_ns is not None else int(now_wall_ns())
            )
            try:
                trace_seq = writer.write(
                    np.asarray(trace, dtype=np.float64).reshape(-1),
                    t0_mono_ns=t0_mono_out,
                    t0_wall_ns=t0_wall_out,
                )
            except Exception:
                return

            descriptor: Json = {
                "version": 1,
                "workspace_id": workspace_id,
                "output_id": output_id,
                "node_id": node_id,
                "kind": "trace",
                "device_id": device_id,
                "stream": stream,
                "shm_name": writer.name,
                "seq": int(trace_seq),
                "t0_mono_ns": t0_mono_out,
                "t0_wall_ns": t0_wall_out,
                "dtype": "float64",
                "shape": [int(trace.size)],
                "point_count": int(trace.size),
                "channel_index": int(output.get("channel_index", 0) or 0),
                "channel_count": int(output.get("channel_count", 1) or 1),
            }
            if bool(output.get("truncated")):
                descriptor["truncated"] = True
            if context_id is not None:
                descriptor["context_id"] = int(context_id)
            if context_fields:
                descriptor["context_fields"] = _sanitize_json(dict(context_fields))
            self._publish_manager_event(
                topic="manager.stream_analysis.trace_ready",
                payload=descriptor,
            )
            return

        payload: Json = {
            "version": 1,
            "device_id": device_id,
            "stream": stream,
            "seq": seq,
            "t0_mono_ns": t0_mono_ns,
            "t0_wall_ns": t0_wall_ns,
            **output,
        }
        if context_id is not None:
            payload["context_id"] = int(context_id)
        if context_fields:
            payload["context_fields"] = _sanitize_json(dict(context_fields))

        self._publish_manager_event(
            topic="manager.stream_analysis.output",
            payload=payload,
        )

    def _handle_chunk_ready(self, msg: Json) -> None:
        parsed = self._normalize_chunk_payload(msg)
        if parsed is None:
            return
        device_id, stream, shm_name = parsed
        key = (device_id, stream)

        workspace_ids = self._stream_to_workspaces.get(key)
        if not workspace_ids:
            return

        reader = self._ensure_reader(key, shm_name)
        if reader is None:
            return

        msg_seq = _normalize_int(msg.get("seq"))
        context_id = _normalize_int(msg.get("context_id"))
        context_fields_raw = msg.get("context_fields")
        context_fields = (
            dict(context_fields_raw) if isinstance(context_fields_raw, dict) else None
        )
        self._remember_context_for_seq(
            key=key,
            seq=msg_seq,
            context_id=context_id,
            context_fields=context_fields,
        )

        last_seq = int(self._last_seq.get(key, 0))
        try:
            events_all = reader.read_events(last_seq)
        except Exception:
            try:
                reader.close()
            except Exception:
                pass
            self._readers.pop(key, None)
            self._last_seq.pop(key, None)
            self._stream_context.pop(key, None)
            self._context_by_seq.pop(key, None)
            return
        if not events_all:
            return

        if msg_seq is None:
            events = events_all
        else:
            events = []
            for event in events_all:
                seq = _normalize_int(event.get("seq"))
                if seq is None:
                    continue
                if seq <= msg_seq:
                    events.append(event)
        if not events:
            self._prune_context_cache(key=key, last_seq=last_seq)
            return

        current_context_id, current_context_fields = self._stream_context.get(
            key, (None, None)
        )
        if current_context_fields is not None:
            current_context_fields = dict(current_context_fields)

        latest_seq = last_seq
        event_count = len(events)
        for event in events:
            seq = _normalize_int(event.get("seq"))
            if seq is not None:
                latest_seq = max(latest_seq, seq)
            event_context_id, event_context_fields = self._pop_context_for_seq(
                key=key, seq=seq
            )
            if (
                event_context_fields is None
                and event_context_id is None
                and msg_seq is not None
                and seq == msg_seq
            ):
                # Fast-path for the common case where this chunk message carries
                # the context for exactly this event.
                event_context_id = context_id
                event_context_fields = context_fields
            if event_context_fields is None and event_context_id is None:
                event_context_id = current_context_id
                event_context_fields = current_context_fields
            else:
                current_context_id = event_context_id
                current_context_fields = (
                    dict(event_context_fields)
                    if isinstance(event_context_fields, dict)
                    else None
                )
            array = self._decode_array(reader, event)
            if array is None:
                continue
            t0_mono_ns = _normalize_int(event.get("t0_mono_ns"))
            t0_wall_ns = _normalize_int(event.get("t0_wall_ns"))
            event_t_mono_s = (
                float(t0_mono_ns) * 1e-9 if t0_mono_ns is not None else None
            )
            is_last_event = event is events[-1] if event_count > 0 else True
            now_mono = time.monotonic()

            for workspace_id in list(workspace_ids):
                workspace = self._workspaces.get(workspace_id)
                if workspace is None or not workspace.compiled.enabled:
                    continue
                include_hist_outputs = self._allow_hist_outputs_for_workspace(
                    workspace, now_mono=now_mono
                )
                include_trace_outputs = False
                if is_last_event:
                    include_trace_outputs = self._allow_trace_outputs_for_workspace(
                        workspace, now_mono=now_mono
                    )
                try:
                    output_payloads = self._execute_workspace_event(
                        workspace=workspace,
                        array=array,
                        context_fields=event_context_fields,
                        event_t_mono_s=event_t_mono_s,
                        include_hist_outputs=include_hist_outputs,
                        include_trace_outputs=include_trace_outputs,
                    )
                except Exception as exc:
                    workspace.dropped_samples += 1
                    self._publish_error(
                        workspace_id=workspace_id,
                        code="workspace_runtime_error",
                        message=str(exc),
                    )
                    continue

                workspace.processed_samples += 1
                self._processed_updates += 1

                for output in output_payloads:
                    self._publish_output_update(
                        output=output,
                        seq=seq,
                        t0_mono_ns=t0_mono_ns,
                        t0_wall_ns=t0_wall_ns,
                        context_id=event_context_id,
                        context_fields=event_context_fields,
                        device_id=device_id,
                        stream=stream,
                    )

        self._last_seq[key] = latest_seq
        self._prune_context_cache(key=key, last_seq=latest_seq)
        if current_context_id is None and current_context_fields is None:
            self._stream_context.pop(key, None)
        else:
            self._stream_context[key] = (
                int(current_context_id) if current_context_id is not None else None,
                dict(current_context_fields)
                if isinstance(current_context_fields, dict)
                else None,
            )

    def _drain_sub(self) -> int:
        processed = 0
        max_events = max(1, int(self._max_events_per_cycle))
        while True:
            if processed >= max_events:
                break
            try:
                topic_b, payload_b = self._sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except Exception:
                break
            processed += 1
            topic = topic_b.decode("utf-8", errors="replace")
            payload = safe_json_loads(payload_b)
            if not isinstance(payload, dict):
                continue
            if topic == "manager.chunk_ready":
                self._handle_chunk_ready(payload)
                continue
            if topic == "manager.telemetry_update":
                self._ingest_telemetry_update(payload)
        return processed

    def _reset_workspace_states(self, workspace: WorkspaceRuntime) -> None:
        for node_id, state in list(workspace.node_state.items()):
            if isinstance(state, BinStatsState):
                state.reset()
                continue
            if isinstance(state, Bin2DStatsState):
                state.reset()
                continue
            if isinstance(state, TraceRollingMeanState):
                state.reset()
                continue
            if isinstance(state, FitCurve1DState):
                state.reset()
                continue
            workspace.node_state.pop(node_id, None)

    def _reset_workspace_node_state(self, workspace: WorkspaceRuntime, node_id: str) -> bool:
        state = workspace.node_state.get(node_id)
        if isinstance(state, BinStatsState):
            state.reset()
            return True
        if isinstance(state, Bin2DStatsState):
            state.reset()
            return True
        if isinstance(state, TraceRollingMeanState):
            state.reset()
            return True
        if isinstance(state, FitCurve1DState):
            state.reset()
            return True
        return False

    def _status_payload(self) -> Json:
        return {
            "workspace_count": len(self._workspaces),
            "stream_count": len(self._stream_to_workspaces),
            "processed_updates": int(self._processed_updates),
            "dropped_updates": int(self._dropped_updates),
            "workspace_store": self._workspace_store_status_payload(),
            "workspaces": [
                self._workspace_summary(self._workspaces[key])
                for key in sorted(self._workspaces.keys())
            ],
        }

    def _handle_workspace_put(self, params: Json) -> Json:
        payload = (
            params.get("workspace") if isinstance(params.get("workspace"), dict) else params
        )
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": {"code": "invalid_params", "message": "workspace payload must be object"},
            }
        expected_raw = params.get("expected_revision")
        expected_revision: int | None = None
        if expected_raw is not None:
            expected_revision = _normalize_int(expected_raw)
            if expected_revision is None or expected_revision < 0:
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_params",
                        "message": "expected_revision must be a non-negative integer",
                    },
                }
        runtime = self._put_workspace_from_config(
            dict(payload),
            expected_revision=expected_revision,
            mark_dirty=True,
            publish=True,
        )
        return {
            "ok": True,
            "result": {
                "workspace": self._workspace_summary(runtime),
                "raw": runtime.raw_config,
            },
        }

    def _handle_workspace_validate(self, params: Json) -> Json:
        payload = (
            params.get("workspace") if isinstance(params.get("workspace"), dict) else params
        )
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": {"code": "invalid_params", "message": "workspace payload must be object"},
            }
        compiled = compile_workspace_graph(dict(payload))
        return {
            "ok": True,
            "result": {
                "workspace_id": compiled.workspace_id,
                "stream": {
                    "device_id": compiled.stream_key[0],
                    "stream": compiled.stream_key[1],
                },
                "node_count": len(compiled.nodes),
                "node_output_types": dict(compiled.node_output_types),
                "outputs": [
                    {
                        "output_id": out.output_id,
                        "node_id": out.node_id,
                        "kind": out.kind,
                    }
                    for out in compiled.outputs
                ],
            },
        }

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common

        request_id = req.get("request_id")
        rtype = str(req.get("type", ""))
        params = req.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._rpc_invalid_params(req, message="params must be a dict")

        try:
            if rtype == "process.capabilities":
                members = [
                    method("stream_analysis.status", params=None, doc="Get stream-analysis runtime status."),
                    method("stream_analysis.operators", params=None, doc="List available DAG operators."),
                    method("stream_analysis.workspace.list", params=None, doc="List workspaces."),
                    method(
                        "stream_analysis.workspace.get",
                        params=[
                            param(
                                "workspace_id",
                                required=True,
                                default=None,
                                annotation="str",
                            )
                        ],
                        doc="Get workspace config and summary.",
                    ),
                    method(
                        "stream_analysis.workspace.put",
                        params=[
                            param(
                                "workspace",
                                required=True,
                                default=None,
                                annotation="dict",
                            )
                        ],
                        doc="Create/update workspace graph.",
                    ),
                    method(
                        "stream_analysis.workspace.validate",
                        params=[
                            param(
                                "workspace",
                                required=True,
                                default=None,
                                annotation="dict",
                            )
                        ],
                        doc="Validate workspace graph without activating it.",
                    ),
                    method(
                        "stream_analysis.workspace.delete",
                        params=[
                            param(
                                "workspace_id",
                                required=True,
                                default=None,
                                annotation="str",
                            )
                        ],
                        doc="Delete a workspace.",
                    ),
                    method(
                        "stream_analysis.workspace.reset",
                        params=[
                            param("workspace_id", required=False, default=None, annotation="str"),
                            param("node_id", required=False, default=None, annotation="str"),
                        ],
                        doc="Reset workspace state (aggregates). If workspace_id omitted, reset all workspaces; if node_id provided, reset only that node in the workspace.",
                    ),
                    method(
                        "stream_analysis.workspace.clear",
                        params=None,
                        doc="Delete all workspaces.",
                    ),
                    method(
                        "stream_analysis.workspace_store.status",
                        params=None,
                        doc="Get workspace-store persistence status.",
                    ),
                    method(
                        "stream_analysis.workspace_store.save",
                        params=[
                            param("path", required=False, default=None, annotation="str"),
                        ],
                        doc="Save all workspaces to the configured workspace store YAML path.",
                    ),
                    method(
                        "stream_analysis.workspace_store.reload",
                        params=[
                            param("path", required=False, default=None, annotation="str"),
                        ],
                        doc="Reload all workspaces from workspace store YAML path.",
                    ),
                ]
                members = self._with_common_capabilities(members)
                return self._rpc_ok(req, result=capabilities_payload(members))

            if rtype == "stream_analysis.status":
                return {"request_id": request_id, "ok": True, "result": self._status_payload()}

            if rtype == "stream_analysis.operators":
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {"operators": operator_catalog_payload()},
                }

            if rtype == "stream_analysis.workspace.list":
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {
                        "workspaces": [
                            self._workspace_summary(self._workspaces[key])
                            for key in sorted(self._workspaces.keys())
                        ]
                    },
                }

            if rtype == "stream_analysis.workspace.get":
                workspace_id = _normalize_id(params.get("workspace_id"))
                if workspace_id is None:
                    return self._rpc_invalid_params(req, message="workspace_id is required")
                workspace = self._workspaces.get(workspace_id)
                if workspace is None:
                    return self._rpc_err(req, code="unknown_workspace")
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {
                        "workspace": self._workspace_summary(workspace),
                        "raw": workspace.raw_config,
                    },
                }

            if rtype == "stream_analysis.workspace.validate":
                result = self._handle_workspace_validate(params)
                if not result.get("ok"):
                    return {
                        "request_id": request_id,
                        "ok": False,
                        "error": result.get("error") or {"code": "validation_failed"},
                    }
                return {"request_id": request_id, **result}

            if rtype == "stream_analysis.workspace.put":
                result = self._handle_workspace_put(params)
                if not result.get("ok"):
                    return {
                        "request_id": request_id,
                        "ok": False,
                        "error": result.get("error") or {"code": "put_failed"},
                    }
                return {"request_id": request_id, **result}

            if rtype == "stream_analysis.workspace.delete":
                workspace_id = _normalize_id(params.get("workspace_id"))
                if workspace_id is None:
                    return self._rpc_invalid_params(req, message="workspace_id is required")
                expected_raw = params.get("expected_revision")
                expected_revision: int | None = None
                if expected_raw is not None:
                    expected_revision = _normalize_int(expected_raw)
                    if expected_revision is None or expected_revision < 0:
                        return self._rpc_invalid_params(
                            req,
                            message="expected_revision must be a non-negative integer",
                        )
                removed = self._delete_workspace(
                    workspace_id,
                    expected_revision=expected_revision,
                    mark_dirty=True,
                    publish=True,
                )
                if not removed:
                    return self._rpc_err(req, code="unknown_workspace")
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {"workspace_id": workspace_id, "deleted": True},
                }

            if rtype == "stream_analysis.workspace.reset":
                workspace_id = _normalize_id(params.get("workspace_id"))
                node_id = _normalize_id(params.get("node_id"))
                if workspace_id is None:
                    if node_id is not None:
                        return self._rpc_invalid_params(
                            req, message="node_id requires workspace_id"
                        )
                    for workspace in self._workspaces.values():
                        self._reset_workspace_states(workspace)
                    return {
                        "request_id": request_id,
                        "ok": True,
                        "result": {"reset": "all", "count": len(self._workspaces)},
                    }
                workspace = self._workspaces.get(workspace_id)
                if workspace is None:
                    return self._rpc_err(req, code="unknown_workspace")
                if node_id is not None:
                    ok = self._reset_workspace_node_state(workspace, node_id)
                    if not ok:
                        return self._rpc_err(req, code="unknown_or_non_stateful_node")
                    return {
                        "request_id": request_id,
                        "ok": True,
                        "result": {"reset": workspace_id, "node_id": node_id},
                    }
                self._reset_workspace_states(workspace)
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {"reset": workspace_id},
                }

            if rtype == "stream_analysis.workspace.clear":
                removed = self._clear_workspaces(mark_dirty=True, publish=True)
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {"removed": removed},
                }

            if rtype == "stream_analysis.workspace_store.status":
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": self._workspace_store_status_payload(),
                }

            if rtype == "stream_analysis.workspace_store.save":
                saved = self._save_workspace_store(path_override=params.get("path"))
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {
                        **saved,
                        "status": self._workspace_store_status_payload(),
                    },
                }

            if rtype == "stream_analysis.workspace_store.reload":
                override = self._normalize_workspace_store_path(params.get("path"))
                if override is not None:
                    self._workspace_store_path = override
                reloaded = self._reload_workspace_store(strict_missing=True)
                return {
                    "request_id": request_id,
                    "ok": True,
                    "result": {
                        **reloaded,
                        "status": self._workspace_store_status_payload(),
                    },
                }
        except WorkspaceRevisionConflict as exc:
            return {
                "request_id": request_id,
                "ok": False,
                "error": {
                    "code": "revision_conflict",
                    "message": str(exc),
                    "details": {
                        "workspace_id": exc.workspace_id,
                        "expected_revision": exc.expected_revision,
                        "current_revision": exc.current_revision,
                    },
                },
            }
        except Exception as exc:
            workspace_id = _normalize_id(params.get("workspace_id"))
            if isinstance(exc, FileNotFoundError):
                return self._rpc_err(req, code="workspace_store_not_found", message=str(exc))
            if isinstance(exc, ValueError) and "workspace_store_path" in str(exc):
                return self._rpc_err(
                    req,
                    code="workspace_store_not_configured",
                    message=str(exc),
                )
            self._publish_error(
                workspace_id=workspace_id,
                code="rpc_error",
                message=str(exc),
            )
            return self._rpc_err(req, code="rpc_error", message=str(exc))

        return self._rpc_unknown(req)

    def run(self) -> None:
        self._stop_evt.clear()
        try:
            while not self._stop_evt.is_set():
                events = self._poll_and_drain(200)
                if events.get(self._sub) == zmq.POLLIN:
                    self._drain_sub()
        finally:
            self.close()

    def close(self) -> None:
        for key in list(self._trace_writers.keys()):
            self._drop_trace_writer(key)
        self._trace_writers.clear()
        self._hist_last_emit_mono.clear()
        self._trace_last_emit_mono.clear()
        for reader in list(self._readers.values()):
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()
        self._last_seq.clear()
        self._stream_context.clear()
        self._context_by_seq.clear()
        self._telemetry_history.clear()

        try:
            self._sub.setsockopt(zmq.LINGER, 0)
            self._sub.close(0)
        except Exception:
            pass

        super().close()


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    proc = StreamAnalysisProcess(
        manager_rpc=ns.manager_rpc,
        manager_pub=ns.manager_pub,
        process_id=ns.process_id,
        rpc_timeout_ms=ns.rpc_timeout_ms,
        heartbeat_endpoint=ns.heartbeat_endpoint,
        process_data_endpoint=ns.process_data_endpoint,
        heartbeat_period_s=ns.heartbeat_period_s,
        max_payload_points=ns.max_payload_points,
        max_events_per_cycle=ns.max_events_per_cycle,
        max_hist_output_hz=ns.max_hist_output_hz,
        max_trace_output_hz=ns.max_trace_output_hz,
        rcvhwm=ns.rcvhwm,
        workspace_store_path=ns.workspace_store_path,
    )
    proc.run()


if __name__ == "__main__":
    main()
