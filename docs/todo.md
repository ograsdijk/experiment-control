# TODO

## Process lifecycle / Windows TUI
- Verify TUI responsiveness (mouse + keyboard) remains stable under long-running sessions.

## Device connect checks
- Identity-based connect check is implemented (`connect_check.identity` + default `on_fail: disconnect`).
- Follow-ups:
  - Add expected-value sources (`env`, startup input map) so serial/model expectations don't have to be hardcoded.
  - Add custom callable/action-based connect checks (beyond fixed `identity`).
  - Add richer comparators (regex/set/range) for identity fields.

## GUI (React Web UI) design + implementation
- Sequencer workflow snippets/presets:
  - Add an "Insert snippet" library in the Sequencer YAML editor (not generic stack config).
  - Snippets should be full workflows (for example: set parameter -> set context -> settle/wait -> acquire trace -> optional averaging).
  - Include built-in workflows (1D scan, 2D scan, adaptive scan), plus support for user-defined snippets.
  - Support placeholder variables for reuse, including:
    - device IDs
    - action/function names
    - parameter names/ranges
    - context field names
    - stream names
  - Insert at cursor and then run existing validate + preflight checks.
- Sequencer snippet implementation plan:
  - Phase 1:
    - Add snippet registry/types + built-in full-workflow templates.
    - Add "Insert snippet" modal with typed placeholders and rendered YAML preview.
    - Extend YAML editor handle to insert at cursor/selection.
    - Wire device/action placeholder dropdowns to current capabilities data.
  - Phase 2:
    - Add user-defined snippets (local storage + UI profile import/export).
    - Keep built-ins read-only, customs editable.
  - Validation/docs/tests:
    - Unit tests for snippet rendering and insertion behavior.
    - UI tests for placeholder dependency flows (device -> action).
    - Add docs for snippet schema and built-in snippet usage.

## Measurement schema TODO
- Clarification: this is for measurement metadata fields (header/notes JSON), not telemetry/stream array payloads.
- Add `array` field type support in `measurement.yaml` notes/profiles schema.
- Schema shape for arrays:
  - `type: array`
  - `items.type: string | number | integer | boolean`
  - optional `min_items`, `max_items`, `unique_items`.
- Extend schema validation + normalization in `src/experiment_control/schemas/measurement.py`:
  - validate `default` for array fields.
  - coerce and validate each element by `items.type`.
- Web UI support in `web/react_ui/src/App.tsx`:
  - render `array` fields in measurement start + note forms.
  - parse/validate array input and show inline validation errors before submit.
- Keep storage format unchanged:
  - arrays are written into existing JSON payloads (`/measurement/header_json`, `/measurement/notes.payload_json`).
- Add tests + docs:
  - `tests/test_measurement_schema.py` coverage for valid/invalid array definitions and values.
  - update docs with example `array` fields.
