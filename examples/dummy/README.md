# Dummy Example (programmatic API reference)

`dummy_devices.py` builds a stack **imperatively in Python** — constructing
`DeviceSpec` / `ProcessSpec` / `TelemetryCall` objects and driving a `Manager`
directly. It is kept as a reference for the programmatic API.

> For new stacks, prefer the declarative YAML form. See
> [`examples/dummy_stack/`](../dummy_stack/), which sets up the same two dummy
> devices plus an HDF writer and an interlock using `stack.yaml` + `devices/` +
> `processes/` + `rules/`, matching the layout of a deployed instance.

## Run

From repo root:

```bash
python examples/dummy/dummy_devices.py
```

This spawns the manager in a subprocess, registers `dummy1` and `dummy2`, starts
the `hdf_writer` managed process, and launches the terminal UI. On exit it stops
the managed process and drivers and shuts the manager down.
