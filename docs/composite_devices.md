# Composite Devices (Wavemeter + Fiber Switch)

## Purpose

Define a safe and clear implementation plan for a composite/virtual device that combines:

- Fiber switch port selection
- Wavemeter readings (wavelength and environment fields)

into one coherent telemetry surface for GUI, sequencer, HDF, and interlock usage.

## Problem

The switch and wavemeter are physically separate devices, but scientifically represent one logical measurement chain.

Critical constraint:
- Wavemeter samples are only trustworthy for a port **after** that port becomes active (plus optional settling time).

## Scope (v1)

- Keep physical devices as independent drivers (`fiber_switch`, `wavemeter`).
- Add one additional composite device driver (for example `laser_mux`).
- Composite publishes fused telemetry only.
- No stream/chunk handling.
- No replacement of underlying maintenance controls (restart/disconnect stay on physical devices).
- Composite may expose normal methods/properties like any other device.

Optional v1.1:
- Add composite command facade (`select_port`, `select_laser`) that forwards to switch commands.

---

## Architecture

1. Manager runs three devices:
- `fiber_switch` (existing)
- `wavemeter` (existing)
- `laser_mux` (new composite)

2. Composite connects to existing manager/router front doors:
- SUB to manager PUB (telemetry source), typically `tcp://127.0.0.1:6001`
- REQ/DEALER to device_router RPC, typically `tcp://127.0.0.1:6000`
- Endpoints are resolved from explicit composite `init_kwargs` overrides, or manager-injected env vars.
- Manager injects:
  - `EXPERIMENT_CONTROL_MANAGER_PUB`
  - `EXPERIMENT_CONTROL_ROUTER_RPC`
  into driver subprocess environments.

3. Composite device subscribes to `manager.telemetry_update` on manager PUB.

4. Composite caches latest source telemetry for:
- configured switch device ID
- configured wavemeter device ID

5. Composite emits one fused telemetry bundle under composite `device_id`.

6. Composite ignores its own telemetry (`device_id == laser_mux`) to avoid self-feedback loops.

---

## Telemetry Model (Recommended v1)

Primary telemetry (authoritative current state):

- `active_port: int | null`
- `active_wavelength_nm: float | null`
- `wm_temp_c: float | null` (if available)
- `wm_pressure_mbar: float | null` (if available)
- `switch_settled: bool`
- `active_quality: "OK" | "SETTLING" | "STALE" | "MISSING"`
- `active_age_s: float | null`

Optional metadata:

- `active_laser_label: str | null` (from configured port map)
- `source_ts_delta_s: float | null` (debug/diagnostics)
- `last_port_change_t_mono: float | null`
- `last_valid_wm_t_mono: float | null`

Not recommended as primary telemetry:

- 8-slot vector with mostly null/NaN values for inactive ports.

If per-port history is needed, expose separate "last-known cache" fields with explicit age/quality semantics.

---

## Trust / Pairing Rules

State tracked by composite:

- `active_port`
- `last_port_change_t_mono`
- `switch_settled`
- `valid_post_switch_count`
- latest wavemeter sample timestamp/value

Acceptance gate for wavelength:

1. On port change:
- set `last_port_change_t_mono`
- set `switch_settled = false`
- reset `valid_post_switch_count = 0`

2. Wavemeter sample is eligible only if:
- `wm_t_mono >= last_port_change_t_mono + settle_s`
- sample age `<= max_age_s`

3. Mark settled only after `min_valid_samples_after_switch` consecutive eligible samples.

4. Until settled:
- publish `active_wavelength_nm = null`
- publish `active_quality = "SETTLING"`

5. If source telemetry goes stale/missing:
- publish `active_quality = "STALE"` or `"MISSING"`
- keep value null unless explicit last-known behavior is enabled.

---

## Configuration Sketch (Draft)

```yaml
version: 1
device_id: laser_mux

driver:
  file: src/experiment_control/drivers/composite_laser_mux.py
  class_name: CompositeLaserMux

init_kwargs:
  # optional explicit overrides; usually omitted
  # manager_pub: tcp://127.0.0.1:6001
  # router_rpc: tcp://127.0.0.1:6000

  switch_device_id: fiber_switch
  wavemeter_device_id: wavemeter

  switch_port_signal: active_port
  wavelength_signal: wavelength_nm
  env_signal_map:
    wm_temp_c: temperature_c
    wm_pressure_mbar: pressure_mbar

  settle_s: 0.25
  max_age_s: 1.0
  min_valid_samples_after_switch: 2

  port_label_map:
    "1": laser_a
    "2": laser_b

  # optional command facade routing (v1.1)
  command_routes:
    select_port:
      target_device_id: fiber_switch
      target_action: set_port
      param_map:
        port: port
    select_laser:
      target_device_id: fiber_switch
      target_action: set_port
      transform: laser_label_to_port
```

Notes:
- Device YAML can omit endpoints for the common local case.
- Composite resolves endpoints in this order:
  1. Explicit constructor kwargs (`manager_pub`, `router_rpc`)
  2. Injected env vars (`EXPERIMENT_CONTROL_MANAGER_PUB`, `EXPERIMENT_CONTROL_ROUTER_RPC`)
  3. If still missing: fail fast with a clear startup error (no implicit localhost fallback)

---

## Command Redirection (Facade, Optional)

How the composite knows where to redirect commands:

1. Routing is explicit in config, not inferred.
- `switch_device_id` and `wavemeter_device_id` identify source/target devices.
- Optional `command_routes` maps each composite command to one target `device_id` + target `action`.

2. Composite receives a normal command RPC for its own `device_id`.

3. Composite resolves the route entry, transforms params if needed, then sends a normal command request to the **device_router front door** (`router_rpc`):
- `{"type":"command","device_id":"<target>","action":"<target_action>","params":{...}}`

4. Composite returns the routed reply (or normalized error) to caller.

Rules:
- Never route to itself.
- No global traffic interception; only commands addressed to the composite are forwarded.
- If a route entry is missing, return a clear `unknown_action`/validation error.
- Do not call manager internal RPC from inside device command handling; use router RPC to avoid re-entrancy deadlocks.

---

## Composite Methods

Composite driver methods can be:

- Local methods that use cached telemetry/state (for example `current_laser_label()`).
- Facade methods that route to underlying devices (for example `select_port(port)`).
- Mixed methods that route plus update local pairing state.

These methods appear in capabilities like any other device command/property.

---

## Implementation Plan

1. Add design + interface contract docs.
- Keep this file as the implementation contract.

2. Define composite networking contract.
- Add `manager_pub` / `router_rpc` init kwargs.
- Read manager/router endpoints from injected env vars when kwargs are omitted.
- Raise a clear startup error if neither kwargs nor env vars are provided.
- Keep explicit override support for non-default ports/topology.

3. Implement composite driver module.
- Add `src/experiment_control/drivers/composite_laser_mux.py`.
- Implement `connect`, `disconnect`, telemetry method(s), and capability surface.

4. Implement telemetry subscriber and source cache.
- SUB to `manager.telemetry_update`.
- Filter by source `device_id` values only.

5. Implement pairing/settling state machine.
- Enforce strict post-port-change acceptance logic.
- Expose quality and settling signals.

6. Add optional command facade and route table.
- Add `command_routes` parsing/validation.
- Implement route dispatch through `router_rpc` (device_router front door).
- Add guardrails (no self-route, missing route errors, deterministic param mapping).

7. Add composite device YAML example/config.
- Add a concrete example under your stack/device config directory.

8. Add tests.
- Unit tests for pairing/gating state transitions and stale behavior.
- Tests for rapid port changes and stale wavemeter samples.
- Tests for route dispatch and invalid-route handling.

9. Integrate in operational workflow.
- Use composite device for scientific control/monitoring.
- Keep physical devices available for maintenance/recovery actions.
- Optionally disable raw devices in HDF if only fused telemetry should be persisted.

---

## Test Matrix (Minimum)

- Port change causes settling state and blocks pre-switch wavemeter samples.
- Post-switch eligible sample is accepted only after `settle_s`.
- `min_valid_samples_after_switch` gate works.
- Stale source telemetry forces `STALE`.
- Missing port or missing wavelength forces `MISSING`.
- Self-feedback telemetry is ignored.
- Env fields propagate with correct missing/stale handling.

---

## Uncertainties / Decisions Needed

1. v1 command surface:
- Telemetry-only composite, or also include `select_port` forwarding?

2. Defaults:
- `settle_s` default value?
- `max_age_s` default value?
- `min_valid_samples_after_switch` default value?

3. Signal naming contract:
- exact switch port signal name
- exact wavemeter wavelength signal name and units (`nm` vs `Hz`)
- exact env signals to include

4. Value retention policy:
- During `SETTLING/STALE`, should `active_wavelength_nm` be null, or last-known value + status?

5. Command route contract:
- fixed schema only (`target_device_id`, `target_action`, `param_map`), or allow callable transform hooks?

6. HDF/UI usage policy:
- Keep raw `fiber_switch`/`wavemeter` visible and recorded, or composite-only in standard views?
