# CeNTREX DAQ Protocol (ZMQ)

## Overview
Topics are published over PUB/SUB. Payloads are JSON and include `version` where noted.

## Driver RPC actions (REQ/REP)

### `capabilities`
Request:
- `{"id": 1, "action": "capabilities", "params": {}}`

Response:
- `{"id": 1, "status": "OK", "result": {"version": 1, "members": [...]}}`

Member schema (each entry in `members`):
- `name`: str
- `kind`: "method" | "property" | "attribute"
- `readable`: bool
- `settable`: bool
- `value_annotation`: str | null
- `doc`: str | null
- `params`: list | null (list of `{name, kind, required, default, annotation}`)
- `return_annotation`: str | null
- `source`: "device" | "stream"

### `get`
Request:
- `{"id": 10, "action": "get", "params": {"name": "temperature"}}`

Response:
- `{"id": 10, "status": "OK", "result": <jsonable value>}`

### `set`
Request:
- `{"id": 11, "action": "set", "params": {"name": "mode", "value": "fast"}}`

Response:
- `{"id": 11, "status": "OK", "result": null}`

### `refresh_capabilities`
Request:
- `{"id": 12, "action": "refresh_capabilities", "params": {}}`

Response:
- `{"id": 12, "status": "OK", "result": {"version": 1, "members": [...]}}`

## DeviceRouter external RPC (ROUTER)

### `command`
Request:
- `{"type": "command", "device_id": "hv", "action": "enable_output", "params": {"enabled": true}, "request_id": "optional", "caller_process_id": "optional", "source_kind": "optional", "source_id": "optional"}`

Notes:
- `source_kind` / `source_id` are forwarded into `manager.command` events for provenance.
- Built-in clients set defaults:
  - FastAPI device endpoints: `source_kind=webui`, `source_id=fastapi` (overridable with `x-ec-source-kind` / `x-ec-source-id` headers)
  - TUI device commands: `source_kind=tui`, `source_id=manager_tui`
  - Managed processes using `ManagerClient.call` for `type=command`: `source_kind=process`, `source_id=<process_id>` plus `caller_process_id=<process_id>`

Response (pass-through from driver):
- `{"ok": true, "result": ...}` or `{"ok": false, "error": ...}`

Response (interceptor blocked):
- `{"ok": false, "error": {"kind": "command_interceptor", "code": "INTERCEPTOR_REJECTED", ...}}`

### `process.rpc.advertise`
Request:
- `{"type": "process.rpc.advertise", "process_id": "sequencer", "rpc_endpoint": "tcp://127.0.0.1:7001"}`

Response:
- `{"ok": true, "result": {"process_id": "sequencer"}}`

### `process.rpc`
Request:
- `{"type": "process.rpc", "process_id": "sequencer", "request": {...}}`

Response:
- `{"ok": true, "result": ...}` or `{"ok": false, "error": {...}}`

### `process.capabilities` (process.rpc payload)
Request:
- `{"type": "process.capabilities"}`

Response:
- `{"ok": true, "result": {"version": 1, "members": [...]}}`

### `manager.event.publish`
Request:
- `{"type": "manager.event.publish", "topic": "manager.watchdog.triggered", "payload": {...}}`

Response:
- `{"ok": true, "result": {"status": "published"}}`

### `manager.log.publish`
Request:
- `{"type": "manager.log.publish", "payload": {"severity": "error", "topic": "sequencer", "message": "..."}}`

Response:
- `{"ok": true, "result": {"status": "published", "entry": {...}}}`

### `manager.log.tail`
Request:
- `{"type": "manager.log.tail", "params": {"limit": 200}}`
- Optional filters in `params`:
  - `since_t_mono`: float
  - `severity_min`: str (`debug|info|warning|error|critical`)
  - `severity`: str or list[str]
  - `source_kind`: str or list[str]
  - `device_ids`: str or list[str]
  - `process_ids`: str or list[str]
  - `source_ids`: str or list[str]
  - `topic_contains`: str
  - `text_contains`: str

Response:
- `{"ok": true, "result": {"entries": [...], "count": 200, "total_matched": 740, "limit": 200, "latest_t_mono": 123456.7}}`

### `manager.command_journal.status`
Request:
- `{"type": "manager.command_journal.status"}`

Response:
- `{"ok": true, "result": {"enabled": false, "path": ".state/instance/command_journal.sqlite3", "start_error": null}}`
- `{"ok": true, "result": {"enabled": true, "path": "...", "queue_depth": 0, "written": 42, "dropped": 0, "write_errors": 0, "...": "see status payload"}}`

Notes:
- Journaled command events skip high-volume acquisition actions with prefixes
  `stream__` and `telemetry__`.
- Journaled command events also skip `process.capabilities`.
- Process commands are journaled with `device_id="process:<process_id>"`.

### `manager.command_journal.tail`
Request:
- `{"type": "manager.command_journal.tail", "params": {"limit": 200}}`
- Optional filters in `params`:
  - `since_t_wall`: float
  - `ok`: bool
  - `device_ids`: str or list[str]
  - `actions`: str or list[str]
  - `source_kind`: str or list[str]
  - `source_ids`: str or list[str]

Response:
- `{"ok": true, "result": {"entries": [...], "count": 200, "total_matched": 740, "limit": 200, "latest_id": 1234}}`
- `{"ok": false, "error": {"code": "journal_disabled"}}`

### `manager.identity`
Request:
- `{"type": "manager.identity"}`

Response:
- `{"ok": true, "result": {"version": 1, "instance_id": "laser-lock-1", "started_ts": {"t_wall": 1700000000.0, "t_mono": 12345.6}}}`

### `device.metadata.get`
Request:
- `{"type": "device.metadata.get", "device_id": "trace1"}`

Response:
- `{"ok": true, "result": {"device_id": "trace1", "revision": 2, "base": {"device_metadata": {...}, "stream_metadata": {...}}, "overrides": {"device_metadata": {...}, "stream_metadata": {...}}, "effective": {"device_metadata": {...}, "stream_metadata": {...}}}}`

### `device.metadata.set`
Request:
- `{"type": "device.metadata.set", "device_id": "trace1", "params": {"mode": "merge", "device_metadata": {"location": "bench_b"}, "stream_metadata": {"trace": {"active_channels": ["A", "B"]}}}}`
- `{"type": "device.metadata.set", "device_id": "trace1", "params": {"mode": "replace", "stream_metadata": {"trace": {"gain": 2.0}}}}`

Notes:
- `mode`: `merge` (default) or `replace`
- runtime overrides apply only to local devices
- successful updates republish `manager.device_config`

Response:
- `{"ok": true, "result": {"changed": true, "mode": "merge", "revision": 3, "...": "same shape as device.metadata.get"}}`

### `device.metadata.clear`
Request:
- `{"type": "device.metadata.clear", "device_id": "trace1"}`
- `{"type": "device.metadata.clear", "device_id": "trace1", "params": {"scope": "stream"}}`

Notes:
- `scope`: `all` (default), `device`, or `stream`

Response:
- `{"ok": true, "result": {"changed": true, "scope": "stream", "revision": 4, "...": "same shape as device.metadata.get"}}`

### `manager.cleanup_orphans`
Request:
- `{"type": "manager.cleanup_orphans"}`
- `{"type": "manager.cleanup_orphans", "params": {"dry_run": true, "stale_only": true, "timeout_s": 2.0}}`

Response:
- `{"ok": true, "result": {"instance_id": "laser-lock-1", "dry_run": true, "stale_only": true, "matched": 2, "terminated": [], "failed": [], "skipped_live_parent": [1234], "candidates": [4567]}}`

### `command_interceptor.register`
Request:
- `{"type": "command_interceptor.register", "process_id": "interlock", "routes": [{"device_id": "hv", "action": "enable_output"}], "replace": true}`

Response:
- `{"ok": true, "result": {"routes": [...]}}`

### `command_interceptor.list`
Request:
- `{"type": "command_interceptor.list"}`

Response:
- `{"ok": true, "result": {"routes": [...]}}`

## Router → interceptor RPC (process.rpc payload)

### `command_interceptor.check`
Request:
- `{"type": "command_interceptor.check", "command": {"device_id": "hv", "action": "enable_output", "params": {"enabled": true}}, "meta": {"request_id": "optional", "caller_process_id": "optional", "t_mono": 12345.67}}`

Response (allow):
- `{"ok": true, "allow": true}`

Response (allow + transform):
- `{"ok": true, "allow": true, "command": {"device_id": "hv", "action": "set_voltage", "params": {"voltage": 5000.0}}, "interceptor_id": "hv_safety", "rule": "clamp_hv_setpoint_max", "note": "transformed"}`

Response (reject):
- `{"ok": true, "allow": false, "interceptor_id": "hv_safety", "rule": "block_hv_enable_pressure", "error": {"code": "CONDITION_FAILED", "message": "...", "details": {...}}}`

## Interlock process RPC (process.rpc payload)

### `interlock.list`
Request:
- `{"type": "interlock.list"}`

Response:
- `{"ok": true, "result": {"interceptors": [...]}}`

### `interlock.status`
Request:
- `{"type": "interlock.status"}`

Response:
- `{"ok": true, "result": {"interceptors": [{"interceptor_id": "hv_safety", "enabled": true, "rule_count": 2, "enabled_rule_count": 2, "rules": [{"rule_id": "r0", "name": "...", "enabled": true, "match": {"device_id": "hv", "action": "enable_output"}, "telemetry": [...], "on_block": {"code": "...", "message": "..."}, "has_allow_transform": false}]}]}}`

### `interlock.load`
Request (from path):
- `{"type": "interlock.load", "params": {"path": "path/to/rules.yaml", "replace": true, "enable": true}}`

Request (from text):
- `{"type": "interlock.load", "params": {"text": "...yaml...", "replace": true, "enable": true, "source": "optional-label"}}`

Response:
- `{"ok": true, "result": {"interceptor_id": "hv_safety", "enabled": true}}`

### `interlock.enable` / `interlock.disable`
Request:
- `{"type": "interlock.enable", "params": {"interceptor_id": "hv_safety"}}`
- `{"type": "interlock.disable", "params": {"interceptor_id": "hv_safety"}}`

Response:
- `{"ok": true, "result": {"interceptor_id": "hv_safety", "enabled": true}}`

### `interlock.enable_rule` / `interlock.disable_rule`
Request:
- `{"type": "interlock.enable_rule", "params": {"interceptor_id": "hv_safety", "rule_id": "r0"}}`
- `{"type": "interlock.disable_rule", "params": {"interceptor_id": "hv_safety", "rule_id": "r0"}}`

Response:
- `{"ok": true, "result": {"interceptor_id": "hv_safety", "rule_id": "r0", "enabled": true}}`

### `interlock.enable_all` / `interlock.disable_all`
Request:
- `{"type": "interlock.enable_all"}`
- `{"type": "interlock.disable_all"}`

Response:
- `{"ok": true, "result": {"enabled": true, "count": 2}}`

## Frequency-power follower process RPC (process.rpc payload)

### `follower.rules`
Request:
- `{"type": "follower.rules"}`

Response:
- `{"ok": true, "result": {"rules": [{"rule_id": "r0", "name": "power_setter_laser_0", "enabled": true, "device_id": "SynthHD", "trigger_action": "set_frequency_channel_0", "trigger_param": "freq_hz", "min_freq_hz": 1.0, "max_freq_hz": 2.0, "csv_path": "...", "effects": [{"device_id": "SynthHD", "action": "set_power_channel_0", "param": "power_dbm"}]}]}}`

### `follower.enable_rule` / `follower.disable_rule`
Request:
- `{"type": "follower.enable_rule", "params": {"rule_id": "r0"}}`
- `{"type": "follower.disable_rule", "params": {"rule_id": "r0"}}`

Response:
- `{"ok": true, "result": {"rule_id": "r0", "enabled": true}}`

## Watchdog process RPC (process.rpc payload)

### `watchdog.status`
Request:
- `{"type": "watchdog.status"}`

Response:
- `{"ok": true, "result": {"watchdogs": [...]}}`

### `watchdog.clear_latch`
Request:
- `{"type": "watchdog.clear_latch", "params": {"rule": "vacuum_high_hv_off", "watchdog_id": "vacuum_watchdog"}}`
- `{"type": "watchdog.clear_latch", "params": {"all": true}}`

Response:
- `{"ok": true, "result": {"cleared": [...]}}`

### `watchdog.enable` / `watchdog.disable`
Request:
- `{"type": "watchdog.enable", "params": {"watchdog_id": "vacuum_watchdog"}}`
- `{"type": "watchdog.disable", "params": {"watchdog_id": "vacuum_watchdog"}}`

Response:
- `{"ok": true, "result": {"watchdog_id": "vacuum_watchdog", "enabled": true}}`

### `watchdog.enable_all` / `watchdog.disable_all`
Request:
- `{"type": "watchdog.enable_all"}`
- `{"type": "watchdog.disable_all"}`

Response:
- `{"ok": true, "result": {"enabled": true, "count": 2}}`

## Influx writer process RPC (process.rpc payload)

For writer data model/config details, see `docs/influx_writer.md`.

### `influx.status`
Request:
- `{"type": "influx.status"}`

Response:
- `{"ok": true, "result": {"enabled": true, "instance_id": "laser-lock-1", "queue_depth": 0, "counters": {"points_written": 1234, "write_errors": 0}}}`

### `influx.enable` / `influx.disable`
Request:
- `{"type": "influx.enable"}`
- `{"type": "influx.disable"}`

Response:
- `{"ok": true, "result": {"enabled": true}}`

### `influx.flush`
Request:
- `{"type": "influx.flush"}`

Response:
- `{"ok": true, "result": {"queue_depth": 0}}`

### `influx.devices.get`
Request:
- `{"type": "influx.devices.get"}`

Response:
- `{"ok": true, "result": {"disabled_devices": ["device_a"]}}`

### `influx.devices.enable` / `influx.devices.disable`
Request:
- `{"type": "influx.devices.enable", "params": {"device_id": "device_a"}}`
- `{"type": "influx.devices.disable", "params": {"device_ids": ["device_a", "device_b"]}}`

Response:
- `{"ok": true, "result": {"disabled_devices": []}}`

## Sequencer process RPC (process.rpc payload)

### `sequencer.validate`
Request (from path):
- `{"type": "sequencer.validate", "params": {"path": "path/to/sequence.yaml"}}`

Request (from text):
- `{"type": "sequencer.validate", "params": {"text": "...yaml..."}}`

Response:
- `{"ok": true, "result": {"valid": true, "diagnostics": []}}`
- `{"ok": true, "result": {"valid": false, "diagnostics": [{"severity": "error", "message": "...", "line": 12, "column": 5, "source": "yaml"}]}}`
- `{"ok": true, "result": {"valid": true, "diagnostics": [{"severity": "warning", "message": "steps[0].if.condition.and: 'and' has only one clause; consider removing the wrapper", "line": null, "column": null, "source": "sequencer.condition"}]}}`

### `sequencer.load`
Request:
- `{"type": "sequencer.load", "params": {"path": "path/to/sequence.yaml"}}`
- `{"type": "sequencer.load", "params": {"text": "...yaml..."}}`

Response:
- `{"ok": true, "result": {"status": "loaded"}}`
- `{"ok": false, "error": {"code": "invalid_sequence", "message": "...", "diagnostics": [...]}}`

### `sequencer.start` / `sequencer.pause` / `sequencer.resume` / `sequencer.stop`
Request:
- `{"type": "sequencer.start"}`
- `{"type": "sequencer.start", "params": {"sequence_id": "main", "repeat_count": 5, "continuous": false, "vars_override": {"port": 3}, "adaptive": {"scan_1": {"mode": "warm_start"}}}}`
- `{"type": "sequencer.pause"}`
- `{"type": "sequencer.resume"}`
- `{"type": "sequencer.stop"}`

Response:
- `{"ok": true, "result": {"status": "running" | "pause_requested" | "stop_requested"}}`

### `sequencer.library.list`
Request:
- `{"type": "sequencer.library.list"}`

Response:
- `{"ok": true, "result": {"configured": true, "manifest_path": "sequences/library.yaml", "description_policy": "warn", "active_sequence_id": "main", "entry_count": 3, "warnings": [], "last_error": null, "entries": [{"id": "main", "path": "sequences/main.yaml", "source": "explicit", "label": "Main", "description": "...", "tags": [], "editable_vars": [], "vars": ["port"], "use_ids": ["fragments.wait"]}]}}`

### `sequencer.library.reload`
Request:
- `{"type": "sequencer.library.reload"}`

Response:
- `{"ok": true, "result": {...same shape as sequencer.library.list...}}`
- `{"ok": false, "error": {"code": "library_not_configured" | "library_reload_failed", "message": "..."}}`

### `sequencer.library.load`
Request:
- `{"type": "sequencer.library.load", "params": {"sequence_id": "main"}}`

Response:
- `{"ok": true, "result": {"status": "loaded", "active_sequence_id": "main"}}`
- `{"ok": false, "error": {"code": "missing_sequence_id" | "unknown_sequence_id" | "load_failed", "message": "..."}}`

### `sequencer.status`
Request:
- `{"type": "sequencer.status"}`

Response:
- `{"ok": true, "result": {"run_id": 7, "state": "...", "current_step": "...", "loop_mode": "once" | "repeat" | "continuous", "loops_completed": 2, "loops_target": 5 | null, "vars": {...}, "vars_override": {...}, "env": {...}, "error": null, "loaded": true, "active_sequence_id": "main" | null, "loaded_source": "...", "loaded_source_kind": "rpc" | "library" | "autoload_path" | null, "context_columns": {...} | null, "sequence_library_configured": true | false, "sequence_library_path": "..." | null, "sequence_library_error": "..." | null, "sequence_library_warnings": [...], "progress": {"run_id": 7, "elapsed_s": 12.3, "completed_steps": 42, "total_steps": 100 | null, "percent": 42.0 | null, "eta_s": 16.8 | null, "step_ewma_s": 0.4 | null, "current_step_elapsed_s": 0.2 | null, "loop_mode": "repeat", "loops_completed": 2, "loops_target": 5}}}`

### `sequencer.loaded_yaml`
Request:
- `{"type": "sequencer.loaded_yaml"}`

Response:
- `{"ok": true, "result": {"loaded": true, "source": "sequences/main.yaml", "source_kind": "library", "active_sequence_id": "main", "text": "...yaml..."}}`

## HDF writer process RPC (process.rpc payload)

### `hdf.status`
Request:
- `{"type": "hdf.status"}`

Response:
- `{"ok": true, "result": {"file": "path/to/file.h5" | null, "pending": 0, "dropped": 0, "dropped_events": 0, "event_log_mode": "all" | "failures_only" | "none", "disabled_devices": [...], "known_devices": [...], "enabled_known_devices": [...], "measurement_id": "uuid" | null, "measurement_type": "frequency_scan" | null, "measurement_schema_version": 1 | null, "measurement_started_wall_ns": 1730000000000000000 | null, "measurement_ended_wall_ns": 1730000010000000000 | null, "measurement_notes_rows": 0, "measurement_schema_configured": true | false, "measurement_schema_available": true | false, "measurement_schema_path": "path/to/measurement.yaml" | null, "measurement_schema_error": "..." | null, "sequencer_event_rows": 0, "sequencer_yaml_snapshots": 0}}`

### `hdf.devices.get`
Request:
- `{"type": "hdf.devices.get"}`

Response:
- `{"ok": true, "result": {"disabled_devices": [...], "known_devices": [...], "enabled_known_devices": [...]}}`

### `hdf.devices.disable`
Request:
- `{"type": "hdf.devices.disable", "params": {"device_ids": ["dev1", "dev2"]}}`

Response:
- `{"ok": true, "result": {"changed": [...], "unknown": [...], "disabled_devices": [...], "known_devices": [...], "enabled_known_devices": [...]}}`
- `unknown` contains IDs that are not currently known to the writer's device map.

### `hdf.devices.enable`
Request:
- `{"type": "hdf.devices.enable", "params": {"device_ids": ["dev1"]}}`

Response:
- `{"ok": true, "result": {"changed": [...], "unknown": [...], "disabled_devices": [...], "known_devices": [...], "enabled_known_devices": [...]}}`

### `hdf.rotate`
Request:
- `{"type": "hdf.rotate", "params": {"filename": "next_run.h5", "disabled_devices": ["dev2"], "measurement_profile": "frequency_scan", "measurement_values": {"measurement_name": "scan-A", "seed1_power_dbm": -5.2}}}`
- `filename`, `disabled_devices`, `measurement_profile`, and `measurement_values` are optional.
- If `measurement_schema_path` is configured and successfully loaded in the HDF writer, `measurement_profile` is required.

Response:
- `{"ok": true, "result": {"old_file": "path/to/old.h5", "new_file": "path/to/new.h5", "measurement_id": "uuid", "measurement_type": "frequency_scan", "unknown": [...], "disabled_devices": [...], "known_devices": [...], "enabled_known_devices": [...]}}`

### `hdf.measurement.schema.get`
Request:
- `{"type": "hdf.measurement.schema.get"}`

Response:
- `{"ok": true, "result": {"schema": {"version": 1, "profiles": [...], "notes": {"fields": [...]}}, "path": "path/to/measurement.yaml"}}`
- `{"ok": false, "error": {"code": "measurement_schema_not_configured"}}`
- `{"ok": false, "error": {"code": "measurement_schema_unavailable", "message": "..."}}`

### `hdf.measurement.note`
Request:
- `{"type": "hdf.measurement.note", "params": {"author": "alice", "kind": "note", "message": "beam looked stable", "...custom fields...": "..."}}`

Response:
- `{"ok": true, "result": {"index": 0, "t_wall": 1730000000.0, "t_mono": 112233.44, "author": "alice", "kind": "note"}}`
- Note payload is written to `/measurement/notes` with `author`, `kind`, `message`, and extra fields in `payload_json`.

### `process.stop` (common process RPC)
Request:
- `{"type": "process.stop"}`

Response:
- `{"ok": true, "result": {"status": "stopping"}}`

## Driver -> Manager topics

### `{device_id}/heartbeat`
- Producer: driver
- Consumers: manager, TUI
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `driver_pid`: int
  - `seq`: int
  - `ts`: {`t_wall`: float, `t_mono`: float}
  - `driver_state`: str
  - `device_reachable`: bool
  - `device_state`: str
  - `last_ok_wall`: float | null
  - `last_ok_mono`: float | null
  - `last_error`: str | null
  - `loop_lag_s`: float | null

### `{device_id}/telemetry`
- Producer: driver
- Consumers: manager, TUI
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `seq`: int
  - `ts`: {`t_wall`: float, `t_mono`: float}
  - `signals`: dict of signal → {`value`, `units`, `quality`, `ts`}

### `{device_id}/chunk_ready`
- Producer: driver
- Consumers: manager
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `stream`: str
  - `descriptor`: {
    - `device_id`: str
    - `stream`: str
    - `shm_name`: str
    - `layout_version`: int
    - `seq`: int
    - `t0_mono_ns`: int
    - `t0_wall_ns`: int
  }

## Manager → external topics

### `manager.telemetry_update`
- Producer: manager
- Consumers: TUI, HDF writer
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `seq`: int
  - `ts`: {`t_wall`, `t_mono`}
  - `signals`: dict of signal → {`value`, `units`, `quality`, `ts`}

### `manager.heartbeat`
- Producer: manager
- Consumers: TUI
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `pid`: int
  - `seq`: int
  - `driver_state`: str
  - `device_state`: str
  - `device_reachable`: bool
  - `device_health`: str | null
  - `last_error`: str | null
  - `last_ok_wall`: float | null
  - `last_ok_mono`: float | null
  - `loop_lag_s`: float | null
  - `ts`: {`t_wall`, `t_mono`}

### `manager.chunk_ready`
- Producer: manager
- Consumers: HDF writer, TUI
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `stream`: str
  - `shm_name`: str
  - `layout_version`: int
  - `seq`: int
  - `t0_mono_ns`: int
  - `t0_wall_ns`: int

### `manager.device_config`
- Producer: manager
- Consumers: HDF writer, influx_writer (optional device metadata extraction)
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `yaml_text`: str | null
  - `metadata_revision`: int
  - `device_metadata`: dict
  - `stream_metadata`: dict
  - `telemetry_calls`: list
  - `stream_calls`: list
  - `run_meta_calls`: list

`stream_calls` entries (per device):
- `method`: str
- `kwargs`: dict
- `outputs`: list of:
  - `stream`: str
  - `dtype`: str
  - `shape`: list[int] (required, per-shot)
  - `units`: str | null
  - `description`: str | null
  - `ring_slots`: int
  - `attrs`: dict | null (free-form metadata, e.g. channel names). These are
    applied to the HDF stream dataset attributes (unless overridden by
    `stream_metadata` for the same stream).

Runtime metadata hooks (HDF writer behavior at measurement/file start):
- For local devices, HDF writer may call driver commands `device_metadata` and
  `stream_metadata` and merge the returned dicts into config metadata.
- These are optional driver methods; missing methods are ignored.
- Merge precedence:
  - `device_metadata`: YAML payload then runtime hook result
  - stream attrs: `stream_calls[].outputs[].attrs` then YAML `stream_metadata`
    then runtime `stream_metadata` hook result

Influx writer (wide mode) also consumes `manager.device_config`:
- Uses `device_metadata[device_type_key]` (default key: `device_type`) to resolve
  measurement names by device type.
- Uses selected device metadata keys (for example `location`) as tags.
- Skips federated mirrors (`source_kind=federated` / `is_remote=true`) so only
  owning instances write their own telemetry.

### `manager.run_metadata`
- Producer: manager
- Consumers: HDF writer
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `run_metadata`: dict

### `manager.telemetry_stale`
- Producer: manager
- Consumers: optional
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `signals`: list[str]
  - `age_s`: float
  - `ts`: {`t_wall`, `t_mono`}

### `manager.command`
- Producer: manager
- Consumers: HDF writer, logs
- Payload (version 1):
  - `version`: int
  - `device_id`: str
  - `action`: str
  - `params_json`: str
  - `ok`: bool
  - `status`: str | null
  - `error`: str | dict | null
  - `result_json`: str
  - `request_id`: any | null (optional)
  - `caller_process_id`: str | null (optional)
  - `source_kind`: str
  - `source_id`: str | null
  - `is_remote_target`: bool
  - `ts`: {`t_wall`, `t_mono`}

### `manager.log`
- Producer: manager
- Consumers: TUI, web UI, HDF writer
- Payload (version 1):
  - `version`: int
  - `severity`: `debug|info|warning|error|critical`
  - `topic`: str
  - `source_kind`: `manager|driver|process`
  - `source_id`: str | null
  - `device_id`: str | null
  - `process_id`: str | null
  - `stream`: `event|stdout|stderr`
  - `message`: str
  - `payload_json`: str
  - `ts`: {`t_wall`, `t_mono`}

### `sequencer.lifecycle`
- Producer: sequencer (via manager event bus)
- Consumers: TUI/logs
- Payload (version 1):
  - `process_id`: str
  - `event`: str
  - `ok`: bool
  - `source`: str
  - `message`: str
  - `payload`: dict (optional)
  - `ts`: {`t_wall`, `t_mono`}

### `sequencer.progress`
- Producer: sequencer (via manager event bus)
- Consumers: optional (dashboards/automation)
- Payload (version 1):
  - `process_id`: str
  - `run_id`: int
  - `state`: str
  - `current_step`: str | null
  - `loop_mode`: `once|repeat|continuous`
  - `loops_completed`: int
  - `loops_target`: int | null
  - `progress`: dict (same shape as `sequencer.status.result.progress`)
  - `ts`: {`t_wall`, `t_mono`}

### `manager.command_interceptor.routes_updated`
- Producer: manager
- Consumers: TUI/logs
- Payload (version 1):
  - `process_id`: str
  - `routes`: list
  - `replace`: bool
  - `ts`: {`t_wall`, `t_mono`}

### `manager.command_interceptor.error`
- Producer: manager
- Consumers: TUI/logs
- Payload:
  - `error`: dict (outer error with `code` = `INTERCEPTOR_*`)
  - `command`: dict

### `manager.command_interceptor.modified`
- Producer: manager
- Consumers: TUI/logs
- Payload:
  - `process_id`: str
  - `interceptor_id`: str | null
  - `rule`: str | null
  - `note`: str | null
  - `before`: dict
  - `after`: dict

### `manager.watchdog.rules_loaded`
- Producer: watchdog (via manager event bus)
- Consumers: TUI/logs
- Payload:
  - `process_id`: str
  - `watchdog_ids`: list[str]
  - `rules`: list

### `manager.watchdog.triggered`
- Producer: watchdog (via manager event bus)
- Consumers: TUI/logs
- Payload:
  - `process_id`, `watchdog_id`, `rule`
  - `severity`, `message`
  - `alarm`: true
  - `unknown`: bool
  - `snapshot`: dict
  - `timing`: dict

### `manager.watchdog.action_sent`
- Producer: watchdog (via manager event bus)
- Consumers: TUI/logs
- Payload:
  - `process_id`, `watchdog_id`, `rule`
  - `command`: dict
  - `attempt`, `retries`

### `manager.watchdog.action_failed`
- Producer: watchdog (via manager event bus)
- Consumers: TUI/logs
- Payload:
  - `process_id`, `watchdog_id`, `rule`
  - `command`: dict
  - `attempt`, `retries`
  - `error`: any

### `manager.watchdog.cleared`
- Producer: watchdog (via manager event bus)
- Consumers: TUI/logs
- Payload:
  - `process_id`, `watchdog_id`, `rule`
  - `previous_latched`: bool

### `manager.process.*` / `manager.driver.*`
- Producer: manager
- Consumers: TUI
- Payload (version 1):
  - `version`: int
  - `process_id` or `device_id`
  - `state`, `pid`, `exit_code`, `error`, `ts`

### `manager.process.rpc_update`
- Producer: manager
- Consumers: device_router / TUI
- Payload:
  - `process_id`
  - `rpc_endpoint`
  - `ts`

### `manager.state_machine.transition`
- Producer: state-machine processes (via manager event bus)
- Consumers: TUI/logs
- Payload:
  - `process_id`
  - `from_state`, `to_state`
  - `reason`
  - `metadata` (optional)
  - `ts`

## Versioning
- New fields are additive and backward compatible.
- Increment `version` only for wire format changes.
