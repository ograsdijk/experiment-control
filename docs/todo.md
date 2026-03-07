# TODO

## Process lifecycle / Windows TUI
- Verify TUI responsiveness (mouse + keyboard) remains stable under long-running sessions.

## GUI (React Web UI) design + implementation
- Configuration:
  - Add YAML snippets (presets) in UI.

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
