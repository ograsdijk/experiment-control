"""Widget-level micro-benchmarks for the Textual TUI render/drain hot paths.

Run from repo root:
    uv run python -m bench.run_tui_render_bench --no-alloc

Pass --bench <name> to run a subset; see `--help`.

Unlike bench/run_microbench.py (pure-function costs), these benches drive a
REAL ``ManagerTUI`` mounted headless via Textual's ``app.run_test`` harness,
so the numbers include the actual DataTable.clear/add_row/update_cell and
RichLog.write costs paid by the 5 Hz drain loop and the render methods in
``src/experiment_control/_tui/app.py``.

The app is constructed with a 3600 s snapshot period (so the snapshot timer
never fires), ``_rpc_call`` / ``_load_manager_log_tail_bootstrap`` monkeypatched
to no-ops (no live backend), and ``_stop_event`` set immediately (the SUB poll
thread exits at once). Synthetic data is fed directly into the real caches.

Output format (Row/_HEADER/fmt) is shared with run_microbench so results drop
straight into bench/FINDINGS.md.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import queue
import sys
import time
from typing import Any, Callable

# Reuse the timing Row + formatting + the synthetic-data makers so the two
# bench suites stay consistent.
from bench.run_microbench import (  # noqa: E402
    Row,
    _HEADER,
    _make_error,
    _make_member,
)
from experiment_control._tui.app import ManagerTUI  # noqa: E402
from experiment_control._tui.models import DeviceStatus  # noqa: E402

Json = dict[str, Any]


# ---------------------------------------------------------------------------
# Headless app construction
# ---------------------------------------------------------------------------


def _make_app() -> ManagerTUI:
    app = ManagerTUI(snapshot_period_s=3600.0, rpc_timeout_ms=20)
    # No live backend: kill the two methods that would block / hit the network
    # during on_mount and the snapshot/log-tail paths.
    app._rpc_call = lambda *a, **k: None  # type: ignore[method-assign]
    app._load_manager_log_tail_bootstrap = lambda *a, **k: None  # type: ignore[method-assign]
    return app


# ---------------------------------------------------------------------------
# Synthetic-data seeders (populate the real caches the render methods read)
# ---------------------------------------------------------------------------


def _make_device_status(device_id: str) -> DeviceStatus:
    return DeviceStatus(
        device_id=device_id,
        registered=True,
        liveness="alive",
        hb_age_s=0.4,
        telemetry_age_s=0.2,
        driver_state="RUNNING",
        device_state="CONNECTED",
        device_reachable=True,
        last_error=None,
        driver_proc_state="RUNNING",
        driver_pid=4321,
        driver_restart_count=0,
        driver_last_exit_code=None,
        driver_last_error=None,
    )


def _seed_devices(app: ManagerTUI, n: int) -> list[str]:
    ids = [f"dev{i:03d}" for i in range(n)]
    app._device_status = {d: _make_device_status(d) for d in ids}
    return ids


def _seed_telemetry(app: ManagerTUI, device_id: str, m: int) -> None:
    app._telemetry_cache[device_id] = {
        f"signal_{i:02d}": {
            "value": float(i) + 0.5,
            "units": "V",
            "quality": "ok",
            "ts": {"t_mono": 1000.0 + i},
        }
        for i in range(m)
    }


def _seed_heartbeat(app: ManagerTUI, device_id: str) -> None:
    app._heartbeat_cache[device_id] = {
        "pid": 4321,
        "seq": 99,
        "driver_state": "RUNNING",
        "device_state": "CONNECTED",
        "device_reachable": True,
        "loop_lag_s": 0.01,
        "last_error": "",
    }


def _seed_members(app: ManagerTUI, device_id: str, k: int) -> None:
    app._members_last[device_id] = [_make_member(i) for i in range(k)]


def _seed_errors(app: ManagerTUI, e: int) -> None:
    app._errors.clear()
    for i in range(e):
        app._errors.append(_make_error(i))


def _make_pub_batch(
    n: int,
    device_ids: list[str],
    m: int,
    offset: int,
    log_severity: str = "warning",
) -> list[tuple[str, Json]]:
    """Realistic drain mix: 60% telemetry (hidden), 30% heartbeat (hidden),
    10% manager.log (errors path + visible event-log write).

    log_severity="info" models the common case of sub-threshold log chatter,
    which must NOT trigger an errors-table rebuild."""
    out: list[tuple[str, Json]] = []
    nd = len(device_ids)
    for i in range(n):
        idx = offset + i
        did = device_ids[i % nd]
        r = i % 10
        if r < 6:
            out.append(
                (
                    "manager.telemetry_update",
                    {
                        "device_id": did,
                        "ts": {"t_mono": 1000.0 + idx},
                        "signals": {
                            f"signal_{j:02d}": {
                                "value": float(j) + idx,
                                "units": "V",
                                "quality": "ok",
                                "ts": {"t_mono": 1000.0 + idx},
                            }
                            for j in range(m)
                        },
                    },
                )
            )
        elif r < 9:
            out.append(
                (
                    "manager.heartbeat",
                    {
                        "device_id": did,
                        "pid": 4321,
                        "seq": idx,
                        "driver_state": "RUNNING",
                        "device_state": "CONNECTED",
                        "device_reachable": True,
                        "loop_lag_s": 0.01,
                        "last_error": "",
                    },
                )
            )
        else:
            out.append(
                (
                    "manager.log",
                    {
                        "severity": log_severity,
                        "topic": "manager.log",
                        "source_kind": "device",
                        "source_id": did,
                        "device_id": did,
                        "message": f"transient warning {idx} on {did}",
                        "ts": {"t_wall": 1.70e9 + idx, "t_mono": 1000.0 + idx},
                    },
                )
            )
    return out


# ---------------------------------------------------------------------------
# Timing (synchronous render methods, called inside the async test context)
# ---------------------------------------------------------------------------


def _time_sync(
    label: str, size_label: str, fn: Callable[[], Any], *, iters: int
) -> Row:
    for _ in range(min(20, iters)):
        fn()
    gc.collect()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    elapsed = time.perf_counter() - t0
    return Row(
        target=label,
        size_label=size_label,
        n_iter=iters,
        total_s=elapsed,
        per_call_us=(elapsed / iters) * 1e6,
        bytes_per_call=0.0,
    )


# ---------------------------------------------------------------------------
# Benches
# ---------------------------------------------------------------------------


async def bench_errors_table(rows: list[Row], pilot: Any, app: ManagerTUI) -> None:
    print()
    print("== render: errors_table (clear + rebuild) ==")
    print(_HEADER)
    for e in (10, 200):
        _seed_errors(app, e)

        def rebuild() -> None:
            # Force the genuine clear+rebuild past the built-in rev skip-guard.
            app._errors_rev += 1
            app._render_errors_table()

        r = _time_sync(
            "errors_table: full rebuild",
            f"{e} rows",
            rebuild,
            iters=400,
        )
        rows.append(r)
        print(r.fmt())
        await pilot.pause()
        # Real built-in skip-guard: repeated render with no change to _errors.
        app._render_errors_table()
        r = _time_sync(
            "errors_table: skip-guard (unchanged)",
            f"{e} rows",
            app._render_errors_table,
            iters=400,
        )
        rows.append(r)
        print(r.fmt())
        await pilot.pause()


async def bench_device_inspector(rows: list[Row], pilot: Any, app: ManagerTUI) -> None:
    print()
    print("== render: device_inspector (telemetry+hb+driver clear+rebuild) ==")
    print(_HEADER)
    ids = _seed_devices(app, 5)
    did = ids[0]
    app._selected_device_id = did
    app._members_source = "device"
    app._inspector_mode = "device"
    _seed_heartbeat(app, did)
    for m in (5, 50):
        _seed_telemetry(app, did, m)
        r = _time_sync(
            "device_inspector: full rebuild",
            f"{m} signals",
            app._render_device_inspector,
            iters=600,
        )
        rows.append(r)
        print(r.fmt())
        await pilot.pause()


async def bench_members_table(rows: list[Row], pilot: Any, app: ManagerTUI) -> None:
    print()
    print("== render: members_table (fingerprint guard + rebuild) ==")
    print(_HEADER)
    ids = _seed_devices(app, 5)
    did = ids[0]
    app._selected_device_id = did
    app._members_source = "device"
    app._inspector_mode = "device"
    for k in (5, 50, 200):
        _seed_members(app, did, k)
        # rebuild path: invalidate the cached fingerprint each call
        def rebuild() -> None:
            app._members_rendered_fingerprint.clear()
            app._members_context_key = None
            app._render_members_table()

        r = _time_sync(
            "members_table: rebuild",
            f"{k} members",
            rebuild,
            iters=400,
        )
        rows.append(r)
        print(r.fmt())
        await pilot.pause()
        # skip path: warm once, then every call hits the fingerprint guard
        app._render_members_table()
        r = _time_sync(
            "members_table: skip (fp unchanged)",
            f"{k} members",
            app._render_members_table,
            iters=400,
        )
        rows.append(r)
        print(r.fmt())
        await pilot.pause()


async def bench_devices_table(rows: list[Row], pilot: Any, app: ManagerTUI) -> None:
    print()
    print("== render: devices_table (steady-state incremental update_cell) ==")
    print(_HEADER)
    for n in (2, 20, 100):
        _seed_devices(app, n)
        # warm so the table is populated; timed calls take the update_cell path
        app._render_devices_table()
        r = _time_sync(
            "devices_table: incremental",
            f"{n} devices",
            app._render_devices_table,
            iters=400,
        )
        rows.append(r)
        print(r.fmt())
        await pilot.pause()


async def bench_drain(rows: list[Row], pilot: Any, app: ManagerTUI) -> None:
    print()
    print("== drain: _drain_pub_queue (full cycle, busy mix) ==")
    print(_HEADER)
    ids = _seed_devices(app, 20)
    did = ids[0]
    app._selected_device_id = did
    app._members_source = "device"
    app._inspector_mode = "device"
    _seed_heartbeat(app, did)
    _seed_telemetry(app, did, 50)
    m_signals = 50
    app.streaming_enabled = True
    # "warn" = unique warning logs (real errors accumulate, render is genuine);
    # "info" = sub-threshold chatter (must NOT trigger an errors rebuild).
    for sev, label in (("warning", "warn logs"), ("info", "info logs")):
        for batch in (10, 100, 500):
            app._pub_drain_max = batch
            # warmup
            for w in range(3):
                for msg in _make_pub_batch(
                    batch, ids, m_signals, offset=-(w + 1) * batch, log_severity=sev
                ):
                    try:
                        app._pub_queue.put_nowait(msg)
                    except queue.Full:
                        break
                app._drain_pub_queue()
            gc.collect()
            iters = 300
            total = 0.0
            for it in range(iters):
                for msg in _make_pub_batch(
                    batch, ids, m_signals, offset=it * batch, log_severity=sev
                ):
                    try:
                        app._pub_queue.put_nowait(msg)
                    except queue.Full:
                        break
                t0 = time.perf_counter()
                app._drain_pub_queue()
                total += time.perf_counter() - t0
            rows.append(
                Row(
                    target=f"_drain_pub_queue: {label}",
                    size_label=f"batch {batch}",
                    n_iter=iters,
                    total_s=total,
                    per_call_us=(total / iters) * 1e6,
                    bytes_per_call=0.0,
                )
            )
            print(rows[-1].fmt())
            await pilot.pause()
    app.streaming_enabled = False


ALL_BENCHES: dict[str, Callable[[list[Row], Any, ManagerTUI], Any]] = {
    "errors_table": bench_errors_table,
    "device_inspector": bench_device_inspector,
    "members_table": bench_members_table,
    "devices_table": bench_devices_table,
    "drain": bench_drain,
}


async def _run(targets: list[str]) -> list[Row]:
    rows: list[Row] = []
    app = _make_app()
    async with app.run_test(headless=True, size=(120, 50)) as pilot:
        app._stop_event.set()  # stop the SUB poll thread immediately
        app.streaming_enabled = False  # quiet the 0.2 s interval drain
        await pilot.pause()
        for name in targets:
            await ALL_BENCHES[name](rows, pilot, app)
    return rows


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
        help="Accepted for parity with run_microbench; allocations are not "
        "tracked in this harness (tracemalloc under Textual's async loop is "
        "noisy). Per-call times are always real.",
    )
    ns = parser.parse_args(argv)
    targets = ns.bench or list(ALL_BENCHES.keys())
    print(f"python {sys.version.split()[0]} (TUI render bench, headless Textual)")
    rows = asyncio.run(_run(targets))

    print()
    print("== summary (interesting lines) ==")
    interesting = [r for r in rows if r.per_call_us >= 100.0]
    if not interesting:
        print("  (no per-call cost >= 100 μs)")
    else:
        print(_HEADER)
        for r in interesting:
            print(r.fmt())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
