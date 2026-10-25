# Web UI benchmark harness

Automates the manual protocol in `web/react_ui/PERFORMANCE.md`. It drives the
production React UI in a real Chrome through the DevTools protocol, against the
`examples/performance_test` stack (driver → manager → gateway → WebSocket →
React), and writes one JSON result per invocation to `bench/ui/results/`
(gitignored).

No extra dependencies: the CDP client uses `websockets`, already a runtime
dependency. `psutil` is optional and enables gateway/stack CPU sampling, so run
with `uv run --with psutil`.

## Quick start

Build the production UI first. Vite dev mode distorts CPU measurements.

```powershell
cd web/react_ui; npm ci; npm run build; cd ../..
uv run --with psutil python -m bench.ui.run --launch --preset heavy --profile mixed_heavy --scenario steady
```

`--launch` starts the stack and gateway for the preset, waits until every
device is ONLINE, and shuts both down afterwards. Without it, the harness uses
whatever gateway is already listening on `--port` (default 8088).

Each repeat (`--repeats`, default 3) uses a fresh Chrome process with a
throwaway profile. The summary holds the median, min and max of every numeric
field. The UI profile is imported through the real Settings → Import UI profile
path.

## Scenarios

| Scenario | Measures | Key options |
|---|---|---|
| `steady` | Renderer CPU (`ProcessTime`), main-thread busy, script, style/layout, heap, DOM nodes, listeners, long animation frames, `?perf=1` counters (renders, `setData`, requests/s), gateway and stack CPU | `--warmup`, `--duration`, `--device-panel open\|collapsed`, `--network` (HTTP/WS traffic per endpoint), `--trace` (gzipped Chrome trace) |
| `load` | DOMContentLoaded, FCP, LCP, first telemetry, first plot `setData`, requests and bytes, long frames during startup | `--cache cold\|warm`, `--external allow\|fail\|hang` (emulate no internet: non-gateway requests fail fast or hang for 20 s) |
| `interactions` | For each header modal: click → content mounted / settled / closed, Event Timing duration (INP-like), long frames; device-panel toggle; plot-area wheel scroll frame pacing. All under live telemetry. The first open includes lazy-chunk loading. | `--interaction-repeats` |
| `soak` | Heap / DOM nodes / listeners / uPlot instances after forced GC: before and after modal churn, before and after profile churn, then a steady trend per hour | `--duration`, `--sample-every`, `--modal-cycles`, `--profile-cycles a,b,...` |
| `clients` | Gateway and stack CPU, plus per-client renderer CPU, as concurrent clients increase (each in its own window so none is hidden) | `--clients 1,2,4,8` |

Every scenario accepts `--cpu-throttle N` (Chrome CPU slowdown, to emulate a
slower acquisition PC), `--width/--height` (default 1278×1303 at DPR 1, as in
PERFORMANCE.md) and `--headless`.

## Comparing runs

```powershell
uv run python -m bench.ui.compare before.json after.json --filter metrics.
```

The table shows medians that changed by at least `--threshold` percent (default
5%). Keep the preset, profile, viewport, throttle and machine power state fixed
between the two runs. The script warns when the metadata differs.

## Full sweep

`bash bench/ui/sweep.sh [out_dir]` runs every scenario across presets in about
two hours, one stack launch per configuration. Leave the machine idle while it
runs: Chrome windows open and close, and background load skews the CPU numbers.

## Notes

- Chrome runs with `--disable-background-timer-throttling`,
  `--disable-renderer-backgrounding` and
  `--disable-backgrounding-occluded-windows`, so an unfocused benchmark window
  is not throttled.
- `page_probe.js` is injected before the app loads. It observes Long Animation
  Frames, Event Timing and paint entries without scheduling per-frame work.
  Frame counting only runs during the scroll interaction.
- Percentages are relative to one core, so renderer CPU can exceed 100%.
- The Sequencer and HDF modals, and typing in the sequence editor
  (`sequencer_typing`), are measured only with `--launch --ui-processes`. That
  flag adds a sequencer and an idle HDF writer to the performance instance.
  Without it, those entries report `skipped`.
