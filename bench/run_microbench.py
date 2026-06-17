"""Micro-benchmark suite for backend hot paths.

Run from repo root:
    .venv\\Scripts\\python.exe -m bench.run_microbench

Pass --bench <name> to run a subset; see `--help`.

Each benchmark times a single hot-path function across realistic
input sizes and reports per-call latency. Designed to validate
or invalidate the audit speculations without bringing up the
full stack.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import sys
import time
import tracemalloc
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import orjson
import zmq.utils.jsonapi
from rich.text import Text

# Use the same JSON encoder as the production stack uses (pyzmq's
# jsonapi.dumps), so timings reflect what the gateway actually
# pays per message.
from experiment_control.utils.zmq_helpers import json_dumps  # noqa: E402
from experiment_control.processes.stream_analysis import (  # noqa: E402
    BinStatsState,
    _sanitize_json as stream_analysis_sanitize_json,
)
from experiment_control.fastapi.app import _build_trace_frame_array  # noqa: E402

Json = dict[str, Any]


# ---------------------------------------------------------------------------
# Bench harness
# ---------------------------------------------------------------------------


@dataclass
class Row:
    target: str
    size_label: str
    n_iter: int
    total_s: float
    per_call_us: float
    bytes_per_call: float

    def fmt(self) -> str:
        kb = self.bytes_per_call / 1024.0
        if kb >= 0.5:
            size_str = f"{kb:7.2f} KB"
        else:
            size_str = f"{self.bytes_per_call:6.0f} B "
        return (
            f"  {self.target:<38} {self.size_label:>10} "
            f"{self.n_iter:>10,} {self.total_s:>9.3f}s "
            f"{self.per_call_us:>10.2f} {size_str}"
        )


_HEADER = (
    f"  {'target':<38} {'size':>10} {'n_iter':>10} "
    f"{'total':>10} {'per call':>10} {'alloc/call':>10}"
)


_TRACK_ALLOC_DEFAULT = True


def time_call(
    label: str,
    size_label: str,
    fn: Callable[[], Any],
    *,
    iters: int,
    track_alloc: bool | None = None,
) -> Row:
    """Call `fn` `iters` times and report timing + allocation."""
    if track_alloc is None:
        track_alloc = _TRACK_ALLOC_DEFAULT
    # warm-up
    for _ in range(min(50, iters)):
        fn()
    gc.collect()

    if track_alloc:
        tracemalloc.start()
        snap_before = tracemalloc.take_snapshot()

    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    elapsed = time.perf_counter() - t0

    bytes_per_call = 0.0
    if track_alloc:
        snap_after = tracemalloc.take_snapshot()
        stats = snap_after.compare_to(snap_before, "lineno")
        delta_bytes = sum(stat.size_diff for stat in stats if stat.size_diff > 0)
        bytes_per_call = max(0.0, delta_bytes / iters)
        tracemalloc.stop()

    return Row(
        target=label,
        size_label=size_label,
        n_iter=iters,
        total_s=elapsed,
        per_call_us=(elapsed / iters) * 1e6,
        bytes_per_call=bytes_per_call,
    )


# ---------------------------------------------------------------------------
# bin_stats benchmark
# ---------------------------------------------------------------------------


def bench_bin_stats(rows: list[Row]) -> None:
    """Time BinStatsState.update_sample() + .payload() at various bin counts.

    At rate-limited 20 Hz emit cadence with the default 100-bin histogram,
    per_call_us tells us whether payload-caching would pay off.
    """
    print()
    print("== bin_stats (1D) ==")
    print(_HEADER)
    rng = np.random.default_rng(42)
    for bin_count in (50, 200, 1000, 5000):
        state = BinStatsState.from_params(
            {
                "bin_count": bin_count,
                "auto_range": False,
                "x_min": 0.0,
                "x_max": 10.0,
            }
        )
        xs = rng.uniform(0.0, 10.0, size=2048).tolist()
        ys = rng.normal(size=2048).tolist()
        i = {"k": 0}

        def update() -> None:
            k = i["k"] & 0x7FF
            state.update_sample(xs[k], ys[k])
            i["k"] += 1

        def payload() -> None:
            state.payload(last_sample=None)

        # Make sure the state has samples in every bin so payload() does
        # the full computation path.
        for _ in range(min(bin_count * 3, 5000)):
            update()

        r = time_call(
            "bin_stats.update_sample",
            f"{bin_count} bins",
            update,
            iters=20_000,
        )
        rows.append(r)
        print(r.fmt())

        r = time_call(
            "bin_stats.payload",
            f"{bin_count} bins",
            payload,
            iters=5_000,
        )
        rows.append(r)
        print(r.fmt())


# ---------------------------------------------------------------------------
# sanitize benchmark
# ---------------------------------------------------------------------------


def bench_sanitize(rows: list[Row]) -> None:
    """Time _sanitize_json on representative payload shapes."""
    print()
    print("== _sanitize_json ==")
    print(_HEADER)
    rng = np.random.default_rng(42)

    cases: list[tuple[str, Any]] = []
    # Telemetry-bundle-shape: shallow dict, ~20 small numeric fields
    telem = {
        "version": 1,
        "device_id": "dev1",
        "ts": {"t_wall": 1.0, "t_mono": 1.0},
        "signals": {
            f"signal_{i}": {"value": float(rng.normal()), "units": "V"}
            for i in range(20)
        },
    }
    cases.append(("telemetry bundle (20 signals)", telem))

    # Stream-frame-shape: flat array of N floats
    for n in (100, 1000, 50_000, 200_000):
        frame = {
            "version": 1,
            "device_id": "dev1",
            "stream": "trace",
            "seq": 42,
            "shape": [n],
            "values": rng.normal(size=n).tolist(),
        }
        cases.append((f"stream frame ({n:,} pts)", frame))

    # bin_stats payload shape: ~7 short lists of N bins
    for bins in (100, 1000):
        hist = {
            "auto_range": False,
            "x_min": 0.0,
            "x_max": 10.0,
            "bin_count": bins,
            "active_bin_count": bins,
            "max_bin_count": bins,
            "populated_bin_count": bins,
            "x_bins": rng.uniform(size=bins).tolist(),
            "count": rng.integers(0, 100, size=bins).tolist(),
            "mean": rng.normal(size=bins).tolist(),
            "std": rng.normal(size=bins).tolist(),
            "sem": rng.normal(size=bins).tolist(),
            "dropped_samples": 0,
        }
        cases.append((f"bin_stats payload ({bins} bins)", hist))

    for label, payload in cases:
        iters = 200 if label.startswith("stream frame (200") else 2_000
        r = time_call(
            "stream_analysis._sanitize_json",
            label,
            lambda p=payload: stream_analysis_sanitize_json(p),
            iters=iters,
        )
        rows.append(r)
        print(r.fmt())


# ---------------------------------------------------------------------------
# json_dumps benchmark
# ---------------------------------------------------------------------------


def bench_json_dumps(rows: list[Row]) -> None:
    """Compare production orjson-backed encoding against pyzmq's encoder."""
    print()
    print("== json encoding (pyzmq baseline vs orjson vs production json_dumps) ==")
    print(_HEADER)
    rng = np.random.default_rng(42)

    cases: list[tuple[str, Any]] = [
        (
            "telemetry bundle (20 signals)",
            {
                "version": 1,
                "device_id": "dev1",
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {
                    f"signal_{i}": {"value": float(rng.normal()), "units": "V"}
                    for i in range(20)
                },
            },
        ),
    ]
    for n in (100, 1000, 50_000, 200_000):
        cases.append(
            (
                f"stream frame ({n:,} pts)",
                {
                    "version": 1,
                    "device_id": "dev1",
                    "stream": "trace",
                    "seq": 42,
                    "shape": [n],
                    "values": rng.normal(size=n).tolist(),
                },
            )
        )

    encoders: tuple[tuple[str, Callable[[Any], bytes]], ...] = (
        ("pyzmq jsonapi.dumps", zmq.utils.jsonapi.dumps),
        ("orjson.dumps", lambda p: orjson.dumps(p, option=orjson.OPT_SERIALIZE_NUMPY)),
        ("production json_dumps", json_dumps),
    )
    for label, payload in cases:
        iters = 100 if label.startswith("stream frame (200") else 1_000
        for encoder_label, encoder in encoders:
            r = time_call(
                encoder_label,
                label,
                lambda p=payload, e=encoder: e(p),
                iters=iters,
            )
            rows.append(r)
            print(r.fmt())


# ---------------------------------------------------------------------------
# output_index benchmark — validate PerfC reverse-index cost
# ---------------------------------------------------------------------------


def bench_output_index(rows: list[Row]) -> None:
    """Time a `buildPanelsByWorkspaceOutput`-equivalent build in Python.

    Mirrors the frontend's reverse index that PerfC introduced. Confirms
    rebuild cost stays sub-millisecond at realistic panel counts so we
    can run it on every panels-list edit.
    """
    print()
    print("== output_index.build (panels × workspaces) ==")
    print(_HEADER)

    def build_index(panels: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        out: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for panel in panels:
            ws = panel.get("workspaceId") or ""
            if not ws:
                continue
            kind = panel.get("kind")
            inner = out.setdefault(ws, {})
            if kind in ("stream_scalar", "stream_bin2d") or (
                kind == "stream_trace" and panel.get("sourceMode") == "dag"
            ):
                oid = panel.get("outputId") or ""
                if oid:
                    inner.setdefault(oid, []).append(panel)
                for oid in panel.get("overlayOutputIds", ()):
                    if oid:
                        inner.setdefault(oid, []).append(panel)
            elif kind == "stream_params":
                for oid in panel.get("outputIds", ()):
                    if oid:
                        inner.setdefault(oid, []).append(panel)
            elif kind == "stream_bin_stats":
                oid = panel.get("outputId") or ""
                if oid:
                    inner.setdefault(oid, []).append(panel)
                for oid in panel.get("overlayOutputIds", ()):
                    if oid:
                        inner.setdefault(oid, []).append(panel)
                for oid in panel.get("fitOverlayOutputIds", ()):
                    if oid:
                        inner.setdefault(oid, []).append(panel)
        return out

    rng = np.random.default_rng(42)
    for n_panels in (10, 50, 200):
        panels = [
            {
                "id": f"panel-{i}",
                "kind": rng.choice(
                    [
                        "stream_scalar",
                        "stream_bin_stats",
                        "stream_trace",
                        "stream_params",
                    ]
                ).item(),
                "sourceMode": "dag",
                "workspaceId": f"ws-{i % 3}",
                "outputId": f"out-{i % 8}",
                "outputIds": [f"out-{j}" for j in range(i % 4)],
                "overlayOutputIds": [f"out-{(i + j) % 8}" for j in range(2)],
                "fitOverlayOutputIds": [f"fit-{i % 3}"],
            }
            for i in range(n_panels)
        ]
        r = time_call(
            "build_panels_by_workspace_output",
            f"{n_panels} panels",
            lambda p=panels: build_index(p),
            iters=5_000,
        )
        rows.append(r)
        print(r.fmt())


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def bench_trace_snapshot_value(rows: list[Row]) -> None:
    """Time the trace-snapshot value materialisation path.

    Compares the original `.tolist()` + recursive `_sanitize_json`
    (what `_remember_latest_output` used to do per trace) against the
    PerfI inline scrub (one `.tolist()` + a vectorised `np.isfinite`
    check; fallback per-element scrub only when NaN/Inf is present).
    """
    print()
    print("== trace snapshot value materialisation ==")
    print(_HEADER)
    rng = np.random.default_rng(42)

    def baseline(arr: np.ndarray) -> Any:
        # Same shape as the old path: tolist + sanitize the resulting list.
        return stream_analysis_sanitize_json(arr.tolist())

    def perfi_fast(arr: np.ndarray) -> Any:
        # New path; takes the fast branch when all-finite (common case).
        if arr.size == 0 or np.isfinite(arr).all():
            return arr.tolist()
        out = arr.tolist()
        for i, x in enumerate(out):
            if not math.isfinite(x):
                out[i] = None
        return out

    for n in (1_000, 50_000, 200_000):
        arr = rng.normal(size=n).astype(np.float64)
        iters = 1000 if n < 100_000 else 200
        r = time_call(
            "baseline (.tolist + _sanitize_json)",
            f"{n:,} pts",
            lambda a=arr: baseline(a),
            iters=iters,
        )
        rows.append(r)
        print(r.fmt())
        r = time_call(
            "PerfI (.tolist + np.isfinite fast path)",
            f"{n:,} pts",
            lambda a=arr: perfi_fast(a),
            iters=iters,
        )
        rows.append(r)
        print(r.fmt())


def bench_snapshot_readout(rows: list[Row]) -> None:
    """Compare workspace snapshot readout strategies for cached trace payloads."""
    print()
    print("== snapshot readout (sanitize vs decimate-first vs clean fast path) ==")
    print(_HEADER)
    rng = np.random.default_rng(42)

    def make_payload(n: int, *, dirty: bool) -> Json:
        values = rng.normal(size=n).astype(np.float64).tolist()
        if dirty and n >= 16:
            values[n // 4] = float("nan")
            values[n // 2] = float("inf")
            values[(3 * n) // 4] = float("-inf")
        return {
            "version": 1,
            "workspace_id": "ws-1",
            "output_id": "trace-1",
            "node_id": "node-1",
            "kind": "trace",
            "device_id": "dev1",
            "stream": "trace",
            "seq": 42,
            "t0_mono_ns": 1_000_000,
            "t0_wall_ns": 2_000_000,
            "channel_index": 0,
            "channel_count": 1,
            "value": values,
            "point_count": n,
            "context_fields": {"shot": 123, "label": "bench"},
        }

    def decimate(values: list[float | None], max_points: int | None) -> list[float | None]:
        if max_points is None or len(values) <= max_points:
            return list(values)
        step = max(1, int(math.ceil(len(values) / float(max_points))))
        out = list(values[::step])
        if values and out[-1] != values[-1]:
            out.append(values[-1])
        if len(out) > max_points:
            out = out[:max_points]
        return out

    def scrub(values: list[float | None]) -> list[float | None]:
        out = list(values)
        for i, value in enumerate(out):
            if value is not None and not math.isfinite(float(value)):
                out[i] = None
        return out

    def current(payload: Json, max_points: int | None) -> Json:
        item = stream_analysis_sanitize_json(dict(payload))
        if max_points is not None:
            item["value"] = decimate(item["value"], max_points)
            item["point_count"] = len(item["value"])
        return item

    def decimate_first(payload: Json, max_points: int | None) -> Json:
        item = dict(payload)
        if max_points is not None:
            item["value"] = decimate(item["value"], max_points)
            item["point_count"] = len(item["value"])
        return stream_analysis_sanitize_json(item)

    def clean_fast_path(payload: Json, max_points: int | None, *, dirty: bool) -> Json:
        item = dict(payload)
        values = decimate(item["value"], max_points)
        if dirty:
            values = scrub(values)
        item["value"] = values
        item["point_count"] = len(values)
        if "context_fields" in item:
            item["context_fields"] = stream_analysis_sanitize_json(dict(item["context_fields"]))
        return item

    variants: tuple[tuple[str, Callable[[Json, int | None, bool], Json]], ...] = (
        ("current sanitize-then-decimate", lambda p, m, _d: current(p, m)),
        ("decimate-then-sanitize", lambda p, m, _d: decimate_first(p, m)),
        ("known-clean fast path", lambda p, m, d: clean_fast_path(p, m, dirty=d)),
    )
    for n in (1_000, 50_000, 200_000):
        for dirty in (False, True):
            payload = make_payload(n, dirty=dirty)
            dirty_label = "dirty" if dirty else "finite"
            for max_points in (None, 2_000):
                max_label = "full" if max_points is None else "max2k"
                size_label = f"{n:,} {dirty_label} {max_label}"
                iters = 1000 if n <= 1_000 else (200 if n <= 50_000 else 50)
                for label, fn in variants:
                    r = time_call(
                        label,
                        size_label,
                        lambda p=payload, m=max_points, d=dirty, f=fn: f(p, m, d),
                        iters=iters,
                    )
                    rows.append(r)
                    print(r.fmt())



def bench_binary_trace_transport(rows: list[Row]) -> None:
    """Compare JSON trace values against binary metadata+bytes framing."""
    print()
    print("== raw trace transport (JSON values vs binary frame) ==")
    print(_HEADER)
    rng = np.random.default_rng(42)

    for n in (1_000, 50_000, 200_000):
        arr = rng.normal(size=n).astype(np.float64)
        values = arr.tolist()
        json_msg = {
            "topic": "manager.stream_frame",
            "payload": {
                "version": 1,
                "device_id": "dev1",
                "stream": "trace",
                "seq": 42,
                "shape": [n],
                "point_count": n,
                "values": values,
            },
        }
        binary_msg = {
            "topic": "manager.stream_frame",
            "payload": {
                **json_msg["payload"],
                "_binary_values": arr,
            },
        }
        meta_msg = {
            "topic": "manager.stream_frame",
            "payload": {
                "version": 1,
                "device_id": "dev1",
                "stream": "trace",
                "seq": 42,
                "shape": [n],
                "point_count": n,
                "encoding": "binary-frame",
                "dtype": "float64",
                "byte_order": "little",
                "byte_length": int(arr.nbytes),
            },
        }

        def json_values() -> bytes:
            return json_dumps(json_msg)

        def binary_from_list() -> tuple[bytes, bytes]:
            payload_arr = np.asarray(values, dtype=np.float64).reshape(-1)
            return json_dumps(meta_msg), payload_arr.astype("<f8", copy=False).tobytes()

        def binary_from_ndarray() -> tuple[bytes, bytes]:
            return json_dumps(meta_msg), arr.astype("<f8", copy=False).tobytes()

        def production_binary_builder() -> tuple[bytes, bytes]:
            built = _build_trace_frame_array(
                binary_msg["payload"],
                channel_index=0,
                trace_decimator="stride",
                trace_max_points=None,
            )
            if built is None:
                raise RuntimeError("failed to build binary frame")
            payload, trace = built
            payload.update(
                {
                    "encoding": "binary-frame",
                    "dtype": "float64",
                    "byte_order": "little",
                    "byte_length": int(trace.nbytes),
                }
            )
            return json_dumps({"topic": json_msg["topic"], "payload": payload}), trace.astype("<f8", copy=False).tobytes()

        iters = 1000 if n <= 1_000 else (200 if n <= 50_000 else 50)
        size_label = f"{n:,} pts"
        for label, fn in (
            ("json values frame", json_values),
            ("old binary frame from list", binary_from_list),
            ("ideal binary from ndarray", binary_from_ndarray),
            ("production binary builder", production_binary_builder),
        ):
            r = time_call(label, size_label, fn, iters=iters)
            rows.append(r)
            print(r.fmt())



def bench_stream_buffer_assembly(rows: list[Row]) -> None:
    """Compare three HDF stream-buffer assembly paths.

    All three produce the same (N, *shape) numpy array ready for HDF5
    dataset assignment. They differ in WHICH thread pays the bytes→numpy
    cost and HOW MANY copies happen end-to-end:

    1. **baseline (today)** — ShmRingReader.read_events copies SHM→bytes
       on the main thread. Main thread appends `bytes` to a Python list.
       Bg thread flushes: `np.empty(N)` then per-event `frombuffer +
       reshape + assign`. Two copies (SHM→bytes on main, bytes→numpy on
       bg) plus per-batch allocation of the destination array.

    2. **prealloc-from-bytes** — same SHM→bytes copy on main thread,
       but then immediately copy bytes→pre-allocated numpy slot on
       main thread. Bg thread just slices and writes. Still two copies,
       but both on main thread; bg thread is fast.

    3. **prealloc-from-shm** — main thread copies SHM bytes directly
       into the pre-allocated numpy slot via `np.frombuffer(shm_view,
       count=...).copy()` (or equivalent). Skips the intermediate
       Python `bytes` object entirely. One copy on main thread, one
       copy on bg thread (HDF write). The best case for high-rate
       streams where main-thread work matters.

    Cases include the upcoming detection instance's projected
    workload: 50 Hz × 5 MHz × 20 ms × 5 ch int16 = 100 events of
    500 K samples per 2-s batch (~100 MB/batch).
    """
    print()
    print("== hdf stream-buffer assembly (3-way: today / pre-alloc / pre-alloc-from-shm) ==")
    print(_HEADER)
    rng = np.random.default_rng(42)

    # (n_events_per_batch, sample_count, dtype, label_extra)
    cases = [
        (64,    256,     np.float64, ""),                 # ~130 KB
        (256,   1024,    np.float64, ""),                 # ~2 MB
        (1024,  4096,    np.float64, ""),                 # ~32 MB
        (4096,  16384,   np.float64, ""),                 # ~512 MB
        (100,   500_000, np.int16,   " [detection 50Hz]"), # ~100 MB
    ]

    for n_events, sample_count, dtype, extra in cases:
        itemsize = np.dtype(dtype).itemsize
        bytes_per_event = sample_count * itemsize
        total_mb = (n_events * bytes_per_event) / (1024 * 1024)

        # Simulate the SHM ring: one big bytes blob; each "event" is a
        # contiguous slice. mirrors how ShmRingReader serves slots.
        shm_blob = rng.integers(
            0, 256, size=n_events * bytes_per_event, dtype=np.uint8
        ).tobytes()
        # Pre-build the per-event bytes objects (the baseline + prealloc-from-bytes
        # paths receive these from read_events).
        events_bytes: list[bytes] = [
            shm_blob[i * bytes_per_event : (i + 1) * bytes_per_event]
            for i in range(n_events)
        ]
        # And the offsets a prealloc-from-shm path would use to slice
        # directly from the shm view.
        shm_view = memoryview(shm_blob)
        shape: tuple[int, ...] = (sample_count,)

        # --- 1. baseline (today) ---
        def baseline() -> np.ndarray:
            buf_data: list[bytes] = []
            for payload in events_bytes:
                buf_data.append(payload)
            arr = np.empty((n_events,) + shape, dtype=dtype)
            for i, payload in enumerate(buf_data):
                arr[i] = np.frombuffer(payload, dtype=dtype).reshape(shape)
            return arr

        # --- 2. prealloc-from-bytes (still receives bytes, but copies into
        #       pre-alloc on the main thread instead of via Python list) ---
        scratch_b = np.empty((n_events,) + shape, dtype=dtype)

        def prealloc_from_bytes() -> np.ndarray:
            n_filled = 0
            for payload in events_bytes:
                scratch_b[n_filled] = np.frombuffer(
                    payload, dtype=dtype
                ).reshape(shape)
                n_filled += 1
            return scratch_b[:n_filled]

        # --- 3. prealloc-from-shm (skip intermediate bytes object entirely;
        #       copy SHM → pre-alloc numpy slot directly) ---
        scratch_s = np.empty((n_events,) + shape, dtype=dtype)

        def prealloc_from_shm() -> np.ndarray:
            n_filled = 0
            for i in range(n_events):
                offset = i * bytes_per_event
                # frombuffer over a memoryview is zero-copy; .copy() into
                # the slot is the single byte movement.
                scratch_s[n_filled] = np.frombuffer(
                    shm_view,
                    dtype=dtype,
                    count=sample_count,
                    offset=offset,
                ).reshape(shape)
                n_filled += 1
            return scratch_s[:n_filled]

        iters = 1000 if n_events <= 256 else (200 if n_events <= 1024 else 50)
        size_label = f"{n_events}×{sample_count} ({total_mb:.0f}MB){extra}"
        r = time_call("1. baseline (list of bytes)", size_label, baseline, iters=iters)
        rows.append(r)
        print(r.fmt())
        r = time_call("2. prealloc-from-bytes", size_label, prealloc_from_bytes, iters=iters)
        rows.append(r)
        print(r.fmt())
        r = time_call("3. prealloc-from-shm", size_label, prealloc_from_shm, iters=iters)
        rows.append(r)
        print(r.fmt())


# ---------------------------------------------------------------------------
# TUI hot-path benchmarks (pure-function; no Textual app needed)
#
# These isolate the data-processing costs paid inside the TUI's 5 Hz drain
# loop and render methods (src/experiment_control/_tui/app.py). Widget-level
# costs (DataTable.clear/add_row, RichLog.write) live in the separate
# bench/run_tui_render_bench.py harness because they require a mounted app.
# ---------------------------------------------------------------------------


def _make_member(i: int) -> dict[str, Any]:
    """A capability member dict shaped like manager capability responses."""
    is_method = (i % 3) == 0
    return {
        "name": f"member_{i:03d}",
        "kind": "method" if is_method else "attribute",
        "readable": True,
        "settable": not is_method,
        "return_annotation": "float | None" if is_method else None,
        "value_annotation": None if is_method else "float",
        "source": "device" if (i % 2) else "driver",
        "doc": f"Docstring for member {i}. " * 3,
        # Non-rendered fields the current json.dumps fingerprint also covers:
        "signature": f"(a: int, b: float = {i}.0)" if is_method else None,
        "default": None if is_method else float(i),
        "units": "V",
    }


def _members_fingerprint_current(members_render: list[dict[str, Any]]) -> str:
    """Current fingerprint: full json.dumps over every member dict."""
    try:
        return json.dumps(members_render, sort_keys=True, default=str)
    except Exception:
        return str(members_render)


def _members_fingerprint_tuplehash(members_render: list[dict[str, Any]]) -> int:
    """Candidate fingerprint: hash of only the 8 rendered fields per member."""
    return hash(
        tuple(
            (
                str(m.get("name", "")),
                str(m.get("kind", "")),
                bool(m.get("readable", False)),
                bool(m.get("settable", False)),
                str(m.get("return_annotation") or ""),
                str(m.get("value_annotation") or ""),
                str(m.get("source", "")),
                str(m.get("doc", "") or "")[:40],
            )
            for m in members_render
        )
    )


def bench_tui_members_fingerprint(rows: list[Row]) -> None:
    """Members-table change-detection fingerprint, computed every render.

    _render_members_table (app.py ~1289) json.dumps the full sorted member
    list every call to decide whether to skip the rebuild. A/B: full json
    vs a tuple-hash over only the rendered fields.
    """
    print()
    print("== tui_members_fingerprint ==")
    print(_HEADER)
    for k in (5, 50, 200):
        members = [_make_member(i) for i in range(k)]
        members_render = sorted(
            members,
            key=lambda d: (str(d.get("kind", "")), str(d.get("name", ""))),
        )
        r = time_call(
            "members fp: json.dumps(sort_keys)",
            f"{k} members",
            lambda mr=members_render: _members_fingerprint_current(mr),
            iters=20_000,
        )
        rows.append(r)
        print(r.fmt())
        r = time_call(
            "members fp: hash(tuple) [candidate]",
            f"{k} members",
            lambda mr=members_render: _members_fingerprint_tuplehash(mr),
            iters=20_000,
        )
        rows.append(r)
        print(r.fmt())


def bench_tui_log_fingerprint(rows: list[Row]) -> None:
    """Per-log-entry dedup fingerprint paid in _ingest_manager_log_entry.

    app.py ~569 json.dumps a fixed 6-key dict per surfaced manager.log entry.
    A/B: json.dumps vs a NUL-joined f-string.
    """
    print()
    print("== tui_log_fingerprint ==")
    print(_HEADER)
    base_fp = {
        "sev": "error",
        "topic": "manager.log",
        "source": "device",
        "id": "dev3",
        "message": "Driver raised RuntimeError: connection refused on attempt 4",
        "t_mono": 12345.678,
    }

    def current() -> str:
        try:
            return json.dumps(base_fp, sort_keys=True, default=str)[:800]
        except Exception:
            return ""

    def candidate() -> str:
        return (
            f"{base_fp['sev']}\x00{base_fp['topic']}\x00{base_fp['source']}"
            f"\x00{base_fp['id']}\x00{base_fp['message']}\x00{base_fp['t_mono']}"
        )[:800]

    r = time_call("log fp: json.dumps(sort_keys)", "6 keys", current, iters=50_000)
    rows.append(r)
    print(r.fmt())
    r = time_call("log fp: f-string [candidate]", "6 keys", candidate, iters=50_000)
    rows.append(r)
    print(r.fmt())


def bench_tui_drain_payload_encode(rows: list[Row]) -> None:
    """Per-message event-log encoding in the drain loop (up to 500x/0.2s).

    app.py ~1706 json.dumps the full payload then truncates to 200 chars for
    a visible non-log topic. A/B: full json.dumps vs str(payload).
    """
    print()
    print("== tui_drain_payload_encode ==")
    print(_HEADER)
    rng = np.random.default_rng(7)
    cases: list[tuple[str, dict[str, Any]]] = [
        ("small (4 keys)", {"device_id": "dev1", "action": "set", "ok": True, "seq": 7}),
        (
            "telemetry (20 signals)",
            {
                "device_id": "dev1",
                "ts": {"t_wall": 1.0, "t_mono": 1.0},
                "signals": {
                    f"sig_{i}": {"value": float(rng.normal()), "units": "V"}
                    for i in range(20)
                },
            },
        ),
        (
            "large (200 floats)",
            {
                "device_id": "dev1",
                "stream": "trace",
                "values": rng.normal(size=200).tolist(),
            },
        ),
    ]
    for label, payload in cases:
        r = time_call(
            "drain encode: json.dumps[:200]",
            label,
            lambda p=payload: json.dumps(p)[:200],
            iters=20_000,
        )
        rows.append(r)
        print(r.fmt())
        r = time_call(
            "drain encode: str()[:200] [cand]",
            label,
            lambda p=payload: str(p)[:200],
            iters=20_000,
        )
        rows.append(r)
        print(r.fmt())


def _make_error(i: int) -> dict[str, Any]:
    return {
        "t_mono": 1000.0 + i,
        "t_wall": 1.70e9 + i,
        "severity": ("critical", "error", "warning", "info")[i % 4],
        "source": "device",
        "id": f"dev{i % 10}",
        "topic": "manager.log",
        "message": f"Iteration {i} failed\r\nwith a second line\nand trailing detail",
        "fingerprint": f"fp{i}",
    }


def _errors_rows_current(errors: list[dict[str, Any]]) -> list[tuple]:
    """Replicates the per-row formatting in _render_errors_table (1374-1404)."""
    out: list[tuple] = []
    for entry in reversed(errors):
        t_wall = entry.get("t_wall")
        time_str = ""
        if isinstance(t_wall, (int, float)):
            try:
                time_str = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(float(t_wall))
                )
            except Exception:
                time_str = ""
        message = str(entry.get("message", ""))
        message = (
            message.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ")
        )
        if len(message) > 220:
            message = message[:217] + "..."
        severity = str(entry.get("severity", ""))
        sev_cell: Any
        if severity == "critical":
            sev_cell = Text(severity, style="bold red")
        elif severity == "error":
            sev_cell = Text(severity, style="red")
        elif severity == "warning":
            sev_cell = Text(severity, style="yellow")
        else:
            sev_cell = severity
        out.append(
            (
                time_str,
                sev_cell,
                str(entry.get("source", "")),
                str(entry.get("id", "")),
                message,
            )
        )
    return out


_SEV_TEXT_CACHE = {
    "critical": Text("critical", style="bold red"),
    "error": Text("error", style="red"),
    "warning": Text("warning", style="yellow"),
}


def _errors_rows_candidate(errors: list[dict[str, Any]]) -> list[tuple]:
    """Candidate: per-second strftime cache + prebuilt severity Text cells."""
    out: list[tuple] = []
    strftime_cache: dict[int, str] = {}
    for entry in reversed(errors):
        t_wall = entry.get("t_wall")
        time_str = ""
        if isinstance(t_wall, (int, float)):
            sec = int(t_wall)
            time_str = strftime_cache.get(sec, "")
            if not time_str:
                try:
                    time_str = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(float(sec))
                    )
                    strftime_cache[sec] = time_str
                except Exception:
                    time_str = ""
        message = str(entry.get("message", ""))
        if "\r" in message or "\n" in message:
            message = (
                message.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ")
            )
        if len(message) > 220:
            message = message[:217] + "..."
        severity = str(entry.get("severity", ""))
        sev_cell: Any = _SEV_TEXT_CACHE.get(severity, severity)
        out.append(
            (
                time_str,
                sev_cell,
                str(entry.get("source", "")),
                str(entry.get("id", "")),
                message,
            )
        )
    return out


def bench_tui_errors_row_format(rows: list[Row]) -> None:
    """Errors-table per-row string/Text formatting (excludes DataTable cost).

    _render_errors_table (app.py 1374-1404) formats every row on each call.
    A/B: current (strftime+3x replace+Text per row) vs per-second strftime
    cache + prebuilt severity Text cells.
    """
    print()
    print("== tui_errors_row_format ==")
    print(_HEADER)
    for e in (10, 200):
        errors = [_make_error(i) for i in range(e)]
        r = time_call(
            "errors fmt: current",
            f"{e} rows",
            lambda er=errors: _errors_rows_current(er),
            iters=5_000,
        )
        rows.append(r)
        print(r.fmt())
        r = time_call(
            "errors fmt: cached [candidate]",
            f"{e} rows",
            lambda er=errors: _errors_rows_candidate(er),
            iters=5_000,
        )
        rows.append(r)
        print(r.fmt())


ALL_BENCHES = {
    "bin_stats": bench_bin_stats,
    "sanitize": bench_sanitize,
    "json_dumps": bench_json_dumps,
    "output_index": bench_output_index,
    "trace_snapshot": bench_trace_snapshot_value,
    "snapshot_readout": bench_snapshot_readout,
    "binary_trace_transport": bench_binary_trace_transport,
    "stream_buffer": bench_stream_buffer_assembly,
    "tui_members_fingerprint": bench_tui_members_fingerprint,
    "tui_log_fingerprint": bench_tui_log_fingerprint,
    "tui_drain_payload_encode": bench_tui_drain_payload_encode,
    "tui_errors_row_format": bench_tui_errors_row_format,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bench",
        action="append",
        choices=sorted(ALL_BENCHES.keys()),
        help="Run only the named bench (repeatable). Default: all.",
    )
    parser.add_argument(
        "--no-alloc",
        action="store_true",
        help=(
            "Skip tracemalloc accounting. Tracemalloc adds 10-100× overhead "
            "to per-call timings — disable for accurate latency numbers, "
            "enable for allocation pressure measurement."
        ),
    )
    ns = parser.parse_args(argv)
    if ns.no_alloc:
        global _TRACK_ALLOC_DEFAULT
        _TRACK_ALLOC_DEFAULT = False

    targets = ns.bench or list(ALL_BENCHES.keys())
    rows: list[Row] = []
    print(f"python {sys.version.split()[0]} / numpy {np.__version__}")
    if ns.no_alloc:
        print("(--no-alloc: allocations not measured; per-call times are real)")
    for name in targets:
        ALL_BENCHES[name](rows)

    print()
    print("== summary (interesting lines) ==")
    interesting = [r for r in rows if r.per_call_us >= 100.0]
    if not interesting:
        print("  (no per-call cost >= 100 μs — all paths are cheap)")
    else:
        print(_HEADER)
        for r in interesting:
            print(r.fmt())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
