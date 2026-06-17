# Bench findings

Initial run: Python 3.13.5 / numpy 2.4.1 / Windows 11, using `--no-alloc` for realistic latency numbers.

## Current status

The original benchmark run identified two main backend costs:

1. JSON encoding large stream frames.
2. Recursive `_sanitize_json` walks over large trace payloads.

Both findings have already informed code changes:

- `json_dumps` now prints pyzmq/stdlib, direct orjson, and production `experiment_control.utils.zmq_helpers.json_dumps` timings side-by-side, making the current speedup directly visible.
- `trace_snapshot` was added to validate the current trace snapshot fast path: finite numpy traces use `np.isfinite` plus one `tolist()` instead of `tolist()` plus a full recursive sanitize walk.
- `snapshot_readout` was added to measure workspace snapshot response cost for cached trace payloads and compare sanitize/decimation ordering.
- `stream_buffer` was added to compare HDF stream-buffer assembly strategies for large shared-memory-backed stream batches.

## Fresh JSON encoder comparison

Run on 2026-06-08 with `uv run python -m bench.run_microbench --no-alloc --bench json_dumps`.

| Payload | pyzmq/stdlib (µs) | orjson (µs) | production `json_dumps` (µs) | Speedup |
|---|---:|---:|---:|---:|
| telemetry bundle, 20 signals | 26.18 | 2.04 | 2.04 | 12.8× |
| stream frame, 100 pts | 67.99 | 3.37 | 3.39 | 20.1× |
| stream frame, 1,000 pts | 775.41 | 30.93 | 28.98 | 26.8× |
| stream frame, 50,000 pts | 39,469.06 | 1,963.96 | 2,019.44 | 19.5× |
| stream frame, 200,000 pts | 147,990.69 | 9,377.60 | 9,014.38 | 16.4× |

Production `json_dumps` tracks direct orjson performance, confirming the fallback wrapper has negligible overhead on normal stream payloads.

## Fresh binary trace transport comparison

Run on 2026-06-15 with `uv run python -m bench.run_microbench --no-alloc --bench binary_trace_transport` after adding ndarray-backed raw-stream binary frames.

| Payload | JSON values frame | old binary from list | ideal binary from ndarray | production binary builder |
|---|---:|---:|---:|---:|
| 1k pts | 30.30 µs | 33.96 µs | 1.13 µs | 11.02 µs |
| 50k pts | 1.97 ms | 1.76 ms | 0.017 ms | 0.051 ms |
| 200k pts | 8.18 ms | 7.67 ms | 0.51 ms | 0.91 ms |

Key conclusions:

- The list-backed binary path did not materially improve backend CPU because it still paid list→ndarray conversion.
- The current production binary builder now uses the private ndarray carried by `StreamFrameHub`, avoiding the `.tolist()`/JSON-values path for binary raw-stream clients.
- At 200k points, backend framing drops from ~8.18 ms JSON to ~0.91 ms production binary, about a 9× improvement.
- The remaining gap to the ideal ndarray path is mostly metadata/build wrapper overhead and safety checks; it is small compared with the original JSON cost.

## Fresh snapshot readout comparison

Run on 2026-06-08 with `uv run python -m bench.run_microbench --no-alloc --bench snapshot_readout`.

Representative rows:

| Payload | current sanitize-then-decimate | decimate-then-sanitize | known-clean fast path |
|---|---:|---:|---:|
| 50k finite, full | 17.04 ms | 16.12 ms | 0.25 ms |
| 50k finite, max2k | 17.20 ms | 0.64 ms | 0.02 ms |
| 50k dirty, max2k | 16.83 ms | 0.60 ms | 0.26 ms |
| 200k finite, full | 69.53 ms | 70.92 ms | 1.96 ms |
| 200k finite, max2k | 69.53 ms | 0.99 ms | 0.10 ms |
| 200k dirty, max2k | 73.53 ms | 0.67 ms | 0.27 ms |

Key conclusions:

- Current snapshot readout still pays the full recursive sanitize cost before applying `max_trace_points`.
- Decimating before sanitizing is a 70–110× win for `max_trace_points=2000` on large traces.
- For known-clean finite cached traces, skipping the recursive sanitize walk is another 6–10× faster than decimate-then-sanitize.
- Full-resolution readout still benefits most from the known-clean path; decimate-first cannot help when no point limit is requested.

This suggests a production follow-up in `stream_analysis.py`: for cached trace payloads, apply `max_trace_points` before `_sanitize_json`, and use a known-clean fast path for trace values created by `_build_trace_snapshot_payload`.

## TUI render hot paths (2026-06-17)

The TUI (`src/experiment_control/_tui/app.py`) updates on a 0.2 s (5 Hz) drain
loop plus a ~2 s snapshot poll. Two new benches profile it:

- `bench/run_microbench.py --bench tui_*` — pure data-processing costs.
- `bench/run_tui_render_bench.py` — real DataTable/RichLog costs against a
  headless-mounted `ManagerTUI`.

### Widget-level baseline (`run_tui_render_bench --no-alloc`)

| Path | size | per call | Notes |
|---|---|---:|---|
| `_render_errors_table` (clear+rebuild) | 10 rows | 0.49 ms | Fires once per drain when `errors_dirty`. |
| `_render_errors_table` (clear+rebuild) | 200 rows | **7.12 ms** | Deque is capped at 200; full rebuild every time. |
| `_render_errors_table` skip-guard no-op | 200 rows | ~0.0001 ms | Cost if we return early when unchanged. |
| `_render_device_inspector` | 5 signals | 0.51 ms | clear+rebuild of telemetry/hb/driver. |
| `_render_device_inspector` | 50 signals | 1.70 ms | At 5 Hz ≈ 8.5 ms/s while a device is selected. |
| `_render_members_table` rebuild | 50 / 200 | 1.77 / 6.68 ms | On capability change. |
| `_render_members_table` skip (fp unchanged) | 50 / 200 | 0.17 / 0.68 ms | **Common case** — almost all the cost is the json.dumps fingerprint. |
| `_render_devices_table` incremental | 20 / 100 | 0.97 / 3.07 ms | Already incremental; runs every ~2 s only. |
| `_drain_pub_queue` busy mix | batch 10 | 7.84 ms | Dominated by the errors rebuild fired by the manager.log entries in the batch. |
| `_drain_pub_queue` busy mix | batch 100 / 500 | 10.9 / 30.3 ms | Adds per-message processing on top of the errors rebuild. |

### Pure-function A/B (`run_microbench --bench tui_*`)

| Candidate | size | current | candidate | speedup |
|---|---|---:|---:|---:|
| members fingerprint: json.dumps → hash(tuple) | 50 | 167 µs | 34 µs | 4.9× |
| members fingerprint: json.dumps → hash(tuple) | 200 | 656 µs | 153 µs | 4.3× |
| log fingerprint: json.dumps → f-string | 6 keys | 6.5 µs | 1.2 µs | 5.3× |
| drain payload encode: json.dumps → str() | telemetry/large | 30 / 163 µs | 28 / 148 µs | ~1.1× |
| errors row *formatting* only: current → cached | 200 rows | 988 µs | 801 µs | ~1.2× |

### Conclusions (drives the optimization set)

- **The errors table rebuild (7.1 ms at 200 rows) is the single largest TUI
  cost** and it fires on *every* drain cycle that ingests a `manager.log` —
  even when the log is below the warning threshold or a duplicate, because
  `_drain_pub_queue` sets `errors_dirty=True` unconditionally for the topic
  (app.py ~1687). **Fix: only mark dirty when an error was actually appended**
  (`_ingest_manager_log_entry` returns whether it recorded), plus a skip-guard
  so the render is a true no-op when nothing changed. Removes ms-scale
  redundant work per cycle. **GO.**
- **The members fingerprint dominates the common "skip" render** (0.68 ms at
  200 members is almost entirely json.dumps). A tuple-hash over the 8 rendered
  fields is 4–5× cheaper and recurs on every inspector render. **GO.**
- **Errors row *formatting* and drain payload `json.dumps` are marginal**
  (~1.1–1.2×). The errors cost is the DataTable rebuild, not the string work;
  and default-hidden topics (telemetry/heartbeat/chunk_ready) never hit the
  drain `json.dumps`. **NO-GO** (documented, left unchanged).
- **Inspector telemetry is debounced to 0.2 s and incremental `update_cell` is
  only ~1.2× faster than clear+rebuild in Textual** — not worth the
  cursor/scroll risk. **DEFERRED.**
- **Batched RichLog writes (B.6)** only matter at high *visible*-topic volume;
  with default hidden topics the drain writes few lines. **DEFERRED.**

### After optimizations (re-run, same hardware)

The errors skip-guard, dirty-on-append fix, and members tuple-hash landed in
`app.py`. Re-measured:

| Path | size | before | after | change |
|---|---|---:|---:|---:|
| `_render_errors_table` unchanged (skip-guard) | 200 rows | 7.12 ms (rebuild) | **0.0001 ms** | render skipped |
| `_render_members_table` skip (fp unchanged) | 50 | 0.17 ms | **0.046 ms** | 3.8× |
| `_render_members_table` skip (fp unchanged) | 200 | 0.68 ms | **0.21 ms** | 3.3× |
| `_drain_pub_queue` sub-threshold (info) logs | batch 10 | ~7.8 ms | **0.099 ms** | ~79× |
| `_drain_pub_queue` sub-threshold (info) logs | batch 100 | ~10.9 ms | **1.19 ms** | ~9× |

A genuine new-error drain still pays one errors rebuild per cycle (`warn logs`
≈ 5.5 ms at batch 10) — that is real work, not waste; an incremental errors
render would be the next step if high-rate error storms become a concern.

## Historical headline numbers

These numbers are from the first `--no-alloc` run before the trace/json improvements landed.

| Path | per call (µs) | Notes |
|---|---:|---|
| `BinStatsState.update_sample` | ~1.8 | Per-sample; effectively free. |
| `BinStatsState.payload(50 bins)` | 150 | At 20 Hz emit = 3 ms/s. Fine. |
| `BinStatsState.payload(200 bins)` | 472 | At 20 Hz = 9 ms/s per workspace. |
| `BinStatsState.payload(1000 bins)` | 2,002 | At 20 Hz = 40 ms/s per workspace. |
| `BinStatsState.payload(5000 bins)` | 9,581 | At 20 Hz = 192 ms/s per workspace. |
| `_sanitize_json(telemetry 20 sigs)` | 32 | Fine. |
| `_sanitize_json(stream frame 1k pts)` | 331 | At 50 Hz = 17 ms/s. |
| `_sanitize_json(stream frame 50k pts)` | 16,676 | At 10 Hz = 167 ms/s. |
| `_sanitize_json(stream frame 200k pts)` | 68,506 | At 5 Hz = 343 ms/s. |
| `_sanitize_json(bin_stats 1000 bins)` | 1,702 | At 20 Hz = 34 ms/s. |
| old `json_dumps(telemetry 20 sigs)` | 32 | Fine. |
| old `json_dumps(stream frame 1k pts)` | 636 | At 50 Hz = 32 ms/s. |
| old `json_dumps(stream frame 50k pts)` | 34,677 | At 10 Hz = 347 ms/s. |
| old `json_dumps(stream frame 200k pts)` | 138,209 | At 5 Hz = 690 ms/s. |
| `build_panels_by_workspace_output(200)` | 127 | Cheap enough to rebuild on panel-list edits. |

## Interpretation

### Large stream-frame serialization remains the path to watch

Large trace payloads dominate CPU when encoded at high rate. The exact numbers should now be regenerated with the current `json_dumps` implementation before making further encoder decisions.

### Trace snapshot sanitization has a focused benchmark

`trace_snapshot` compares the old full recursive sanitize path with the current finite-array fast path. This should be rerun when changing trace output or snapshot storage code.

### Histogram payload caching is still low priority

`BinStatsState.update_sample()` is cheap. `BinStatsState.payload()` becomes noticeable only at high bin counts; default 100–200-bin usage is not worth caching unless production profiling shows thousands of bins at high emit rate.

### Frontend reverse-index construction is cheap

The output-index benchmark showed sub-millisecond rebuilds at realistic panel counts. The reverse-index design is not a backend bottleneck.

## Useful next runs

```powershell
uv run python -m bench.run_microbench --no-alloc --bench json_dumps --bench trace_snapshot
uv run python -m bench.run_microbench --no-alloc --bench binary_trace_transport
uv run python -m bench.run_microbench --no-alloc --bench snapshot_readout
uv run python -m bench.run_microbench --no-alloc --bench stream_buffer
uv run python -m bench.run_microbench --no-alloc
```

Update this file with fresh current-encoder numbers after the next full run on the target hardware.
