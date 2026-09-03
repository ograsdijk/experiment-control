# Web UI performance test instance

This instance drives the production manager, device runners, shared-memory stream
transport, FastAPI gateway, WebSockets, stream-analysis process, and React UI. It
does not inject messages into React.

## Run it

Build the production UI first (Vite development mode distorts CPU measurements):

```powershell
cd web/react_ui
npm ci
npm run build
cd ../..
```

Start the stack and gateway in separate terminals:

```powershell
uv run python examples/performance_test/run_performance_test.py --preset baseline
uv run python examples/performance_test/run_performance_test_fastapi.py `
  --preset baseline --ui-dist web/react_ui/dist
```

Open <http://127.0.0.1:8088/?perf=1>. In **Settings**, choose **Import UI
profile**, then select a JSON file from `ui_profiles/`.

The accepted presets are `light`, `baseline`, `heavy`, and `stress`. Generation is
deterministic and writes only beneath `.generated/<preset>/`; generated device and
process YAML is intentionally ignored by Git. Each preset uses a separate group of
manager ports beginning at 6400, 6420, 6440, or 6460. The gateway defaults to 8088.

## Reproducible profiling protocol

1. Use an incognito Chrome window with extensions disabled and a production build.
2. Select one preset and use the same preset for both launch commands.
3. Import the matching benchmark profile (start with `baseline.json`).
4. Wait 60 seconds for telemetry history, histogram state, and JIT warm-up.
5. Reset the `?perf=1` counters, record 60 seconds in Chrome Performance, then
   capture the instrumentation snapshot.
6. Record browser CPU, main-thread busy percentage, scripting/rendering/painting,
   GC, message rates, React renders, and plot/canvas redraws in
   `web/react_ui/PERFORMANCE.md`.

Expected input rates are listed in `performance_presets.yaml`. Verify them against
the instrumentation message counters. Also check the Devices view for every
`perf_tel_*` and `perf_trace_*` device, and verify that the raw trace and analysis
panels visibly update before accepting a run.

Use `telemetry_heavy.json` to isolate telemetry fan-out, `smoothing_heavy.json` for
SMA/EMA work, `mixed_heavy.json` for all stream-analysis panel kinds, and
`many_panels.json` when checking offscreen suppression. Profiles use the same
version-1 import/export schema as the normal Settings UI.
