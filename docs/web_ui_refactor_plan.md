# Web UI Refactor Plan

## Goal

Refactor the React Web UI to reduce complexity in the top-level app, improve reuse of shared UI pieces, and make large features easier to maintain without changing runtime behavior.

The highest-priority outcomes are:

- shrink `web/react_ui/src/App.tsx` substantially
- reduce very large feature components and controller hooks
- consolidate repeated UI patterns into shared components
- move feature-specific code closer to its feature instead of keeping everything in top-level `components/`

## Current Hotspots

The main complexity centers at the time of writing are:

- `web/react_ui/src/App.tsx`
- `web/react_ui/src/features/hdf/useHdfController.ts`
- `web/react_ui/src/components/InterlocksModal.tsx`
- `web/react_ui/src/components/DaqWorkspacesModal.tsx`
- `web/react_ui/src/features/sequencer/useSequencerController.ts`
- `web/react_ui/src/components/DagGraphPreview.tsx`

These are the files that should drive the refactor order.

## Guiding Principles

1. Prefer larger structural cuts over micro-extractions.
2. Reuse grouped controller/state objects instead of exploding prop lists.
3. Put generic UI primitives in shared files.
4. Put feature-specific UI under the relevant feature where possible.
5. Keep behavior unchanged while refactoring unless a small cleanup naturally falls out.

## Phase 1: Break Up App.tsx

This is the highest-value first step.

### 1. Extract the main dashboard body

Create a `DashboardMainLayout` component to own the `AppShell.Main` body.

Target:

- move the page body out of `App.tsx`
- leave `App.tsx` responsible for top-level hooks, global state, and composition

Suggested file:

- `web/react_ui/src/components/DashboardMainLayout.tsx`

### 2. Split the dashboard body into major regions

Inside `DashboardMainLayout`, split into:

- `DeviceSidebar`
- `PanelsGrid`
- optional future `DashboardStatusRail` if the side/status UI grows further

This reduces the current single huge render tree into stable page regions.

### 3. Stop rendering every panel kind inline

Inside `PanelsGrid`, add a `PanelRenderer` switch that delegates by panel kind.

Create separate panel-card components such as:

- `TelemetryPanelCard`
- `StreamTracePanelCard`
- `StreamScalarPanelCard`
- `StreamBinStatsPanelCard`
- `StreamBin2dPanelCard`
- `StreamParamsPanelCard`

This is the biggest remaining structural cut in `App.tsx`.

## Phase 2: Move Orchestration Logic Out of App.tsx

After the render tree is split, move the large state/effect clusters into focused hooks.

### 1. Extract panel/grid state management

Create:

- `useDashboardPanels`
- `usePanelDragAndDrop`

Responsibilities:

- add/remove/update panels
- panel option setters
- y-axis modal draft state
- drag/drop hover and insertion state

### 2. Extract streaming subscription logic

Create:

- `useRawStreamSubscriptions`
- `useStreamAnalysisSubscriptions`

Responsibilities:

- websocket setup/teardown
- incoming message routing
- panel buffer updates
- overlay updates
- plot tick invalidation

This is one of the largest non-render complexity clusters still inside `App.tsx`.

### 3. Extract app bootstrap/status effects

Create:

- `useGatewayBootstrap`
- `useUnreadIndicators`

Responsibilities:

- initial settings bootstrap
- document title updates
- log websocket bootstrap
- command/log unread state logic

## Phase 3: Consolidate Shared UI Primitives

There is repeated UI structure across modals, settings, panels, and status displays.

### 1. Shared modal/form scaffolding

Add reusable components such as:

- `ModalFormShell`
- `ModalSection`
- `ModalActionRow`

Use them in:

- stream option modals
- settings modal
- HDF modals
- sequencer modal sections

### 2. Shared compact field controls

Add reusable small form controls such as:

- `InlineFieldLabel`
- `CompactNumberField`
- `CompactSelectField`
- `CompactToggleField`

These replace repeated combinations of:

- dimmed text labels
- compact `NumberInput`
- small `Select`
- small `Switch`

### 3. Shared badges and small status elements

Add generic components such as:

- `ClickableBadge`
- `ConnectionBadge`
- `MetricBadge`

These should replace repeated “light badge with optional click handler” patterns used in:

- panel cards
- header chips
- feature summaries
- connection-status labels

### 4. Shared clipboard actions

Add a common clipboard helper and, if useful, a tiny UI helper:

- `useClipboardActions`
- optional `CopyActionIcon`

This should unify copy flows currently spread across:

- logs
- params
- command history
- JSON inspectors

## Phase 4: Consolidate Plot-Related UI

The plot panels are separate, but many of their structural concerns overlap.

### 1. Add a shared plot frame

Introduce a wrapper such as:

- `PlotFrame`
- or `BasePlotPanel`

Responsibilities:

- common sizing
- color-scheme plumbing
- empty-state rendering
- shared toolbar placement

Apply to:

- `PlotPanel`
- `StreamRawPanel`
- `StreamWaterfallPanel`
- `StreamBinStatsPanel`
- `StreamBin2dPanel`

### 2. Standardize plot toolbar usage

Expand the role of `PlotToolbar` so it becomes the standard action row for plot-style panels.

Use it consistently for:

- expand
- axis settings
- trace/bin options
- panel-level actions

### 3. Unify plot option modal state

Move toward a single active plot-options state shape rather than one modal state per plot kind.

Longer-term target:

- one `activePlotOptions`
- one renderer that dispatches by panel kind

This will reduce modal-state sprawl in the top-level page.

## Phase 5: Split Large Feature Files Internally

After `App.tsx`, these are the next biggest maintainability issues.

### 1. DaqWorkspacesModal

Split `web/react_ui/src/components/DaqWorkspacesModal.tsx` into focused pieces, for example:

- `DaqWorkspaceHeader`
- `DaqNodesEditor`
- `DaqOutputsEditor`
- `DaqWorkspaceStoreActions`

Move feature-specific pieces under a stream feature subfolder, for example:

- `web/react_ui/src/features/stream/components/daq/...`

### 2. InterlocksModal

Split `web/react_ui/src/components/InterlocksModal.tsx` into:

- `InterlockProcessSection`
- `FollowerRulesTable`
- `InterlockRulesTable`
- `CommandRoutesSection`

### 3. HdfWriterModal

Split `web/react_ui/src/components/HdfWriterModal.tsx` into:

- `HdfStatusSection`
- `HdfRotateSection`
- `HdfMeasurementSection`
- `HdfDeviceControlSection`

### 4. Sequencer UI and controller

Split:

- `web/react_ui/src/components/SequencerModal.tsx`
- `web/react_ui/src/features/sequencer/useSequencerController.ts`

Suggested UI sections:

- `SequencerStatusHeader`
- `SequencerActionsRow`
- `SequencerEditorPane`
- `SequencerAdaptiveReuseSection`

Suggested controller splits:

- load/validate actions
- runtime status polling
- adaptive reuse state

### 5. HDF controller hook

Split `web/react_ui/src/features/hdf/useHdfController.ts` into focused hooks:

- `useHdfStatus`
- `useHdfMeasurementSchema`
- `useHdfRotateDraft`
- `useHdfMeasurementNoteDraft`
- `useHdfDeviceToggles`

## Phase 6: Reorganize Shared vs Feature-Specific Files

The current file layout mixes global reusable components with feature-specific UI.

### 1. Add `components/shared/`

Use this for truly generic visual components:

- badges
- modal shells
- compact field rows
- empty states
- small clipboard actions

### 2. Keep `features/common/` for generic hooks/helpers

Use it for:

- normalization helpers
- shallow compare helpers
- async action wrappers
- clipboard hooks
- generic form helpers

### 3. Move feature-specific components under `features/<feature>/components/`

Examples:

- `features/stream/components/...`
- `features/sequencer/components/...`
- `features/hdf/components/...`
- `features/interlocks/components/...`

The top-level `components/` folder should gradually become mostly generic, reusable UI.

## Phase 7: Reduce Prop Explosion

One risk in this refactor is making `App.tsx` shorter while creating giant prop interfaces elsewhere.

### 1. Prefer grouped prop objects

Use grouped props such as:

- `header`
- `settings`
- `logs`
- `daq`
- `deviceCommand`
- `plotOptions`

instead of flattening dozens of unrelated props.

### 2. Pass controller/state objects where reasonable

Continue the current direction of passing grouped hook return objects such as:

- `processesController`
- `commandHistoryController`
- `processCommandController`
- `interlocksController`
- `sequencerController`
- `hdfController`

This keeps component boundaries simpler and reduces repacking.

### 3. Add narrow adapter types per boundary

Where a full hook return is too broad, define a focused interface for that boundary rather than passing dozens of scalars individually.

## Recommended Execution Order

Implement in this order:

1. Extract `DashboardMainLayout`
2. Extract `PanelsGrid`
3. Extract `PanelRenderer` and per-panel-card components
4. Move websocket/subscription logic into dedicated hooks
5. Split `DaqWorkspacesModal`
6. Split `InterlocksModal`
7. Split `useHdfController`
8. Add shared modal/form/badge primitives
9. Move feature-specific UI under `features/*/components`

This order prioritizes the largest structural wins first.

## Success Criteria

The refactor should aim for:

- `App.tsx` below roughly 3000 lines
- no feature modal exceeding roughly 400-500 lines without a clear reason
- controller hooks split by concern rather than owning an entire feature
- top-level `components/` mostly containing generic or cross-feature reusable UI

## TODO Notes

As this refactor proceeds:

- prefer behavior-preserving extractions first
- avoid mixing feature work with structural refactors in the same change
- keep validating with `npm --prefix web/react_ui run build` after each stage
