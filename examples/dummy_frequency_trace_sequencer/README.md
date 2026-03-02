# Dummy Frequency + Resonance Trace Example

This example provides:

- `freq1`: scalar telemetry device with frequency setpoint plus noisy readback.
- `trace1`: stream device that returns 5000-point `int16` traces with a Gaussian-in-time pulse whose amplitude depends on set frequency.
- `sequencer` process for frequency sweeps.
- `hdf_writer` process for logging telemetry/streams.
- schema-driven measurement metadata and notes via
  `examples/dummy_frequency_trace_sequencer/measurement.yaml`.
- `stream_analysis` process for DAG outputs with file-backed workspace persistence.

## Run

From repo root:

```bash
python -m experiment_control.cli.run_stack examples/dummy_frequency_trace_sequencer/stack.yaml
```

Or use the helper script:

```bash
python examples/dummy_frequency_trace_sequencer/run_dummy_frequency_trace_stack.py
```

This uses stack-configured TUI startup (`tui.enabled: true`), so the terminal UI opens automatically.
The stack helper also auto-loads `sequence_frequency_sweep.yaml` into the sequencer process.
The sequencer process itself is also configured with `autoload_path`, so loading does not depend on helper-script timing.
`stream_analysis` autoloads DAG workspaces from:
`examples/dummy_frequency_trace_sequencer/stream_workspaces.yaml`.
`hdf_writer` loads measurement schema from:
`examples/dummy_frequency_trace_sequencer/measurement.yaml`.
Because `stack.yaml` sets `startup.start_processes: false`, the stack itself does not
auto-start processes. The helper script starts `sequencer`, `stream_analysis`, and
`hdf_writer` for you.
`hdf_writer` starts in idle mode (no file open) when schema is configured; run
`hdf.rotate` from the HDF modal and fill in `measurement_profile` + schema fields to
begin writing.

## Run FastAPI gateway

Start the stack first, then run:

```bash
python examples/dummy_frequency_trace_sequencer/run_dummy_frequency_trace_fastapi.py
```

In the UI, add a `Stream scalar` panel to configure stream/channels and plot
derived scalar outputs from `stream_analysis`.

## Measurement schema + notes

- Schema file:
  `examples/dummy_frequency_trace_sequencer/measurement.yaml`
- Supported field types:
  - `string`
  - `number`
  - `integer`
  - `boolean`
- Optional per-field controls:
  - `required`
  - `default`
  - `options`
  - `allow_custom`
  - `placeholder`
  - `description`
  - `multiline` (UI hint)
- Dot-keys are supported (for example `scan.start_hz`) and are written as nested
  objects in `/measurement/header_json`.
- Notes are appended with `hdf.measurement.note` into `/measurement/notes`.
  Core fields are `author`, `kind`, `message`; extra fields go into `payload_json`.

## Sweep sequence

Load and run:

`examples/dummy_frequency_trace_sequencer/sequence_frequency_sweep.yaml`

Defaults:

- 30 frequency steps
- 1 MHz step spacing
- `n_traces_per_step = 20`

You can adjust these in the `vars` section of the sequence file.

## Adaptive sequence

Load and run:

`examples/dummy_frequency_trace_sequencer/sequence_frequency_adaptive.yaml`

This uses:

- the `adaptive.adaptive_grid_1d` sequencer step
- an explicit adaptive study id: `resonance_scan`
- curvature loss with `min_loss: 0.2` as the adaptive convergence signal
- the existing `workspace-1 / integral` scalar output from `stream_analysis`
- `context_id` correlation so each adaptive trial waits for the matching
  `manager.stream_analysis.output`
- one adaptive score per suggested scan point computed from exactly
  `n_traces_per_point` acquisitions at that point

Notes:

- this requires the optional Python package `adaptive` to be installed in the same
  environment as the sequencer process
- the `stream_analysis` process must be running with
  `examples/dummy_frequency_trace_sequencer/stream_workspaces.yaml` loaded
- `workspace-1` is the workspace id defined in
  `examples/dummy_frequency_trace_sequencer/stream_workspaces.yaml`; in that
  workspace, `integral` is the published scalar output used as the adaptive
  objective
- the example fit node in that workspace now sets `dense_eval_points: 200`, so if
  you enable the published `fit_curve` output as a fit overlay in a stream
  bin-stats panel, the fitted curve renders smoothly instead of only at the sparse
  source x points
- the Sequencer modal now shows per-study `Reset / Resume / Warm start` controls
  before you press Start, so you can reuse or clear saved adaptive trials without
  editing the YAML
