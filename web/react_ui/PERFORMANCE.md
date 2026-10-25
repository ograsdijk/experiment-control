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

## Automated sweep and low-risk fixes (2026-10-24)

From here on, measurements come from the automated harness in `bench/ui/`
(see its README). It drives the production bundle in Chrome 155 over CDP
against `examples/performance_test`, with three fresh-browser repeats per row
(medians shown), a 60 s warm-up, a 60 s window, and a 1278×1303 viewport at
DPR 1. "Main-thread busy" is `TaskDuration / wall`. "4×" is Chrome CPU
throttling, used to emulate a slower acquisition PC. The harness reproduced the
`2293663` telemetry-heavy checkpoint at 21.8% renderer CPU (21.3% recorded).

### Findings

- **Telemetry plots redrew at the data rate.** A 120 s window on a ~400 px plot
  advances ~3 px/s but redrew 20–30 times/s. Each redraw also re-rendered the
  whole `PanelCard`. Telemetry panels now redraw about once per pixel of x
  advance (5–30 fps). The expanded view keeps 30 fps.
- **The plot scheduler re-armed `requestAnimationFrame` every vsync** while a
  rate-limited panel waited, which is up to 8 empty wake-ups per flush on a
  240 Hz display. It now waits on a timer until the next panel is due.
- **Each device value had a mounted Mantine `Tooltip`.** Mantine 7 mounts the
  tooltip's portal container even when it is closed, and React attaches its
  full delegated listener set to every portal container. The result was 268
  empty portals and 35k of the ~40k JS event listeners at the heavy preset
  (63k at stress), plus Floating UI work on each 4 Hz value render. The tooltip
  now mounts only while its value is hovered.
- **The heatmap colormap allocated per pixel.** The waterfall and 2-D bin
  panels built the colour-stop table and a result tuple for every pixel; this
  was the top self-time function in a CPU profile. A shared 256-entry lookup
  table replaces it, within two 8-bit levels of the exact ramp.
- **Gateway trace decimation was a per-sample Python loop.** It ran once per
  socket per frame, and a py-spy profile with four clients put it at 48% of
  gateway time. Vectorized m4/minmax give identical output (equivalence test
  against the loops) at ~7× lower cost (4096→1024 points: 508→69 µs).
- **WebSocket permessage-deflate took 38% of gateway time** at four clients,
  mostly compressing binary float64 traces. It is unchanged in code. Setting
  `UVICORN_WS_PER_MESSAGE_DEFLATE=false` halved gateway CPU (4 clients:
  99→51%) at the cost of uncompressed LAN traffic (~1.8 MB/s per client at the
  heavy preset).
- **Google Fonts was loaded as a render-blocking stylesheet.** When the
  network drops the connection (emulated 20 s connect timeout), the UI did not
  paint for 20 s. The fonts are now bundled (`@fontsource-variable/*`), and
  first paint is 130 ms with external requests hanging.
- **Profile import discarded DAG workspaces that already existed on the
  server.** It cleared the known revisions, so each `workspace.put` sent
  `expected_revision` 0, conflicted, and reloaded the server copy. It now keeps
  the known revisions.
- **No memory leaks.** After the plot buffers fill, a 30-minute soak with 10
  modal churn cycles and 6 profile swaps kept heap (~70 MB), DOM nodes,
  listeners and uPlot instances flat.

### Before / after (steady state)

| Preset / profile | Device panel | Main-thread busy | Renderer CPU | Long frames/min | JS listeners | Gateway CPU |
|---|---|---:|---:|---:|---:|---:|
| baseline / telemetry_heavy | open | 15.3 → 6.0% | 21.8 → 11.0% | 0 → 0 | 34.7k → 8.5k | 4.3 → 4.3% |
| heavy / mixed_heavy | open | 16.7 → 6.6% | 23.8 → 11.6% | 0 → 0 | 40.8k → 10.0k | 29.1 → 15.3% |
| heavy / mixed_heavy | collapsed | 23.5 → 13.9% | 32.7 → 28.4% | 0 → 0 | 40.1k → 9.1k | 25.4 → 15.7% |
| stress / mixed_heavy | collapsed | 29.1 → 14.6% | 41.2 → 30.1% | 0 → 0 | 63.5k → 10.4k | 29.7 → 18.5% |
| heavy / mixed_heavy, 4× | open | 53.9 → 27.4% | 117.8 → 110.0% | 2 → 1 | 43.1k → 15.0k | 25.5 → 14.1% |
| heavy / mixed_heavy, 4× | collapsed | 99.3 → 53.0% | 132.0 → 118.7% | 163 → 2 | 42.2k → 11.0k | 24.2 → 13.0% |

Message throughput was unchanged: telemetry, raw-frame and analysis rates match
within ±3% in every row. Gateway CPU with heavy / mixed_heavy, by client count:
1: 25 → 16%, 2: 45 → 25%, 4: 99 → 46%, 8: 100 → 97% (saturated in both).
Modal open latency was unchanged (40–55 ms to mount). The DAG modal improved
from 129 to 90 ms because there was less background load.

## Second round: render boundaries, shared gateway frames, lazy sequencer (2026-10-24)

Same harness, presets and protocol as the previous section. Values are medians:
original → after round 1 → after round 2.

### Changes

- **Plot card render boundary.** `PanelRevisionBoundary` re-renders only the
  plot body, legend and status line on a data update. The card itself (header,
  settings, trace chips, Mantine chrome) follows data only while its settings
  popover is open, because the popover shows live counters.
  `PanelCard` data-driven renders went from 30–144/s to zero.
- **Gateway frames are shared across sockets.** `_trace_frame_cache.py` keeps
  the finished wire form (decimated values, JSON metadata, binary body) of each
  raw-stream and workspace trace frame per parameter set. Keys name the source
  frame exactly: device/stream (raw) or workspace/output (DAG), plus seq and
  the acquisition timestamps, so a restarted producer cannot match an old
  entry; frames without them are not shared. Entries hold only encoded
  frames, never the hub payloads, so large digitizer records are not kept
  alive. Sockets that use rolling averages never share.
- **Adaptive float32 binary traces.** Clients send `dtype=auto`, and the
  gateway then sends float32 only when its rounding error is below 1e-6 of the
  frame's span. Offset traces, such as absolute frequencies, stay float64.
  Older clients always get float64.
- **WebSocket compression is off by default** in every gateway launcher
  (`apply_gateway_uvicorn_env_defaults`, and centrex `fastapi_helper`).
  `UVICORN_WS_PER_MESSAGE_DEFLATE=true` restores it.
- **Waterfall.** The grid is built once per redraw (it was built twice, once for
  the auto range). Coloured rows are reused while the z range and width are
  unchanged, and only new rows are coloured, with a tight per-row loop. Canvases
  are no longer reallocated on every redraw, and the ResizeObserver lives for
  the panel's lifetime. Waterfall drawing fell from ~325 ms to ~25 ms of main
  thread per 10 s at the heavy preset.
- **Lazy sequencer YAML code.** The `yaml` package and the
  outline/validation/template helpers load with the Sequencer modal
  (`useSequencerYamlTools`). Entry chunk: 725 → 598 KB, 222 → 182 KB gzip.
  `ui-bundle-budget` in `scripts/ci_check.py` now guards the entry-chunk size,
  keeps `yaml` out of it, and rejects external URLs in `index.html`.
- **DAG modal.** Its Selects no longer keep closed dropdowns mounted
  (`keepMounted: false`). Before, 18 closed dropdowns were each positioned by
  Floating UI on open.
- **`HoverTooltip`.** Per-row tooltips (device values, restart, pinned commands,
  remote-device icon) mount a Mantine Tooltip only while hovered.
- **Performance instance.** `--ui-processes` adds a sequencer, which autoloads
  an ~890-line benchmark sequence, and an idle HDF writer. That makes the
  Sequencer and HDF modals and editor typing measurable.

### Steady state (original → round 1 → round 2)

| Preset / profile | Device panel | Main-thread busy | Renderer CPU | JS listeners | Gateway CPU |
|---|---|---:|---:|---:|---:|
| baseline / telemetry_heavy | open | 15.3 → 6.0 → 4.7% | 21.8 → 11.0 → 9.3% | 34.7k → 8.5k → 7.0k | 4.3 → 4.3 → 4.7% |
| heavy / mixed_heavy | open | 16.7 → 6.6 → 6.1% | 23.8 → 11.6 → 11.9% | 40.8k → 10.0k → 7.1k | 29.1 → 15.3 → 6.6% |
| heavy / mixed_heavy | collapsed | 23.5 → 13.9 → 9.0% | 32.7 → 28.4 → 21.7% | 40.1k → 9.1k → 7.1k | 25.4 → 15.7 → 6.2% |
| stress / mixed_heavy | collapsed | 29.1 → 14.6 → 10.2% | 41.2 → 30.1 → 25.4% | 63.5k → 10.4k → 7.6k | 29.7 → 18.5 → 9.0% |
| heavy / mixed_heavy, 4× | open | 53.9 → 27.4 → 24.8% | 118 → 110 → 109% | 43.1k → 15.0k → 7.1k | 25.5 → 14.1 → 5.8% |
| heavy / mixed_heavy, 4× | collapsed | 99.3 → 53.0 → 33.5% | 132 → 119 → 113% | 42.2k → 11.0k → 7.1k | 24.2 → 13.0 → 6.6% |

Long animation frames stayed at 0/min in every unthrottled row. The 4×
collapsed row went 163 → 2 → 1 per minute. Message throughput was unchanged
(within ±5%).

Gateway CPU with heavy / mixed_heavy, by client count (original → round 2):
1: 25 → 9%, 2: 45 → 11%, 4: 99 → 17%, 8: 100 (saturated, frames dropped) →
30%. The round-2 client run overlapped an unrelated 12-worker job on the same
machine, so these numbers are if anything pessimistic.

### Interactions (heavy / mixed_heavy, warm medians)

All modals mount in 34–51 ms (40–56 ms originally). The DAG modal went from
129 ms with a 97 ms long frame to 64 ms with none. With `--ui-processes`: the
Sequencer modal takes 58 ms warm (80 ms first open, including its lazy chunks),
the HDF modal 46 ms, and typing in the 890-line sequence editor takes 20 ms
median (40 ms max) per keystroke event with no long frames.

### Load

Load timings are only comparable within one session. The round-2 load runs
overlapped the unrelated job above, which moved DOMContentLoaded from ~38 ms
to ~90 ms for every build. Under that same contention, the unchanged original
bundle (`HEAD`'s `_ui_dist`) and the round-2 bundle both measured a median FCP
of 216 ms. With external requests hanging, first paint no longer waits for the
connection timeout.
