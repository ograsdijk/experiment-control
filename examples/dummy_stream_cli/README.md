# Dummy Stream CLI Example

A YAML-driven stack with a scalar dummy device (`dummy1`) and a stream device
(`trace1`, `DummyTraceDriver`, 5x10000 `float64` traces), plus an `hdf_writer`.

## Run

From repo root:

```bash
python -m experiment_control.cli.run_stack examples/dummy_stream_cli/stack.yaml
```

Or use the helper script (launches the stack + TUI and an acquire loop):

```bash
python examples/dummy_stream_cli/run_dummy_stream_cli.py
```

## Verify stream chunk delivery

With the stack running, in another terminal:

```bash
python examples/dummy_stream_cli/verify_stream_chunks.py
```

This triggers `trace1.acquire_trace`, waits for the matching `manager.chunk_ready`
event, reads the payload back out of the shared-memory ring, and checks the
shape/dtype (exits non-zero on failure). It is the SHM-reader demonstration that
previously lived in the imperative `dummy_sequencer` example.
