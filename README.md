## experiment-control

Experiment control framework with:

- a manager process (device/process lifecycle + routing)
- device drivers and managed processes (for example HDF writer, sequencer, interlocks)
- a terminal UI (TUI)
- a FastAPI gateway and React web UI

This README focuses on the current recommended workflow used in `examples/linien_cli`.

## Examples

All examples are YAML-driven stacks (`stack.yaml` + `devices/` + `processes/`),
the same shape used by deployed instances.

- `examples/dummy_stack` - minimal, no-hardware starting point: two dummy devices,
  an HDF writer, and a command interlock. Includes a TUI launcher
  (`run_dummy_stack.py`) and a FastAPI/web-UI gateway
  (`run_dummy_stack_fastapi.py`). Best place to start.
- `examples/dummy_stream_cli` - adds a stream (trace) device; includes a
  `verify_stream_chunks.py` SHM smoke test.
- `examples/dummy_frequency_trace_sequencer` - sequencer + stream analysis +
  adaptive scans + measurement schema.
- `examples/linien_cli` - real Linien/SynthHD devices (used in the quick start
  below).
- `examples/federation_dummy` - multi-stack federation (hub mirrors a leaf device).
- `examples/dummy` - the same dummy setup wired up imperatively in Python
  (`DeviceSpec`/`Manager`), kept as a programmatic-API reference.

## Current Code Structure

Recent refactors split the large manager/router/process paths into focused
modules under private subpackages (`_manager/`, `_driver/`).

- Manager orchestration remains in `src/experiment_control/manager.py`.
- Manager routing and handlers live under `src/experiment_control/_manager/`:
  - `request_routing.py`
  - `internal_rpc.py`
  - `route_handlers.py`
  - `device_routing.py`
- Process supervision/recovery/logging:
  - `_manager/process_supervision.py`
  - `_manager/process_recovery.py`
  - `_manager/process_logs.py`
- Command interceptor route state: `_manager/interceptor_routes.py`
- Driver PUB ingest/caches: `_manager/driver_pub.py`
- Driver subprocess class loading: `_driver/loading.py`
- Router worker/backpressure logic: `processes/device_router.py`

## Runtime Safety Bounds

Process heartbeat supervision performs a queued-heartbeat refresh before marking a process stale, so transient manager-loop backlog is not blamed on child processes.

If heartbeat starvation continues under very high telemetry load, consider moving process heartbeat ingest to a dedicated lightweight receiver thread/task that only updates process heartbeat timestamps.

The stack now has explicit memory/backpressure bounds in hot paths:

- `device_router` uses bounded worker queues, bounded reply queue, and inflight caps with
  fail-fast `router_busy` responses on saturation.
- Manager telemetry/chunk descriptor caches are bounded by configurable device/signal/stream limits.
- FastAPI stream hub bounds retained stream keys by count and TTL, and exposes stream-hub stats.

See `docs/manager_start.md` for all manager/stack knobs and `docs/protocol.md` for runtime
stats endpoints (`manager.info.identity`, `router.stats`, `/api/settings`).

## Requirements

- Python `>=3.13`
- Node.js `>=18` (for React UI development/build)
- A virtual environment tool (`uv` recommended, `venv` + `pip` also works)

## Install

### Option A: `uv` (recommended)

```powershell
uv sync
```

Run commands with `uv run ...`, or activate `.venv` first.

Convenience wrappers (from repo root):

```powershell
./scripts/sync.ps1       # uv sync
./scripts/sync_ui.ps1    # npm ci for web/react_ui
./scripts/sync_all.ps1   # uv sync + npm ci
./scripts/build_packaged_ui.ps1   # build UI and copy to src/experiment_control/_ui_dist
```

### Option B: `venv` + `pip`

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

Install web dependencies:

```powershell
npm --prefix web/react_ui install
```

## Quick Start (Linien Example)

### 1. Start manager + devices/processes (+ TUI)

From repo root:

```powershell
python examples/linien_cli/run_linien_cli.py
```

This uses `examples/linien_cli/stack.yaml` and starts the stack through `experiment_control.cli.run_stack`.
By default in that stack file, `tui.enabled: true`, so you get the TUI in this terminal.

### 2. Start FastAPI gateway (and serve built web UI)

In a second terminal:

```powershell
python examples/linien_cli/run_linien_fastapi.py --stack examples/linien_cli/stack.yaml --host 0.0.0.0 --port 8000
```

Then open:

- `http://127.0.0.1:8000` (web UI, if built and UI serving enabled)
- `http://127.0.0.1:8000/api/health` (health endpoint)

Notes:

- `run_linien_fastapi.py` reads manager endpoints from `stack.yaml`.
- It always uses local loopback connects and also exports remote/public endpoint hints.

## Web UI Development

If you want hot-reload development instead of FastAPI-served static files:

1. Start stack (`run_linien_cli.py`)
2. Start FastAPI API only:

```powershell
python examples/linien_cli/run_linien_fastapi.py --stack examples/linien_cli/stack.yaml --no-ui --host 127.0.0.1 --port 8000
```

3. Start Vite dev server:

```powershell
$env:VITE_API_BASE="http://127.0.0.1:8000"
npm --prefix web/react_ui run dev
```

Open `http://127.0.0.1:5173`.

## Build Bundled UI (for package/wheel)

```powershell
./scripts/build_packaged_ui.ps1
```

This builds `web/react_ui/dist` and copies it into `src/experiment_control/_ui_dist`
so installed packages can serve UI without a repo checkout.

## Built-in vs Custom UI

FastAPI serves UI only when `EXPERIMENT_CONTROL_SERVE_UI=1`.

- Built-in packaged UI (default): do not set `EXPERIMENT_CONTROL_UI_DIST`.
- Custom UI build: set `EXPERIMENT_CONTROL_UI_DIST` to your own dist folder.
- Extra instance UIs: set `EXPERIMENT_CONTROL_EXTRA_UI_JSON` to a JSON list of
  `{ "slug": "...", "label": "...", "dist": "..." }` entries. Each `dist`
  must contain `index.html` and is served at `/instance-ui/{slug}/`.

Examples:

```powershell
# Built-in packaged UI
$env:EXPERIMENT_CONTROL_SERVE_UI = "1"
Remove-Item Env:EXPERIMENT_CONTROL_UI_DIST -ErrorAction SilentlyContinue

# Custom UI dist
$env:EXPERIMENT_CONTROL_SERVE_UI = "1"
$env:EXPERIMENT_CONTROL_UI_DIST = "C:\path\to\my-ui\dist"

# Extra instance UI alongside the default UI
$env:EXPERIMENT_CONTROL_SERVE_UI = "1"
$env:EXPERIMENT_CONTROL_EXTRA_UI_JSON = '[{"slug":"rc-microwave-control","label":"RC Microwave Control","dist":"C:\path\to\rc-ui\dist"}]'
```

`GET /api/ui/extra` returns the configured extra UI links. The default React UI
shows those links in its header when any are configured.

## UI Profiles (Web UI)

The web UI can export/import a profile from the **Settings** modal.

- `Export UI profile` saves a JSON file to disk.
- `Import UI profile` loads a previously saved JSON file.

Current profile contents:

- layout (`navWidth`, device order, telemetry collapse state)
- plot panel configuration (panels/traces/active panel/time windows)
- pinned commands

Log filter settings are intentionally not included.

## Running Directly With `run_stack`

You can run any stack YAML directly:

```powershell
python -m experiment_control.cli.run_stack examples/linien_cli/stack.yaml
```

or with script entrypoint:

```powershell
experiment-control-stack examples/linien_cli/stack.yaml
```

See `docs/manager_start.md` for full stack schema and config details.

## Python Script Control (direct ZMQ)

For automation scripts, you can control the stack directly without FastAPI:

```python
from experiment_control.client import StackClient

with StackClient.from_stack_yaml("examples/dummy_frequency_trace_sequencer/stack.yaml") as ec:
    ec.processes.start("sequencer")
    ec.wait.process_rpc_ready("sequencer", probe_action="sequencer.status", timeout_s=10.0)
    ec.sequencer.load(path="examples/dummy_frequency_trace_sequencer/sequence_frequency_sweep.yaml")
    ec.sequencer.start()
```

See `docs/python_client_sdk.md` for full API details (device commands, HDF rotate,
sequencer helpers, and PUB/SUB subscriptions).

## Ports and Networking

In `stack.yaml`, manager networking is configured under `manager`:

- `bind_host` (external listener host, for example `127.0.0.1`, `0.0.0.0`, or LAN IP)
- `advertise_host` (optional host for remote/public endpoint hints)
- `external.rpc_port` and `external.pub_port`
- `internal_ports.registry`, `internal_ports.rpc`, `internal_ports.heartbeat_base`, `internal_ports.event_base`

Common pattern:

- Use `bind_host: 0.0.0.0` for LAN reachability.
- Local TUI/processes/FastAPI connects stay on loopback and are derived automatically.
- Set `advertise_host` to control what remote clients should use (for example `laser-lock-1.local`).

Legacy endpoint keys remain supported for compatibility:
`external_rpc_bind`, `external_pub_bind`, `registry_bind`, `internal_rpc_bind`,
`process_hb_bind_base`, and `external_*_connect_local`.

For FastAPI, the manager endpoints are taken from env:

- `EXPERIMENT_CONTROL_ROUTER_RPC`
- `EXPERIMENT_CONTROL_MANAGER_PUB`
- `EXPERIMENT_CONTROL_ROUTER_RPC_HINT` (optional remote/public hint)
- `EXPERIMENT_CONTROL_MANAGER_PUB_HINT` (optional remote/public hint)
- `EXPERIMENT_CONTROL_RPC_TIMEOUT_MS` (router RPC timeout)
- `EXPERIMENT_CONTROL_RPC_QUEUE_MAX` (gateway request queue bound)
- `EXPERIMENT_CONTROL_STREAM_MAX_PAYLOAD_POINTS` (per-frame truncation cap)
- `EXPERIMENT_CONTROL_STREAM_MAX_KEYS` (stream key retention cap)
- `EXPERIMENT_CONTROL_STREAM_KEY_TTL_S` (stream key idle eviction TTL)

The helper script (`run_linien_fastapi.py`) sets these automatically from stack config.

## Useful Docs

- `docs/manager_start.md` - stack runner and YAML schema
- `docs/protocol.md` - canonical RPC/PUB-SUB protocol (`manager.*` / `manager.processes.*` namespaces)
- `docs/python_client_sdk.md` - Python SDK for direct stack control
- `docs/sequencer_config.md` - sequencer configuration
- `docs/state_machines.md` - state machine process base/template

## Troubleshooting

- If stack startup fails with `Address already in use`, another process is holding one of the configured ports.
- If FastAPI shows healthy but UI is blank:
  - ensure stack is running
  - ensure FastAPI points to correct manager endpoints
  - if using Vite dev server, set `VITE_API_BASE` to FastAPI origin
- If using wildcard binds (`0.0.0.0`) in stack, do not use those literal values as connect endpoints; use loopback/LAN host for clients.
