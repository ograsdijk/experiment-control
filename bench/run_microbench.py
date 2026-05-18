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
import math
import sys
import time
import tracemalloc
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

# Use the same JSON encoder as the production stack uses (pyzmq's
# jsonapi.dumps), so timings reflect what the gateway actually
# pays per message.
from experiment_control.utils.zmq_helpers import json_dumps  # noqa: E402
from experiment_control.processes.stream_analysis import (  # noqa: E402
    BinStatsState,
    Bin2DStatsState,
    _sanitize_json as stream_analysis_sanitize_json,
)


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
            f"bin_stats.update_sample",
            f"{bin_count} bins",
            update,
            iters=20_000,
        )
        rows.append(r)
        print(r.fmt())

        r = time_call(
            f"bin_stats.payload",
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
    """Time the production `json_dumps` on representative payloads."""
    print()
    print("== json_dumps (pyzmq jsonapi) ==")
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

    for label, payload in cases:
        iters = 100 if label.startswith("stream frame (200") else 1_000
        r = time_call(
            "json_dumps",
            label,
            lambda p=payload: json_dumps(p),
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


ALL_BENCHES = {
    "bin_stats": bench_bin_stats,
    "sanitize": bench_sanitize,
    "json_dumps": bench_json_dumps,
    "output_index": bench_output_index,
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
