# Sequencer Progress Tracker (Design Notes)

## Goals
- Show percent complete when total steps are known.
- Show elapsed time (monotonic, pause-aware).
- Show ETA/remaining time when total steps are reasonably predictable.
- Keep overhead low and avoid changing execution semantics.

## Definitions
- elapsed_s: seconds since `sequencer.start`, excluding paused time (monotonic clock).
- progress: ratio of completed countable steps / total countable steps.
- eta_s: estimated remaining seconds (only when total steps are known and step timing is stable enough).

## What counts as a "step"
- Each executable YAML step (call/set/assign/sleep/wait_until/if/while/atomic/etc.) can be a unit.
- For composite steps (atomic/if/while), progress uses internal substep counters.
- sleep / wait_until count as a step; duration contributes to elapsed, but only fixed durations are useful for ETA.

## Total steps: known vs unknown
Known totals:
- Static sequences with:
  - for/repeat loops with explicit iteration count
  - linspace-driven loops (known length)
  - fixed-length do blocks
- total_steps can be computed at load time.

Unknown totals:
- while with runtime-dependent condition
- wait_until without timeout
- rules depending on live telemetry/state

Behavior when unknown:
- percent = None
- eta_s = None
- still report elapsed_s and current step info

## ETA strategy (when total known)
- Maintain rolling step duration stats:
  - step_durations: time per completed step
- Estimate:
  - eta_s = avg_step_s * (total_steps - completed_steps)
- Optional: if a step has a fixed duration (sleep), use its known time instead of average.

## Pause/resume semantics
- On pause:
  - freeze progress counters
  - stop elapsed timer (store paused start)
- On resume:
  - continue elapsed from stored base
- elapsed_s should not include time spent paused.

## Data model (suggested fields)
progress = {
  "state": "running|paused|stopped",
  "run_id": <uuid or int>,
  "elapsed_s": <float>,
  "completed_steps": <int>,
  "total_steps": <int|None>,
  "percent": <float|None>,
  "eta_s": <float|None>,
  "current_step": {
    "path": "root[3].do[1]",
    "kind": "call|set|wait_until|sleep|if|while|atomic",
    "started_ts_mono": <int>,
  },
}

## API / transport idea
- sequencer.status RPC includes the progress snapshot.
- Optional: publish `sequencer.progress` events at a throttled rate (e.g., 5-10 Hz max).

## UI behavior (TUI)
- If percent known: show XX% and ETA.
- If unknown: show elapsed and current step label.
- If paused: show PAUSED and keep elapsed fixed.

## Open decisions
- Granularity: leaf steps only vs include composite wrapper steps.
- wait_until with timeout: treat as fixed-duration for ETA vs unknown.
- Persist progress to HDF: likely unnecessary for v1.

## Implementation outline (later)
1. Add a ProgressTracker helper in sequencer runtime:
   - counts completed steps
   - tracks current step path
   - tracks step timings
2. Precompute total_steps on YAML load (when statically known).
3. Update tracker at each step boundary.
4. Expose via RPC + optional PUB event.
