# Micro-benchmark suite

Isolated timings of specific backend hot paths. Built to answer
"is this code path actually expensive enough to optimise?"
without having to bring up the whole stack and profile under
production load.

Each script self-contains: synthetic inputs, repeated calls, reports
per-call timing + allocation pressure. No external services
required.

## Running

From the repo root:

```
.venv\Scripts\python.exe -m bench.run_microbench
```

(or `python -m bench.run_microbench` if your venv is already
active).

Pass `--bench <name>` to run a subset (`bin_stats`, `sanitize`,
`json_dumps`, `output_index`). Default: run all.

## What's measured

- **`bin_stats`** — `BinStatsState.update_sample()` + `.payload()`
  cost as a function of bin count. Validates whether the histogram
  payload-build path is fast enough at the rate-limited
  `max_hist_output_hz=20` cadence, or worth caching.

- **`sanitize`** — `_sanitize_json()` cost as a function of payload
  shape (deep + narrow vs flat + wide). Validates whether the
  redundant-walk patterns flagged in audits actually matter at
  realistic payload sizes.

- **`json_dumps`** — `zmq.utils.jsonapi.dumps` cost on representative
  telemetry / stream-frame payload shapes. Sets a floor for what
  the WS broadcast path can possibly achieve.

- **`output_index`** — `buildPanelsByWorkspaceOutput`-equivalent
  index build cost as a function of panel count. Validates the
  reverse-index design from PerfC.

## Reading the output

Each section prints:

```
target              size      n_iter   total_s    per_call_us   allocs/call
bin_stats.payload   100      10000    0.234      23.4           ~1.2 KB
bin_stats.payload   1000     10000    1.876      187.6          ~12 KB
```

`per_call_us` is the actionable number. If it's <10 μs, the path
is cheap; if it's >100 μs, anything called at 50 Hz × N panels
will show up in a profiler.
