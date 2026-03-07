# Sequencer Visual Editor

## Purpose

The sequencer UI provides a visual editor on top of YAML while keeping YAML as the source-of-truth format for load/run/export.

The editor is block/tree based because sequencer execution is ordered and nested, not graph-first.

## Current Layout

The sequencer modal uses three working areas:

1. Sequence outline (tree)
2. Step inspector + sequence metadata
3. Full sequence YAML + diagnostics

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

Diagnostics support line/column jump:

- selecting a diagnostic expands the YAML section if needed
- switches to edit mode if needed
- focuses the code editor at the exact offset

## Parsing Resilience

If outline parsing fails for the current YAML text:

- the modal remains usable
- a parser error notice is shown in the outline area
- full YAML editing remains available

No UI blank-screen behavior is expected from outline parse errors.

## Known Limits

- Complex YAML constructs outside the supported sequencer patterns may reduce outline fidelity.
- Visual inspector editing is intentionally schema-driven for known step structures; raw YAML remains the fallback for anything unusual.

## Notes

- Preview and edit modes share the same YAML token color palette to avoid color drift.
- The CodeMirror editor is loaded lazily when needed to reduce initial UI load cost.

