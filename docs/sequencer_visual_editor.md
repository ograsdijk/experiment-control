# Sequencer Visual Editor

## Purpose

The sequencer UI provides a visual editor on top of YAML while keeping YAML as the source-of-truth format for load/run/export.

The editor is block/tree based because sequencer execution is ordered and nested, not graph-first.

## Current Layout

The sequencer modal has a sidebar and three full-height tabs.

The same Sequencer also opens as a page of its own: the icon next to the modal's title opens the dashboard URL with `?view=sequencer` in a new tab. That page shows only the Sequencer, with no dashboard rendering behind it, so it stays responsive on a slow PC. Each tab or window has its own editor text; only what is loaded into the sequencer process is shared.

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
- **Timeline**: what the loaded sequence does, as a flame graph that wraps (see below).

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

## Resolved Values

Each `${...}` in the Steps and YAML tabs shows its value next to it, without changing the text. The values come from `sequencer.resolve_vars`, which is called shortly after the editor text or the run overrides change.

- **Plain var:** the name and value, e.g. `freq_settle_s 0.05 s`. Sleep durations and `wait_until` timeouts get `s`.
- **Run override:** orange, with a dot. The tooltip gives the value without overrides. While a run is going, chips show the overrides it started with, and a sidebar override that differs is listed as "Next run".
- **Derived var** (written as `${...}` in `vars`): violet with `ƒ`. The tooltip and the step editor show the formula, the formula with values filled in, and the result. A derived var that changes because of an override also gets the orange dot.
- **Runtime name** (loop variable, loop index, `assign`, `save_as`, adaptive parameter): cyan, labelled `loop`, `index`, `assign`, ... The tooltip says which step sets it and, for a loop, what it runs over. While the editor holds the running sequence, it shows the current value in green instead. A var built from such a name (`label: run_${i}`) is shown the same way, with `ƒ`.
- **Unknown name:** yellow, when a name is neither a var nor set by any step in the sequence (likely a typo). Steps that set names implicitly are known: `wait_until` (`sample`, `samples`), adaptive (`score`, `<metric>_<stat>`) and call `extract`/`assign`.
- **Error:** red, with the message.

When a request fails (the YAML doesn't parse, or the sequencer doesn't answer), the last values stay but are dimmed, and the tooltip says why they may be out of date. Closing the modal drops them.

Numbers use about 5 significant figures with an exponent that is a multiple of 3 (`985.5e6`); tooltips show the full value. Lists and generators are summarized (`list[3]`, `linspace ×30`).

In the YAML tab, derived and overridden vars are also marked on their line in the `vars` block, and the line with the cursor hides its chips while you type (by CSS on CodeMirror's active line, so moving the cursor rebuilds nothing). The **Values** switch next to **Follow** hides all chips; the choice is kept per browser.

Step rows of `call` steps show their first two params, and `for` steps show their generator settings, so their values are visible too.

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

## Timeline

The **Timeline** tab draws the loaded sequence as a flame graph: every step is a block, and each loop or other container (`try`, `finally`, `atomic`, an opened `use:`) is a band above the blocks it holds, so nesting reads top to bottom. A `try`'s `finally` comes right after the `try` band, at the same depth (the `try` band then says `finally after`; a `try` with nothing in its body is a block of its own, `empty`). A disabled step, and everything inside it (for a `try`, its `finally` too), is greyed and striped. Blocks wrap onto more lines instead of running off one; a band that goes on to the next line has a dashed end and an arrow, and starts the next line with `↳`. All blocks are the same width, since most step times are unknown before a run. The branches of an `if` and of a `parallel` sit side by side in one block.

The counts come from `sequencer.plan`. Each block shows how often it runs in total (`×660`) and, where it is known, its time: a sleep's length, a `wait_until`'s timeout (`≤ 1:00`), or during and after a run the measured average. A loop's band shows its passes per run and, up to 48 passes, one square per pass. Counts that are not certain are marked: `≈` for approximate (an `if` decided only while running, a `while`), a striped band end for a loop whose passes are only known while running, `×?` where the count is unknown (`adaptive`). A loop over a list a call returns is counted from the list the call was given (`prepare_targets` given `freq_grid_hz`); the line under the summary says so. The summary also gives the total step runs, the time in sleeps, the longest the waits can take, and, once steps have been measured, the expected time at those averages. While idle the counts use the sidebar's run overrides.

The bar above the graph covers the whole run: steps outside any loop make one segment, every outermost loop one segment divided into its passes (labelled by number; the tooltip has the loop's values once the run is in it, in the order a shuffled loop visits them), and `finally` its own segment. Loops inside loops are not divided; the headline names them instead (`freq_hz 6 of 11 · x 3 of 4`).

During a run the running block is outlined, the bands of the loops it is inside are underlined, and blocks already done in the current pass are dimmed; each block shows runs so far against its total. Finished steps before the main loop fold into one block (a finished loop with them); click it to open it again. A `use:` block opens to show the sub-sequence's steps. Clicking any other block opens it in the Steps tab. Once steps have measured times, a bar under the graph splits one pass of the main loop by time.

The timeline describes the loaded sequence, so it is not shown while the editor holds other text.

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

