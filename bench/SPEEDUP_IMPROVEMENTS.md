# Possible speedup improvements

This note captures follow-up performance work suggested by the benchmark results and current backend hot paths.

## Current baseline

The benchmark suite shows that the largest historical backend costs were:

1. JSON encoding large stream/trace payloads.
2. Recursive JSON sanitization of large trace values.

The project has already reduced both:

- `json_dumps` now uses an orjson-backed encoder with pyzmq fallback.
- FastAPI WebSocket sends use the same production encoder.
- Trace snapshot storage uses an `np.isfinite` fast path and `_remember_latest_output_clean(...)` to avoid re-sanitizing large finite trace lists on write.

Fresh `json_dumps` benchmark results on 2026-06-08:

| Payload | pyzmq/stdlib | production `json_dumps` | Speedup |
|---|---:|---:|---:|
| telemetry bundle, 20 signals | 26.18 µs | 2.04 µs | 12.8× |
| stream frame, 100 pts | 67.99 µs | 3.39 µs | 20.1× |
| stream frame, 1,000 pts | 775.41 µs | 28.98 µs | 26.8× |
| stream frame, 50,000 pts | 39.47 ms | 2.02 ms | 19.5× |
| stream frame, 200,000 pts | 147.99 ms | 9.01 ms | 16.4× |

Even after the encoder improvement, large numeric arrays remain the main path to watch.

## 1. Avoid JSON for large trace values

A 200k-point trace still costs about 9 ms to encode with orjson. That is much better than the old pyzmq/stdlib path, but still meaningful at high frame rates.

Potential design:

- Send JSON only for metadata:
  - `workspace_id`
  - `output_id`
  - `seq`
  - `dtype`
  - `shape`
  - `point_count`
  - `shm_name` or binary-frame id
- Send numeric values separately as binary data:
  - shared-memory descriptor
  - WebSocket binary frame
  - msgpack/Arrow/npy chunk
  - browser fetch of a binary buffer

Why this helps:

- Numeric arrays are already binary data.
- JSON expands every float into text.
- Browser rendering usually wants typed arrays anyway.
- This reduces CPU, payload size, and parsing cost.

This is likely the largest remaining architectural win for large live traces.

## 2. Decimate before encoding UI-bound traces

Most live plots do not need 200k points if the display is only 1k–3k pixels wide.

Potential design:

- Keep full-resolution data in shared memory and HDF.
- Send a preview representation for live UI:
  - min/max envelope per pixel bucket
  - fixed point cap, e.g. 2k–10k points
  - level-of-detail based on zoom
- Fetch full-resolution data only for export or zoomed inspection.

Why this helps:

- Encoding 2k points instead of 200k cuts payload size by about 100×.
- Rendering becomes faster.
- Network traffic drops.
- This helps regardless of JSON vs binary transport.

## 3. Benchmark snapshot readout sanitization

Trace snapshot storage avoids the old recursive sanitize walk, but snapshot readout still calls `_sanitize_json(dict(cached))` in `stream_analysis.py` when building workspace snapshot responses.

A dedicated benchmark now exists:

```powershell
uv run python -m bench.run_microbench --no-alloc --bench snapshot_readout
```

It compares:

- current sanitize-before-decimation behavior
- decimate-before-sanitize behavior
- a known-clean fast path that sanitizes only small metadata/context and skips recursive trace-value walks

Fresh results show that, for a 200k-point cached trace with `max_trace_points=2000`, current readout costs about 70 ms while decimate-before-sanitize costs about 1 ms and the known-clean fast path costs about 0.1–0.3 ms.

Potential production fixes:

- Decimate trace values before sanitizing.
- Skip `_sanitize_json` for known-clean cached trace values.
- Sanitize only metadata and context fields.
- Store a flag or separate path for clean trace payloads.

## 4. Use stream-buffer benchmark results for HDF write path

The `stream_buffer` benchmark compares three stream-buffer assembly strategies:

1. Current-style list of bytes.
2. Preallocated numpy buffer copied from bytes.
3. Preallocated numpy buffer copied from a shared-memory view.

Run:

```powershell
uv run python -m bench.run_microbench --no-alloc --bench stream_buffer
```

This matters most for high-rate stream workloads, especially large detection-style batches such as:

- 50 Hz
- 5 MHz sample rate
- 20 ms windows
- multiple int16 channels
- approximately 100 MB per flush batch

Potential wins:

- Less Python object churn.
- Fewer intermediate copies.
- Faster flush thread assembly.
- Better separation of main-loop work from background write work.

## 5. Keep bin-stats caching low priority

Current benchmark findings:

- `BinStatsState.update_sample()` is effectively free.
- `BinStatsState.payload()` is fine at default 100–200 bin counts.
- 1000+ bin payloads can become noticeable at high output rates.

Possible optimization if production uses large histograms:

- Cache payload output between updates.
- Invalidate only when a new sample arrives.
- Avoid rebuilding full lists unless a consumer needs the payload.

Do not prioritize this unless production profiling shows large bin counts at high emit rates.

## 6. TUI render hot paths (landed 2026-06-17)

Benchmark-first pass over the Textual TUI (`src/experiment_control/_tui/app.py`),
profiled with `bench/run_tui_render_bench.py` and the `tui_*` microbenches.

### Landed (confirmed wins)

- **Errors-table skip-guard + correct dirty-tracking.** `_render_errors_table`
  does a full `clear()`+rebuild (~7.1 ms at the 200-row cap) and the drain loop
  fired it for *every* `manager.log` message — even sub-threshold or duplicate
  ones that change nothing. Added an `_errors_rev` counter (bumped in
  `_record_error`) with a render skip-guard, and made `_ingest_manager_log_entry`
  return whether it actually appended so the drain only marks the table dirty on
  a real change. Result: an unchanged render drops from ~6.3 ms to ~0.0001 ms,
  and a full drain cycle over sub-threshold log chatter drops from ~7.8 ms to
  **~0.1 ms** (batch 10) — about a 79× reduction.

- **Members-table tuple-hash fingerprint.** `_render_members_table` json.dumped
  the full sorted member list every render for change detection. Replaced with
  `hash(tuple(...))` over exactly the rendered fields. The common "unchanged"
  render drops ~3.3–3.8× (172→46 µs at 50 members, 678→208 µs at 200).

### Measured but not worth it (left unchanged)

- **Errors row *formatting* cache** (strftime/Text): only ~1.2× — the errors
  cost is the `DataTable` rebuild, not the string work. The skip-guard above is
  the real win.
- **Drain `json.dumps(payload)` → `str()`**: ~1.1× on telemetry/large payloads,
  and the default-hidden topics (telemetry/heartbeat/chunk_ready) never reach
  this line anyway.
- **Inspector telemetry incremental `update_cell`**: only ~1.2× vs clear+rebuild
  in Textual, and the inspector is already debounced to 0.2 s — not worth the
  cursor/scroll-preservation risk. Deferred.
- **Batched `RichLog.write` per drain cycle**: only matters at high *visible*-
  topic volume; with default hidden topics the drain writes few lines. Deferred.

Baseline and A/B tables are in `FINDINGS.md` ("TUI render hot paths").

## Recommended next steps

1. Run focused current benchmarks:

   ```powershell
   uv run python -m bench.run_microbench --no-alloc --bench trace_snapshot --bench stream_buffer
   ```

2. Add a dedicated snapshot readout benchmark for cached trace payloads.

3. If snapshot readout is hot, decimate before sanitizing and skip sanitize for known-clean trace values.

4. For live trace UI, evaluate a binary/SHM descriptor transport or strict preview decimation.

5. Revisit bin-stats caching only if production uses thousands of bins.

The biggest remaining principle is: avoid sending large numeric arrays as JSON text when a binary or decimated representation would preserve the user-visible behavior.
