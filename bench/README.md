# Micro-benchmark suite

Isolated timings of backend hot paths. The suite answers "is this path expensive enough to optimize?" without bringing up the full stack.

Each benchmark uses synthetic but representative inputs, repeated calls, and reports per-call latency plus optional allocation pressure. No external services are required.

## Running

From the repo root:

```powershell
uv run python -m bench.run_microbench --no-alloc
```

Use `--no-alloc` for realistic timing. Omit it only when you specifically want `tracemalloc` allocation accounting; allocation tracking adds substantial overhead.

Run a subset with `--bench <name>`; the flag is repeatable.

Available benchmarks:

- `bin_stats`
- `sanitize`
- `json_dumps`
- `output_index`
- `trace_snapshot`
- `snapshot_readout`
- `stream_buffer`

## What's measured

- **`bin_stats`** — `BinStatsState.update_sample()` and `.payload()` cost as a function of bin count. Validates whether histogram payload construction is fast enough at `max_hist_output_hz=20` or worth caching.

- **`sanitize`** — `_sanitize_json()` cost as a function of payload shape. Validates whether recursive JSON-safe sanitization is significant for large stream outputs.

- **`json_dumps`** — side-by-side encoding cost for pyzmq/stdlib `jsonapi.dumps`, direct `orjson.dumps`, and production `experiment_control.utils.zmq_helpers.json_dumps` on representative telemetry and stream-frame payloads.

- **`output_index`** — `buildPanelsByWorkspaceOutput`-equivalent reverse-index construction cost as a function of panel count.

- **`trace_snapshot`** — compares the old trace snapshot materialization path (`tolist()` plus recursive sanitize) with the current `np.isfinite` fast path used for finite traces.

- **`snapshot_readout`** — compares workspace snapshot response strategies for cached trace payloads: current sanitize-then-decimate, decimate-before-sanitize, and a known-clean fast path.

- **`stream_buffer`** — compares HDF stream-buffer assembly strategies: list-of-bytes baseline, preallocated copy from bytes, and preallocated copy from a shared-memory view.

## Reading the output

Each section prints:

```text
target                                  size     n_iter      total   per call alloc/call
bin_stats.payload                    100 bins      5,000     0.234s      46.80    1.20 KB
```

`per_call_us` is the actionable number. As a rough guide: <10 µs is cheap; >100 µs can matter if called at high rate or across many panels/workspaces.
