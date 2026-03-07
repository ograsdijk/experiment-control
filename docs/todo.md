# TODO

## Process lifecycle / Windows TUI
- Keep Windows PID liveness probes on Win32 APIs (`OpenProcess` + `GetExitCodeProcess`) and do not regress to `os.kill(pid, 0)`.
- Add a higher-level Windows integration check for two-process TUI startup (`run_stack` parent + `--no-tui` child manager) with lifecycle flags enabled.
- Verify TUI responsiveness (mouse + keyboard) remains stable under long-running sessions.

## Manager logging
- Add env/config flags:
  - `MANAGER_LOG_STDERR=1|0` (default 1)
  - `MANAGER_LOG_FILE=path` (optional)
- Manager should log error events (e.g., `manager.*_error` and `manager.log` severity>=error) to stderr when enabled.
- If `MANAGER_LOG_FILE` is set, append log lines there as well.
- When the TUI launcher is used, pass `MANAGER_LOG_STDERR=0` and (optionally) `MANAGER_LOG_FILE=...` to the manager subprocess.

## GUI (React Web UI) design + implementation
- Configuration:
  - Add YAML snippets (presets) in UI.

## Web UI TODO
- Sequencer YAML editor:
  - Current `Textarea` only supports syntax highlighting in read-only Preview mode.
  - Add true syntax highlighting while editing by replacing `Textarea` with a code editor component (prefer `CodeMirror 6`; `Monaco` as heavier alternative).
  - Keep current behavior:
    - Diagnostics line/column jump should focus editor at exact offset.
    - `Show loaded YAML` should still populate the editable buffer.
    - Preview mode can remain as a read-only formatted view.

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
