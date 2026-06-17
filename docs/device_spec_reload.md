# Device spec reload on restart

This note describes a manager-side design for propagating device YAML changes when a device is restarted, without requiring a full stack restart.

## Problem

Today, a device driver restart does not reload the device YAML spec.

Current behavior:

- Device YAML is parsed once into `DeviceSpec` by `device_spec_from_yaml(...)`.
- The parsed spec is stored in `DeviceHandle.spec`.
- `restart_driver(...)` stops the driver and schedules a restart.
- `start_driver(...)` rebuilds the child process command from the cached `handle.spec`.
- Manager telemetry schema is also generated from the cached `handle.spec.telemetry_calls`.
- `manager.device_config` payloads are generated from the cached `handle.spec`.

This creates a stale-state problem:

- Driver code changes can propagate if the child process restarts.
- YAML-defined telemetry/config changes do not propagate on plain device restart.
- Network clients can keep seeing the old telemetry schema.
- `hdf_writer` and `influx_writer` can keep using stale `manager.device_config` payloads.

## Evidence in current code

Relevant paths:

- `src/experiment_control/manager.py`
  - `DeviceSpec` stores YAML-derived fields and `config_yaml_text`
  - `DeviceHandle.spec` caches the parsed spec
  - `_telemetry_schema_list()` builds schema from `handle.spec.telemetry_calls`
- `src/experiment_control/_manager/process_supervision.py`
  - `restart_driver(...)` only disconnects/stops/schedules restart
  - `start_driver(...)` builds child args from `handle.spec`
  - `build_driver_cmd(...)` serializes telemetry calls from `spec.telemetry_calls`
- `src/experiment_control/_manager/runtime_metadata.py`
  - `device_config_payload(...)` uses `handle.spec`
  - `publish_device_config(...)` publishes `manager.device_config`
- `src/experiment_control/processes/hdf_writer.py`
  - caches latest `manager.device_config`
- `src/experiment_control/processes/influx_writer.py`
  - consumes `manager.device_config`

## Goal

Make YAML-defined device changes propagate when restarting a device, while keeping YAML as the source of truth.

## Recommendation

Implement an explicit manager-side device spec reload path, and optionally integrate it into device restart.

## Proposed design

### 1. Store the original device config path in `DeviceSpec`

Add a field to `DeviceSpec`:

- `config_path: str | Path | None`

Populate it in `device_spec_from_yaml(...)`.

Reason:

- Reloading requires a canonical way to find the original YAML file.
- `config_yaml_text` alone is not enough because it does not preserve the source path.

### 2. Add a manager method to reload a device spec

Add manager-side method and routing entry, for example:

- `device.spec.reload`

Behavior:

1. Resolve `device_id`
2. Look up `handle`
3. Read `handle.spec.config_path`
4. Parse fresh YAML with `device_spec_from_yaml(...)`
5. Validate the new spec matches the same `device_id`
6. Atomically replace `handle.spec` only if parsing/validation succeeds
7. Force device config republish
8. Publish a manager event announcing reload

Suggested result payload:

```json
{
  "ok": true,
  "result": {
    "device_id": "...",
    "reloaded": true,
    "config_path": "..."
  }
}
```

Suggested failure payload:

```json
{
  "ok": false,
  "error": {
    "code": "spec_reload_failed",
    "message": "..."
  }
}
```

### 3. Force republish `manager.device_config`

After successful spec reload:

- call `_publish_device_config(handle)` unconditionally
- do not rely on `handle.config_published`

Reason:

- downstream consumers cache config payloads
- HDF writer and Influx writer need a new `manager.device_config` event to refresh

### 4. Keep runtime metadata overrides intact

Do not clear:

- `_runtime_device_metadata_overrides`
- `_runtime_stream_metadata_overrides`
- `_runtime_metadata_revision`

Reason:

- runtime metadata overrides are separate from base YAML
- reloading the base spec should not silently discard runtime-applied metadata

Expected behavior:

- base YAML changes replace the base spec
- runtime metadata overlay still applies on top of the new base spec

### 5. Integrate reload into restart flow

Recommended restart sequence:

1. `device.spec.reload`
2. if reload succeeds, do current `restart_driver(...)`
3. if reload fails, abort restart and keep old spec/driver state unchanged

Two possible API shapes:

#### Option A: extend existing restart request

Add an optional flag:

- `device.driver.restart` with `reload_spec: true`

Recommended default:

- `reload_spec: true`

#### Option B: separate explicit RPC

Require callers to do:

1. `device.spec.reload`
2. `device.driver.restart`

Recommendation:

- implement both
- keep `device.spec.reload` as a direct tool
- make `device.driver.restart` optionally call it with `reload_spec: true`

### 6. Publish an explicit event for clients

Publish a manager event such as:

- `manager.device_spec_reloaded`

Suggested payload:

```json
{
  "device_id": "...",
  "config_path": "...",
  "ts": {"t_wall": ..., "t_mono": ...}
}
```

Reason:

- UI/network clients can use this as a hint to refresh telemetry schema and config views

## Why not derive telemetry schema from driver registration?

Do not make runtime driver registration authoritative for telemetry schema.

Reasons:

- YAML remains the audit-friendly source of truth
- HDF and Influx metadata already depend on manager-side config payloads
- a driver-runtime-derived schema would be harder to reason about and could drift from config
- explicit spec reload is a smaller, cleaner change

## Potential issues and edge cases

### 1. Reload failure must be atomic

Do not partially mutate `handle.spec` before the new spec is fully parsed and validated.

Safe approach:

- parse into a temporary `new_spec`
- only assign `handle.spec = new_spec` on success

### 2. Device ID mismatch

If the YAML file now contains a different `device_id`, reject reload.

Reason:

- `DeviceHandle` identity is already established in manager state
- silently swapping logical device identity would be dangerous

### 3. Running-driver mismatch window

If spec is reloaded without immediate restart, manager config/schema may reflect the new YAML while the driver process is still running the old child args.

Recommendation:

- for operator-facing workflows, use reload together with restart
- standalone `device.spec.reload` can still be allowed, but should be documented as a config-only operation unless followed by restart

### 4. Telemetry schema changes can affect clients

Changing signals may break cached UI panels or sequence/preflight assumptions.

Mitigation:

- publish `manager.device_spec_reloaded`
- republish `manager.device_config`
- clients should re-fetch `manager.telemetry.schema.list` and `device.config.get`

### 5. Downstream process refresh

`hdf_writer` and `influx_writer` already listen for `manager.device_config`.

As long as reload forces config republish, they should refresh automatically.

### 6. Recovery / auto-restart paths

Current watchdog-style restart paths (`maybe_restart_device_driver(...)`) restart from cached spec.

Decision needed:

- whether automatic crash restarts should also reload YAML

Recommendation:

- no automatic YAML reload on crashloop/auto-restart at first
- keep spec reload explicit or only tied to user-requested restart
- this avoids surprising behavior if a file is mid-edit or temporarily invalid

## Suggested implementation steps

1. Extend `DeviceSpec` with `config_path`
2. Populate `config_path` in `device_spec_from_yaml(...)`
3. Add manager helper:
   - `_reload_device_spec(device_id: str) -> Json`
4. Add route:
   - `device.spec.reload`
5. Force `_publish_device_config(handle)` after successful reload
6. Publish `manager.device_spec_reloaded`
7. Extend `device.driver.restart` with `reload_spec` option
8. Make user-requested restart call spec reload first
9. Add tests

## Test plan

### Unit tests

1. **spec reload success**
   - load device
   - edit YAML telemetry outputs
   - call `device.spec.reload`
   - assert `handle.spec.telemetry_calls` updated
   - assert `device.config.get` payload updated
   - assert telemetry schema list updated

2. **spec reload failure**
   - break YAML
   - call reload
   - assert error response
   - assert old spec unchanged

3. **restart with reload**
   - edit YAML
   - call restart with reload
   - assert `build_driver_cmd(...)` uses updated telemetry/init args

4. **config republish**
   - assert `manager.device_config` is emitted on reload even when `config_published` was already true

5. **runtime metadata preservation**
   - apply runtime device/stream metadata override
   - reload spec
   - assert override still appears in effective config payload

### Integration tests

1. restart a device after YAML telemetry signal change
2. verify `manager.telemetry.schema.list` reflects new signals
3. verify HDF/Influx-side config consumer receives updated `manager.device_config`

## Recommended default behavior

For user-initiated restart:

- restart should reload spec by default

For automatic restart after crash:

- keep current behavior initially (no implicit spec reload)

This gives the safest operational model:

- editing YAML + restarting device does what operators expect
- crash recovery remains stable and predictable

## Summary

The current architecture has no blocker for this feature.

The cleanest implementation is:

- keep YAML as source of truth
- add explicit manager-side spec reload
- republish config after reload
- optionally fold reload into user-requested device restart

This should solve stale telemetry schema/config propagation for:

- manager RPC clients
- UI consumers
- HDF writer
- Influx writer

without requiring a full stack restart.
