# Web UI CPU performance

This document is the measurement log for the browser CPU overhaul. Workloads come
from `examples/performance_test` and traverse the production driver → manager →
FastAPI → WebSocket → React path.

## Instrumentation

Open the production UI with `?perf=1`. Instrumentation is disabled otherwise and
its hot-path calls return immediately. In the browser console:

```js
window.__EC_PERF__.reset()
window.__EC_PERF__.table()
window.__EC_PERF__.snapshot()
```

`reset()` starts a measurement interval. `table()` prints message rates, React
render counts, invalidation/plot/canvas counts, API request counts, and measured
conversion timing. No individual telemetry or stream message is logged.

## Protocol

Use the exact production-build procedure in
`examples/performance_test/README.md`. Warm up for 60 seconds, reset counters, and
record a 60-second Chrome Performance trace. Keep the preset, imported profile,
viewport, Chrome version, and machine power state fixed for before/after runs.

## Measurements

Baseline measurements below were captured from the pinned instrumentation commit
`e51ae89` on Windows with Chrome 152, a 1278×1303 CSS-pixel viewport at DPR 1,
and 16 reported logical CPUs. Each row followed a 60-second warm-up. Trace
durations are slightly longer than 60 seconds because the measurement commands
themselves run on the profiled main thread.

"Browser CPU" is renderer `ProcessTime / elapsed wall time`, so it may exceed
100% when renderer threads use more than one core. Main-thread, scripting,
rendering, painting, and GC percentages come from Chrome Performance metrics and
the lossless trace event stream. No estimated values are included.

| Revision | Preset / profile | Browser CPU | Main-thread busy | Scripting | Rendering | Painting | GC | Notes |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `e51ae89` | baseline / baseline | 110.8% | 72.0% | 67.3% | 0.32% | 0.89% | 5.06% | 70.63 s; 16 panels |
| `e51ae89` | baseline / telemetry-heavy | 105.0% | 68.4% | 65.0% | 0.26% | 0.32% | 4.93% | 69.74 s; 12 telemetry panels |
| `e51ae89` | baseline / smoothing-heavy | 105.2% | 68.5% | 65.2% | 0.23% | 0.21% | 4.86% | 68.53 s; 6 smoothed panels |
| `e51ae89` | heavy / mixed-heavy | 158.9% | 91.5% | 87.9% | 0.42% | 0.39% | 9.12% | 70.18 s; 12 mixed panels |
| `e51ae89` | baseline / many-panels | 112.2% | 73.1% | 69.7% | 0.27% | 0.28% | 5.14% | 68.40 s; 24 panels |

## Baseline instrumentation snapshots

| Preset / profile | telemetry msg/s | signal values/s | raw frames/s | analysis msg/s | App renders | PanelsGrid renders | DeviceCard renders/device | PanelCard renders/panel | uPlot `setData` | Canvas redraws |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline / baseline | 75.56 | 1471.56 | 55.83 | 57.79 | 1995 | 1995 | 1995 | 69 | 897 + 207 raw + 69 bin | 69 waterfall + 69 bin2d |
| baseline / telemetry-heavy | 75.64 | 1473.28 | 0 | 0 | 1990 | 1990 | 1990 | 69 | 828 | 0 |
| baseline / smoothing-heavy | 75.56 | 1471.47 | 0 | 0 | 1956 | 1956 | 1956 | 67 | 402 | 0 |
| heavy / mixed-heavy | 112.35 | 2206.99 | 102.35 | 66.87 | 1803 | 1803 | 1803 | 69 | 552 + 207 raw + 69 bin | 138 waterfall + 69 bin2d |
| baseline / many-panels | 75.50 | 1470.15 | 0 | 0 | 1945 | 1945 | 1945 | 67 | 1608 | 0 |

The smoothing-heavy interval spent 380.1 ms in instrumented telemetry data
construction (402 calls). The valid mixed-heavy interval spent 76.3 ms in
telemetry data construction and 165.5 ms in waterfall conversion. The first
mixed-heavy import attempt retained the prior many-panels profile; its trace was
discarded before these results were recorded.

Lossless traces (not committed because they total hundreds of megabytes) were
saved in the pinned temporary worktree:

```text
C:\Users\ogras\AppData\Local\Temp\ec-perf-baseline-e51ae89\baseline-baseline-trace.json
C:\Users\ogras\AppData\Local\Temp\ec-perf-baseline-e51ae89\baseline-telemetry-heavy-trace.json
C:\Users\ogras\AppData\Local\Temp\ec-perf-baseline-e51ae89\baseline-smoothing-heavy-trace.json
C:\Users\ogras\AppData\Local\Temp\ec-perf-baseline-e51ae89\baseline-many-panels-trace.json
C:\Users\ogras\AppData\Local\Temp\ec-perf-baseline-e51ae89\heavy-mixed-heavy-trace.json
```

## Panel invalidation and routing checkpoint

Commit `a1f19b2` replaces the global plot revision with targeted per-panel
invalidation, adds the raw-stream reverse index, and suppresses hidden/offscreen
redraws. These measurements use the same Chrome build, viewport, presets,
profiles, warm-up, and trace protocol as the pinned baseline.

| Revision | Preset / profile | Browser CPU | Main-thread busy | Scripting | Rendering | Painting | GC | Browser CPU change |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `e51ae89` | baseline / baseline | 110.8% | 72.0% | 67.3% | 0.32% | 0.89% | 5.06% | — |
| `a1f19b2` | baseline / baseline | 29.0% | 21.5% | 18.5% | 0.27% | 0.81% | 0.92% | −73.9% |
| `e51ae89` | baseline / telemetry-heavy | 105.0% | 68.4% | 65.0% | 0.26% | 0.32% | 4.93% | — |
| `a1f19b2` | baseline / telemetry-heavy | 25.1% | 18.6% | 17.1% | 0.28% | 0.24% | 0.83% | −76.1% |

Message throughput was maintained: the checkpoint baseline received 75.80
telemetry messages/s, 1475.98 signal values/s, 55.43 raw frames/s, and 57.11
analysis messages/s. Telemetry-heavy received 75.92 messages/s and 1478.40
signal values/s. App and PanelsGrid total renders fell from 1995 to 45 for the
baseline profile and from 1990 to 48 for telemetry-heavy; those remaining
renders are low-rate configuration/poll updates, not telemetry-driven state.

At the captured scroll position, six baseline plot cards and eight
telemetry-heavy cards were within the observer margin. Offscreen cards issued
zero `setData` or canvas redraws while their data continued to ingest. The
checkpoint traces are:

```text
C:\Users\ogras\AppData\Local\Temp\ec-perf-current-a1f19b2-baseline-trace.json
C:\Users\ogras\AppData\Local\Temp\ec-perf-current-a1f19b2-telemetry-heavy-trace.json
```

The architectural acceptance counters are the primary deterministic regression
checks: top-level telemetry-driven renders, unrelated device/panel renders, hidden
and offscreen redraws, exact routing fan-out, and overlapping poll attempts.

## Final current-tip checkpoint

The final telemetry-heavy checkpoint was captured from the production packaged
UI at `2293663`, after the buffer/smoothing, raw-stream, visibility/FPS, polling,
and remaining render-boundary phases. It used the same Chrome 152 build,
1278×1303 viewport, baseline preset, 12-panel `telemetry_heavy.json` profile,
and a 60.005-second steady-state interval. This checkpoint used Chrome
`Performance.getMetrics` plus the opt-in counters; painting and GC breakdowns
require a lossless Performance trace and are therefore not reported for this row.

| Revision | Preset / profile | Browser CPU | Main-thread busy | Scripting | Rendering | Painting | GC | Browser CPU change |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `e51ae89` | baseline / telemetry-heavy | 105.0% | 68.4% | 65.0% | 0.26% | 0.32% | 4.93% | — |
| `2293663` | baseline / telemetry-heavy | 21.3% | 14.6% | 13.0% | 0.26% | — | — | −79.7% |
| `e51ae89` | baseline / smoothing-heavy | 105.2% | 68.5% | 65.2% | 0.23% | 0.21% | 4.86% | — |
| `67ebdcc` | baseline / smoothing-heavy | 22.5% | 17.0% | 15.3% | 0.29% | — | — | −78.6% |
| `e51ae89` | baseline / many-panels | 112.2% | 73.1% | 69.7% | 0.27% | 0.28% | 5.14% | — |
| `67ebdcc` | baseline / many-panels | 22.5% | 17.2% | 15.5% | 0.26% | — | — | −80.0% |

The final interval received 75.99 telemetry messages/s and 1480.43 signal
values/s. It recorded 4566 messages and 88,960 signal values, 22 App/PanelsGrid
renders, and 480 `uPlot.setData` calls: exactly 60 for each of the eight visible
panels and zero for the four offscreen panels. No overlapping poll-attempt
counter was recorded. Device telemetry bodies updated at device rate while the
outer DeviceCards remained on the low-rate App path.

The smoothing-heavy interval received 76.24 telemetry messages/s and 1484.91
signal values/s. Incremental smoothing used 5.3 ms total across 1440 updates
(0.0037 ms mean); telemetry data construction used 11.1 ms across 360 visible
plot updates. The many-panels interval received 75.69 telemetry messages/s and
1474.60 signal values/s. Only panels 1–6 were inside the observer margin: each
issued 61 `setData` calls, while panels 7–24 issued zero. These runs used the
same 60-second warm-up and 60-second measurement protocol.

## Final mixed-heavy trace

A lossless Chrome Performance trace was captured at `c4c32bd` using the exact
original mixed-heavy workload: Chrome 152, 1278×1303 viewport, the `heavy`
preset, `mixed_heavy.json`, a 60-second warm-up, and a 70.35-second captured
interval. Chrome reported no trace data loss.

| Revision | Preset / profile | Browser CPU | Main-thread busy | Scripting | Rendering | Painting | GC | Browser CPU change |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `e51ae89` | heavy / mixed-heavy | 158.9% | 91.5% | 87.9% | 0.42% | 0.39% | 9.12% | — |
| `c4c32bd` | heavy / mixed-heavy | 52.7% | 35.9% | 32.2% | 0.31% | 0.11% | 2.00% | −66.8% |

The optimized interval received 115.34 telemetry messages/s, 2266.97 signal
values/s, 115.12 raw-stream frames/s, and 67.50 stream-analysis messages/s. The
original interval received 112.35, 2206.99, 102.35, and 66.87 respectively, so
the CPU reduction was measured while processing slightly more input rather than
by reducing acquisition throughput.

Only the four panels inside the observer margin rendered: each issued 71
`uPlot.setData` calls, while all eight offscreen panels issued zero. Telemetry
data construction took 10.7 ms across 213 updates, smoothing took 6.8 ms across
568 trace updates, and raw-stream construction took 1.2 ms across 71 updates.
These transforms are no longer material hot spots.

The trace attributes 22.51 seconds to JavaScript function calls and 1.40 seconds
to main-thread garbage collection. All twelve visible device telemetry bodies
rendered 665 times (about 9.45 renders/s per device), even though the outer
device cards rendered only 18 times. CPU samples are concentrated in React DOM
diffing and Mantine component/style-property construction. This identifies the
device telemetry value table as the strongest measured candidate for a further
optimization phase, for example a capped presentation refresh or narrower
per-signal row updates while retaining immediate external-store ingestion.

The compressed trace is not committed and is stored at:

```text
C:\Users\ogras\AppData\Local\Temp\ec-perf-current-c4c32bd-heavy-mixed-heavy-trace.json.gz
```

## Device telemetry presentation checkpoint

The device-telemetry hotspot was addressed after the final mixed-heavy trace by
moving live value cells onto narrow per-signal subscriptions, pacing their
React-facing snapshots to at most 4 Hz, and suspending those subscriptions when
the device panel or individual cards are outside the viewport margin. The
authoritative latest-value store and plot-buffer ingestion remain immediate and
unthrottled.

The follow-up trace used the same Chrome 152 build, 1278×1303 viewport, `heavy`
preset, `mixed_heavy.json` profile, collapsed device panel, and production Vite
bundle as the preceding mixed-heavy checkpoint. Its captured interval was
71.88 seconds and Chrome reported no trace data loss.

| Revision | Preset / profile | Browser CPU | Main-thread busy | Scripting | Rendering | Painting | GC | Browser CPU change |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `c4c32bd` | heavy / mixed-heavy | 52.7% | 35.9% | 32.2% | 0.31% | 0.11% | 2.00% | — |
| `b399b41` | heavy / mixed-heavy | 13.1% | 5.45% | 3.07% | 0.08% | 0.08% | 0.16% | −75.2% |

The interval sustained 114.32 telemetry messages/s, 2246.99 signal values/s,
110.74 raw-stream frames/s, and 67.87 stream-analysis messages/s. Four visible
plot panels each completed 72 `uPlot.setData` calls; eight offscreen panels
completed none. With the device panel collapsed, active device-value
subscriptions and device-value renders were zero while telemetry continued to
ingest at full rate.

An additional live check opened the device panel with two cards inside the
observer margin. It reported exactly two source subscriptions and 40 signal-cell
subscriptions. The presentation scheduler coalesced source updates into shared
flushes, and the displayed counter advanced without changing the immediate
latest-value path. Closing the panel returned active signal subscriptions to
zero.

Relative to the original `e51ae89` heavy/mixed-heavy result of 158.9% renderer
CPU, the cumulative reduction is 91.8%. The compressed follow-up trace is:

```text
C:\Users\ogras\AppData\Local\Temp\ec-perf-device-telemetry-heavy-mixed-trace.json.gz
```
