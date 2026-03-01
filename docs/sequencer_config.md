# Sequencer Config YAML (v1)

This document explains how the sequencer YAML is parsed and executed by
`src/experiment_control/sequencer/sequencer.py` and `sequencer/runtime.py`.

## Quick summary
- YAML describes a sequence of steps executed sequentially.
- Variables are defined in `vars` and are visible to templates.
- Templates use `${...}` with a restricted expression evaluator.
- Control flow: `for`, `repeat`, `while`, `if`, `atomic`, `pause`, `assign`.
- `for` loops iterate over records and bind selected fields into local names.
- Timing: `sleep`, `wait_until`.
- Device I/O: `set`, `call`.
- Stream context: `set_context` (writes `context_id` + `context_fields`).

## Top-level schema
```yaml
version: 1
meta: {}            # optional
vars: {}            # optional
context_columns: {} # optional (see below)
steps: []           # required
```

### `context_columns` (optional, explicit schema)
May appear at top level or under `meta:`. Values must be one of:
`float64`, `int64`, `bool`.

```yaml
meta:
  context_columns:
    hv_v: float64
    freq_hz: float64
    shot_index: int64
```

If `context_columns` is not provided, the writer will auto-generate
columns from the first non-empty `context_fields` it sees. Only scalar
numeric and bool fields are converted, and all auto columns are stored
as float64 with missing values set to NaN.

## Templates and expressions
Use `${...}` anywhere a value is accepted.

Supported operators/functions:
- Arithmetic: `+ - * / // % **`
- Comparisons: `== != < <= > >=`
- Boolean: `and or not`
- Functions: `abs`, `len`, `min`, `max`

Allowed names:
- Variables from `vars`
- Loop variables (e.g., `hv_v`)
- Values created via `save_as`, `assign`, or `extract`
- `vars.<name>` access (attribute-style) for top-level vars

Limitations:
- No imports, no attribute access on plain dicts, no subscripting.
- Use `extract`/`assign` to pull values from device call results.

Examples:
```yaml
vars:
  f0_hz: 500e6

steps:
  - set: {device: rf, name: freq, value: ${f0_hz + 1e6}}
```

## Step types

### `call`
Call a device RPC action.
```yaml
- call:
    device: yag
    action: fire
    params: {}
  save_as: resp
  extract: {kind: scalar}
  assign:
    locked: {kind: key, ref: locked}
```

Notes:
- `save_as` stores the full response envelope in a variable.
- `extract` and `assign` are mutually exclusive.
- `extract` pulls a single value from `resp.result`.
- `assign` pulls multiple values from `resp.result`.
- Stream acquisitions use `action: stream__<method>` with optional `params: {n_batch: N}`.
  Per-shot array shape is defined in the device `stream_calls`.

Extractor kinds:
- `scalar` (use the whole result)
- `index` (list/tuple index)
- `key` (dict key)
- `attr` (attribute name)

### `set`
Calls the driver `set` action.
```yaml
- set: {device: psu, name: voltage_v, value: ${hv_v}}
```

### `assign`
Set or update one or more **runtime** sequencer variables.
```yaml
- assign: {shots: ${shots + 1}}
```

Notes:
- `assign` updates the **runtime environment** (same namespace used by
  loop variables, `save_as`, and `extract`).
- It does **not** modify the top-level `vars` block; use `vars.<name>`
  to reference those stable values explicitly.
- Each value is resolved the same way as other expressions, so it can
  include templates or structured telemetry/call lookups.

Example using telemetry:
```yaml
- assign:
    last_lock: {telemetry: {device: laser, signal: lock_error_hz}}
```

### `sleep`
Interruptible sleep in seconds.
```yaml
- sleep: 0.25
```

### `wait_until`
Poll until condition becomes true.
```yaml
- wait_until:
    timeout_s: 10
    every_s: 0.2
    sample:
      telemetry: {device: psu, signal: voltage, max_age_s: 0.6}
    reduce:
      method: mean
      window_s: 1.0
    condition:
      abs_lt: [${sample_reduced - hv_v}, 1.0]
    stable_for_s: 0.8
```

Fields:
- `timeout_s`: fail after this many seconds (0 means no timeout)
- `every_s`: polling interval
- `sample`: telemetry or call (see below)
- `reduce`: optional windowed reduction of samples
- `condition`: structured condition (see below)
- `stable_for_s`: require condition to hold for this duration

Sample sources:
- Telemetry: `telemetry: {device, signal, max_age_s?}`
- Call: `call: {device, action, params?, extract?}`

Implicit locals during wait:
- `sample`, `samples`, `sample_reduced`

This step is interruptible by pause/stop.

### `for`
Loop over a list or generator. All loop sources resolve to **records**.
`bind` selects which record fields become local variables inside the loop body.

String form:
```yaml
- for:
    bind: hv_v
    in: [0, 200, 400]
    do:
      - set: {device: psu, name: voltage_v, value: ${hv_v}}
```

The string form is shorthand for binding the record field `value`.

Mapping form:
```yaml
- for:
    bind:
      value: freq_hz
      index: freq_step_idx
    in:
      gen:
        linspace: {start: -30e6, stop: 30e6, num: 301}
    do:
      - call:
          device: freq1
          action: set_frequency_hz
          params:
            frequency_hz: ${freq_hz}
```

Notes:
- `bind` may map only the fields you need.
- Missing bound fields are an error.
- Plain lists/scalars are wrapped as records automatically.
- 1D generators emit `value`, `index`, `u`, `count`.
- `scan2d` emits `x`, `y`, `row`, `col`, `index`, `u`, `v`, `count`.

### `repeat`
Repeat a block N times.
```yaml
- repeat:
    times: 5
    do:
      - call: {device: yag, action: fire}
```

### `while`
Loop while a condition is true.
```yaml
- while:
    condition:
      lt: [${shots}, 100]
    do:
      - atomic:
          name: shot
          do:
            - call: {device: yag, action: fire}
            - call: {device: trace1, action: stream__acquire_trace}
      - assign: {shots: ${shots + 1}}
```

### `if`
Conditional execution.
```yaml
- if:
    condition: {gt: [${sample_reduced}, 1e5]}
    then:
      - pause: {reason: "Threshold exceeded"}
    else:
      - call: {device: foo, action: do_bad}
```

### `atomic`
Critical section. Pause/stop is deferred until atomic completes.
```yaml
- atomic:
    name: shot
    do:
      - call: {device: yag, action: fire}
      - call: {device: trace, action: stream__acquire_trace}
```

### `pause`
Request pause and stop the sequence at a safe point.
```yaml
- pause: {reason: "Operator check"}
```

### `parallel`
Syntax is accepted but **not supported** at runtime (v1).
```yaml
- parallel:
    do:
      - call: {device: a, action: foo}
      - call: {device: b, action: bar}
```

### `set_context`
Set stream context for subsequent stream chunks.
```yaml
- set_context:
    streams:
      - {device: cam, stream: frames}
      - "scope.trace"
    fields:
      hv_v: ${hv_v}
      freq_hz: ${freq_hz}
```

Behavior:
- `context_id` increments on each `set_context`.
- `context_id` is monotonic for the sequencer process lifetime.
- `context_id` resets when the sequencer process restarts.
- `context_fields` must be JSON-serializable scalars (float, int, bool, str).
- Drivers attach `context_id` + `context_fields` to `chunk_ready`.

## Range generators (`gen`)
```yaml
in:
  gen:
    linspace: {start: -30e6, stop: 30e6, num: 301}
    offset: 500e6
    shuffle: true
    seed: 123
    serpentine: true
```

Supported generators (exactly one):
- `range: {start, stop, step}`
- `linspace: {start, stop, num}`
- `triangle: {start, stop, num}` (forward linspace + reverse linspace; total `2*num` points)
- `logspace: {start, stop, num, base?}`
- `geomspace: {start, stop, num}`
- `values: [...]`
- `scan2d: ...`

Modifiers:
- `offset`
- `shuffle` + `seed`
- `serpentine` (alternates direction based on parent loop index)

### `scan2d`
Generates 2D scan-point records for raster-like motion.

Convenience shorthand:
```yaml
in:
  gen:
    scan2d:
      center: {x: 0.0, y: 0.0}
      width: 2.0
      height: 1.0
      steps: {x: 101, y: 51}
      pattern: serpentine
      order: row_major
```

Equivalent explicit form:
```yaml
in:
  gen:
    scan2d:
      x:
        linspace: {start: -1.0, stop: 1.0, num: 101}
      y:
        linspace: {start: -0.5, stop: 0.5, num: 51}
      pattern: serpentine
      order: row_major
```

Shorthand rules:
- `center` is required and must provide `x`/`y`.
- Use either `width` + `height`, or `size`.
- `size` may be a scalar (square scan) or `{width, height}`.
- Use exactly one of `steps` or `pitch`.
- `steps` may be a scalar or `{x, y}`.
- `pitch` may be a scalar or `{x, y}`.

Patterns:
- `serpentine` (default)
- `raster`
- `random` (requires optional `seed` for reproducibility)
- `center_out`

Orders:
- `row_major` (default)
- `col_major`

Returned fields:
- `x`, `y`
- `row`, `col`
- `index`
- `u`, `v`
- `count`

## Conditions
Structured condition operators:
- `eq`, `ne`, `gt`, `ge`, `lt`, `le`
- `and`, `or`, `not`
- `abs_lt: [x, tol]`

Example:
```yaml
condition:
  and:
    - {gt: [${sample_reduced}, 0.0]}
    - {lt: [${sample_reduced}, 10.0]}
```

## Execution behavior
- Sequential by default.
- `sleep` and `wait_until` are interruptible by pause/stop.
- Errors transition the sequencer to `ERROR`.
- `pause` transitions to `PAUSED` at the next safe point.

## Minimal example
```yaml
version: 1
vars:
  hv_values_v: {gen: {values: [0, 200, 400]}}
steps:
  - for:
      bind: hv_v
      in: ${vars.hv_values_v}
      do:
        - set: {device: psu, name: voltage_v, value: ${hv_v}}
        - sleep: 0.2
```
