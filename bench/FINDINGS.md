# Initial bench findings

Run on python 3.13.5 / numpy 2.4.1 / Windows 11. Per-call timings
captured with `--no-alloc` (tracemalloc adds 10–100× overhead and
would skew comparisons).

## Headline numbers (per-call, microseconds)

| Path                                         | per call (µs) | Notes |
|----------------------------------------------|---------------|-------|
| `BinStatsState.update_sample`                | ~1.8          | Per-sample; fires up to ~1 kHz. Effectively free. |
| `BinStatsState.payload(50 bins)`             | 150           | At 20 Hz emit = 3 ms/s. Fine. |
| `BinStatsState.payload(200 bins)`            | 472           | At 20 Hz = 9 ms/s per workspace. |
| `BinStatsState.payload(1000 bins)`           | 2,002         | At 20 Hz = **40 ms/s** per workspace. |
| `BinStatsState.payload(5000 bins)`           | 9,581         | At 20 Hz = **192 ms/s** per workspace (≈20% of one core). |
| `_sanitize_json(telemetry 20 sigs)`          | 32            | Fine. |
| `_sanitize_json(stream frame 1k pts)`        | 331           | At 50 Hz = 17 ms/s. |
| `_sanitize_json(stream frame 50k pts)`       | 16,676        | At 10 Hz = **167 ms/s**. |
| `_sanitize_json(stream frame 200k pts)`      | 68,506        | At 5 Hz = **343 ms/s**. |
| `_sanitize_json(bin_stats 1000 bins)`        | 1,702         | At 20 Hz = 34 ms/s. |
| `json_dumps(telemetry 20 sigs)`              | 32            | Fine. |
| `json_dumps(stream frame 1k pts)`            | 636           | At 50 Hz = 32 ms/s. |
| `json_dumps(stream frame 50k pts)`           | 34,677        | At 10 Hz = **347 ms/s**. |
| `json_dumps(stream frame 200k pts)`          | **138,209**   | At 5 Hz = **690 ms/s** ≈ 70% of one core. |
| `build_panels_by_workspace_output(200)`      | 127           | Cheap enough to run on every panels-list edit. |

(Bolded rows are >5% of a core at realistic emit rates.)

## What the numbers say

### The dominant backend cost is JSON-encoding large stream frames.

`json_dumps` on a 200k-point trace takes **138 ms**. For comparison,
the same payload through orjson would take ~5–15 ms (5–25×
faster). For a 50k-point trace at 10 Hz, switching encoders saves
~300 ms/s of CPU. This is the single largest win on the table.

The current encoder is `zmq.utils.jsonapi.dumps`, which falls back
to the stdlib `json` module — pure Python with C-accelerated
serialization but no native numpy support.

### `_sanitize_json` is the second-largest cost on stream frames.

68 ms for 200k points (recursive Python walk, dict-comprehension
rebuild). This runs **in addition** to `json_dumps`. Audit item #1
(from the broad backend pass) suggested avoiding redundant
re-sanitization — that observation is more important than I
estimated, *because the per-call cost is much higher than I
assumed*. Skipping one redundant walk per 200k-point frame at 10 Hz
saves ~685 ms/s of CPU.

The trace-output path in `stream_analysis.py:4334` and `4497` does
re-sanitize the full payload via `_remember_latest_output(payload)`
(line 3257). The values list comes from `trace.tolist()` which can
produce `nan`/`inf` Python floats — so we can't drop sanitize
entirely, but we can sanitize ONCE at build time and skip the
snapshot re-sanitize (same fix as PR #31 for the gateway).

### `BinStatsState.payload` is moderately expensive at high bin counts.

Default 100–200-bin histograms cost ~150–500 µs and emit at 20 Hz —
fine. 1000+ bin histograms become noticeable (40+ ms/s). Caching
the serialised payload between emits (per audit #1 from the
stream_analysis pass) would help **only at large bin counts**, and
real workloads with 5000-bin histograms are likely rare.

### `update_sample` is genuinely free.

1.8 µs per sample regardless of bin count — `np.searchsorted` +
numpy in-place updates. No optimization needed.

### Reverse-index build is fast.

127 µs at 200 panels — clearly safe to run on every panels-list
change in `App.tsx` (which was the PerfC concern).

## Recommended follow-up PRs

Ordered by impact / effort ratio:

1. **Switch the WS-broadcast path to orjson** (or msgpack) for large
   stream frames. ~5–25× faster encoding, saves 100–600 ms/s of
   CPU under heavy stream load. **L effort** (new dep, must
   audit numpy-int / NaN-encoding behaviour). **High impact when
   stream-heavy.**

2. **Push `_sanitize_json` upstream in stream_analysis trace
   outputs**, then drop the redundant walk in `_remember_latest_output`.
   Same pattern as gateway PR #31. **M effort**, audit each output
   kind's build path. Saves ~10–60 ms/s under stream load.

3. **Defer**: BinStatsState payload caching. Win is small at
   default bin counts (≤200); only worth doing if measurement
   shows a deployment with thousands of bins.

## What the bench did NOT measure

- ZMQ socket-level overhead (poll, recv, send). Pure-Python
  encoding cost dominates, but a real-stack benchmark would surface
  any per-frame socket cost we're missing.
- HDF / Influx writer batching under realistic burst patterns.
- Manager loop scheduling latency.
- Cross-process IPC latency (driver → manager → gateway).

These would require an end-to-end stack benchmark — a follow-up if
the synthetic numbers above don't fully match observed production
CPU profiles.
