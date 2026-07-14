# Sequencer Config YAML (v1)

This document explains how the sequencer YAML is parsed and executed by
`src/experiment_control/sequencer/sequencer.py` and `sequencer/runtime.py`.

## Quick summary
- YAML describes a sequence of steps executed sequentially.
- Variables are defined in `vars` and are visible to templates.
- Templates use `${...}` with a restricted expression evaluator.
- Control flow: `for`, `repeat`, `while`, `if`, `try`, `atomic`, `pause`, `assign`, `use`.
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

Every step kind accepts an optional `disabled: true` sibling key to skip it
during execution, preflight validation, and progress estimation without
deleting it:
```yaml
- call:
    device: yag
    action: fire
    params: {}
  disabled: true
```
Omitting `disabled` (or setting it to `false`) runs the step normally; this
is the default, so existing sequence files are unaffected.

### `call`
Call a device or process RPC action.
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
- Use exactly one of `device` or `process`.
- `device`, `process`, `action`, and `params` are rendered as templates at runtime,
  so generic sequences can use targets such as `device: ${synth_device}` and
  `action: ${set_frequency_action}`.
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

Example using a templated call source:
```yaml
- assign:
    freq_center_hz:
      call:
        device: ${synth_device}
        action: ${get_frequency_action}
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
      max_samples: 10000
    condition:
      abs_lt: [${sample_reduced - hv_v}, 1.0]
    stable_for_s: 0.8
```

Fields:
- `timeout_s`: fail after this many seconds (0 means no timeout)
- `every_s`: polling interval
- `sample`: telemetry or call (see below)
- `reduce`: optional reduction of retained samples. `method` supports `mean`, `min`, `max`, or last-sample fallback; `window_s` trims by age; `max_samples` caps retained samples and defaults to `10000`.
- `condition`: required structured condition (see below)
- `stable_for_s`: require condition to hold for this duration

Sample sources:
- Telemetry: `telemetry: {device, signal, max_age_s?}`. Freshness uses the
  consuming sequencer's local receipt age (`age_s` / `t_mono_recv`) when
  available; a producer's monotonic timestamp is not comparable across hosts.
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

### `try`
Run cleanup steps even when the protected body fails, times out, is stopped, or
is interrupted by an external sequencer fault.
```yaml
- try:
    do:
      - call: {device: bigsky_yag, action: start_qswitch}
      - call: {device: pxie5171, action: stream__read_waveform_frame}
    finally:
      - call: {device: bigsky_yag, action: stop_qswitch}
      - call: {device: bigsky_yag, action: stop_flashlamp}
```

Notes:
- There is no `except`; the original failure still fails the run.
- `pause` does not run `finally`; resuming continues the protected body.
- Cleanup failures set the run to `ERROR` and preserve the original error context.

### `use`
Run another sequence from the configured sequence library.

String form:
```yaml
- use: switch_and_wait
```

Mapping form (with argument overrides):
```yaml
- use:
    id: switch_and_wait
    args:
      port: 3
      settle_s: 0.5
```

Notes:
- `use.id` resolves through the sequencer library (`sequence_library_path`).
- `use.args` is optional and is rendered in the caller environment.
- Called sequence vars are applied only for that call frame.
- Recursive `use` chains are rejected.

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
`condition` is required.

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
`condition` is required.

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
Run independent branches concurrently. Each direct child of `do` is one
branch. Supported branches are direct `call`/`set` steps, `atomic` branches,
and `repeat` branches. Atomic and repeat bodies may contain only `call`/`set`.
```yaml
- parallel:
    do:
      - atomic:
          name: prepare_a
          do:
            - set: {device: a, name: mode, value: ready}
            - call: {device: a, action: arm}
      - atomic:
          name: prepare_b
          do:
            - set: {device: b, name: mode, value: ready}
            - call: {device: b, action: arm}
      - repeat:
          times: "${n_traces}"
          do:
            - call: {device: trace_reader, action: read}
```

Behavior:
- Up to eight branches execute concurrently; additional branches queue.
- Operations inside one atomic branch remain sequential. Sibling atomic
  branches may overlap.
- Operations and iterations inside one repeat branch remain sequential. The
  complete repeat runs in one worker and may overlap sibling branches.
- A `parallel` step cannot appear inside an ordinary `atomic` block; place the
  atomic blocks directly under `parallel.do` when they should overlap.
- Different branches must target disjoint devices/processes. Repeated calls to
  one target are allowed within the same atomic or repeat branch.
- Branch targets must render from the parent environment at dispatch time;
  outputs created inside a branch cannot be used to choose a later target.
- Every branch starts from the same environment snapshot. Outputs produced
  within an atomic branch are visible to later operations in that branch.
- `save_as`, `extract`, and `assign` output names must be unique across
  branches. Outputs merge only after every branch succeeds.
- A branch stops at its first failed operation. The sequencer waits for all
  already-dispatched branches, aggregates failures, and does not merge outputs.
- Pause/stop is deferred until the parallel group joins. Parallel execution is
  always opt-in; ordinary neighboring steps remain sequential.
- Repeat is the only loop supported as a direct parallel branch. Its count must
  render to a positive integer and its body may contain only `call`/`set`.
  Other loops, waits, sleeps, nested parallel/atomic blocks, context operations,
  adaptive steps, `try`, `use`, and standalone `assign` are not supported.

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
- Sequencer applies bounded retry for transient `stream.context.set` failures
  (for example device restart windows), then fails the step if retries are exhausted
  or if the error is non-transient.
- HDF writer resolves context by `seq` (not sticky per-stream carry-forward). If a
  matching context descriptor never arrives within the configured TTL, samples are
  written as `context_id = -1`.

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
- `centered_triangle: {center, span, num, dir}` (`num` odd points across the full span; `dir: 1` goes `center -> high -> low -> center`, `dir: -1` goes `center -> low -> high -> center`; default `dir` is `1`; total `2*num + 1` points)
- `logspace: {start, stop, num, base?}`
- `geomspace: {start, stop, num}`
- `values: [...]`
- `scan2d: ...`

Modifiers:
- `offset`
- `shuffle` + `seed`
- `serpentine` (alternates direction based on parent loop index)

### `sample`
Draw a random subset from the generated set. `sample` is a sibling of the
generator under `gen`, so it composes with any generator (e.g. visit `m` random
spots on a `scan2d` grid instead of the whole grid).
```yaml
in:
  gen:
    scan2d:
      center: {x: 0.0, y: 0.0}
      size: 10
      steps: {x: 11, y: 11}
    sample: {count: 8, replace: true}
```
- `count` (required): number of records to draw.
- `replace` (default `false`): allow repeats. `count` may exceed the population
  size only when `replace: true`.
- `seed` (optional): omit for a fresh (non-deterministic) draw each time.

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

Validation rules used by `sequencer.validate`:
- A condition object must contain exactly one operator key.
- `always` must be the only key when present.
- Comparison operators require exactly two arguments.
- `and` / `or` require a list with at least one clause.
- `and` / `or` with one clause are valid but produce a warning.

`sequencer.validate` is intentionally structural only (YAML + AST + condition DSL).
For runtime reachability checks (device/action/member/stream/signal references),
use `sequencer.preflight`.

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

## Run orchestration (`sequencer.start`)
`sequencer.start` supports run-level controls:
- `sequence_id` (optional): load this library entry before start.
- `repeat_count` (optional int >= 1): run N full loops.
- `continuous` (optional bool): run forever until stopped.
- `vars_override` (optional dict): run-scoped var overrides.
- `adaptive` (optional dict): per-study adaptive start mode overrides.

Semantics:
- If `continuous=true`, `repeat_count` is ignored.
- If `continuous=false` and `repeat_count` is omitted, one run executes.
- `vars_override` keys must already exist in `vars`.
- `vars_override` is not persisted to YAML.

`sequencer.status` exposes:
- `run_id`
- `loop_mode`, `loops_completed`, `loops_target`
- active `vars_override`
- progress snapshot with loop/run fields.

## Sequence library manifest
Set these in `processes/sequencer.yaml` `init_kwargs`:
- `sequence_library_path`: manifest path.
- `autoload_sequence_id` (optional): load a library sequence on process start.
- `library_description_policy`: `off | warn | error`.

Manifest schema (`version: 1`):
```yaml
version: 1
sequences:
  main:
    path: sequences/main.yaml
    label: Main sequence
    description: Main operator workflow
    tags: [routine]
autoload_dirs:
  - dir: sequences/fragments
    pattern: "*.yaml"
    namespace: fragments
```

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
