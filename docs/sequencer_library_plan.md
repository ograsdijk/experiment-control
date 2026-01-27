# Sequencer Library, Composition, and Run-Orchestration Plan

Status: planning only (no implementation in this doc).

## Scope
Add sequencer features for:
1. Run looping controls (run once, run N times, run continuously).
2. Named sequence library (start by sequence ID via RPC/UI).
3. Sequence composition (`use` one sequence inside another).
4. Multiple sequence YAMLs loaded/managed at once.
5. Optional variable overrides at start time (`vars_override`).
6. Support both explicit sequence mapping and auto-discovery from directories.
7. Sequencer UI library browser button/modal with sequence descriptions and editable-var hints.

## Current Baseline
1. Sequencer currently loads one sequence at a time (`sequencer.load` by path/text).
2. Runtime already supports `repeat` and `while` inside YAML steps.
3. No built-in sequence registry/library.
4. No native `use`/include step in AST/runtime.
5. No start-time variable override mechanism.

## Target Behavior
1. Operator can select a sequence by ID and start it through RPC/UI.
2. Operator can run once, run N loops, or run continuously.
3. A sequence can call reusable fragments by ID.
4. Library can be defined by explicit IDs and/or auto-loaded directories.
5. Sequence start can apply temporary variable overrides without mutating files.

## Library Manifest Design
Use one manifest file, configured in sequencer init kwargs.

Example:

```yaml
version: 1
sequences:
  port_cycle:
    path: sequences/port_cycle.yaml
    label: Port Cycle
    description: Cycle configured fiber ports
    tags: [routine]
  switch_and_wait:
    path: sequences/fragments/switch_and_wait.yaml
    tags: [fragment]

autoload_dirs:
  - dir: sequences/fragments
    pattern: "*.yaml"
    namespace: fragments
```

Notes:
1. `sequences:` is explicit mapping (stable IDs chosen by user).
2. `autoload_dirs:` auto-discovers files and generates IDs.
3. `namespace` is an ID prefix for generated IDs (example: `fragments.switch_and_wait`).
4. Conflict policy: explicit mapping wins over autoload.
5. Manifest is the source of truth; no implicit random folder scan outside configured `autoload_dirs`.
6. `description` should be strongly encouraged for operator usability.

## Library Metadata and Description Policy
Recommended metadata for each library entry:
1. `label` (short UI name)
2. `description` (operator-facing explanation)
3. `tags` (search/filter)
4. Optional `editable_vars` hints (see below)

Description enforcement recommendation:
1. Default mode: soft enforcement (warn for missing descriptions).
2. Optional strict mode: reject explicit entries missing description.
3. Auto-discovered entries can stay non-strict by default (or require description only when promoted to explicit mapping).

Rationale:
1. A hard requirement everywhere can be annoying for fast iteration.
2. Soft-by-default + strict option keeps velocity while enabling production hygiene.

## Sequence Composition (`use`)
Add new step type:

```yaml
- use:
    id: switch_and_wait
    args:
      port: 3
      wait_s: 0.5
```

Behavior:
1. `id` resolves against loaded library entries.
2. Sequencer expands `use` at load/compile time (macro-style inline expansion).
3. `args` overrides the called sequence vars for that use site only.
4. Detect and reject recursive cycles (`A -> B -> A`).
5. Error reports should include include-stack for debugging.

## Run Loop Controls
Extend `sequencer.start` params:
1. `sequence_id` optional (start a library entry directly).
2. `repeat_count` optional integer (`>=1`).
3. `continuous` optional bool.
4. `vars_override` optional object.

Semantics:
1. Default: single run.
2. If `continuous=true`, ignore `repeat_count`.
3. If `continuous=false` and `repeat_count` provided, run exactly N times.
4. Pause/stop/error behavior remains per current runtime semantics.

## `vars_override` Design
Example:

```json
{
  "type": "sequencer.start",
  "params": {
    "sequence_id": "port_cycle",
    "repeat_count": 5,
    "vars_override": {
      "ports": [1, 3, 5],
      "port_settle_s": 0.8
    }
  }
}
```

Rules:
1. Overrides are run-scoped only (not persisted to YAML).
2. Apply after base vars load and before template resolution.
3. Recommended default: strict unknown-key rejection.
4. Expose effective vars in status/lifecycle payload for traceability.
5. UI can use library metadata and/or parsed sequence vars to show which vars are intended to be user-editable.

## RPC Additions
Add:
1. `sequencer.library.list`
2. `sequencer.library.reload`
3. `sequencer.library.load` (set active by ID)
4. `sequencer.active.get` / `sequencer.active.set` (optional but useful)

Extend:
1. `sequencer.start` with `sequence_id`, `repeat_count`, `continuous`, `vars_override`.
2. `sequencer.status` with:
   - active sequence ID/source
   - loop mode info
   - loops completed / loops target
   - effective vars (or summary/hash)
3. `sequencer.loaded_yaml` with active source metadata in library mode.

## Sequencer Config Additions
In `processes/sequencer.yaml` init kwargs:
1. `sequence_library_path` (manifest path).
2. Optional `autoload_sequence_id` (initial active ID on startup).
3. Optional policy knob for descriptions (example: `library_description_policy: warn|error|off`).
4. Location is sequencer-local config only: `processes/sequencer.yaml -> init_kwargs` (not top-level `stack.yaml`).

Example:

```yaml
version: 1
process_id: sequencer
process:
  module: experiment_control.sequencer.sequencer
  class_name: SequencerProcess
init_kwargs:
  rpc_timeout_ms: 2000
  sequence_library_path: sequences/library.yaml
  autoload_sequence_id: port_cycle
  library_description_policy: warn
```

## UI Changes
1. Add sequence selector (library IDs + labels).
2. Add start mode controls:
   - once
   - N times
   - continuous
3. Add variable override editor (compact JSON/object form or typed controls later).
4. Add library reload action and validation/error view.
5. Add a "Library" button in sequencer UI that opens a modal listing all included sequences.
6. In the library modal, show per-entry:
   - ID and label
   - description
   - source path and source type (explicit/autoload)
   - editable vars (derived from sequence vars and optional manifest hints)
   - validation status/errors
7. Allow starting a selected sequence directly from this modal.

## Phased Implementation Plan
Phase 1: Library foundation
1. Manifest parser for explicit + autoload dirs.
2. In-memory registry of compiled specs.
3. RPC for list/reload/load active sequence.

Phase 2: Start controls + vars override
1. Extend `sequencer.start` params.
2. Add runtime loop orchestration wrapper.
3. Add effective-vars and loop progress in status.

Phase 3: Composition
1. Add `use` to AST.
2. Add compile-time expander with cycle detection.
3. Add diagnostic include-stack paths.

Phase 4: UI + docs
1. Sequencer modal controls for library + run mode + overrides.
2. Update `docs/sequencer_config.md` and `docs/protocol.md`.
3. Add usage examples for fragments and mixed library modes.

Phase 5: Tests
1. Manifest parse tests (explicit + autoload + conflict precedence).
2. Composition tests (`use` expansion, recursion failure).
3. Runtime tests for repeat/continuous semantics.
4. RPC tests for library lifecycle and start overrides.

## Useful Optional Additions
1. `sequencer.library.validate_all` RPC to preflight every entry.
2. Sequence metadata fields: `owner`, `tags`, `estimated_duration_hint_s`.
3. Per-entry default overrides in manifest (merged with start overrides).
4. Sequence hash pinning in status/events for reproducibility.
