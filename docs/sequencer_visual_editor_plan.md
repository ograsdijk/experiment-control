# Sequencer Visual Editor Plan

## Goal

Add a visual editing experience for the sequencer UI while keeping YAML as the source-of-truth serialization format.

The visual editor should make sequences easier to understand and edit without turning the sequencer into a graph editor.

## Recommended UI Model

Use a nested block/tree editor as the main visual interface:

1. Left: step tree
2. Right: step inspector
3. Bottom or tabbed section: YAML preview/edit and diagnostics

This matches the actual sequencer model better than a node graph because the sequencer is primarily:

- ordered
- nested
- block-oriented

rather than an arbitrary graph with free-form edges.

## Why Not a Graph-First Editor

A graph suggests:

- arbitrary branching
- explicit edges
- multiple next steps
- dataflow semantics

The sequencer actually behaves more like:

- do this
- then do this
- when a block owns a body, run the child steps in order

So a graph would likely make the model harder to understand rather than easier.

## Why Not a Table-Only Editor

A simple table is not enough for the general sequencer because it breaks down once you have:

- nested loops
- adaptive steps
- repeated blocks
- condition/wait blocks

Tables are still useful as specialized editors for some step internals (for example parameter maps), but not as the full sequence editor.

## Phased Implementation

### Phase 1: Read-Only Structured View

Goal:

- make the loaded YAML understandable visually without replacing YAML editing yet

Add:

- a parsed step tree
- click-to-select a step
- a read-only inspector for the selected step
- YAML remains the only editable source

This phase should support rendering all existing step types as an outline, including nested blocks like:

- `for`
- `repeat`
- `adaptive`

### Phase 2: Basic Visual Editing

Goal:

- allow common simple steps to be edited visually

Start with:

- `call`
- `sleep`
- `repeat`

Add:

- add step
- delete step
- duplicate step
- reorder within a valid scope
- inspector forms for the selected step

YAML remains available as preview and fallback raw editing.

### Phase 3: Loop Editing

Goal:

- support common structured control flow visually

Add visual editing for:

- `for`

Inspector support should include:

- `bind`
- generator configuration
- nested `do` body navigation/editing

### Phase 4: Adaptive Editing

Goal:

- make adaptive studies usable without hand-editing YAML

Add visual editing for:

- `adaptive`

Inspector support should include:

- study `id`
- controller kind and config
- search space
- bind mapping
- state/observe configuration
- score expression
- stopping
- nested `do` body

### Phase 5: Advanced Step Support

Add visual editing for the remaining complex steps, such as:

- `wait_until`
- `set_context`
- `assign`
- `set`

## Suggested Layout

### Left Pane: Step Tree

Each step should show:

- step type
- short human-readable summary
- nesting/indentation

Block steps should show child steps indented underneath.

### Right Pane: Inspector

For the selected step, show:

- step type
- location / line range
- summary
- detailed read-only view in Phase 1
- editable form fields in later phases

### Bottom or Secondary Pane

Tabs or sections for:

- YAML
- diagnostics
- later: run status / live context

## Internal Model

The visual UI should edit a structured representation of the sequence, not raw text.

The long-term flow should be:

1. parse YAML into structured step data
2. edit structured step data visually
3. serialize back to YAML for load/run/export

For Phase 1, a read-only outline can be produced heuristically from the YAML text without requiring a full AST in the browser yet.

## Best First Milestone

The first useful milestone is:

1. read-only step tree for the current YAML
2. click to inspect a step
3. YAML still edited manually
4. diagnostics remain visible

That gives immediate usability value with low implementation risk.
