# Manager startup (stack runner)

This project includes a small stack runner that starts a manager, loads device/process specs from YAML, and optionally auto-starts them.

## Quick start

Run with a stack YAML file:

```bash
python -m experiment_control.cli.run_stack path\to\stack.yaml
```

If you installed the CLI entrypoint, you can also use:

```bash
experiment-control-stack path\to\stack.yaml
```

Paths inside the stack YAML are resolved relative to the stack file location.

## FastAPI Gateway Wiring

FastAPI reads manager endpoints from environment variables:

- `EXPERIMENT_CONTROL_ROUTER_RPC` (defaults to `tcp://127.0.0.1:6000`)
- `EXPERIMENT_CONTROL_MANAGER_PUB` (defaults to `tcp://127.0.0.1:6001`)
- `EXPERIMENT_CONTROL_ROUTER_RPC_HINT` (optional remote/public hint endpoint)
- `EXPERIMENT_CONTROL_MANAGER_PUB_HINT` (optional remote/public hint endpoint)
- `EXPERIMENT_CONTROL_RPC_TIMEOUT_MS` (defaults to `2000`)

To serve the React UI from FastAPI:

- `EXPERIMENT_CONTROL_SERVE_UI=1`
- `EXPERIMENT_CONTROL_UI_DIST=<path to web/react_ui/dist>` (optional; defaults to repo `web/react_ui/dist`)

For the Linien example, use the helper that reads endpoints from `stack.yaml`:

```bash
python examples/linien_cli/run_linien_fastapi.py --stack examples/linien_cli/stack.yaml --host 0.0.0.0 --port 8000
```

Run stack/TUI and FastAPI in separate terminals if you want both UIs/log streams visible at once.

## Federation Design

For multi-instance manager federation (mirroring selected remote devices into a local instance), see:

- `docs/federation.md`

## Composite Device Design

For combining multiple physical devices into one logical telemetry surface (for example, fiber switch + wavemeter), see:

- `docs/composite_devices.md`

## Stack YAML schema (version 1)

```yaml
version: 1
instance_id: your-instance-id

manager:
  bind_host: 127.0.0.1                # external listener host
  advertise_host: null                # optional remote/public host hint
  external:
    rpc_port: 6000                    # device_router front door
    pub_port: 6001                    # manager PUB (telemetry/log/stream)
  internal_ports:
    registry: 5555
    rpc: 6002                         # manager internal RPC
    heartbeat_base: 6100              # managed-process heartbeat port base
  heartbeat_timeout_s: 3.0
  telemetry_stale_s: 10.0
  device_rpc_timeout_ms: 1500
  interceptor_rpc_timeout_ms: 500
  auto_connect_on_register: true

devices:
  dirs: [devices]
  files: []
  glob: "*.yaml"

processes:
  dirs: [processes]
  files: []
  glob: "*.yaml"

startup:
  start_devices: true
  start_processes: true
  process_order: [hdf_writer]
  wait_processes_running: true
  connect: null
  wait_for_registered: true
  wait_for_online: true
  timeout_s: 10.0
  poll_ms: 50

tui:
  enabled: false
  rpc_timeout_ms: 1500
  snapshot_period_s: 2.0
  startup_delay_s: 1.0
```

Notes:
- `instance_id` is required. It is the runtime identity used by manager/processes and surfaced in FastAPI/UI.
- `devices.dirs` and `processes.dirs` are searched with `glob` (default `*.yaml`).
- `files` can list explicit YAML paths in addition to `dirs`.
- `process_order` is optional. If omitted, `hdf_writer` is started first if present.
- If `tui.enabled: true`, the stack runner starts a manager subprocess and runs the TUI in the same terminal.
- When the TUI exits, the runner sends `manager.shutdown` and then terminates the manager subprocess.
- Startup timeouts (registration / process running / online) are logged as warnings; the stack continues.
- The `device_router` process is started automatically by the manager and is not listed under `processes:`.
- `external.rpc_port` is the **device_router** front door; `external.pub_port` is manager PUB.
- The manager binds its **internal** RPC on `internal_ports.rpc` and the router forwards to it.
- `bind_host` controls external bind/listen host and can be wildcard (`0.0.0.0`, `*`, `[::]`).
- Local clients launched by `run_stack` (TUI + managed processes) always use loopback connect
  addresses (`tcp://127.0.0.1:<port>`) derived from the external ports.
- Remote/public endpoint hints use `advertise_host` if set, otherwise:
  non-wildcard `bind_host`, otherwise first non-loopback host IP.
- Legacy keys are still accepted:
  `external_rpc_bind`, `external_pub_bind`, `registry_bind`, `internal_rpc_bind`,
  `process_hb_bind_base`, `external_rpc_connect_local`, `external_pub_connect_local`.

## Device config (example)

```yaml
version: 1
device_id: dummy1

driver:
  module: experiment_control.drivers.dummy_driver
  class_name: DummyDriver
  # or:
  # file: src/experiment_control/drivers/dummy_driver.py

init_kwargs:
  port: 12345

device_metadata:
  device_type: hipace700
  location: rack_a

stream_metadata:
  trace:
    counts_to_volt: 3.05e-4
    adc_gain_db: 20.0

telemetry_calls: []     # empty list disables defaults
stream_calls: []        # empty list disables defaults

telemetry_period_s: 1.0
heartbeat_period_s: 1.0
command_poll_period_s: 0.01
```

### Stream call schema (example)
```yaml
stream_calls:
  - method: traces
    outputs:
      - stream: trace
        dtype: float64
        shape: [3, 8192]
        units: V
        attrs:
          channel_names: ["error", "monitor", "control"]
          axis_names: ["channel", "sample"]
```

Notes:
- `shape` is required and describes a single shot.
- `attrs` is free-form metadata (useful for channel/axis labels) and is applied
  to the HDF stream dataset attributes. If `stream_metadata` also defines the
  same stream, its keys override `attrs`.
- `device_metadata` is device-level metadata (for example device type, location,
  and tags for sink processes such as Influx).
- `stream_metadata` is stream-level metadata (for example calibration factors).
- Optional driver runtime hooks (called when a new HDF measurement/file starts):
  - `device_metadata()` -> `dict[str, object]`
  - `stream_metadata()` -> `dict[str, dict[str, object]]`
  - these are invoked by HDF writer on local devices only (not federated mirrors)
  - merge precedence:
    - `device_metadata`: YAML `device_metadata` then runtime `device_metadata()`
    - stream attrs: `stream_calls.outputs[].attrs` then YAML `stream_metadata`
      then runtime `stream_metadata()`

Example driver hooks:

```python
def device_metadata(self) -> dict[str, object]:
    return {"device_type": "hipace700", "location": "rack_a"}

def stream_metadata(self) -> dict[str, dict[str, object]]:
    return {
        "trace": {
            "adc_gain_db": 20.0,
            "counts_to_volt": 3.05e-4,
        }
    }
```

## Process config (example)

```yaml
version: 1
process_id: hdf_writer

process:
  module: experiment_control.processes.hdf_writer
  class_name: HdfWriter
  # or:
  # file: src/experiment_control/processes/hdf_writer.py

init_kwargs:
  out_dir: data
  filename: null
  timezone: America/Chicago
  rpc_timeout_ms: 2000
  rcvhwm: 10000
  write_every_s: 1.0
  buffer_max_messages: 200000
  flush_every_n: 200
  flush_every_s: 2.0
  disabled_devices: []   # optional: device_ids to skip writing (default write-all)
  event_log_mode: all    # all | failures_only | none
  measurement_schema_path: examples/dummy_frequency_trace_sequencer/measurement.yaml  # optional

heartbeat_period_s: 1.0
heartbeat_timeout_s: 3.0
shutdown_timeout_s: 3.0
restart_policy: NEVER
restart_backoff_s: 0.5
max_restarts: null
```

Notes:
- `process.file` and `process.module` are mutually exclusive.
- The manager injects `process_id`, `manager_rpc` (router front door), `manager_pub`, and `heartbeat_endpoint` automatically.
- `heartbeat_period_s` is passed to the process runner if you supply it.
- `disabled_devices` is the startup filter only; you can adjust it at runtime via
  HDF process RPC (`hdf.devices.get`, `hdf.devices.enable`, `hdf.devices.disable`,
  `hdf.rotate`).
- `event_log_mode` controls writes to `/events/data`:
  `all` writes all manager command/log rows, `failures_only` keeps failed commands
  plus warning/error/critical logs, and `none` disables event row writes.
- `measurement_schema_path` enables schema-driven measurement metadata and notes:
  - `hdf.rotate` can take `measurement_profile` + `measurement_values`.
  - `hdf.measurement.schema.get` exposes schema to clients.
  - `hdf.measurement.note` appends rows to `/measurement/notes`.
  - Per-file metadata is written under `/measurement`:
    - attrs: `measurement_id`, `measurement_type`, `schema_version`, `started_wall_ns`,
      optional `ended_wall_ns`, optional `schema_source`
    - dataset `/measurement/header_json` (scalar UTF-8 string)
    - dataset `/measurement/notes` (append-only structured rows)
  - Recommended startup policy: do not auto-start `hdf_writer`; start it when you
    are ready to open a new file via `hdf.rotate` with measurement parameters.
- Sequencer lifecycle and loaded YAML snapshots are written under `/sequencer`.
- For custom workflow-style managed processes, see `docs/state_machines.md`.

## Instance lifecycle recovery (lock + orphan cleanup)

`run_stack` now performs a startup preflight:

- probes manager RPC for a live manager on the same `instance_id`
- runs stale-child orphan cleanup before starting a new manager

When you suspect a stuck/stale instance, use these tools in order:

1. Web UI: click the instance title in the header, then use `Dry-run` in `Orphan cleanup`.
2. Review `matched`/`candidates`, then use `Execute` to terminate stale child runners.
3. TUI fallback: `o` runs cleanup preview (dry-run), `O` executes cleanup after confirmation.

Lock status meanings shown in Web/TUI:

- `active`: lock is owned by the running manager process.
- `running_unlocked`: manager is reachable but no active lock is held.
- `stale`: lock file exists but its owner process is not alive.
- `missing`: no lock file exists for this instance.

Windows liveness probe note:

- On Windows, do not use `os.kill(pid, 0)` as a liveness probe.
- In this stack it can terminate the target process instead of acting as a pure probe.
- Use Win32 process-query APIs (`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess`) for PID liveness checks.
- This applies to instance-lock status and orphan-cleanup stale-parent checks.

## Common issues

- **Heartbeat bind failed: Address in use**
  A previous process may still be running and holding the port. Find and stop it:

  ```powershell
  $ports = 5500,6000,6001,6200

  Get-NetTCPConnection -State Listen |
    Where-Object { $ports -contains $_.LocalPort } |
    Select-Object LocalAddress, LocalPort, OwningProcess
  ```

  If you already know the ports and want to force-kill the owning processes:

  ```powershell
  $ports = 6000,6101,6102,6103

  foreach ($port in $ports) {
      $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
      
      if ($conns) {
          $processIds = $conns | Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique
          
          foreach ($procId in $processIds) {
              if ($procId -and $procId -ne 0) {
                  Write-Host "Killing PID $procId (port $port)"
                  Stop-Process -Id $procId -Force
              }
          }
      }
  }
  ```
