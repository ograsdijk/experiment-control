# Device State Save/Restore (Design Plan)

Status: planning only, not implemented.

## Goals

- Capture current runtime configuration/state of selected devices.
- Restore that state later (same machine or another machine).
- Keep implementation minimally invasive: reuse existing capabilities and `get`/`set`/command routing, avoid per-driver boilerplate where possible.

## Scope

- Manager-level feature (not tied to any specific process).
- Works for connected devices with available RPC.
- Best-effort by default; strict mode optional.

## Non-goals (first pass)

- Perfect reconstruction of every driver-specific transient state.
- Automatic support for commands that are not inferable from capabilities metadata.
- Historical state replay / full command history export.

## Snapshot Semantics

- Profiles are point-in-time snapshots.
- They do not store every command call made during a run.
- For any restorable target, only one effective value is stored.
- If command-derived tracking is added later, behavior should be last-write-wins per target key (for example per channel), not append-only history.

## Current Building Blocks

- Device capabilities already expose member metadata (`kind`, `readable`, `settable`, params, annotations).
- Device RPC already supports:
  - `get` / `set` for members
  - direct command calls routed through manager/device_router
- Manager already has device list/status and command routing.

## Proposed Architecture

1. Add manager-side state capture/apply engine.
2. Persist profiles on disk in a manager-owned folder (for example `data/device_state_profiles`).
3. Expose profile lifecycle through manager RPC.
4. (Optional later) surface in FastAPI/UI.

## Capture Strategy (minimal invasive)

Capture should be triggerable at any time while the stack is running (manual mid-run snapshot), not only at startup/shutdown.

Capture candidates, in this order:

1. Members marked `readable && settable` with `kind in {property, attribute}`:
   - read via `get(name=...)`
   - restore via `set(name=..., value=...)`
2. Heuristic method pairs:
   - `get_*` with no required params
   - matching `set_*` with one required value param
   - capture via command call to getter, restore via setter command

Filter out values that cannot be JSON-serialized (or mark as skipped with reason).

## Restore Strategy

- Apply entries per device in stored order.
- Options:
  - `dry_run`: validate and report without sending commands
  - `fail_fast`: stop at first failure (strict mode)
  - default best-effort: continue and return detailed per-entry results

## Proposed Manager RPC

- `manager.state.capture`
  - params: `profile_name?`, `device_ids?`, `persist=true|false`, `overwrite?`
- `manager.state.list`
- `manager.state.get`
  - params: `profile_name`
- `manager.state.apply`
  - params: `profile_name?`, `profile?`, `dry_run?`, `fail_fast?`, `device_ids?`
- `manager.state.delete`
  - params: `profile_name`

## Profile Schema (suggested)

Use JSON (recommended for first implementation).

Reason: direct compatibility with existing Python/JS tooling, easy diffing, easy transport over RPC/API, no extra parsing complexity.

Suggested top-level fields:

- `kind`: `"experiment-control-device-state-profile"`
- `version`: integer
- `created_at`: ISO8601 string
- `capture_mode`: `"snapshot"`
- `source`: manager endpoint/host metadata
- `devices`: array of device state entries
- `warnings`: optional capture-time warnings

Per-device entry:

- `device_id`
- `entries`: ordered list of captured operations
  - `mode`: `"set_member"` or `"call_action"`
  - `target`: member/action name
  - `params` or `value`
  - `annotation`/metadata (optional)

## Error Reporting

Return structured results:

- `applied`: count
- `skipped`: count
- `failed`: count
- per-device details with specific reasons:
  - unknown member/action
  - device disconnected/unavailable
  - coercion/type error
  - command timeout/error

## Optional Device-Specific Hints (later)

Only for devices that need finer control:

- include/exclude lists
- explicit getter/setter mappings
- explicit apply ordering groups

These hints can live in device YAML under an optional section (for example `state_profile_hints`) without requiring driver code changes.

## Rollout Plan

1. Manager-only RPC implementation + JSON persistence.
2. Add protocol docs and tests for capture/apply behavior.
3. Add FastAPI endpoints that proxy new manager RPC commands.
4. Add UI actions (capture/apply/select profile) once backend behavior is stable.
