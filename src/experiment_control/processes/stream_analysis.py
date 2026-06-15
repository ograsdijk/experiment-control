from __future__ import annotations

import argparse
import bisect
import math
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import zmq

from ..capabilities import capabilities_payload, method, param
from ..shm.shm_ring import ShmRingReader, ShmRingWriter, now_mono_ns, now_wall_ns
from ..utils.cli_args import (
    add_heartbeat_args,
    add_manager_args,
    add_process_id_arg,
    add_rpc_timeout_arg,
)
from ..utils.rpc_dispatch import RpcDispatchRegistry
from ..utils.yaml_helpers import load_yaml_file
from ..utils.zmq_helpers import safe_json_loads
from .manager_client_helper import ManagerClientHelper
from .process_base import ManagedProcessBase
from .stream_analysis_fit import (
    FitCurve1DState,
    _coerce_trace,
    _gate_open,
    _normalize_fit_param_name,
    _normalize_float,
    _normalize_int,
    _validate_fit_curve_params,
    _validate_fit_from_hist_params,
    execute_fit_curve_1d,
    execute_fit_from_hist_agg,
    execute_fit_param,
    execute_fit_params,
    execute_fit_xhat,
    execute_fit_xhat_dense,
    execute_fit_yhat,
    execute_fit_yhat_dense,
)

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
    stream_source_node_ids: list[str]
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
        for x_value, y_value in zip(self.samples_x, self.samples_y, strict=False):
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
                # SEM is undefined for n<=1 (a single sample has no
                # spread). Previously this returned 0 (std=0/sqrt(1)),
                # which falsely communicated "perfectly known mean" to
                # downstream consumers (UI error bars, fit weights).
                # Return NaN so consumers can render "n/a" or skip the
                # bin in weighted fits.
                sem = np.where(
                    self.counts > 1, std / np.sqrt(counts_f), np.nan
                )
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
            for bx, by, v in zip(
                xv.tolist(),
                yv.tolist(),
                zv.tolist(),
                strict=False,
            ):
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
            # SEM is undefined for n<=1 — see the 1D BinStatsState.payload
            # comment for rationale. Return NaN so consumers don't read
            # a single-sample bin as a perfectly-known mean.
            sem = np.where(self.counts > 1, std / np.sqrt(counts_f), np.nan)
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



OPS: dict[str, OpSpec] = {
    "source.stream": OpSpec(input_types={}, output_type="trace", stateful=False),
    "source.records": OpSpec(input_types={}, output_type="record", stateful=False),
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
    "record.field": OpSpec(
        input_types={"record": "record"}, output_type="scalar", stateful=False
    ),
    "record.filter_eq": OpSpec(
        input_types={"record": "record"}, output_type="scalar", stateful=False
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
    "trace.scale": OpSpec(input_types={"trace": "trace"}, output_type="trace", stateful=False),
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
    "source.records": [
        {"name": "device_id", "kind": "string", "required": True},
        {"name": "stream", "kind": "string", "required": True},
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
    "record.field": [
        {"name": "field", "kind": "string", "required": True},
    ],
    "record.filter_eq": [
        {"name": "field", "kind": "string", "required": True},
        {"name": "value", "kind": "number", "required": True},
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
    "trace.scale": [
        {"name": "factor", "kind": "number", "required": False, "default": 1.0},
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


def _structured_record_to_dict(array: np.ndarray) -> dict[str, Any]:
    dtype = array.dtype
    names = dtype.names or ()
    if not names:
        return {}
    record = np.asarray(array, dtype=dtype).reshape(())
    out: dict[str, Any] = {}
    for name in names:
        try:
            value = record[name].item()
        except Exception:
            value = None
        out[str(name)] = value
    return out


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


def execute_trace_scale(trace_raw: Any, params: Json) -> np.ndarray | None:
    """Multiply a trace by a literal `factor` (default 1.0).

    `factor: -1.0` negates a trace — used as a per-channel polarity step so
    negative-going detector traces can be flipped to a positive-going convention
    (the op set has no other constant/negate primitive).
    """
    trace = _coerce_trace(trace_raw)
    if trace is None:
        return None
    factor = _normalize_float(params.get("factor"))
    if factor is None:
        factor = 1.0
    return trace * factor


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


def _decimate_trace_stride(points: np.ndarray, *, target_points: int) -> np.ndarray:
    n = int(points.size)
    step = max(1, int(math.ceil(float(n) / float(target_points))))
    out = points[::step]
    if out.size > 0 and float(out[-1]) != float(points[-1]):
        out = np.concatenate([out, points[-1:]])
    return out[:target_points]


def _decimate_trace_mean(points: np.ndarray, *, target_points: int) -> np.ndarray:
    n = int(points.size)
    bucket_count = max(1, min(target_points, n))
    out = np.zeros(bucket_count, dtype=np.float64)
    out_count = 0
    for start, stop in _bucket_ranges(n, bucket_count):
        chunk = points[start:stop]
        if chunk.size <= 0:
            continue
        out[out_count] = float(np.mean(chunk, dtype=np.float64))
        out_count += 1
    return out[:out_count]


def _decimate_trace_m4(points: np.ndarray, *, target_points: int) -> np.ndarray:
    n = int(points.size)
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


def _decimate_trace_minmax(points: np.ndarray, *, target_points: int) -> np.ndarray:
    n = int(points.size)
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


def _decimate_trace(points: np.ndarray, *, method: str, target_points: int) -> np.ndarray:
    if method == "stride":
        return _decimate_trace_stride(points, target_points=target_points)
    if method == "mean":
        return _decimate_trace_mean(points, target_points=target_points)
    if method == "m4":
        return _decimate_trace_m4(points, target_points=target_points)
    return _decimate_trace_minmax(points, target_points=target_points)


def execute_trace_decimate(trace_raw: Any, params: Json) -> np.ndarray | None:
    trace = _coerce_trace(trace_raw)
    if trace is None:
        return None
    method, target_points = _validate_trace_decimate_params(params)
    n = int(trace.size)
    if n <= 0 or n <= target_points:
        return trace
    points = trace.astype(np.float64, copy=False)
    return _decimate_trace(points, method=method, target_points=target_points)


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
        node_id = _normalize_id(raw.get("node_id"))
    if node_id is None:
        raise ValueError(f"graph.nodes[{index}].id is required")
    op = _normalize_id(raw.get("op"))
    if op is None:
        raise ValueError(f"graph.nodes[{index}].op is required")
    params = _normalize_node_params(raw.get("params"), index=index)
    inputs = _normalize_node_inputs(raw.get("inputs"), index=index)
    op = _normalize_node_legacy_op(op=op, params=params)

    return NodeSpec(node_id=node_id, op=op, params=params, inputs=inputs)


def _normalize_node_params(params_raw: Any, *, index: int) -> Json:
    if params_raw is None:
        return {}
    if isinstance(params_raw, dict):
        return dict(params_raw)
    raise ValueError(f"graph.nodes[{index}].params must be an object")


def _normalize_node_inputs(inputs_raw: Any, *, index: int) -> dict[str, str]:
    if inputs_raw is None:
        return {}
    if not isinstance(inputs_raw, dict):
        raise ValueError(f"graph.nodes[{index}].inputs must be an object")
    inputs: dict[str, str] = {}
    for key, value in inputs_raw.items():
        port = _normalize_id(key)
        source = _normalize_id(value)
        if port is None or source is None:
            raise ValueError(f"graph.nodes[{index}].inputs entries must be strings")
        inputs[port] = source
    return inputs


def _normalize_node_legacy_op(*, op: str, params: Json) -> str:
    # Legacy migration: old dedicated stream reducer ops are folded into source.stream.
    if op == "source.stream_average":
        params.setdefault("channel_mode", "average")
        return "source.stream"
    if op == "source.stream_sum":
        params.setdefault("channel_mode", "sum")
        return "source.stream"
    return op


def _parse_workspace_root(config: Json) -> tuple[str, bool, list[NodeSpec], dict[str, NodeSpec]]:
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
    return workspace_id, enabled, nodes_list, nodes


def _compile_workspace_dependencies(
    *, nodes_list: list[NodeSpec], nodes: dict[str, NodeSpec]
) -> dict[str, set[str]]:
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
    return deps


def _compile_workspace_order(deps: dict[str, set[str]]) -> list[str]:
    try:
        sorter = TopologicalSorter(deps)
        return list(sorter.static_order())
    except Exception as exc:
        raise ValueError(f"graph is cyclic: {exc}") from exc


def _validate_compiled_node(
    *,
    node: NodeSpec,
    out_type: dict[str, str],
    source_stream_nodes: list[str],
) -> None:
    sig = _node_signature(node.op)
    _validate_node_input_contract(node=node, sig=sig, out_type=out_type)
    _validate_node_op_params(node=node, source_stream_nodes=source_stream_nodes)
    out_type[node.node_id] = sig.output_type


def _validate_node_input_contract(
    *,
    node: NodeSpec,
    sig: OpSpec,
    out_type: dict[str, str],
) -> None:
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
        _validate_node_input_type(
            node=node,
            sig=sig,
            out_type=out_type,
            port=port,
            source_id=source_id,
        )


def _validate_node_input_type(
    *,
    node: NodeSpec,
    sig: OpSpec,
    out_type: dict[str, str],
    port: str,
    source_id: str,
) -> None:
    if _is_special_input_source(node.op, port, source_id):
        return
    expected_type = sig.input_types.get(port) or sig.optional_input_types.get(port)
    if expected_type is None:
        raise ValueError(f"node {node.node_id!r} has unknown input port {port!r}")
    source_type = out_type.get(source_id)
    if source_type != expected_type:
        raise ValueError(
            f"node {node.node_id!r} input {port!r} expects {expected_type}, "
            f"got {source_type} from {source_id!r}"
        )


def _validate_node_op_params(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    validator = _NODE_OP_PARAM_VALIDATORS.get(node.op)
    if validator is None:
        return
    validator(node=node, source_stream_nodes=source_stream_nodes)


def _validate_source_stream_node(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    source_stream_nodes.append(node.node_id)
    did = _normalize_id(node.params.get("device_id"))
    stream = _normalize_id(node.params.get("stream"))
    if did is None or stream is None:
        raise ValueError(f"node {node.node_id!r} {node.op} requires device_id and stream")
    _ = _parse_stream_source_mode(node.params.get("channel_mode"))
    _ = _parse_channel_indices(node.params.get("channel_indices"))


def _validate_source_records_node(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    source_stream_nodes.append(node.node_id)
    did = _normalize_id(node.params.get("device_id"))
    stream = _normalize_id(node.params.get("stream"))
    if did is None or stream is None:
        raise ValueError(f"node {node.node_id!r} {node.op} requires device_id and stream")


def _validate_source_context_field_node(node: NodeSpec) -> None:
    field = _normalize_id(node.params.get("field"))
    if field is None:
        raise ValueError(f"node {node.node_id!r} source.context_field requires field")


def _validate_source_telemetry_nearest_node(node: NodeSpec) -> None:
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


def _validate_node_scalar_threshold(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = _validate_scalar_threshold_params(node.params)


def _validate_node_trace_rolling_mean(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = TraceRollingMeanState.from_params(node.params)


def _validate_node_trace_decimate(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = _validate_trace_decimate_params(node.params)


def _validate_node_fit_curve_1d(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = _validate_fit_curve_params(node.params)


def _validate_node_fit_from_hist(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = _validate_fit_from_hist_params(node.params)


def _validate_node_fit_param(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = _normalize_fit_param_name(node.params.get("name", "center"))


def _validate_node_aggregate_bin_stats(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = BinStatsState.from_params(node.params)


def _validate_node_aggregate_bin2d_stats(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _ = Bin2DStatsState.from_params(node.params)


def _validate_node_source_context_field(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _validate_source_context_field_node(node)


def _validate_node_source_telemetry_nearest(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    _validate_source_telemetry_nearest_node(node)


def _validate_node_record_field(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    field = _normalize_id(node.params.get("field"))
    if field is None:
        raise ValueError(f"node {node.node_id!r} {node.op} requires field")


def _validate_node_record_filter_eq(
    *,
    node: NodeSpec,
    source_stream_nodes: list[str],
) -> None:
    del source_stream_nodes
    field = _normalize_id(node.params.get("field"))
    if field is None:
        raise ValueError(f"node {node.node_id!r} {node.op} requires field")


# All validators take node + source_stream_nodes as keyword-only args.
# The Protocol below documents the exact signature so the dict's value
# type matches what's actually invoked at the call site (kwargs).
class _NodeValidator(Protocol):
    def __call__(
        self, *, node: NodeSpec, source_stream_nodes: list[str]
    ) -> None: ...


_NODE_OP_PARAM_VALIDATORS: dict[str, _NodeValidator] = {
    "source.stream": _validate_source_stream_node,
    "source.records": _validate_source_records_node,
    "source.context_field": _validate_node_source_context_field,
    "source.telemetry_nearest": _validate_node_source_telemetry_nearest,
    "scalar.threshold": _validate_node_scalar_threshold,
    "record.field": _validate_node_record_field,
    "record.filter_eq": _validate_node_record_filter_eq,
    "trace.rolling_mean": _validate_node_trace_rolling_mean,
    "trace.decimate": _validate_node_trace_decimate,
    "fit.curve_1d": _validate_node_fit_curve_1d,
    "fit.from_hist_agg": _validate_node_fit_from_hist,
    "fit.param": _validate_node_fit_param,
    "aggregate.bin_stats": _validate_node_aggregate_bin_stats,
    "aggregate.bin2d_stats": _validate_node_aggregate_bin2d_stats,
}


def _compile_workspace_outputs(
    *,
    config: Json,
    nodes: dict[str, NodeSpec],
    out_type: dict[str, str],
) -> list[PublishOutput]:
    outputs_raw = _compile_workspace_outputs_raw(config)
    outputs: list[PublishOutput] = []
    output_ids: set[str] = set()
    for idx, item in enumerate(outputs_raw):
        output = _compile_workspace_output_item(
            item=item,
            idx=idx,
            nodes=nodes,
            out_type=out_type,
            seen_output_ids=output_ids,
        )
        outputs.append(output)
    return outputs


def _compile_workspace_outputs_raw(config: Json) -> list[Any]:
    publish_raw = config.get("publish")
    publish = publish_raw if isinstance(publish_raw, dict) else {}
    outputs_raw = publish.get("outputs")
    if outputs_raw is None:
        return []
    if not isinstance(outputs_raw, list):
        raise ValueError("publish.outputs must be a list")
    return outputs_raw


def _compile_workspace_output_item(
    *,
    item: Any,
    idx: int,
    nodes: dict[str, NodeSpec],
    out_type: dict[str, str],
    seen_output_ids: set[str],
) -> PublishOutput:
    if not isinstance(item, dict):
        raise ValueError(f"publish.outputs[{idx}] must be an object")
    output_id = _normalize_id(item.get("output_id"))
    if output_id is None:
        raise ValueError(f"publish.outputs[{idx}].output_id is required")
    if output_id in seen_output_ids:
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
        raise ValueError(f"publish.outputs[{idx}].node_id has no output type {node_id!r}")
    if kind not in {"scalar", "hist_agg", "hist2d", "trace", "params_map", "fit_1d"}:
        raise ValueError(
            f"publish.outputs[{idx}].node_id type {kind!r} is not publishable in v1"
        )
    seen_output_ids.add(output_id)
    return PublishOutput(output_id=output_id, node_id=node_id, kind=kind)


def _prune_unreachable_nodes(
    *,
    order: list[str],
    deps: dict[str, set[str]],
    outputs: list[PublishOutput],
    source_stream_node_ids: list[str],
) -> list[str]:
    """Return `order` filtered to nodes reachable from any published output.

    Per-event execution walks every node in `order`; a node that no output
    transitively depends on does nothing observable but still pays its
    dispatch + handler cost on every chunk_ready. Pruning is purely a
    compile-time optimization — runtime behaviour is unchanged for any
    workspace whose output set genuinely depends on every node (the common
    case for UI-built workspaces).

    All source stream nodes are always considered reachable so the per-event
    loop keeps reading each subscribed channel selection even when nothing
    downstream consumes it directly.

    Stateful operators (fits, aggregates) are preserved as long as they
    sit on a path to a published output — they need to run on every event
    to keep their internal state coherent.
    """
    reachable: set[str] = set(source_stream_node_ids)
    queue: list[str] = [out.node_id for out in outputs] + list(source_stream_node_ids)
    while queue:
        node_id = queue.pop()
        # `reachable` is a set so `add` is a no-op for already-visited
        # nodes; the `dep not in reachable` guard below keeps us from
        # re-enqueuing them, so the walk still terminates.
        reachable.add(node_id)
        for dep in deps.get(node_id, ()):  # type: ignore[arg-type]
            if dep not in reachable:
                queue.append(dep)
    return [node_id for node_id in order if node_id in reachable]


def compile_workspace_graph(config: Json) -> CompiledWorkspace:
    workspace_id, enabled, nodes_list, nodes = _parse_workspace_root(config)
    deps = _compile_workspace_dependencies(nodes_list=nodes_list, nodes=nodes)
    order = _compile_workspace_order(deps)
    out_type: dict[str, str] = {}
    source_stream_nodes: list[str] = []
    for node_id in order:
        node = nodes[node_id]
        _validate_compiled_node(
            node=node,
            out_type=out_type,
            source_stream_nodes=source_stream_nodes,
        )

    if not source_stream_nodes:
        raise ValueError("graph must contain at least one source stream node")
    # Multiple source.stream nodes are allowed (e.g. one channel selection for
    # fluorescence, another for absorption), but they must all read the SAME
    # (device_id, stream) — a workspace subscribes to exactly one stream.
    stream_keys = {
        (
            str(nodes[node_id].params["device_id"]).strip(),
            str(nodes[node_id].params["stream"]).strip(),
        )
        for node_id in source_stream_nodes
    }
    if len(stream_keys) != 1:
        raise ValueError(
            "all source.stream nodes must reference the same device_id/stream"
        )
    stream_key = next(iter(stream_keys))
    outputs = _compile_workspace_outputs(config=config, nodes=nodes, out_type=out_type)

    # Prune nodes that don't feed any published output. Done after
    # outputs are compiled so we have the full set of consumer node_ids
    # to walk backwards from.
    order = _prune_unreachable_nodes(
        order=order,
        deps=deps,
        outputs=outputs,
        source_stream_node_ids=source_stream_nodes,
    )

    return CompiledWorkspace(
        workspace_id=workspace_id,
        enabled=enabled,
        nodes=nodes,
        order=order,
        stream_source_node_ids=source_stream_nodes,
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
        # Per-stream bucket type. OrderedDict gives O(1) "drop oldest"
        # eviction via popitem(last=False); the prior dict + sorted(keys())
        # eviction was O(n log n) per insert past capacity, which dominated
        # CPU on high-frequency streams once the bucket filled.
        self._context_by_seq: dict[
            tuple[str, str],
            "OrderedDict[int, tuple[int | None, dict[str, Any] | None]]",
        ] = {}
        self._context_cache_limit = 8192
        self._telemetry_history: dict[tuple[str, str], list[tuple[float, float]]] = {}
        self._telemetry_history_max_points = 4096
        self._telemetry_history_max_age_s = 300.0
        self._telemetry_history_prune_period_s = 30.0
        self._telemetry_history_last_prune_mono = 0.0
        self._latest_output_payloads: dict[tuple[str, str], Json] = {}

        self._processed_updates = 0
        self._dropped_updates = 0
        self._rpc_registry = self._build_rpc_registry()
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
        source_items = StreamAnalysisProcess._workspace_store_source_items(raw)
        entries = [
            StreamAnalysisProcess._workspace_store_normalize_entry(key=key, item=item)
            for key, item in source_items
        ]
        entries.sort(key=lambda item: str(item.get("workspace_id", "")))
        return entries

    @staticmethod
    def _workspace_store_source_items(raw: Any) -> list[tuple[str | None, Any]]:
        if isinstance(raw, list):
            return [(None, item) for item in raw]
        if not isinstance(raw, dict):
            raise ValueError("workspace store payload must be a dict or list")
        if "workspaces" in raw:
            return StreamAnalysisProcess._workspace_store_items_from_workspaces(
                raw.get("workspaces")
            )
        if "workspace_id" in raw and "graph" in raw:
            return [(None, raw)]
        if len(raw) == 0:
            return []
        raise ValueError("workspace store must contain workspaces list or mapping")

    @staticmethod
    def _workspace_store_items_from_workspaces(
        workspaces_raw: Any,
    ) -> list[tuple[str | None, Any]]:
        if workspaces_raw is None:
            return []
        if isinstance(workspaces_raw, list):
            return [(None, item) for item in workspaces_raw]
        if isinstance(workspaces_raw, dict):
            return list(workspaces_raw.items())
        raise ValueError("workspace store workspaces must be list or mapping")

    @staticmethod
    def _workspace_store_normalize_entry(*, key: str | None, item: Any) -> Json:
        if not isinstance(item, dict):
            raise ValueError("workspace entry must be an object")
        cfg = dict(item)
        if _normalize_id(cfg.get("workspace_id")) is None and key is not None:
            cfg["workspace_id"] = str(key).strip()
        workspace_id = _normalize_id(cfg.get("workspace_id"))
        if workspace_id is None:
            raise ValueError("workspace entry missing workspace_id")
        return cfg

    def _clear_workspaces(self, *, mark_dirty: bool, publish: bool) -> list[str]:
        removed = sorted(self._workspaces.keys())
        self._workspaces.clear()
        self._latest_output_payloads.clear()
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
        self._reconcile_stream_runtime_keys()

    def _reconcile_stream_runtime_keys(self) -> None:
        active = set(self._stream_to_workspaces.keys())
        for key in list(self._readers.keys()):
            if key in active:
                continue
            reader = self._readers.pop(key, None)
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass
            self._last_seq.pop(key, None)
            self._stream_context.pop(key, None)
            self._context_by_seq.pop(key, None)

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
        self._validate_workspace_expected_revision(
            workspace_id=compiled.workspace_id,
            existing=existing,
            expected_revision=expected_revision,
        )
        existing_for_state = self._workspace_reusable_existing(
            existing=existing,
            compiled=compiled,
        )
        node_state = self._workspace_reused_node_state(
            existing=existing_for_state,
            compiled=compiled,
        )
        runtime = self._workspace_runtime_from_compiled(
            compiled=compiled,
            config=config,
            existing=existing_for_state,
            node_state=node_state,
        )
        self._workspace_init_stateful_nodes(runtime)

        self._workspaces[compiled.workspace_id] = runtime
        self._prune_workspace_snapshot_outputs(runtime)
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

    def _validate_workspace_expected_revision(
        self,
        *,
        workspace_id: str,
        existing: WorkspaceRuntime | None,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            return
        if existing is None:
            if int(expected_revision) != 0:
                raise WorkspaceRevisionConflict(
                    workspace_id=workspace_id,
                    expected_revision=int(expected_revision),
                    current_revision=None,
                )
            return
        current_revision = int(existing.revision)
        if int(expected_revision) != current_revision:
            raise WorkspaceRevisionConflict(
                workspace_id=workspace_id,
                expected_revision=int(expected_revision),
                current_revision=current_revision,
            )

    def _workspace_reusable_existing(
        self,
        *,
        existing: WorkspaceRuntime | None,
        compiled: CompiledWorkspace,
    ) -> WorkspaceRuntime | None:
        if existing is None:
            return None
        if not self._workspace_graph_unchanged(existing.compiled, compiled):
            return None
        return existing

    @staticmethod
    def _workspace_graph_unchanged(
        old_compiled: CompiledWorkspace,
        new_compiled: CompiledWorkspace,
    ) -> bool:
        if set(old_compiled.nodes.keys()) != set(new_compiled.nodes.keys()):
            return False
        for node_id, old_node in old_compiled.nodes.items():
            new_node = new_compiled.nodes.get(node_id)
            if new_node is None:
                return False
            if old_node.op != new_node.op:
                return False
            if old_node.params != new_node.params:
                return False
            if old_node.inputs != new_node.inputs:
                return False
        return True

    @staticmethod
    def _workspace_reused_node_state(
        *,
        existing: WorkspaceRuntime | None,
        compiled: CompiledWorkspace,
    ) -> dict[str, Any]:
        if existing is None:
            return {}
        node_state: dict[str, Any] = {}
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
        return node_state

    def _workspace_runtime_from_compiled(
        self,
        *,
        compiled: CompiledWorkspace,
        config: Json,
        existing: WorkspaceRuntime | None,
        node_state: dict[str, Any],
    ) -> WorkspaceRuntime:
        revision = (existing.revision + 1) if existing is not None else 1
        runtime = WorkspaceRuntime(
            compiled=compiled,
            raw_config={},
            node_state=node_state,
            processed_samples=existing.processed_samples if existing else 0,
            dropped_samples=existing.dropped_samples if existing else 0,
            revision=revision,
            etag=self._workspace_etag(compiled.workspace_id, revision),
        )
        runtime.raw_config = self._workspace_normalized_raw_config(
            config=config,
            compiled=compiled,
        )
        return runtime

    @staticmethod
    def _workspace_normalized_raw_config(
        *,
        config: Json,
        compiled: CompiledWorkspace,
    ) -> Json:
        raw_cfg = _sanitize_json(dict(config))
        raw_cfg["workspace_id"] = compiled.workspace_id
        raw_cfg["enabled"] = bool(compiled.enabled)
        if not isinstance(raw_cfg.get("graph"), dict):
            raw_cfg["graph"] = {}
        if not isinstance(raw_cfg.get("publish"), dict):
            raw_cfg["publish"] = {}
        return raw_cfg

    def _workspace_init_stateful_nodes(self, runtime: WorkspaceRuntime) -> None:
        for node_id in runtime.compiled.order:
            if node_id in runtime.node_state:
                continue
            node = runtime.compiled.nodes[node_id]
            spec = OPS[node.op]
            if not spec.stateful:
                continue
            state = self._workspace_new_stateful_node(node)
            if state is not None:
                runtime.node_state[node_id] = state

    @staticmethod
    def _workspace_new_stateful_node(node: NodeSpec) -> Any:
        if node.op == "aggregate.bin_stats":
            return BinStatsState.from_params(node.params)
        if node.op == "aggregate.bin2d_stats":
            return Bin2DStatsState.from_params(node.params)
        if node.op == "trace.rolling_mean":
            return TraceRollingMeanState.from_params(node.params)
        if node.op in {"fit.curve_1d", "fit.from_hist_agg"}:
            return FitCurve1DState.from_params(node.params)
        return None

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
        self._clear_workspace_snapshot_outputs(workspace_id)
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

    @staticmethod
    def _snapshot_output_key(workspace_id: str, output_id: str) -> tuple[str, str]:
        return (str(workspace_id).strip(), str(output_id).strip())

    def _remember_latest_output(self, payload: Json) -> None:
        workspace_id = _normalize_id(payload.get("workspace_id"))
        output_id = _normalize_id(payload.get("output_id"))
        if workspace_id is None or output_id is None:
            return
        self._latest_output_payloads[self._snapshot_output_key(workspace_id, output_id)] = (
            _sanitize_json(dict(payload))
        )

    def _remember_latest_output_clean(self, payload: Json) -> None:
        """Snapshot-store a payload whose values are already JSON-clean.

        Same shape as `_remember_latest_output` but skips the recursive
        `_sanitize_json` walk. Callers MUST guarantee that every value
        in the payload (including nested lists/dicts) is already free
        of NaN/Inf — typically because each upstream value-producing
        path sanitised at build time.

        Used by the trace-output snapshot path where the value list is
        sanitised inline in `_build_trace_snapshot_payload` (200k-point
        traces saw ~68 ms per snapshot walk in the bench; this path
        eliminates that).
        """
        workspace_id = _normalize_id(payload.get("workspace_id"))
        output_id = _normalize_id(payload.get("output_id"))
        if workspace_id is None or output_id is None:
            return
        self._latest_output_payloads[self._snapshot_output_key(workspace_id, output_id)] = (
            dict(payload)
        )

    def _clear_workspace_snapshot_outputs(
        self, workspace_id: str, *, node_id: str | None = None
    ) -> None:
        workspace_id_text = str(workspace_id).strip()
        if not workspace_id_text:
            return
        if node_id is None:
            keys = [
                key
                for key in self._latest_output_payloads.keys()
                if key[0] == workspace_id_text
            ]
            for key in keys:
                self._latest_output_payloads.pop(key, None)
            return
        node_id_text = str(node_id).strip()
        if not node_id_text:
            return
        keys = []
        for key, payload in self._latest_output_payloads.items():
            if key[0] != workspace_id_text:
                continue
            if str(payload.get("node_id") or "").strip() != node_id_text:
                continue
            keys.append(key)
        for key in keys:
            self._latest_output_payloads.pop(key, None)

    def _prune_workspace_snapshot_outputs(self, workspace: WorkspaceRuntime) -> None:
        workspace_id = workspace.compiled.workspace_id
        valid_ids = {str(out.output_id) for out in workspace.compiled.outputs}
        keys = [
            key
            for key in self._latest_output_payloads.keys()
            if key[0] == workspace_id and key[1] not in valid_ids
        ]
        for key in keys:
            self._latest_output_payloads.pop(key, None)

    @staticmethod
    def _normalize_snapshot_filter_set(raw: Any) -> set[str] | None:
        if raw is None:
            return None
        values: set[str] = set()
        if isinstance(raw, str):
            for part in raw.split(","):
                text = part.strip()
                if text:
                    values.add(text)
        elif isinstance(raw, list):
            for item in raw:
                text = str(item or "").strip()
                if text:
                    values.add(text)
        else:
            text = str(raw or "").strip()
            if text:
                values.add(text)
        return values if values else None

    @staticmethod
    def _snapshot_trace_max_points(raw: Any) -> int | None:
        if raw is None:
            return None
        parsed = _normalize_int(raw)
        if parsed is None or parsed <= 0:
            return None
        return max(32, min(20000, int(parsed)))

    @staticmethod
    def _decimate_snapshot_trace(values_raw: Any, *, max_points: int) -> list[float] | None:
        trace = _coerce_trace(values_raw)
        if trace is None:
            return None
        if trace.size <= int(max_points):
            return trace.astype(np.float64, copy=False).reshape(-1).tolist()
        step = max(1, int(math.ceil(float(trace.size) / float(max_points))))
        decimated = trace.reshape(-1)[::step]
        if decimated.size > 0 and float(decimated[-1]) != float(trace.reshape(-1)[-1]):
            decimated = np.concatenate([decimated, trace.reshape(-1)[-1:]])
        if decimated.size > int(max_points):
            decimated = decimated[: int(max_points)]
        return decimated.astype(np.float64, copy=False).tolist()

    def _workspace_snapshot_payload(self, params: Json) -> Json:
        workspace_id = _normalize_id(params.get("workspace_id"))
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise KeyError(workspace_id)

        kinds_filter = self._normalize_snapshot_filter_set(params.get("kinds"))
        output_ids_filter = self._normalize_snapshot_filter_set(params.get("output_ids"))
        max_trace_points = self._snapshot_trace_max_points(params.get("max_trace_points"))

        outputs: list[Json] = []
        for output in workspace.compiled.outputs:
            if output_ids_filter is not None and output.output_id not in output_ids_filter:
                continue
            key = self._snapshot_output_key(workspace_id, output.output_id)
            cached = self._latest_output_payloads.get(key)
            if not isinstance(cached, dict):
                continue
            kind = str(cached.get("kind") or output.kind).strip()
            if kinds_filter is not None and kind not in kinds_filter:
                continue
            item_raw = dict(cached)
            if kind == "trace" and max_trace_points is not None:
                original_values = item_raw.get("value")
                values = self._decimate_snapshot_trace(
                    original_values, max_points=max_trace_points
                )
                if values is None:
                    continue
                original_len = (
                    len(original_values) if isinstance(original_values, list) else len(values)
                )
                item_raw["value"] = values
                item_raw["point_count"] = int(len(values))
                if int(original_len) > int(len(values)):
                    item_raw["truncated"] = True
            outputs.append(_sanitize_json(item_raw))

        return {
            "workspace_id": workspace_id,
            "revision": int(workspace.revision),
            "etag": str(workspace.etag),
            "outputs": outputs,
            "generated_ts": {"t_wall": time.time(), "t_mono": time.monotonic()},
        }

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
        bucket = self._context_by_seq.setdefault(key, OrderedDict())
        seq_key = int(seq)
        # If the seq already has an entry, remove it first so the new value
        # lands at the end of the insertion order; otherwise OrderedDict
        # would keep the old position and break the oldest-first invariant.
        if seq_key in bucket:
            del bucket[seq_key]
        bucket[seq_key] = (
            int(context_id) if context_id is not None else None,
            dict(context_fields) if isinstance(context_fields, dict) else None,
        )
        while len(bucket) > self._context_cache_limit:
            bucket.popitem(last=False)

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

    def _prune_telemetry_history(self, *, now_mono: float | None = None) -> None:
        if not self._telemetry_history:
            return
        now = float(now_mono) if now_mono is not None else float(time.monotonic())
        if now < (
            self._telemetry_history_last_prune_mono
            + self._telemetry_history_prune_period_s
        ):
            return
        self._telemetry_history_last_prune_mono = now
        max_age_s = float(self._telemetry_history_max_age_s)
        if not (math.isfinite(max_age_s) and max_age_s > 0):
            return
        cutoff = now - max_age_s
        for key in list(self._telemetry_history.keys()):
            samples = self._telemetry_history.get(key)
            if not samples:
                self._telemetry_history.pop(key, None)
                continue
            trim_idx = bisect.bisect_left(samples, (cutoff, -math.inf))
            if trim_idx > 0:
                del samples[:trim_idx]
            if not samples:
                self._telemetry_history.pop(key, None)

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
        self._prune_telemetry_history(now_mono=time.monotonic())

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

    def _execute_workspace_node(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        array: np.ndarray,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        include_hist_outputs: bool,
    ) -> tuple[int, int] | None:
        handlers = (
            self._execute_workspace_node_source_ops,
            self._execute_workspace_node_scalar_ops,
            self._execute_workspace_node_trace_ops,
            self._execute_workspace_node_fit_ops,
            self._execute_workspace_node_aggregate_ops,
        )
        for handler in handlers:
            handled, source_update = handler(
                workspace=workspace,
                node=node,
                node_id=node_id,
                values=values,
                array=array,
                context_fields=context_fields,
                event_t_mono_s=event_t_mono_s,
                include_hist_outputs=include_hist_outputs,
            )
            if handled:
                return source_update
        raise RuntimeError(f"unexpected op {node.op!r}")

    def _execute_workspace_node_source_ops(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        array: np.ndarray,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        include_hist_outputs: bool,
    ) -> tuple[bool, tuple[int, int] | None]:
        del include_hist_outputs
        op = node.op
        if op == "source.stream":
            source_update = self._workspace_node_source_stream(
                workspace=workspace,
                node=node,
                node_id=node_id,
                values=values,
                array=array,
            )
            return True, source_update
        if op == "source.records":
            values[node_id] = _structured_record_to_dict(array)
            return True, (0, 1)
        if op == "source.context_field":
            field = _normalize_id(node.params.get("field"))
            scalar = None
            if field is not None and isinstance(context_fields, dict):
                scalar = _normalize_float(context_fields.get(field))
            values[node_id] = scalar
            return True, None
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
            return True, None
        return False, None

    def _workspace_node_source_stream(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        array: np.ndarray,
    ) -> tuple[int, int]:
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
        return source_channel_index, source_channel_count

    def _execute_workspace_node_scalar_ops(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        array: np.ndarray,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        include_hist_outputs: bool,
    ) -> tuple[bool, tuple[int, int] | None]:
        del workspace, array, context_fields, event_t_mono_s, include_hist_outputs
        op_name = {
            "scalar.add": "add",
            "scalar.subtract": "subtract",
            "scalar.multiply": "multiply",
            "scalar.divide": "divide",
        }.get(node.op)
        if op_name is not None:
            values[node_id] = execute_scalar_binary(
                values.get(node.inputs["a"]),
                values.get(node.inputs["b"]),
                op=op_name,
            )
            return True, None
        if node.op == "scalar.threshold":
            threshold, mode = _validate_scalar_threshold_params(node.params)
            values[node_id] = execute_scalar_threshold(
                values.get(node.inputs["x"]),
                threshold=threshold,
                mode=mode,
            )
            return True, None
        if node.op == "record.field":
            record = values.get(node.inputs["record"])
            field = _normalize_id(node.params.get("field"))
            value = record.get(field) if isinstance(record, dict) and field else None
            values[node_id] = _normalize_float(value)
            return True, None
        if node.op == "record.filter_eq":
            record = values.get(node.inputs["record"])
            field = _normalize_id(node.params.get("field"))
            expected = node.params.get("value")
            actual = record.get(field) if isinstance(record, dict) and field else None
            values[node_id] = 1.0 if actual == expected else 0.0
            return True, None
        return False, None

    def _execute_workspace_node_trace_ops(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        array: np.ndarray,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        include_hist_outputs: bool,
    ) -> tuple[bool, tuple[int, int] | None]:
        del array, context_fields, event_t_mono_s, include_hist_outputs
        op = node.op
        if op == "trace.divide":
            values[node_id] = execute_trace_divide(
                values.get(node.inputs["a"]),
                values.get(node.inputs["b"]),
            )
            return True, None
        scalar_op = {
            "trace.add_scalar": "add",
            "trace.subtract_scalar": "subtract",
            "trace.multiply_scalar": "multiply",
            "trace.divide_scalar": "divide",
        }.get(op)
        if scalar_op is not None:
            values[node_id] = execute_trace_scalar_math(
                values.get(node.inputs["trace"]),
                values.get(node.inputs["scalar"]),
                op=scalar_op,
            )
            return True, None
        if op == "trace.rolling_mean":
            src = values.get(node.inputs["trace"])
            state = workspace.node_state.get(node_id)
            if not isinstance(state, TraceRollingMeanState):
                state = TraceRollingMeanState.from_params(node.params)
                workspace.node_state[node_id] = state
            values[node_id] = state.update(src)
            return True, None
        return self._execute_workspace_node_trace_unary_ops(
            node=node,
            node_id=node_id,
            values=values,
        )

    @staticmethod
    def _execute_workspace_node_trace_unary_ops(
        *,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
    ) -> tuple[bool, tuple[int, int] | None]:
        op = node.op
        src = values.get(node.inputs["trace"]) if "trace" in node.inputs else None
        if op == "trace.decimate":
            values[node_id] = execute_trace_decimate(src, node.params)
            return True, None
        if op == "trace.crop":
            values[node_id] = execute_trace_crop(src, node.params)
            return True, None
        if op == "trace.scale":
            values[node_id] = execute_trace_scale(src, node.params)
            return True, None
        if op == "trace.subtract_background":
            values[node_id] = execute_trace_subtract_background(src, node.params)
            return True, None
        if op == "trace.integrate":
            values[node_id] = execute_trace_integrate(src)
            return True, None
        return False, None

    def _execute_workspace_node_fit_ops(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        array: np.ndarray,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        include_hist_outputs: bool,
    ) -> tuple[bool, tuple[int, int] | None]:
        del array, context_fields, event_t_mono_s, include_hist_outputs
        if node.op == "fit.curve_1d":
            values[node_id] = self._execute_workspace_fit_curve_1d(
                workspace=workspace,
                node=node,
                node_id=node_id,
                values=values,
            )
            return True, None
        if node.op == "fit.from_hist_agg":
            values[node_id] = self._execute_workspace_fit_from_hist(
                workspace=workspace,
                node=node,
                node_id=node_id,
                values=values,
            )
            return True, None
        if node.op == "fit.param":
            fit_val = values.get(node.inputs["fit"])
            values[node_id] = execute_fit_param(fit_val, node.params)
            return True, None
        if node.op == "fit.params":
            fit_val = values.get(node.inputs["fit"])
            values[node_id] = execute_fit_params(fit_val)
            return True, None
        return self._execute_workspace_node_fit_extract_ops(
            node=node,
            node_id=node_id,
            values=values,
        )

    @staticmethod
    def _execute_workspace_node_fit_extract_ops(
        *,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
    ) -> tuple[bool, tuple[int, int] | None]:
        fit_ops: dict[str, Callable[[Any], Any]] = {
            "fit.yhat": execute_fit_yhat,
            "fit.xhat": execute_fit_xhat,
            "fit.yhat_dense": execute_fit_yhat_dense,
            "fit.xhat_dense": execute_fit_xhat_dense,
        }
        func = fit_ops.get(node.op)
        if func is None:
            return False, None
        fit_val = values.get(node.inputs["fit"])
        values[node_id] = func(fit_val)
        return True, None

    def _execute_workspace_fit_curve_1d(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        y_raw = values.get(node.inputs["y"])
        x_input = str(node.inputs.get("x") or "").strip()
        if x_input == SAMPLE_INDEX_INPUT_TOKEN:
            y_trace = _coerce_trace(y_raw)
            x_raw = np.arange(int(y_trace.size), dtype=np.float64) if y_trace is not None else None
        else:
            x_raw = values.get(node.inputs["x"])
        state = workspace.node_state.get(node_id)
        if not isinstance(state, FitCurve1DState):
            state = FitCurve1DState.from_params(node.params)
            workspace.node_state[node_id] = state
        gate_val = values.get(node.inputs["gate"]) if "gate" in node.inputs else None
        return execute_fit_curve_1d(
            state=state,
            x_raw=x_raw,
            y_raw=y_raw,
            gate_raw=gate_val,
        )

    def _execute_workspace_fit_from_hist(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
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
        ) = _validate_fit_from_hist_params(node.params)
        gate_val = values.get(node.inputs["gate"]) if "gate" in node.inputs else None
        return execute_fit_from_hist_agg(
            state=state,
            hist_raw=hist_val,
            gate_raw=gate_val,
            y_source=y_source,
            chi2_sigma_source=chi2_sigma_source,
            min_count=min_count,
            x_min=x_min,
            x_max=x_max,
        )

    def _execute_workspace_node_aggregate_ops(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        array: np.ndarray,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        include_hist_outputs: bool,
    ) -> tuple[bool, tuple[int, int] | None]:
        del array, context_fields, event_t_mono_s
        if node.op == "aggregate.bin_stats":
            values[node_id] = self._execute_workspace_aggregate_bin_stats(
                workspace=workspace,
                node=node,
                node_id=node_id,
                values=values,
                include_hist_outputs=include_hist_outputs,
            )
            return True, None
        if node.op == "aggregate.bin2d_stats":
            values[node_id] = self._execute_workspace_aggregate_bin2d_stats(
                workspace=workspace,
                node=node,
                node_id=node_id,
                values=values,
                include_hist_outputs=include_hist_outputs,
            )
            return True, None
        return False, None

    def _execute_workspace_aggregate_bin_stats(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        include_hist_outputs: bool,
    ) -> Json | None:
        x_val = values.get(node.inputs["x"])
        y_val = values.get(node.inputs["y"])
        state = workspace.node_state.get(node_id)
        if not isinstance(state, BinStatsState):
            state = BinStatsState.from_params(node.params)
            workspace.node_state[node_id] = state
        gate_val = values.get(node.inputs["gate"]) if "gate" in node.inputs else None
        last_sample = state.update_sample(x_val, y_val) if _gate_open(gate_val, default=True) else None
        if not include_hist_outputs:
            return None
        return state.payload(last_sample=last_sample)

    def _execute_workspace_aggregate_bin2d_stats(
        self,
        *,
        workspace: WorkspaceRuntime,
        node: NodeSpec,
        node_id: str,
        values: dict[str, Any],
        include_hist_outputs: bool,
    ) -> Json | None:
        x_val = values.get(node.inputs["x"])
        y_val = values.get(node.inputs["y"])
        z_val = values.get(node.inputs["z"])
        state = workspace.node_state.get(node_id)
        if not isinstance(state, Bin2DStatsState):
            state = Bin2DStatsState.from_params(node.params)
            workspace.node_state[node_id] = state
        gate_val = values.get(node.inputs["gate"]) if "gate" in node.inputs else None
        last_sample = (
            state.update_sample(x_val, y_val, z_val)
            if _gate_open(gate_val, default=True)
            else None
        )
        if not include_hist_outputs:
            return None
        return state.payload(last_sample=last_sample)

    def _build_workspace_output_payloads(
        self,
        *,
        workspace: WorkspaceRuntime,
        values: dict[str, Any],
        include_hist_outputs: bool,
        include_trace_outputs: bool,
    ) -> list[Json]:
        outputs: list[Json] = []
        for output in workspace.compiled.outputs:
            payload = self._build_workspace_output_payload(
                workspace=workspace,
                output=output,
                raw_value=values.get(output.node_id),
                include_hist_outputs=include_hist_outputs,
                include_trace_outputs=include_trace_outputs,
            )
            if payload is not None:
                outputs.append(payload)
        return outputs

    def _build_workspace_output_payload(
        self,
        *,
        workspace: WorkspaceRuntime,
        output: PublishOutput,
        raw_value: Any,
        include_hist_outputs: bool,
        include_trace_outputs: bool,
    ) -> Json | None:
        parsed = self._normalize_workspace_output_value(
            output=output,
            raw_value=raw_value,
            include_hist_outputs=include_hist_outputs,
            include_trace_outputs=include_trace_outputs,
        )
        if parsed is None:
            return None
        value, point_count, truncated = parsed
        payload: Json = {
            "workspace_id": workspace.compiled.workspace_id,
            "output_id": output.output_id,
            "node_id": output.node_id,
            "kind": output.kind,
            "value": value,
        }
        if point_count is not None:
            payload["point_count"] = int(point_count)
        if truncated:
            payload["truncated"] = True
        return payload

    def _normalize_workspace_output_value(
        self,
        *,
        output: PublishOutput,
        raw_value: Any,
        include_hist_outputs: bool,
        include_trace_outputs: bool,
    ) -> tuple[Any, int | None, bool] | None:
        if output.kind == "scalar":
            return self._normalize_workspace_scalar_output(raw_value)
        if output.kind in {"hist_agg", "hist2d"}:
            return self._normalize_workspace_hist_output(
                raw_value,
                include_hist_outputs=include_hist_outputs,
            )
        if output.kind in {"params_map", "fit_1d"}:
            return self._normalize_workspace_map_output(raw_value)
        if output.kind == "trace":
            return self._normalize_workspace_trace_output(
                raw_value,
                include_trace_outputs=include_trace_outputs,
            )
        return None

    @staticmethod
    def _normalize_workspace_scalar_output(
        raw_value: Any,
    ) -> tuple[float, None, bool] | None:
        scalar = _normalize_float(raw_value)
        if scalar is None:
            return None
        return float(scalar), None, False

    @staticmethod
    def _normalize_workspace_hist_output(
        raw_value: Any,
        *,
        include_hist_outputs: bool,
    ) -> tuple[Json, None, bool] | None:
        if not include_hist_outputs or not isinstance(raw_value, dict):
            return None
        return _sanitize_json(dict(raw_value)), None, False

    @staticmethod
    def _normalize_workspace_map_output(raw_value: Any) -> tuple[Json, None, bool] | None:
        if not isinstance(raw_value, dict):
            return None
        return _sanitize_json(dict(raw_value)), None, False

    def _normalize_workspace_trace_output(
        self,
        raw_value: Any,
        *,
        include_trace_outputs: bool,
    ) -> tuple[np.ndarray, int, bool] | None:
        if not include_trace_outputs:
            return None
        trace = _coerce_trace(raw_value)
        if trace is None:
            return None
        truncated = False
        if trace.size > self._max_payload_points:
            trace = trace[: self._max_payload_points]
            truncated = True
        return trace, int(trace.size), truncated

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

        for node_id in workspace.compiled.order:
            node = workspace.compiled.nodes[node_id]
            # Return value (per-node "source update" channel metadata) is no
            # longer consumed — output payloads don't carry channel_index/count.
            self._execute_workspace_node(
                workspace=workspace,
                node=node,
                node_id=node_id,
                values=values,
                array=array,
                context_fields=context_fields,
                event_t_mono_s=event_t_mono_s,
                include_hist_outputs=include_hist_outputs,
            )

        return self._build_workspace_output_payloads(
            workspace=workspace,
            values=values,
            include_hist_outputs=include_hist_outputs,
            include_trace_outputs=include_trace_outputs,
        )

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
            self._publish_trace_output_update(
                output=output,
                context_id=context_id,
                context_fields=context_fields,
                device_id=device_id,
                stream=stream,
                t0_mono_ns=t0_mono_ns,
                t0_wall_ns=t0_wall_ns,
            )
            return

        payload = self._build_non_trace_output_payload(
            output=output,
            seq=seq,
            t0_mono_ns=t0_mono_ns,
            t0_wall_ns=t0_wall_ns,
            context_id=context_id,
            context_fields=context_fields,
            device_id=device_id,
            stream=stream,
        )
        if context_id is not None:
            payload["context_id"] = int(context_id)
        if context_fields:
            payload["context_fields"] = _sanitize_json(dict(context_fields))
        self._remember_latest_output(payload)

        self._publish_manager_event(
            topic="manager.stream_analysis.output",
            payload=payload,
        )

    def _publish_trace_output_update(
        self,
        *,
        output: Json,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
        device_id: str,
        stream: str,
        t0_mono_ns: int | None,
        t0_wall_ns: int | None,
    ) -> None:
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
        t0_mono_out = int(t0_mono_ns) if t0_mono_ns is not None else int(now_mono_ns())
        t0_wall_out = int(t0_wall_ns) if t0_wall_ns is not None else int(now_wall_ns())
        try:
            trace_seq = writer.write(
                np.asarray(trace, dtype=np.float64).reshape(-1),
                t0_mono_ns=t0_mono_out,
                t0_wall_ns=t0_wall_out,
            )
        except Exception:
            return

        descriptor = self._build_trace_descriptor_payload(
            output=output,
            workspace_id=workspace_id,
            output_id=output_id,
            node_id=node_id,
            trace=trace,
            trace_seq=trace_seq,
            writer_name=writer.name,
            context_id=context_id,
            context_fields=context_fields,
            device_id=device_id,
            stream=stream,
            t0_mono_out=t0_mono_out,
            t0_wall_out=t0_wall_out,
        )
        snapshot_payload = self._build_trace_snapshot_payload(
            output=output,
            workspace_id=workspace_id,
            output_id=output_id,
            node_id=node_id,
            trace=trace,
            trace_seq=trace_seq,
            context_id=context_id,
            context_fields=context_fields,
            device_id=device_id,
            stream=stream,
            t0_mono_out=t0_mono_out,
            t0_wall_out=t0_wall_out,
        )
        # PerfI: `_build_trace_snapshot_payload` sanitises the value
        # list inline and context_fields are sanitised at line 4533;
        # remaining header fields are ints/strs. Snapshot-store can
        # skip the recursive walk that `_remember_latest_output` does.
        self._remember_latest_output_clean(snapshot_payload)
        self._publish_manager_event(
            topic="manager.stream_analysis.trace_ready",
            payload=descriptor,
        )

    @staticmethod
    def _build_trace_descriptor_payload(
        *,
        output: Json,
        workspace_id: str,
        output_id: str,
        node_id: str,
        trace: np.ndarray,
        trace_seq: int,
        writer_name: str,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
        device_id: str,
        stream: str,
        t0_mono_out: int,
        t0_wall_out: int,
    ) -> Json:
        descriptor: Json = {
            "version": 1,
            "workspace_id": workspace_id,
            "output_id": output_id,
            "node_id": node_id,
            "kind": "trace",
            "device_id": device_id,
            "stream": stream,
            "shm_name": writer_name,
            "seq": int(trace_seq),
            "t0_mono_ns": t0_mono_out,
            "t0_wall_ns": t0_wall_out,
            "dtype": "float64",
            "shape": [int(trace.size)],
            "point_count": int(trace.size),
        }
        if bool(output.get("truncated")):
            descriptor["truncated"] = True
        if context_id is not None:
            descriptor["context_id"] = int(context_id)
        if context_fields:
            descriptor["context_fields"] = _sanitize_json(dict(context_fields))
        return descriptor

    @staticmethod
    def _build_trace_snapshot_payload(
        *,
        output: Json,
        workspace_id: str,
        output_id: str,
        node_id: str,
        trace: np.ndarray,
        trace_seq: int,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
        device_id: str,
        stream: str,
        t0_mono_out: int,
        t0_wall_out: int,
    ) -> Json:
        # PerfI: sanitise the values list inline so the snapshot-store
        # path below (`_remember_latest_output_clean`) can skip the
        # recursive _sanitize_json walk over a 200k-point list per
        # frame (~68 ms/walk in the bench).
        #
        # Fast path: when the trace has no non-finite values (the
        # common case), `np.isfinite(arr).all()` is one vectorised
        # check; the list is produced by a single `.tolist()` call
        # as before. Slow path only triggers when there's actual
        # NaN/Inf to scrub.
        value_arr = trace.astype(np.float64, copy=False).reshape(-1)
        if value_arr.size == 0 or np.isfinite(value_arr).all():
            value_list = value_arr.tolist()
        else:
            value_list = value_arr.tolist()
            for i, x in enumerate(value_list):
                if not math.isfinite(x):
                    value_list[i] = None
        snapshot_payload: Json = {
            "version": 1,
            "workspace_id": workspace_id,
            "output_id": output_id,
            "node_id": node_id,
            "kind": "trace",
            "device_id": device_id,
            "stream": stream,
            "seq": int(trace_seq),
            "t0_mono_ns": t0_mono_out,
            "t0_wall_ns": t0_wall_out,
            "value": value_list,
            "point_count": int(trace.size),
        }
        if bool(output.get("truncated")):
            snapshot_payload["truncated"] = True
        if context_id is not None:
            snapshot_payload["context_id"] = int(context_id)
        if context_fields:
            snapshot_payload["context_fields"] = _sanitize_json(dict(context_fields))
        return snapshot_payload

    @staticmethod
    def _build_non_trace_output_payload(
        *,
        output: Json,
        seq: int | None,
        t0_mono_ns: int | None,
        t0_wall_ns: int | None,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
        device_id: str,
        stream: str,
    ) -> Json:
        del context_id, context_fields
        return {
            "version": 1,
            "device_id": device_id,
            "stream": stream,
            "seq": seq,
            "t0_mono_ns": t0_mono_ns,
            "t0_wall_ns": t0_wall_ns,
            **output,
        }

    @staticmethod
    def _filter_chunk_events_for_message(
        *, events_all: list[Json], msg_seq: int | None
    ) -> list[Json]:
        if msg_seq is None:
            return events_all
        events: list[Json] = []
        for event in events_all:
            seq = _normalize_int(event.get("seq"))
            if seq is None:
                continue
            if seq <= msg_seq:
                events.append(event)
        return events

    def _process_chunk_events(
        self,
        *,
        key: tuple[str, str],
        workspace_ids: set[str],
        events: list[Json],
        msg_seq: int | None,
        msg_context_id: int | None,
        msg_context_fields: dict[str, Any] | None,
        current_context_id: int | None,
        current_context_fields: dict[str, Any] | None,
        device_id: str,
        stream: str,
        initial_latest_seq: int,
    ) -> tuple[int, int | None, dict[str, Any] | None]:
        latest_seq = initial_latest_seq
        for event_index, event in enumerate(events):
            seq = _normalize_int(event.get("seq"))
            latest_seq = self._max_seq(latest_seq, seq)
            (
                event_context_id,
                event_context_fields,
                current_context_id,
                current_context_fields,
            ) = self._resolve_chunk_event_context(
                key=key,
                seq=seq,
                msg_seq=msg_seq,
                msg_context_id=msg_context_id,
                msg_context_fields=msg_context_fields,
                current_context_id=current_context_id,
                current_context_fields=current_context_fields,
            )
            array = self._decode_array(self._readers[key], event)
            if array is None:
                continue
            t0_mono_ns = _normalize_int(event.get("t0_mono_ns"))
            t0_wall_ns = _normalize_int(event.get("t0_wall_ns"))
            event_t_mono_s = float(t0_mono_ns) * 1e-9 if t0_mono_ns is not None else None
            is_last_event = event_index == (len(events) - 1)
            self._process_chunk_event_workspaces(
                workspace_ids=workspace_ids,
                array=array,
                seq=seq,
                t0_mono_ns=t0_mono_ns,
                t0_wall_ns=t0_wall_ns,
                context_id=event_context_id,
                context_fields=event_context_fields,
                event_t_mono_s=event_t_mono_s,
                is_last_event=is_last_event,
                device_id=device_id,
                stream=stream,
            )
        return latest_seq, current_context_id, current_context_fields

    @staticmethod
    def _max_seq(current: int, seq: int | None) -> int:
        if seq is None:
            return current
        return max(current, seq)

    def _resolve_chunk_event_context(
        self,
        *,
        key: tuple[str, str],
        seq: int | None,
        msg_seq: int | None,
        msg_context_id: int | None,
        msg_context_fields: dict[str, Any] | None,
        current_context_id: int | None,
        current_context_fields: dict[str, Any] | None,
    ) -> tuple[
        int | None,
        dict[str, Any] | None,
        int | None,
        dict[str, Any] | None,
    ]:
        event_context_id, event_context_fields = self._pop_context_for_seq(
            key=key,
            seq=seq,
        )
        if (
            event_context_fields is None
            and event_context_id is None
            and msg_seq is not None
            and seq == msg_seq
        ):
            # Common case: this chunk-ready message carries the event context.
            event_context_id = msg_context_id
            event_context_fields = msg_context_fields
        if event_context_fields is None and event_context_id is None:
            event_context_id = current_context_id
            event_context_fields = current_context_fields
            return (
                event_context_id,
                event_context_fields,
                current_context_id,
                current_context_fields,
            )
        next_context_fields = (
            dict(event_context_fields) if isinstance(event_context_fields, dict) else None
        )
        return (
            event_context_id,
            event_context_fields,
            event_context_id,
            next_context_fields,
        )

    def _process_chunk_event_workspaces(
        self,
        *,
        workspace_ids: set[str],
        array: np.ndarray,
        seq: int | None,
        t0_mono_ns: int | None,
        t0_wall_ns: int | None,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
        event_t_mono_s: float | None,
        is_last_event: bool,
        device_id: str,
        stream: str,
    ) -> None:
        now_mono = time.monotonic()
        for workspace_id in list(workspace_ids):
            workspace = self._workspaces.get(workspace_id)
            if workspace is None or not workspace.compiled.enabled:
                continue
            include_hist_outputs = self._allow_hist_outputs_for_workspace(
                workspace,
                now_mono=now_mono,
            )
            include_trace_outputs = False
            if is_last_event:
                include_trace_outputs = self._allow_trace_outputs_for_workspace(
                    workspace,
                    now_mono=now_mono,
                )
            try:
                output_payloads = self._execute_workspace_event(
                    workspace=workspace,
                    array=array,
                    context_fields=context_fields,
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
            self._publish_workspace_outputs(
                output_payloads=output_payloads,
                seq=seq,
                t0_mono_ns=t0_mono_ns,
                t0_wall_ns=t0_wall_ns,
                context_id=context_id,
                context_fields=context_fields,
                device_id=device_id,
                stream=stream,
            )

    def _publish_workspace_outputs(
        self,
        *,
        output_payloads: list[Json],
        seq: int | None,
        t0_mono_ns: int | None,
        t0_wall_ns: int | None,
        context_id: int | None,
        context_fields: dict[str, Any] | None,
        device_id: str,
        stream: str,
    ) -> None:
        for output in output_payloads:
            self._publish_output_update(
                output=output,
                seq=seq,
                t0_mono_ns=t0_mono_ns,
                t0_wall_ns=t0_wall_ns,
                context_id=context_id,
                context_fields=context_fields,
                device_id=device_id,
                stream=stream,
            )

    def _handle_chunk_ready(self, msg: Json) -> None:
        prepared = self._prepare_chunk_ready(msg)
        if prepared is None:
            return
        device_id, stream, key, workspace_ids, reader, last_seq = prepared
        msg_seq, context_id, context_fields = self._extract_chunk_message_context(
            msg=msg,
            key=key,
        )
        events = self._load_chunk_events(
            key=key,
            reader=reader,
            last_seq=last_seq,
            msg_seq=msg_seq,
        )
        if events is None:
            return
        if not events:
            return
        current_context_id, current_context_fields = self._stream_context.get(
            key, (None, None)
        )
        if current_context_fields is not None:
            current_context_fields = dict(current_context_fields)

        latest_seq, current_context_id, current_context_fields = self._process_chunk_events(
            key=key,
            workspace_ids=workspace_ids,
            events=events,
            msg_seq=msg_seq,
            msg_context_id=context_id,
            msg_context_fields=context_fields,
            current_context_id=current_context_id,
            current_context_fields=current_context_fields,
            device_id=device_id,
            stream=stream,
            initial_latest_seq=last_seq,
        )

        self._store_chunk_stream_context(
            key=key,
            latest_seq=latest_seq,
            current_context_id=current_context_id,
            current_context_fields=current_context_fields,
        )

    def _prepare_chunk_ready(
        self,
        msg: Json,
    ) -> tuple[
        str,
        str,
        tuple[str, str],
        set[str],
        ShmRingReader,
        int,
    ] | None:
        parsed = self._normalize_chunk_payload(msg)
        if parsed is None:
            return None
        device_id, stream, shm_name = parsed
        key = (device_id, stream)
        workspace_ids = self._stream_to_workspaces.get(key)
        if not workspace_ids:
            return None
        reader = self._ensure_reader(key, shm_name)
        if reader is None:
            return None
        last_seq = int(self._last_seq.get(key, 0))
        return device_id, stream, key, workspace_ids, reader, last_seq

    def _extract_chunk_message_context(
        self,
        *,
        msg: Json,
        key: tuple[str, str],
    ) -> tuple[int | None, int | None, dict[str, Any] | None]:
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
        return msg_seq, context_id, context_fields

    def _load_chunk_events(
        self,
        *,
        key: tuple[str, str],
        reader: ShmRingReader,
        last_seq: int,
        msg_seq: int | None,
    ) -> list[Json] | None:
        try:
            events_all = reader.read_events(last_seq)
        except Exception:
            self._reset_chunk_reader_state(key=key, reader=reader)
            return None
        if not events_all:
            return []
        events = self._filter_chunk_events_for_message(
            events_all=events_all,
            msg_seq=msg_seq,
        )
        if not events:
            self._prune_context_cache(key=key, last_seq=last_seq)
            return []
        return events

    def _reset_chunk_reader_state(
        self,
        *,
        key: tuple[str, str],
        reader: ShmRingReader,
    ) -> None:
        try:
            reader.close()
        except Exception:
            pass
        self._readers.pop(key, None)
        self._last_seq.pop(key, None)
        self._stream_context.pop(key, None)
        self._context_by_seq.pop(key, None)

    def _store_chunk_stream_context(
        self,
        *,
        key: tuple[str, str],
        latest_seq: int,
        current_context_id: int | None,
        current_context_fields: dict[str, Any] | None,
    ) -> None:
        self._last_seq[key] = latest_seq
        self._prune_context_cache(key=key, last_seq=latest_seq)
        if current_context_id is None and current_context_fields is None:
            self._stream_context.pop(key, None)
            return
        self._stream_context[key] = (
            int(current_context_id) if current_context_id is not None else None,
            dict(current_context_fields) if isinstance(current_context_fields, dict) else None,
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

    def _stream_analysis_capability_members(self) -> list[Any]:
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
                "stream_analysis.workspace.snapshot",
                params=[
                    param("workspace_id", required=True, default=None, annotation="str"),
                    param("kinds", required=False, default=None, annotation="list[str]|str"),
                    param("output_ids", required=False, default=None, annotation="list[str]|str"),
                    param("max_trace_points", required=False, default=None, annotation="int"),
                ],
                doc="Get latest published outputs for a workspace (latest-state snapshot).",
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
        return self._with_common_capabilities(members)

    def _rpc_stream_analysis_capabilities(self, req: Json) -> Json:
        return self.rpc_ok(req, result=capabilities_payload(self._stream_analysis_capability_members()))

    def _rpc_stream_analysis_status(self, req: Json) -> Json:
        return self.rpc_ok(req, result=self._status_payload())

    def _rpc_stream_analysis_operators(self, req: Json) -> Json:
        return self.rpc_ok(req, result={"operators": operator_catalog_payload()})

    def _rpc_stream_analysis_workspace_list(self, req: Json) -> Json:
        return self.rpc_ok(
            req,
            result={
                "workspaces": [
                    self._workspace_summary(self._workspaces[key])
                    for key in sorted(self._workspaces.keys())
                ]
            },
        )

    def _rpc_stream_analysis_workspace_store_status(self, req: Json) -> Json:
        return self.rpc_ok(req, result=self._workspace_store_status_payload())

    def _rpc_stream_analysis_workspace_get(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        workspace_id = _normalize_id(params.get("workspace_id"))
        if workspace_id is None:
            return self.rpc_invalid_params(req, message="workspace_id is required")
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            return self.rpc_err(req, code="unknown_workspace")
        return self.rpc_ok(
            req,
            result={
                "workspace": self._workspace_summary(workspace),
                "raw": workspace.raw_config,
            },
        )

    def _rpc_stream_analysis_workspace_snapshot(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        try:
            snapshot = self._workspace_snapshot_payload(params)
        except ValueError as exc:
            return self.rpc_invalid_params(req, message=str(exc))
        except KeyError:
            return self.rpc_err(req, code="unknown_workspace")
        return self.rpc_ok(req, result=snapshot)

    def _rpc_stream_analysis_workspace_validate(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        result = self._handle_workspace_validate(params)
        if not result.get("ok"):
            return self.rpc_err(
                req,
                code=str((result.get("error") or {}).get("code") or "validation_failed"),
                message=(result.get("error") or {}).get("message"),
            )
        return self.rpc_ok(req, result=result.get("result"))

    def _rpc_stream_analysis_workspace_put(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        result = self._handle_workspace_put(params)
        if not result.get("ok"):
            return self.rpc_err(
                req,
                code=str((result.get("error") or {}).get("code") or "put_failed"),
                message=(result.get("error") or {}).get("message"),
            )
        return self.rpc_ok(req, result=result.get("result"))

    def _rpc_stream_analysis_workspace_delete(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        workspace_id = _normalize_id(params.get("workspace_id"))
        if workspace_id is None:
            return self.rpc_invalid_params(req, message="workspace_id is required")
        expected_raw = params.get("expected_revision")
        expected_revision: int | None = None
        if expected_raw is not None:
            expected_revision = _normalize_int(expected_raw)
            if expected_revision is None or expected_revision < 0:
                return self.rpc_invalid_params(
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
            return self.rpc_err(req, code="unknown_workspace")
        return self.rpc_ok(req, result={"workspace_id": workspace_id, "deleted": True})

    def _rpc_stream_analysis_workspace_reset(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        workspace_id = _normalize_id(params.get("workspace_id"))
        node_id = _normalize_id(params.get("node_id"))
        if workspace_id is None:
            if node_id is not None:
                return self.rpc_invalid_params(
                    req, message="node_id requires workspace_id"
                )
            for workspace in self._workspaces.values():
                self._reset_workspace_states(workspace)
            self._latest_output_payloads.clear()
            return self.rpc_ok(req, result={"reset": "all", "count": len(self._workspaces)})
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            return self.rpc_err(req, code="unknown_workspace")
        if node_id is not None:
            ok = self._reset_workspace_node_state(workspace, node_id)
            if not ok:
                return self.rpc_err(req, code="unknown_or_non_stateful_node")
            self._clear_workspace_snapshot_outputs(workspace_id, node_id=node_id)
            return self.rpc_ok(req, result={"reset": workspace_id, "node_id": node_id})
        self._reset_workspace_states(workspace)
        self._clear_workspace_snapshot_outputs(workspace_id)
        return self.rpc_ok(req, result={"reset": workspace_id})

    def _rpc_stream_analysis_workspace_clear(self, req: Json) -> Json:
        removed = self._clear_workspaces(mark_dirty=True, publish=True)
        return self.rpc_ok(req, result={"removed": removed})

    def _rpc_stream_analysis_workspace_store_save(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        saved = self._save_workspace_store(path_override=params.get("path"))
        return self.rpc_ok(
            req,
            result={**saved, "status": self._workspace_store_status_payload()},
        )

    def _rpc_stream_analysis_workspace_store_reload(self, req: Json) -> Json:
        params = req.get("params", {}) or {}
        if not isinstance(params, dict):
            return self.rpc_invalid_params(req, message="params must be a dict")
        override = self._normalize_workspace_store_path(params.get("path"))
        if override is not None:
            self._workspace_store_path = override
        reloaded = self._reload_workspace_store(strict_missing=True)
        return self.rpc_ok(
            req,
            result={**reloaded, "status": self._workspace_store_status_payload()},
        )

    def _build_rpc_registry(self) -> RpcDispatchRegistry:
        handlers = {
            "process.capabilities": self._rpc_stream_analysis_capabilities,
            "stream_analysis.status": self._rpc_stream_analysis_status,
            "stream_analysis.operators": self._rpc_stream_analysis_operators,
            "stream_analysis.workspace.list": self._rpc_stream_analysis_workspace_list,
            "stream_analysis.workspace_store.status": self._rpc_stream_analysis_workspace_store_status,
            "stream_analysis.workspace.get": self._rpc_stream_analysis_workspace_get,
            "stream_analysis.workspace.snapshot": self._rpc_stream_analysis_workspace_snapshot,
            "stream_analysis.workspace.validate": self._rpc_stream_analysis_workspace_validate,
            "stream_analysis.workspace.put": self._rpc_stream_analysis_workspace_put,
            "stream_analysis.workspace.delete": self._rpc_stream_analysis_workspace_delete,
            "stream_analysis.workspace.reset": self._rpc_stream_analysis_workspace_reset,
            "stream_analysis.workspace.clear": self._rpc_stream_analysis_workspace_clear,
            "stream_analysis.workspace_store.save": self._rpc_stream_analysis_workspace_store_save,
            "stream_analysis.workspace_store.reload": self._rpc_stream_analysis_workspace_store_reload,
        }
        return RpcDispatchRegistry(
            handlers=handlers,
            aliases={
                "stream_analysis.get_status": "stream_analysis.status",
                "stream_analysis.workspace.upsert": "stream_analysis.workspace.put",
                "stream_analysis.workspace.remove": "stream_analysis.workspace.delete",
                "stream_analysis.workspace_store.persist": "stream_analysis.workspace_store.save",
                "stream_analysis.workspace_store.load": "stream_analysis.workspace_store.reload",
            },
        )

    def _dispatch_rpc_with_error_mapping(self, req: Json, params: Any) -> Json | None:
        try:
            return self._rpc_registry.dispatch(req)
        except WorkspaceRevisionConflict as exc:
            return {
                "request_id": req.get("request_id"),
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
            workspace_id = (
                _normalize_id(params.get("workspace_id"))
                if isinstance(params, dict)
                else None
            )
            if isinstance(exc, FileNotFoundError):
                return self.rpc_err(
                    req,
                    code="workspace_store_not_found",
                    message=str(exc),
                )
            if isinstance(exc, ValueError) and "workspace_store_path" in str(exc):
                return self.rpc_err(
                    req,
                    code="workspace_store_not_configured",
                    message=str(exc),
                )
            self._publish_error(
                workspace_id=workspace_id,
                code="rpc_error",
                message=str(exc),
            )
            return self.rpc_err(req, code="rpc_error", message=str(exc))

    def _handle_rpc(self, req: Json) -> Json:
        common = self._handle_common_rpc(req)
        if common is not None:
            return common

        if not hasattr(self, "_rpc_registry"):
            self._rpc_registry = self._build_rpc_registry()
        req = self._rpc_registry.canonicalize_request(req)

        params = req.get("params", {})
        if params is None:
            params = {}

        dispatched = self._dispatch_rpc_with_error_mapping(req, params)
        if dispatched is not None:
            return dispatched
        return self.rpc_unknown(req)

    def _handle_rpc_legacy(self, req: Json) -> Json:
        return self.rpc_unknown(req)

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
        self._latest_output_payloads.clear()

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
