# Adaptive Studies

Current status: implemented v1 for one controller family, with explicit limits.

## What Exists Now

The adaptive framework currently supports:

1. one sequencer step: `adaptive`
2. one real controller: `adaptive.adaptive_grid_1d`
3. observation sources:
   - `call`
   - `telemetry`
   - `analysis_output`
4. repeated measurements at one suggested point
5. aggregate statistics per metric:
   - `mean`
   - `std`
   - `min`
   - `max`
   - `median`
   - `sem`
   - `n`
   - `n_ok`
6. controller convergence via:
   - `controller.config.min_loss`
7. saved adaptive trial history keyed by study id
8. run-time reuse controls:
   - `reset`
   - `resume`
   - `warm_start`

The control loop lives in the sequencer runtime, not in `stream_analysis`.

## Current Architecture

The split is:

1. `stream_analysis`
   - computes scalar metrics from stream data
   - publishes `manager.stream_analysis.output`
2. adaptive controller
   - suggests the next point
   - consumes completed trial results through `tell(...)`
3. sequencer runtime
   - executes the trial body
   - handles waiting, context ids, repeated measurements, aggregation, and stopping

`stream_analysis` is an input source for adaptive studies, not the place where adaptive control logic runs.

## Current YAML Shape

```yaml
- adaptive:
    id: resonance_scan
    controller:
      kind: adaptive.adaptive_grid_1d
      config:
        loss:
          kind: curvature
        min_loss: 0.2
    space:
      x:
        type: float
        min: -1.0
        max: 1.0
        step: 0.01
        min_step: 0.002
        snap: true
        origin: -1.0
    bind:
      x: scan_x
      trial_index: trial_idx
    state:
      x_actual:
        kind: telemetry
        config:
          device: mirror
          signal: x_actual
    do:
      - set:
          device: mirror
          name: x
          value: ${scan_x}
      - set_context:
          streams:
            - device: camera
              stream: frame
          fields:
            scan_x: ${scan_x}
            trial_idx: ${trial_idx}
      - call:
          device: camera
          action: stream__acquire_frame
    observe:
      repeats: 3
      metrics:
        brightness:
          kind: analysis_output
          config:
            workspace_id: camera_metric
            output_id: brightness
            require_current_context: true
            timeout_s: 2.0
      aggregate:
        brightness: [mean, std, n_ok]
      score: ${brightness_mean}
    stopping:
      max_trials: 50
```

## Field Reference

### `adaptive.id`

Required.

- Stable study identifier.
- Used for saved adaptive history.
- Must be unique within the sequence.

This is the key used by the sequencer UI and `sequencer.start` adaptive overrides.

### `controller`

Currently supported:

```yaml
controller:
  kind: adaptive.adaptive_grid_1d
  config:
    learner_kind: average_learner1d
    loss:
      kind: curvature
      params:
        area_factor: 1.0
        euclid_factor: 0.02
        horizontal_factor: 0.02
    min_loss: 0.2
    min_samples: 3
    max_samples: 3
    delta: 0.05
    alpha: 0.05
    neighbor_sampling: 0.0
    min_error: 0.0
```

Current behavior:

1. `kind` must be `adaptive.adaptive_grid_1d`
2. `loss.kind` may be:
   - `curvature` (default)
   - `default`
3. `min_loss` is compared against the learner's current maximum interval loss
4. if `observe.repeats == 1`, the default learner is `learner1d`
5. if `observe.repeats > 1`, the default learner is `average_learner1d`

Important:

- `min_loss` is a controller-internal convergence threshold, not a target score.
- Its numeric meaning depends on the selected loss function.
- It is dimensionless.

### `space`

Defines the controller-facing decision variables.

Currently, `adaptive.adaptive_grid_1d` requires exactly one numeric parameter.

Supported parameter shapes:

```yaml
space:
  x:
    type: float   # or int
    min: -1.0
    max: 1.0
    step: 0.01
    min_step: 0.002
    snap: true
    origin: 0.0
```

or

```yaml
space:
  mode:
    type: categorical
    choices: [a, b, c]
```

Current runtime support:

1. `adaptive.adaptive_grid_1d` only works with one `float` or `int` parameter
2. `step` + `snap` are applied by the sequencer runtime before the trial executes
3. `min_step` is accepted as schema/metadata but is not yet used as a stop condition

### `bind`

Maps controller proposal fields into normal sequencer variables.

Example:

```yaml
bind:
  x: scan_x
  trial_index: trial_idx
```

Current proposal fields available in practice:

1. the space parameter(s), e.g. `x`
2. `trial_index`
3. some controller metadata, such as `adaptive_loss` when available

### `state`

Optional.

Samples realized/measured state once per trial after the `do` block.

This is useful when:

1. commanded setpoints are not exact
2. you want to record actual realized actuator values separately from the requested point

Uses the same source wrapper shape as `observe.metrics`.

### `do`

Normal sequencer step list.

This is where you:

1. move devices
2. wait for settling
3. set stream context
4. trigger acquisition

The adaptive runtime does not move hardware directly.

### `observe`

Defines the measured values for one adaptive trial.

Example:

```yaml
observe:
  repeats: 3
  metrics:
    brightness:
      kind: analysis_output
      config:
        workspace_id: camera_metric
        output_id: brightness
        require_current_context: true
        timeout_s: 2.0
  aggregate:
    brightness: [mean, std, n_ok]
  score: ${brightness_mean}
```

Current behavior:

1. `metrics` is required
2. `repeats` defaults to `1`
3. all repeats are taken at the same suggested point
4. aggregates are always computed internally
5. flattened aggregate names like `brightness_mean` are exposed to the score expression and the sequencer env
6. v1 uses one scalarized `score`

### source wrappers

Every metric or state entry uses:

```yaml
kind: ...
config: {...}
```

Current source kinds:

1. `call`
2. `telemetry`
3. `analysis_output`

`analysis_output` is the preferred source when the objective comes from stream data.

### `stopping`

Generic outer stop conditions:

```yaml
stopping:
  max_trials: 50
  max_runtime_s: 120
  target_score: 0.95
  patience: 10
```

Current behavior:

1. `max_trials`
2. `max_runtime_s`
3. `target_score`
4. `patience`

These are independent of controller-specific convergence (`min_loss`).

### `constraints`

Accepted in the schema, but not implemented yet.

### `fail_on_trial_error`

Optional, default `false`.

If `false`:

1. failed trials are recorded
2. the controller still receives the trial through `tell(...)`

If `true`:

1. the adaptive step errors out on the first failed trial

## Current Runtime Model

One adaptive iteration currently runs like this:

1. the controller suggests the next proposal
2. the sequencer runtime applies snapping/clamping to build the actual `params`
3. bound values are exposed through `bind`
4. the `do` block runs
5. `state` is sampled once
6. `observe.metrics` is collected
7. if `repeats > 1`, multiple measurements are taken at the same point
8. aggregates are computed
9. the scalar `score` is evaluated
10. the runtime builds a trial record
11. the runtime calls `controller.tell(proposal, trial)`
12. stopping logic is checked

The user does not manually write `tell(...)` in YAML.

## Trial Data Model

Each trial stores:

1. `params_raw`
   - controller proposal before snapping
2. `params`
   - applied parameters after snapping/clamping
3. `proposal_meta`
4. `state`
5. `metrics`
6. `replicates`
7. `aggregates`
8. `score`
9. `ok`
10. `error`
11. `context_id`

This is what makes warm-start/resume possible: the controller can be rebuilt by replaying prior trials.

## Saved Adaptive Study Reuse

Adaptive trial history is now stored by `adaptive.id`.

Current run-time reuse modes:

1. `reset`
   - start with no saved history
2. `resume`
   - rebuild a fresh controller and replay compatible saved trials
3. `warm_start`
   - currently the same replay behavior as `resume`

Compatibility rules in v1:

1. same `adaptive.id`
2. same controller kind
3. same parameter names
4. only successful trials are reused
5. reused trials must still be in bounds for the current `space`

This is exposed in two places:

1. the Sequencer modal
2. `sequencer.start` with an `adaptive` override payload

Example start override:

```json
{
  "type": "sequencer.start",
  "params": {
    "adaptive": {
      "resonance_scan": {
        "mode": "warm_start"
      }
    }
  }
}
```

Current extra sequencer RPCs:

1. `sequencer.adaptive.status`
2. `sequencer.adaptive.clear`
3. `sequencer.adaptive.clear_all`

## Example in This Repo

The main example is:

[sequence_frequency_adaptive.yaml](/c:/Users/ogras/Documents/GitHub/experiment-control/examples/dummy_frequency_trace_sequencer/sequence_frequency_adaptive.yaml)

It uses:

1. `adaptive.adaptive_grid_1d`
2. `curvature` loss
3. `min_loss`
4. `analysis_output` from `workspace-1 / integral`
5. `context_id` matching
6. repeated trace acquisitions per point

## Current Limits

The adaptive framework is still intentionally narrow.

Current limits:

1. only `adaptive.adaptive_grid_1d` is implemented
2. no 2D adaptive controller yet
3. no random / coordinate-search / Bayesian / CMA-ES controllers yet
4. `constraints` is not implemented
5. `min_step` is not yet used as an actual convergence rule
6. `resume` and `warm_start` currently use the same replay-based behavior
7. no controller serialization; studies are rebuilt from saved trial history
8. `analysis_output` only works inside the sequencer process runtime, because it waits on `manager.stream_analysis.output`

## TODO

High-value next steps:

1. add more controllers:
   - `adaptive.random`
   - `adaptive.coordinate_search`
2. add real 2D adaptive controllers
3. implement real `warm_start` semantics distinct from `resume`
4. surface richer controller diagnostics in the UI
5. use `min_step` as a proper refinement floor where it makes sense
6. add motion-aware next-point scheduling for slow actuators

### Motion-aware scheduling

This is the next important control-layer improvement for hardware that cannot jump quickly.

The current controller only picks informative points. It does not account for move cost.

The recommended next design is:

1. let the controller produce several promising candidate points
2. add a scheduler layer that chooses the next actual point using:
   - information value
   - estimated move time from the current position
   - optional settle penalty
   - optional max jump

That keeps:

1. the learner focused on information / refinement
2. the scheduler focused on hardware cost

This should be implemented as a scheduling layer on top of the current adaptive controller, not by trying to bake move-time cost directly into the external learner first.
