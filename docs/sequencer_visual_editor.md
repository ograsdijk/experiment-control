# Sequencer Visual Editor

## Purpose

The sequencer UI provides a visual editor on top of YAML while keeping YAML as the source-of-truth format for load/run/export.

The editor is block/tree based because sequencer execution is ordered and nested, not graph-first.

## Current Layout

The sequencer modal has a sidebar and three full-height tabs.

Sidebar (top to bottom):

1. **Sequence**: library picker, load/reload, and the name of the sequence being edited.
2. **Editor**: load the editor YAML into the sequencer, validate + preflight, and reload the loaded source (discarding edits). A "not loaded" badge shows when the editor differs from the loaded sequence.
3. **Run overrides** and **Adaptive reuse** (when the sequence has adaptive steps).
4. **Diagnostics**: validator errors and warnings, grouped by severity; click one to jump to it.
5. **This run**: events of the current or last run (see below).

Tabs:

- **Steps**: the step tree, with an inspector for the selected step.
- **Variables**: `vars` and `context_columns`.
- **YAML**: the full sequence text (CodeMirror edit mode or read-only preview).

Value fields keep a local draft: text is written to the YAML on blur or Enter, and Escape discards it. Edits rewrite only the changed part of a step, so the rest of the YAML keeps its formatting, comments and number spelling.

## What You Can Edit Visually

Top-level metadata:

- `vars`
- `context_columns`

Step editing (inspector forms):

- `call`
- `sleep`
- `repeat`
- `for` (generator and config)
- `adaptive` (core + advanced config)
- `wait_until`
- `set`
- `assign`
- `set_context`
- `if` condition
- `while` condition

Tree operations:

- add top-level step
- insert below
- insert into child body (`do` / `then` / `else` where valid)
- duplicate
- delete
- move up/down among siblings
- collapse/expand nested blocks

## Nested Body Editing

Nested bodies are edited structurally from the tree controls (insert/move/delete child steps), not as an inline inspector table.

This applies to loop and branch bodies, including adaptive `do`.

## YAML Editing + Diagnostics

Full YAML editing uses CodeMirror (edit mode) and a read-only formatted preview mode.

Diagnostics are shown where they are, not only in the sidebar list:

- each step in the Steps tab shows separate error and warning badges for its own lines, and its collapsed parents count what is inside them
- the YAML editor marks the lines (hover for the message)
- selecting a diagnostic in the list switches to the right tab and focuses the step or the exact line/column

Diagnostics computed for other text (the editor changed since validating) are marked stale and only listed, since their lines may no longer be right.

## Run Events

The **This run** card lists what happened during the current or last run (`sequencer.run_events`): pauses (who paused and why, e.g. a watchdog rule), resumes, stops, step and cleanup failures, external faults, and warnings or errors other processes logged while the run was going. Each event is tied to the step that was running and is also marked on that step, like a diagnostic. A pause by the watchdog is a warning; a pause by the operator, the sequence or a script is info.

## Running Step Highlight

While a run is going (or paused), the step it is executing is highlighted:

- **YAML tab**: the step's whole block (all its lines) gets a background and a ▶ in the gutter on its first line. Enclosing loops and branches get a thin bar in the gutter along their range. When a container step is itself the current step (e.g. a `for` moving to its next iteration), only its header lines are highlighted.
- **Steps tab**: the step's row is highlighted and its parents are tinted; a collapsed parent shows "running inside".
- **Follow** (switch next to the tabs, off by default, remembered per browser): scrolls the running step into view when it changes and expands its collapsed parents. Scrolling the panel yourself (mouse wheel, scrollbar, PageUp/PageDown) turns Follow off.

The highlight follows `sequencer.status` (polled every 1.5 s), so during a fast run of short steps it shows a sample rather than every step. To avoid pointing at the wrong place, nothing is highlighted when:

- the editor text is not the loaded sequence (edited since loading, or another sequence was loaded since)
- the step belongs to a `use:` sub-sequence (its lines are in another file)
- the step line is not one the step tree recognizes

## Sequence Loaded Elsewhere

`sequencer.status.loaded_revision` counts loads. When another client loads a sequence while the modal is open:

- if the editor has no unsaved edits, the modal silently fetches the newly loaded YAML
- if it has edits, they are kept and a notice offers to discard them and show the loaded sequence

Until the editor holds the loaded text, the running step is not highlighted and run events are marked stale.

## Parsing Resilience

If outline parsing fails for the current YAML text:

- the modal remains usable
- a parser error notice is shown in the outline area
- full YAML editing remains available

No UI blank-screen behavior is expected from outline parse errors.

## Known Limits

- Complex YAML constructs outside the supported sequencer patterns may reduce outline fidelity. Steps written as bare flow mappings (`- {sleep: 2}`) are not shown in the step tree, so they get no markers or running highlight; write them as `- sleep: 2`.
- Visual inspector editing is intentionally schema-driven for known step structures; raw YAML remains the fallback for anything unusual.

## Notes

- Preview and edit modes share the same YAML token color palette to avoid color drift.
- The CodeMirror editor is loaded lazily when needed to reduce initial UI load cost.

