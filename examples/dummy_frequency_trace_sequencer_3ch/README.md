# Dummy Frequency + 3-Channel Resonance Trace Example

This example provides:

- `freq1`: scalar telemetry device with frequency setpoint plus noisy readback.
- `trace1`: stream device that returns `int16` traces with shape `[3, 5000]`
  (`[channel, time]`), each channel having a small fixed offset and a
  Gaussian-in-time pulse whose amplitude depends on set frequency.
- `sequencer` process for frequency sweeps.
- `hdf_writer` process for logging telemetry/streams.
- schema-driven measurement metadata and notes via
  `examples/dummy_frequency_trace_sequencer_3ch/measurement.yaml`.
- `stream_analysis` process for DAG outputs with file-backed workspace persistence.

## Run

From repo root:

```bash
python -m experiment_control.cli.run_stack examples/dummy_frequency_trace_sequencer_3ch/stack.yaml
```

Or use the helper script:

```bash
python examples/dummy_frequency_trace_sequencer_3ch/run_dummy_frequency_trace_3ch_stack.py
```

This uses stack-configured TUI startup (`tui.enabled: true`), so the terminal UI opens automatically.
The stack helper also auto-loads `sequence_frequency_sweep.yaml` into the sequencer process.
The sequencer process itself is also configured with `autoload_path`, so loading does not depend on helper-script timing.
`stream_analysis` autoloads DAG workspaces from:
`examples/dummy_frequency_trace_sequencer_3ch/stream_workspaces.yaml`.
`hdf_writer` loads measurement schema from:
`examples/dummy_frequency_trace_sequencer_3ch/measurement.yaml`.
Because `stack.yaml` sets `startup.start_processes: false`, the stack itself does not
auto-start processes. The helper script starts `sequencer`, `stream_analysis`, and
`hdf_writer` for you.
`hdf_writer` starts in idle mode (no file open) when schema is configured; run
`hdf.rotate` from the HDF modal and fill in `measurement_profile` + schema fields to
begin writing.

## Run FastAPI gateway

Start the stack first, then run:

```bash
python examples/dummy_frequency_trace_sequencer_3ch/run_dummy_frequency_trace_3ch_fastapi.py
```

In the UI, add a `Stream trace` or `Stream waterfall` panel and switch channel
index to compare channel offsets. Add a `Stream scalar` panel to plot
derived scalar outputs from `stream_analysis`.

## Measurement schema + notes

- Schema file:
  `examples/dummy_frequency_trace_sequencer_3ch/measurement.yaml`
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

`examples/dummy_frequency_trace_sequencer_3ch/sequence_frequency_sweep.yaml`

Defaults:

- 30 frequency steps
- 1 MHz step spacing
- `n_traces_per_step = 20`

You can adjust these in the `vars` section of the sequence file.
