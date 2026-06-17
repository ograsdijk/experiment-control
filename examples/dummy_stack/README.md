# Dummy Stack Example

A minimal, fully YAML-driven stack: two scalar dummy devices, an HDF telemetry
writer, and a command interlock. This is the canonical "copy me" starting point —
it mirrors the layout of a deployed instance (`stack.yaml` + `devices/` +
`processes/` + `rules/`).

For the equivalent setup wired up imperatively in Python (the `DeviceSpec` /
`ProcessSpec` / `Manager` API), see [`examples/dummy/`](../dummy/), which is kept
as a programmatic-API reference rather than a template.

## Contents

- `devices/dummy1.yaml`, `devices/dummy2.yaml`: `DummyDriver` scalar devices with
  `temperature` and `voltage` telemetry.
- `processes/hdf_writer.yaml`: logs telemetry to `data/`.
- `processes/interlock.yaml`: command interlock loading `rules/interlock_dummy.yaml`.
- `rules/interlock_dummy.yaml`: two interlock rules (see below).

## Run

From repo root:

```bash
python -m experiment_control.cli.run_stack examples/dummy_stack/stack.yaml
```

Or use the helper script:

```bash
python examples/dummy_stack/run_dummy_stack.py
```

`stack.yaml` sets `tui.enabled: true`, so the terminal UI starts automatically,
and `startup.start_processes: true` starts `interlock` then `hdf_writer`.

## Interlock rules

`rules/interlock_dummy.yaml` demonstrates the two rule styles:

1. **Params-only** (`limit_temperature_setpoint`): blocks `set_temperature` on any
   device when the requested value is outside `10-30 C`. The condition references
   only the command's own params (`${params.temperature}`).
2. **Telemetry-gated** (`block_dummy2_set_while_dummy1_hot`): blocks
   `dummy2.set_temperature` while `dummy1`'s live `temperature` reading is
   `>= 25 C` (or unavailable). This pulls a telemetry signal into the condition via
   `inputs.telemetry`, using the `.value` and `.ok` accessors; `defaults.max_age_s`
   rejects stale readings.

To see the interlock fire, issue a `set_temperature` command from the TUI/UI with
a value outside the bounds, or set `dummy1` above 25 C and then try to set
`dummy2`.
